"""Probe exactly which OpenCypher constructs HydraDB v0.1.x supports.

The engine advertises "a practical OpenCypher subset". Building a schema against
assumed syntax and discovering the gaps at load time is how projects die, so we
establish the real surface first and design to it.

Run:  python scripts/probe_cypher.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WSLIP = (ROOT / ".wslip").read_text().strip() if (ROOT / ".wslip").exists() else "127.0.0.1"
URL = f"http://{WSLIP}:8443/v1/graphs/default/query"
HEADERS = {
    "Authorization": "Bearer local-development-token-32-bytes",
    "X-Graph-Namespace": "default",
    "Content-Type": "application/json",
}

PROBES = [
    ("CREATE node",            "CREATE (n:_P {k: 'a', v: 1})"),
    ("MATCH ... RETURN",       "MATCH (n:_P {k: 'a'}) RETURN n.v AS v"),
    ("MATCH WHERE",            "MATCH (n:_P) WHERE n.v > 0 RETURN n.k AS k"),
    ("MERGE",                  "MERGE (n:_P {k: 'b'})"),
    ("MERGE ON CREATE SET",    "MERGE (n:_P {k: 'c'}) ON CREATE SET n.v = 3"),
    ("MATCH + SET",            "MATCH (n:_P {k: 'a'}) SET n.v = 2"),
    ("MATCH + CREATE edge",    "MATCH (a:_P {k:'a'}), (b:_P {k:'b'}) CREATE (a)-[:_R]->(b)"),
    ("MATCH + MERGE edge",     "MATCH (a:_P {k:'a'}), (b:_P {k:'c'}) MERGE (a)-[:_R]->(b)"),
    ("UNWIND + CREATE",        "UNWIND [{k:'u1'},{k:'u2'}] AS row CREATE (n:_P {k: row.k})"),
    ("UNWIND + MERGE",         "UNWIND [{k:'m1'},{k:'m2'}] AS row MERGE (n:_P {k: row.k})"),
    ("parameters $p",          "MATCH (n:_P {k: $k}) RETURN n.v AS v"),
    ("UNWIND $rows + MERGE",   "UNWIND $rows AS row MERGE (n:_P {k: row.k})"),
    ("1-hop traversal",        "MATCH (a:_P {k:'a'})-[:_R]->(b) RETURN b.k AS k"),
    ("var-length *1..3",       "MATCH (a:_P {k:'a'})-[:_R*1..3]->(b) RETURN b.k AS k"),
    ("var-length *",           "MATCH (a:_P {k:'a'})-[:_R*]->(b) RETURN b.k AS k"),
    ("count() aggregate",      "MATCH (n:_P) RETURN count(n) AS c"),
    ("collect() aggregate",    "MATCH (n:_P) RETURN collect(n.k) AS ks"),
    ("ORDER BY + LIMIT",       "MATCH (n:_P) RETURN n.k AS k ORDER BY n.k DESC LIMIT 2"),
    ("DISTINCT",               "MATCH (n:_P) RETURN DISTINCT n.k AS k"),
    ("OPTIONAL MATCH",         "MATCH (a:_P {k:'a'}) OPTIONAL MATCH (a)-[:_R]->(b) RETURN b.k AS k"),
    ("WITH clause",            "MATCH (n:_P) WITH n.k AS k RETURN k"),
    ("multi-label + IN",       "MATCH (n:_P) WHERE n.k IN ['a','b'] RETURN n.k AS k"),
    ("CREATE INDEX",           "CREATE INDEX ON :_P(k)"),
    ("DELETE",                 "MATCH (n:_P) DETACH DELETE n"),
]


def run(client, query, params=None):
    body = {"cell_id": "cell-0", "query": query}
    if params:
        body["parameters"] = params
    try:
        r = client.post(URL, json=body, headers=HEADERS, timeout=30)
    except Exception as e:
        return "ERROR", str(e)[:90]
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        return "FAIL", msg[:110]
    try:
        d = r.json()
        if isinstance(d, dict) and "error" in d:
            return "FAIL", str(d["error"].get("message", ""))[:110]
        return "OK", f"cols={d.get('columns')} rows={len(d.get('rows') or [])}"
    except Exception:
        return "OK", r.text[:80]


def main():
    supported, unsupported = [], []
    with httpx.Client() as c:
        print(f"probing {URL}\n")
        print(f"{'construct':<26}{'status':<8}detail")
        print("-" * 96)
        for name, q in PROBES:
            params = None
            if "$k" in q:
                params = {"k": "a"}
            if "$rows" in q:
                params = {"rows": [{"k": "p1"}, {"k": "p2"}]}
            status, detail = run(c, q, params)
            (supported if status == "OK" else unsupported).append(name)
            print(f"{name:<26}{status:<8}{detail}")

    print("\n" + "=" * 96)
    print(f"SUPPORTED ({len(supported)}): {', '.join(supported)}")
    print(f"\nNOT SUPPORTED ({len(unsupported)}): {', '.join(unsupported)}")
    Path(ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results/cypher_support.json").write_text(
        json.dumps({"supported": supported, "unsupported": unsupported}, indent=2),
        encoding="utf-8")
    print("\nwrote results/cypher_support.json")


if __name__ == "__main__":
    main()
