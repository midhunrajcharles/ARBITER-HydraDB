"""Arbiter HTTP API.

Serves the resolver over HTTP and, deliberately, returns the Cypher it executed
alongside every answer. The traversal is the argument this project is making, so
hiding it behind a JSON blob would be the wrong product decision: a judge, or an
engineer, should be able to read the query that produced the answer and re-run it
themselves.

    uvicorn arbiter.api:app --port 8000
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

# Windows' mimetypes registry often lacks .webp, which would otherwise make
# StaticFiles serve it as text/plain and break rendering as a CSS background.
mimetypes.add_type("image/webp", ".webp")

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from arbiter.build import build_all
from arbiter.benchmark import load_benchmark_report
from arbiter.herb import load_questions
from arbiter.hydra import Hydra
from arbiter.query import Resolver

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

app = FastAPI(title="Arbiter", description="Graph-native enterprise context resolution on HydraDB")
app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

_state: dict = {}


def state():
    if "resolver" not in _state:
        h = Hydra()
        b = build_all()
        _state["hydra"] = h
        _state["build"] = b
        _state["resolver"] = Resolver(h, b)
    return _state


class Ask(BaseModel):
    question: str
    product: str


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
def health():
    h = state()["hydra"]
    return {"hydradb_host": h.host, "ready": h.ready()}


@app.get("/api/stats")
def stats():
    """Loader statistics.

    `artifacts` counts every artifact seen while parsing HERB. `artifacts_loaded`
    counts the ones that were actually minted an id and written to the graph -
    an artifact that takes part in no edge never reaches HydraDB. The two differ,
    so anything describing the database must use the second; the first describes
    the corpus. /api/ontology reports the same loaded figure.
    """
    from arbiter import schema as sch

    b = state()["build"]
    s = b.summary()
    s["artifacts_loaded"] = sum(b.ids.count(k) for k in sch.ARTIFACT_KINDS)
    s["hydradb"] = {"host": state()["hydra"].host, "ready": state()["hydra"].ready()}
    return s


@app.get("/api/benchmark")
def benchmark():
    """Headline figures, read out of results/eval_hydra.json at request time.

    The page shows no literal statistic of its own. Every number it renders is
    derived here from the evaluation artifact, so the UI cannot drift away from
    what was actually measured - if the eval is re-run, the page changes with it.

    `demo` picks what the page auto-resolves on load. The rule is deliberately
    blind to correctness: the alphabetically-first product that has a release
    participation question, and that product's first question. It does not
    filter on whether the case passed.
    """
    path = ROOT / "results" / "eval_hydra.json"
    if not path.exists():
        return JSONResponse({"error": "no evaluation artifact"}, status_code=404)
    d = load_benchmark_report(path)
    s = d["summary"]
    prev, docs = s["person_previous_release"], s["doc_reviewers"]
    unans, ans = s["abstain_unanswerable"], s["abstain_answerable"]

    prev_cases = [r for r in d["detail"] if r["family"] == "person_previous_release"]
    first_product = sorted({r["product"] for r in prev_cases})[0]
    first_q = next(r for r in prev_cases if r["product"] == first_product)

    return {
        "release_participation": {
            "n": prev["n"],
            "arbiter_exact": prev["a_exact"],
            "arbiter_pct": round(100 * prev["a_exact"] / prev["n"]),
            "bm25_exact": prev["b_exact"],
            "bm25_pct": round(100 * prev["b_exact"] / prev["n"]),
        },
        "doc_reviewers": {
            "n": docs["n"],
            "arbiter_exact": docs["a_exact"],
            "arbiter_f1": docs["a_f1"],
            "bm25_f1": docs["b_f1"],
        },
        "abstention": {
            "n": unans["n"],
            "correct": unans["a_exact"],
            "false_refusals": len(s.get("false_refusals") or {}),
            "answerable_n": ans["n"],
            "answerable_correct": ans["a_exact"],
        },
        "elapsed_s": d.get("elapsed_s"),
        "demo": {"product": first_product, "question": first_q["question"]},
    }


@app.get("/api/ontology")
def ontology():
    """The schema as the loader actually built it.

    Node counts come from the IdMap (which minted every id), edge counts from
    the build, and relationship endpoints are declared here to match
    `build.py` exactly. The diagrams in the UI render from this - nothing about
    the ontology is typed into the page by hand.
    """
    from arbiter import schema as sch

    b = state()["build"]
    nodes = {k: b.ids.count(k) for k in sch.BASE}
    artifact_kinds = {k: nodes[k] for k in sch.ARTIFACT_KINDS}
    s = b.summary()
    e = s["edges_by_type"]

    # Endpoints and cardinality come from schema.EDGE_ENDPOINTS - the same
    # declaration scripts/gen_dbml.py reads - so the UI diagrams, schema.dbml
    # and the README ER diagram cannot disagree with each other.
    rels = []
    for rel in sch.EDGE_TYPES:
        src, dst, card = sch.EDGE_ENDPOINTS[rel]
        n = e.get(rel, 0)
        r = {"rel": rel, "from": src, "to": dst, "card": card, "n": n}
        if src == dst:
            r["self"] = True
        if rel == sch.PRECEDES:
            r["key"] = True
        if rel in sch.UNPOPULATED:
            r["declared_only"] = True
        rels.append(r)

    herb_sources = ["team", "customers", "slack", "documents",
                    "meeting_transcripts", "meeting_chats", "urls", "prs"]

    # The address the resolver actually talks to, read off the live client, so
    # the infrastructure diagram states the endpoint in use rather than one
    # typed into the page.
    from urllib.parse import urlparse
    h = state()["hydra"]
    u = urlparse(h.url)

    return {
        "engine": {"host": h.host, "query_url": h.url, "admin_url": h.admin,
                   "query_path": u.path, "query_port": u.port},
        "nodes": nodes,
        "nodes_total": sum(nodes.values()),
        "artifact_kinds": artifact_kinds,
        "artifacts_total": sum(artifact_kinds.values()),
        "edges": e,
        "edges_total": s["edges_total"],
        "id_bands": {k: v for k, v in sch.BASE.items()},
        "id_span": sch.SPAN,
        "relationships": rels,
        "herb_sources": herb_sources,
        "herb_source_count": len(herb_sources),
    }


MAX_ROWS = 200
NEIGHBOUR_SAMPLE = 25


def _entity_of(node_id: int):
    from arbiter import schema as sch
    k = sch.kind_of(node_id)
    return (sch.ENTITY_OF_KIND.get(k), k) if k else (None, None)


@app.get("/api/browse")
def browse(label: str, limit: int = 50, offset: int = 0, q: str = "",
           sort: str = "id", dir: str = "asc"):
    """A table view of one label, straight out of HydraDB.

    Every row is whatever the engine returned for the query echoed back in
    `queries` - there is no local table, no cache and no synthesised row. If a
    property is not stored on the node it comes back null rather than being
    filled in from the loader; the one resolver-side value (the name for Role
    and Product, which the graph genuinely does not hold) is returned in a
    separate column flagged `source: resolver` so it cannot be mistaken for
    stored data.
    """
    from arbiter import schema as sch

    if label not in sch.STORED_PROPS:
        return JSONResponse(
            {"error": f"unknown label {label!r}",
             "supported": sorted(sch.STORED_PROPS)}, status_code=400)

    props = sch.STORED_PROPS[label]
    sortable = ["id"] + props
    if sort not in sortable:
        return JSONResponse({"error": f"cannot sort on {sort!r}",
                             "sortable": sortable}, status_code=400)
    direction = "DESC" if str(dir).lower() == "desc" else "ASC"
    limit = max(1, min(int(limit), MAX_ROWS))
    offset = max(0, int(offset))

    search_on = sch.SEARCH_PROP.get(label)
    q = (q or "").strip()
    where, params = "", {}
    if q:
        if not search_on:
            return JSONResponse(
                {"error": f"{label} nodes store no searchable property",
                 "detail": "in the graph these nodes carry an id only"},
                status_code=400)
        where = f" WHERE n.{search_on} STARTS WITH $q"
        params["q"] = q

    proj = ", ".join([f"n.{c} AS {c}" for c in ["id"] + props])
    count_q = f"MATCH (n:{label}){where} RETURN count(*) AS c"
    rows_q = (f"MATCH (n:{label}){where} RETURN {proj} "
              f"ORDER BY n.{sort} {direction} SKIP {offset} LIMIT {limit}")

    h = state()["hydra"]
    try:
        counted = h.run(count_q, params)
        rows = h.run(rows_q, params)
    except Exception as exc:
        return JSONResponse({"error": "HydraDB rejected the query",
                             "detail": str(exc)[:400],
                             "queries": [count_q, rows_q]}, status_code=502)
    # Defaulting the total to 0 here would paginate real rows against a made-up
    # total ("1-25 of 0"). count(*) always returns a row on v0.1.0, so an empty
    # result is the engine misbehaving and is reported as such.
    if not counted or counted[0].get("c") is None:
        return JSONResponse({"error": "HydraDB returned no count",
                             "detail": f"{count_q} produced no row",
                             "queries": [count_q, rows_q]}, status_code=502)
    total = counted[0]["c"]

    columns = [{"name": c, "source": "graph", "sortable": True}
               for c in ["id"] + props]
    if not props:
        columns.append({"name": "name", "source": "resolver", "sortable": False})
        ids = state()["build"].ids
        for r in rows:
            r["name"] = ids.key(r["id"])

    return {
        "label": label, "columns": columns, "rows": rows,
        "total": total, "limit": limit, "offset": offset,
        "sort": sort, "dir": direction.lower(), "q": q,
        "searchable_on": search_on,
        "queries": [count_q, rows_q],
        "params": params,
    }


@app.get("/api/node/{node_id}")
def node(node_id: int):
    """One node's stored properties plus its 1-hop neighbourhood.

    Which edges are even possible for this node is derived from
    schema.EDGE_ENDPOINTS, so the tree cannot list a branch the ontology does
    not allow. Each branch carries the Cypher that produced it and a real
    count(*) - the sample is capped, the count is not.
    """
    from arbiter import schema as sch

    entity, kind = _entity_of(node_id)
    if not entity:
        return JSONResponse(
            {"error": f"id {node_id} falls in no known band",
             "bands": {k: v for k, v in sch.BASE.items()}}, status_code=404)

    h = state()["hydra"]
    ids = state()["build"].ids
    props = sch.STORED_PROPS.get(entity, [])
    queries = []

    if props:
        proj = ", ".join([f"n.{c} AS {c}" for c in ["id"] + props])
        pq = f"MATCH (n:{entity} {{id: {node_id}}}) RETURN {proj}"
    else:
        pq = f"MATCH (n {{id: {node_id}}}) RETURN n.id AS id"
    queries.append(pq)
    try:
        found = h.run(pq)
    except Exception as exc:
        return JSONResponse({"error": "HydraDB rejected the query",
                             "detail": str(exc)[:400], "queries": queries},
                            status_code=502)
    if not found:
        return JSONResponse({"error": f"no node with id {node_id}",
                             "queries": queries}, status_code=404)

    properties = dict(found[0])
    resolver_key = ids.key(node_id)

    # Document is an artifact kind, so it also participates in Artifact edges.
    aliases = {entity} | ({"Artifact"} if entity == "Document" else set())

    branches = []
    for rel in sch.EDGE_TYPES:
        src, dst, card = sch.EDGE_ENDPOINTS[rel]
        for direction in ("out", "in"):
            if direction == "out" and src not in aliases:
                continue
            if direction == "in" and dst not in aliases:
                continue
            other = dst if direction == "out" else src
            if direction == "out":
                cq = f"MATCH (n {{id: {node_id}}})-[:{rel}]->(m) RETURN count(*) AS c"
                sq = (f"MATCH (n {{id: {node_id}}})-[:{rel}]->(m) "
                      f"RETURN m.id AS id LIMIT {NEIGHBOUR_SAMPLE}")
            else:
                cq = f"MATCH (m)-[:{rel}]->(n {{id: {node_id}}}) RETURN count(*) AS c"
                sq = (f"MATCH (m)-[:{rel}]->(n {{id: {node_id}}}) "
                      f"RETURN m.id AS id LIMIT {NEIGHBOUR_SAMPLE}")
            try:
                counted = h.run(cq)
            except Exception as exc:
                # Skipping a failed branch would silently under-report the
                # neighbourhood, which reads as "no such edges" rather than
                # "the engine did not answer". Say which it is.
                return JSONResponse({"error": "HydraDB rejected the query",
                                     "detail": str(exc)[:400],
                                     "queries": queries + [cq]}, status_code=502)
            # Same rule for a missing count row: "0 neighbours" is a claim
            # about the graph, so it is only ever reported when the engine
            # actually said zero.
            if not counted or counted[0].get("c") is None:
                return JSONResponse({"error": "HydraDB returned no count",
                                     "detail": f"{cq} produced no row",
                                     "queries": queries + [cq]}, status_code=502)
            n = counted[0]["c"]
            queries.append(cq)
            sample = []
            if n:
                queries.append(sq)
                for row in h.run(sq):
                    nid = row.get("id")
                    ent, _k = _entity_of(nid) if nid is not None else (None, None)
                    sample.append({"id": nid, "entity": ent, "key": ids.key(nid)})
            branches.append({
                "rel": rel, "direction": direction, "other": other,
                "cardinality": card, "count": n,
                "sample": sample, "truncated": n > len(sample),
                "queries": [cq] + ([sq] if n else []),
                "declared_only": rel in sch.UNPOPULATED,
            })

    return {
        "id": node_id, "entity": entity, "kind": kind,
        "resolver_key": resolver_key,
        "properties": properties,
        "stored_props": props,
        "branches": branches,
        "total_neighbours": sum(b["count"] for b in branches),
        "queries": queries,
    }


@app.get("/api/products")
def products():
    b = state()["build"]
    return sorted(b.products)


@app.get("/api/questions")
def questions(product: str, limit: int = 12):
    """Real HERB questions, so the demo is never driven by invented input."""
    path = ROOT / f"data/herb/products/{product}.json"
    if not path.exists():
        return JSONResponse({"error": f"unknown product {product}"}, status_code=404)
    ans, unans = load_questions(path)
    keep = [q for q in ans if q.get("type") == "person"]
    out = [{"question": q["question"], "answerable": True,
            "ground_truth": q["ground_truth"]} for q in keep[:limit]]
    out += [{"question": u, "answerable": False, "ground_truth": []}
            for u in unans[:4]]
    return out


@app.post("/api/ask")
def ask(body: Ask):
    r = state()["resolver"]
    b = state()["build"]

    try:
        return _ask(r, b, body)
    except Exception as exc:
        # An engine failure is not an abstention. Abstaining means "no path
        # exists"; this means "the question was never put to the graph", and
        # conflating them would turn an outage into a claim about the data.
        return JSONResponse({"error": "HydraDB rejected the query",
                             "detail": str(exc)[:400],
                             "answered": False, "abstained": False,
                             "answer": [], "queries": [], "evidence": []},
                            status_code=502)


def _ask(r, b, body: "Ask"):
    ok, reason = r.can_answer(body.question, body.product)
    if not ok:
        return {
            "answered": False,
            "abstained": True,
            "reason": reason,
            "answer": [],
            "queries": [],
            "evidence": [],
        }

    family, (answer, trace) = r.route(body.question, body.product)
    if family == "unsupported":
        return {
            "answered": False, "abstained": True,
            "reason": "no traversal is registered for this question shape",
            "family": family, "answer": [], "queries": [], "evidence": [],
        }

    named = []
    for eid in answer:
        e = b.employees.get(eid, {})
        named.append({"employee_id": eid, "name": e.get("name"), "role": e.get("role")})
    return {
        "answered": True,
        "abstained": False,
        "family": family,
        "answer": named,
        "role_matched": trace.get("role_matched") or trace.get("doc_type"),
        "queries": trace.get("queries", []),
        "evidence": trace.get("evidence", []),
    }


# ---------------------------------------------------------------------------
# Graph explorer
#
# Two endpoints feeding a 3D force-directed view. They keep the same rule as
# the rest of this API: a node or a link exists on screen only because HydraDB
# returned a row for it. There is no fixture, no sample graph and no
# client-side synthesis - if the engine is unreachable these return 502 and the
# UI renders an error, rather than showing something that looks like the graph
# but is not.
# ---------------------------------------------------------------------------

GRAPH_BRANCH_CAP = 60


def _graph_node(node_id: int, ids, name=None, **extra):
    """One node record.

    `name` is passed in only when a query actually read it off the node. When
    it is not, the label falls back to the loader's id ledger and says so, so a
    reader can tell a stored value from a resolver-side one - Product and Role
    nodes carry an id and nothing else in the graph.
    """
    from arbiter import schema as sch

    kind = sch.kind_of(node_id)
    rec = {
        "id": node_id,
        "kind": kind,
        "label": sch.ENTITY_OF_KIND.get(kind) if kind else None,
        "name": name if name is not None else ids.key(node_id),
        "name_source": "graph" if name is not None else "resolver",
    }
    rec.update(extra)
    return rec


@app.get("/api/graph/seed")
def graph_seed(product: str):
    """A product, its releases, the ordering between them, and their documents.

    The product name is resolved to an id through the loader's ledger because
    the graph does not store product names; everything after that - which
    releases exist, how they are ordered, which documents are about them - is
    read out of HydraDB by the three queries echoed back in `queries`.
    """
    from arbiter import schema as sch

    b = state()["build"]
    if product not in b.products:
        return JSONResponse(
            {"error": f"unknown product {product!r}",
             "supported": sorted(b.products)}, status_code=404)

    pid, ids, h = b.prod(product), b.ids, state()["hydra"]

    q_rel = (f"MATCH (p:Product {{id: {pid}}})<-[:{sch.OF_PRODUCT}]-(r:Release) "
             f"RETURN r.id AS id, r.key AS key, r.seq AS seq")
    q_prec = (f"MATCH (p:Product {{id: {pid}}})<-[:{sch.OF_PRODUCT}]-(a:Release)"
              f"-[:{sch.PRECEDES}]->(b:Release) RETURN a.id AS s, b.id AS t")
    q_doc = (f"MATCH (p:Product {{id: {pid}}})<-[:{sch.OF_PRODUCT}]-(r:Release)"
             f"<-[:{sch.ABOUT_RELEASE}]-(d:Document) "
             f"RETURN r.id AS rid, d.id AS did, d.key AS dkey, d.dtype AS dtype")
    queries = [q_rel, q_prec, q_doc]

    try:
        releases, precedes, docs = h.run(q_rel), h.run(q_prec), h.run(q_doc)
    except Exception as exc:
        return JSONResponse({"error": "HydraDB rejected the query",
                             "detail": str(exc)[:400], "queries": queries},
                            status_code=502)

    nodes = {pid: _graph_node(pid, ids)}
    links = []

    for r in releases:
        rid = r["id"]
        nodes[rid] = _graph_node(rid, ids, name=r.get("key"), seq=r.get("seq"))
        links.append({"source": rid, "target": pid, "type": sch.OF_PRODUCT})

    for e in precedes:
        s, t = e["s"], e["t"]
        # `b` is only constrained to be a Release, so if the ordering ever left
        # this product the far node is still a real row - add it rather than
        # dropping the edge and quietly under-reporting the ordering.
        for nid in (s, t):
            if nid not in nodes:
                nodes[nid] = _graph_node(nid, ids)
        links.append({"source": s, "target": t, "type": sch.PRECEDES})

    for d in docs:
        did = d["did"]
        nodes[did] = _graph_node(did, ids, name=d.get("dkey"), dtype=d.get("dtype"))
        links.append({"source": did, "target": d["rid"], "type": sch.ABOUT_RELEASE})

    return {
        "seed": {"product": product, "id": pid, "name_source": "resolver"},
        "nodes": list(nodes.values()), "links": links,
        "counts": {"nodes": len(nodes), "links": len(links)},
        "queries": queries,
    }


@app.get("/api/graph/expand")
def graph_expand(id: int):
    """The 1-hop neighbourhood of one node, tagged by edge type.

    v0.1.0 has no `type(r)`, so an edge's type cannot be read back from a
    traversal - it can only be known by asking for one type at a time. Every
    edge type in schema.EDGE_TYPES is therefore queried in both directions and
    the answer is tagged with the type that produced it. Directions the
    ontology forbids are deliberately not skipped: that keeps this endpoint
    honest even if EDGE_ENDPOINTS were ever wrong, and it sidesteps Document
    doubling as an Artifact.
    """
    from arbiter import schema as sch

    entity, kind = _entity_of(id)
    if not entity:
        return JSONResponse(
            {"error": f"id {id} falls in no known band",
             "bands": {k: v for k, v in sch.BASE.items()}}, status_code=404)

    h, ids = state()["hydra"], state()["build"].ids
    cols = sch.STORED_PROPS.get(entity, [])
    if cols:
        proj = ", ".join([f"n.{c} AS {c}" for c in ["id"] + cols])
        pq = f"MATCH (n:{entity} {{id: {id}}}) RETURN {proj}"
    else:
        pq = f"MATCH (n {{id: {id}}}) RETURN n.id AS id"
    queries = [pq]

    try:
        found = h.run(pq)
    except Exception as exc:
        return JSONResponse({"error": "HydraDB rejected the query",
                             "detail": str(exc)[:400], "queries": queries},
                            status_code=502)
    if not found:
        return JSONResponse({"error": f"no node with id {id}",
                             "queries": queries}, status_code=404)

    properties = dict(found[0])
    stored_name = properties.get("name") or properties.get("key")
    nodes = {id: _graph_node(id, ids, name=stored_name)}
    links, branches = [], []

    for rel in sch.EDGE_TYPES:
        for direction in ("out", "in"):
            if direction == "out":
                q = (f"MATCH (n {{id: {id}}})-[:{rel}]->(m) "
                     f"RETURN m.id AS id LIMIT {GRAPH_BRANCH_CAP}")
            else:
                q = (f"MATCH (m)-[:{rel}]->(n {{id: {id}}}) "
                     f"RETURN m.id AS id LIMIT {GRAPH_BRANCH_CAP}")
            try:
                rows = h.run(q)
            except Exception as exc:
                # A partial neighbourhood would silently under-report the graph,
                # which is worse than saying the engine failed.
                return JSONResponse({"error": "HydraDB rejected the query",
                                     "detail": str(exc)[:400],
                                     "queries": queries + [q]}, status_code=502)
            queries.append(q)
            if not rows:
                continue
            for row in rows:
                mid = row.get("id")
                if mid is None:
                    continue
                if mid not in nodes:
                    nodes[mid] = _graph_node(mid, ids)
                links.append({"source": id, "target": mid, "type": rel}
                             if direction == "out" else
                             {"source": mid, "target": id, "type": rel})
            branches.append({"rel": rel, "direction": direction,
                             "returned": len(rows),
                             "capped": len(rows) == GRAPH_BRANCH_CAP})

    return {
        "focus": {"id": id, "entity": entity, "kind": kind,
                  "properties": properties, "resolver_key": ids.key(id),
                  "stored_props": cols},
        "nodes": list(nodes.values()), "links": links, "branches": branches,
        "counts": {"nodes": len(nodes), "links": len(links)},
        "cap": GRAPH_BRANCH_CAP,
        "queries": queries,
    }
