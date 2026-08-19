"""Build the Arbiter graph from HERB and load it into HydraDB.

Split into two phases because HydraDB v0.1.0 has two write forms with very
different costs:

  * property nodes  - one labelled `CREATE ... -[:REL]->...` per node, ~62ms
                      each. Only employees, releases and roles need properties,
                      which is ~700 writes.
  * edges           - `UNWIND $rows` batches of 500. Everything else, ~100k edges.

Artifacts carry no properties at all: their kind is encoded in their integer id
range (see `schema.py`), so the graph stays cheap to load and the typed-edge
constraint costs nothing to evaluate.
"""
from __future__ import annotations

import glob
import re
import time
from pathlib import Path

from arbiter import schema
from arbiter.herb import load_employees, load_product, load_questions
from arbiter.schema import IdMap

SAFE = re.compile(r'[^A-Za-z0-9 ._\-/&()+]')


def _q(s) -> str:
    """Escape for inline Cypher string literals (no string params on v0.1.0)."""
    return SAFE.sub("", str(s or ""))[:120]


class GraphBuild:
    def __init__(self):
        self.ids = IdMap()
        self.employees: dict[str, dict] = {}
        self.roles: set[str] = set()
        self.releases: list[dict] = []
        self.edges: dict[str, list[tuple[int, int]]] = {k: [] for k in schema.EDGE_TYPES}
        self.artifact_kind: dict[str, str] = {}
        self.products: set[str] = set()
        self.documents: list[dict] = []

    # -- helpers -----------------------------------------------------------
    def emp(self, key): return self.ids.get("employee", key)
    def role(self, key): return self.ids.get("role", key)
    def art(self, key, kind): return self.ids.get(kind, key)
    def rel(self, key): return self.ids.get("release", key)
    def prod(self, key): return self.ids.get("product", key)
    def comp(self, key): return self.ids.get("company", key)
    def cust(self, key): return self.ids.get("customer", key)

    def edge(self, rel, a, b):
        self.edges[rel].append((a, b))

    # -- ingest ------------------------------------------------------------
    def add_employees(self, rows):
        for e in rows:
            eid = e.get("employee_id")
            if not eid:
                continue
            self.employees[eid] = e
            r = e.get("role") or "Unknown"
            self.roles.add(r)
            self.edge(schema.HAS_ROLE, self.emp(eid), self.role(r))

    def add_org(self, team_rows):
        """salesforce_team.json nests VP -> engineering_leads -> engineers."""
        def walk(node, manager=None):
            eid = node.get("employee_id")
            if eid and manager:
                self.edge(schema.REPORTS_TO, self.emp(eid), self.emp(manager))
            for key in ("engineering_leads", "engineers", "reports"):
                for child in node.get(key) or []:
                    if isinstance(child, dict):
                        walk(child, eid)
        for top in team_rows:
            walk(top)

    def add_customers(self, rows):
        for c in rows:
            cid, company = c.get("id"), c.get("company")
            if cid and company:
                self.edge(schema.WORKS_FOR, self.cust(cid), self.comp(company))

    def add_product(self, path):
        g = load_product(path)
        self.products.add(g.product)
        pid = self.prod(g.product)

        for r in g.order_releases():
            rid = self.rel(r["release_id"])
            self.releases.append({"id": rid, "key": r["release_id"],
                                  "seq": r["seq"], "product_id": pid})
            self.edge(schema.OF_PRODUCT, rid, pid)

        for p in g.precedes():
            self.edge(schema.PRECEDES, self.rel(p["earlier"]), self.rel(p["later"]))

        for a in g.artifacts:
            self.artifact_kind[a["artifact_id"]] = a["kind"]

        for e in g.authored:
            k = self.artifact_kind.get(e["artifact_id"])
            if k and e["employee_id"] in self.employees:
                self.edge(schema.AUTHORED, self.emp(e["employee_id"]),
                          self.art(e["artifact_id"], k))

        for e in g.about_release:
            k = self.artifact_kind.get(e["artifact_id"])
            if k:
                self.edge(schema.ABOUT_RELEASE, self.art(e["artifact_id"], k),
                          self.rel(e["release_id"]))

        for did, dtype in g.doc_types.items():
            self.documents.append({"id": self.art(did, "document"), "key": did,
                                   "dtype": dtype, "product_id": pid})

        for r in g.reviews:
            sk = self.artifact_kind.get(r["artifact_id"])
            dk = self.artifact_kind.get(r["document_id"])
            if sk and dk:
                self.edge(schema.REVIEWS, self.art(r["artifact_id"], sk),
                          self.art(r["document_id"], dk))

        for m in g.mentions:
            k = self.artifact_kind.get(m["artifact_id"])
            if k and m["employee_id"] in self.employees:
                self.edge(schema.MENTIONS, self.art(m["artifact_id"], k),
                          self.emp(m["employee_id"]))
        return g

    # -- stats -------------------------------------------------------------
    def summary(self):
        return {
            "employees": len(self.employees),
            "roles": len(self.roles),
            "products": len(self.products),
            "releases": len(self.releases),
            "artifacts": len(self.artifact_kind),
            "edges_total": sum(len(v) for v in self.edges.values()),
            "edges_by_type": {k: len(v) for k, v in self.edges.items() if v},
        }


def build_all(data_dir="data/herb", products=None) -> GraphBuild:
    b = GraphBuild()
    b.add_employees(load_employees(f"{data_dir}/metadata/employee.json"))
    team = Path(f"{data_dir}/metadata/salesforce_team.json")
    if team.exists():
        import json
        b.add_org(json.loads(team.read_text(encoding="utf-8")))
    cust = Path(f"{data_dir}/metadata/customers_data.json")
    if cust.exists():
        import json
        b.add_customers(json.loads(cust.read_text(encoding="utf-8")))
    files = sorted(glob.glob(f"{data_dir}/products/*.json"))
    if products:
        files = [f for f in files if Path(f).stem in set(products)]
    for f in files:
        b.add_product(f)
    return b


def load_into_hydra(b: GraphBuild, hydra, verbose=True, batch=500):
    """Push the build into HydraDB. Returns timing/count stats."""
    t0 = time.time()
    stats = {}

    # 1. Employees (carry name + role) -- one labelled CREATE each.
    n = 0
    for key, e in b.employees.items():
        hydra.run(schema.CREATE_EMPLOYEE.format(
            eid=b.emp(key), key=_q(key), name=_q(e.get("name")),
            role=_q(e.get("role")), rid=b.role(e.get("role") or "Unknown")))
        n += 1
        if verbose and n % 100 == 0:
            print(f"    employees {n}/{len(b.employees)}", flush=True)
    stats["employees"] = n
    t_emp = time.time()

    # 2. Releases (carry seq -- the ordering the whole task depends on).
    for r in b.releases:
        hydra.run(schema.CREATE_RELEASE.format(
            relid=r["id"], key=_q(r["key"]), seq=r["seq"], pid=r["product_id"]))
    stats["releases"] = len(b.releases)

    # 2b. Documents (carry their type -- lets doc-type questions stay server-side).
    for d in b.documents:
        hydra.run(schema.CREATE_DOCUMENT.format(
            did=d["id"], key=_q(d["key"]), dtype=_q(d["dtype"]), pid=d["product_id"]))
    stats["documents"] = len(b.documents)
    t_rel = time.time()

    # 3. Edges in bulk.
    edge_counts = {}
    for rel, pairs in b.edges.items():
        if not pairs:
            continue
        # HAS_ROLE / OF_PRODUCT already created above with their nodes
        if rel in (schema.HAS_ROLE, schema.OF_PRODUCT):
            continue
        written = hydra.batch_edges(rel, pairs, chunk=batch)
        edge_counts[rel] = written
        if verbose:
            print(f"    {rel:<16} {written}", flush=True)
    stats["edges"] = edge_counts
    stats["timing"] = {
        "employees_s": round(t_emp - t0, 1),
        "releases_s": round(t_rel - t_emp, 1),
        "edges_s": round(time.time() - t_rel, 1),
        "total_s": round(time.time() - t0, 1),
    }
    return stats
