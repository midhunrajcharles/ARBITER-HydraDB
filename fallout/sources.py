"""Real, keyless data sources: deps.dev (dependency graphs) and OSV.dev (advisories)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

DEPS_DEV = "https://api.deps.dev/v3"
OSV = "https://api.osv.dev/v1"
CACHE = Path("data/cache")

_SYSTEM = {"npm": "npm", "pypi": "pypi"}
_OSV_ECOSYSTEM = {"npm": "npm", "pypi": "PyPI"}


def _cache_get(key: str) -> Any | None:
    p = CACHE / f"{key}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _cache_put(key: str, value: Any) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{key}.json").write_text(json.dumps(value), encoding="utf-8")


def _slug(*parts: str) -> str:
    return "_".join(p.replace("/", "%2F").replace("@", "AT") for p in parts)


def resolve_version(client: httpx.Client, system: str, name: str) -> str | None:
    """Latest default version for a package, via deps.dev."""
    key = _slug("ver", system, name)
    if (c := _cache_get(key)) is not None:
        return c
    url = f"{DEPS_DEV}/systems/{_SYSTEM[system]}/packages/{httpx.URL(name).path.lstrip('/') or name}"
    try:
        r = client.get(f"{DEPS_DEV}/systems/{_SYSTEM[system]}/packages/{name.replace('/', '%2F')}")
        r.raise_for_status()
        versions = r.json().get("versions", [])
        default = [v for v in versions if v.get("isDefault")]
        out = (default or versions)[-1]["versionKey"]["version"] if versions else None
    except Exception:
        out = None
    _cache_put(key, out)
    return out


def dependency_graph(client: httpx.Client, system: str, name: str, version: str) -> dict:
    """Full transitive dependency graph for one package version.

    Returns deps.dev's node/edge structure. `relation` is SELF | DIRECT | INDIRECT.
    """
    key = _slug("deps", system, name, version)
    if (c := _cache_get(key)) is not None:
        return c
    url = (
        f"{DEPS_DEV}/systems/{_SYSTEM[system]}/packages/"
        f"{name.replace('/', '%2F')}/versions/{version}:dependencies"
    )
    try:
        r = client.get(url)
        r.raise_for_status()
        out = r.json()
    except Exception as e:  # unresolvable version, yanked package, etc.
        out = {"nodes": [], "edges": [], "error": str(e)}
    _cache_put(key, out)
    return out


def osv_query_batch(client: httpx.Client, pkgs: list[tuple[str, str, str]]) -> dict[tuple, list[dict]]:
    """Batch-query OSV for (system, name, version) triples. Returns advisories per triple."""
    results: dict[tuple, list[dict]] = {}
    CHUNK = 500
    for i in range(0, len(pkgs), CHUNK):
        chunk = pkgs[i : i + CHUNK]
        body = {
            "queries": [
                {
                    "package": {"name": n, "ecosystem": _OSV_ECOSYSTEM[s]},
                    "version": v,
                }
                for (s, n, v) in chunk
            ]
        }
        try:
            r = client.post(f"{OSV}/querybatch", json=body, timeout=60)
            r.raise_for_status()
            for triple, res in zip(chunk, r.json().get("results", [])):
                results[triple] = res.get("vulns", []) or []
        except Exception:
            for triple in chunk:
                results[triple] = []
        time.sleep(0.2)
    return results


def osv_get(client: httpx.Client, vuln_id: str) -> dict:
    """Full advisory record (summary, severity, affected ranges)."""
    key = _slug("osv", vuln_id)
    if (c := _cache_get(key)) is not None:
        return c
    try:
        r = client.get(f"{OSV}/vulns/{vuln_id}", timeout=30)
        r.raise_for_status()
        out = r.json()
    except Exception as e:
        out = {"id": vuln_id, "error": str(e)}
    _cache_put(key, out)
    return out
