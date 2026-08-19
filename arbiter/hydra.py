"""HydraDB client - OpenCypher over the HTTP JSON API.

HydraDB is an object-store-native distributed graph database written in Rust,
storing on SlateDB, speaking OpenCypher over Bolt 5.x and an HTTP JSON/NDJSON
API, with SuiteSparse GraphBLAS traversal.

Endpoints on a dev node:
    Bolt   127.0.0.1:7687   Neo4j-driver compatible
    HTTP   127.0.0.1:8443   /v1/graphs/{graph}/query
    Admin  127.0.0.1:9090   /readyz, Prometheus metrics

We use HTTP: no driver dependency, and the same path handles batch UNWIND writes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

DEFAULT_TOKEN = "local-development-token-32-bytes"


class HydraError(RuntimeError):
    pass


def _default_host() -> str:
    """The node runs inside WSL; .wslip records its address."""
    p = Path(__file__).resolve().parents[1] / ".wslip"
    if p.exists():
        ip = p.read_text().strip()
        if ip:
            return ip
    return "127.0.0.1"


class Hydra:
    def __init__(self, host=None, graph="default", cell_id="cell-0",
                 token=None, namespace="default", timeout=120.0):
        self.host = host or os.getenv("HYDRA_HOST") or _default_host()
        self.graph = graph
        self.cell_id = cell_id
        self.token = token or os.getenv("HYDRA_TOKEN", DEFAULT_TOKEN)
        self.url = f"http://{self.host}:8443/v1/graphs/{graph}/query"
        self.admin = f"http://{self.host}:9090"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Graph-Namespace": namespace,
            "Content-Type": "application/json",
        }
        self._c = httpx.Client(timeout=timeout)

    # -- lifecycle ---------------------------------------------------------
    def ready(self) -> bool:
        try:
            return self._c.get(f"{self.admin}/readyz", timeout=5).status_code == 200
        except Exception:
            return False

    def verify(self) -> bool:
        """A listening port is not proof; a round-tripped write is."""
        self.run("CREATE (a {id: 999999})-[:_VERIFY]->(b {id: 999998})")
        rows = self.run("MATCH (a {id: 999999})-[:_VERIFY]->(b) RETURN b.id AS id")
        return bool(rows) and rows[0].get("id") == 999998

    # -- query -------------------------------------------------------------
    def run(self, query: str, parameters: dict | None = None) -> list[dict]:
        body = {"cell_id": self.cell_id, "query": query}
        if parameters:
            body["parameters"] = parameters
        try:
            r = self._c.post(self.url, json=body, headers=self.headers)
        except Exception as e:
            raise HydraError(f"cannot reach HydraDB at {self.url}: {e}") from e
        try:
            data = r.json()
        except json.JSONDecodeError:
            raise HydraError(f"HTTP {r.status_code}: {r.text[:300]}") from None
        if isinstance(data, dict) and "error" in data:
            msg = data["error"].get("message", str(data["error"]))
            raise HydraError(f"{msg}\n  query: {query[:180]}")
        if r.status_code >= 400:
            raise HydraError(f"HTTP {r.status_code}: {r.text[:300]}")
        return _rows(data)

    def batch_edges(self, rel: str, pairs, chunk=500) -> int:
        """Bulk-create edges.

        `UNWIND $rows AS row CREATE (a {id: row.a})-[:REL]->(b {id: row.b})` is
        the only batch write form v0.1.0 accepts - no labels, no properties.
        """
        from arbiter.schema import BATCH_EDGE

        q = BATCH_EDGE.format(rel=rel)
        pairs = list(pairs)
        n = 0
        for i in range(0, len(pairs), chunk):
            rows = [{"a": int(a), "b": int(b)} for a, b in pairs[i:i + chunk]]
            if not rows:
                continue
            self.run(q, {"rows": rows})
            n += len(rows)
        return n

    def close(self):
        self._c.close()


def _rows(data) -> list[dict]:
    """Unwrap HydraDB's typed cell envelope into plain dicts.

    Rows arrive as [[{"type":"vertex_id","value":30}, ...]] alongside a
    `columns` list, so we zip them back into name -> python value.
    """
    if not isinstance(data, dict):
        return []
    cols = data.get("columns") or []
    out = []
    for row in data.get("rows") or []:
        rec = {}
        for name, cell in zip(cols, row):
            rec[name] = _cell(cell)
        out.append(rec)
    return out


def _cell(cell):
    if not isinstance(cell, dict):
        return cell
    t = cell.get("type")
    if t == "null":
        return None
    return cell.get("value")
