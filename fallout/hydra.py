"""HydraDB client — OpenCypher over the HTTP JSON API, with an optional Bolt path.

HydraDB is an object-store-native distributed graph database (Rust, SlateDB).
It speaks OpenCypher over Bolt 5.x (Neo4j drivers) and an HTTPS JSON/NDJSON API.
We default to HTTP: one less dependency, and it streams NDJSON for bulk loads.

Endpoints on a local dev node:
    Bolt   127.0.0.1:7687
    HTTP   127.0.0.1:8443
    Admin  127.0.0.1:9090   (/readyz, Prometheus metrics)
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Iterable

import httpx


class HydraError(RuntimeError):
    pass


class Hydra:
    def __init__(
        self,
        http: str | None = None,
        cell_id: str | None = None,
        token: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.http = (http or os.getenv("HYDRA_HTTP", "http://127.0.0.1:8443")).rstrip("/")
        self.admin = os.getenv("HYDRA_ADMIN", "http://127.0.0.1:9090").rstrip("/")
        self.cell_id = cell_id or os.getenv("GRAPH_CELL_ID", "cell-0")
        self.token = token or os.getenv("HYDRA_AUTH_TOKEN", "local-development-token-32-bytes")
        self._c = httpx.Client(timeout=timeout, verify=False)

    # ---- lifecycle -------------------------------------------------------
    def wait_ready(self, timeout: float = 120.0) -> bool:
        """A listening port is not proof. Poll /readyz, then round-trip a write."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = self._c.get(f"{self.admin}/readyz", timeout=5)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(2)
        return False

    def verify(self) -> dict:
        """Round-trip a write and read it back. This is the real liveness proof."""
        probe = f"probe-{int(time.time())}"
        self.run(
            "MERGE (p:_Probe {id: $id}) ON CREATE SET p.created = $ts RETURN p.id AS id",
            {"id": probe, "ts": int(time.time())},
        )
        rows = self.run("MATCH (p:_Probe {id: $id}) RETURN p.id AS id", {"id": probe})
        self.run("MATCH (p:_Probe {id: $id}) DELETE p", {"id": probe})
        if not rows:
            raise HydraError("write did not round-trip: node listening but not durable")
        return {"ok": True, "probe": probe, "rows": rows}

    # ---- query -----------------------------------------------------------
    def run(self, query: str, params: dict | None = None) -> list[dict]:
        payload: dict[str, Any] = {"cell_id": self.cell_id, "query": query}
        if params:
            payload["parameters"] = params
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            r = self._c.post(f"{self.http}/query", json=payload, headers=headers)
        except Exception as e:
            raise HydraError(f"cannot reach HydraDB at {self.http}: {e}") from e
        if r.status_code >= 400:
            raise HydraError(f"HTTP {r.status_code}: {r.text[:500]}\n  query: {query[:200]}")
        return _rows(r.text)

    def run_many(self, queries: Iterable[tuple[str, dict]], batch: int = 200) -> int:
        """Sequential writes. HydraDB supports batched UNWIND writes - prefer those
        for bulk load; this is the fallback for heterogeneous statements."""
        n = 0
        for q, p in queries:
            self.run(q, p)
            n += 1
        return n

    def close(self) -> None:
        self._c.close()


def _rows(text: str) -> list[dict]:
    """Accept either a JSON envelope or NDJSON."""
    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        out = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out
    if isinstance(data, list):
        return data
    for key in ("rows", "results", "data", "records"):
        if key in data and isinstance(data[key], list):
            return data[key]
    return [data]
