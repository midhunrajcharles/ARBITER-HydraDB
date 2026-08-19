# HydraDB v0.1.0 — the OpenCypher subset that actually executes

Measured against `ghcr.io/hydra-db/hydradb:latest` (reporting `version 0.1.0`)
over the HTTP API at `POST /v1/graphs/{graph}/query`, during Hack Hydra 2026.

The README describes "a practical OpenCypher subset". This is that subset,
enumerated by probing, because designing a schema against assumed syntax and
discovering the gaps at load time is an expensive way to lose a weekend.

Reproduce with:

```bash
bash scripts/hydra_up.sh
python scripts/probe_cypher.py
```

Raw output lands in `results/cypher_support.json`.

---

## Writes

| Construct | Status | Notes |
|---|---|---|
| `CREATE (a {id:1})-[:R]->(b {id:2})` | ✅ | The one-hop edge pattern. This is the write form. |
| `CREATE (a:L {id:1, k:"v"})-[:R]->(b:L2 {id:2})` | ✅ | Labels and multiple properties are fine here. |
| `CREATE (a {id:1})-[:R {w:1}]->(b {id:2})` | ✅ | Edge properties allowed in the single form. |
| `CREATE (n:L {id:1})` | ❌ | *"only one-hop edge patterns are executable"* — a node cannot be created alone. |
| `MERGE (n {id:1})` | ❌ | Same error. MERGE does not execute. |
| `MERGE (n) ON CREATE SET ...` | ❌ | *"MERGE ON CREATE/ON MATCH actions are not supported"* |
| `MATCH (a),(b) CREATE (a)-[:R]->(b)` | ❌ | *"write query is not executable by this protocol"* |
| `UNWIND $rows AS row CREATE (a {id:row.a})-[:R]->(b {id:row.b})` | ✅ | **The** batch form. Verified at 500 rows/request. |
| `UNWIND [{...}] AS row CREATE ...` | ❌ | *"UNWIND batch input must be a parameter"* — inline lists rejected. |
| `UNWIND $rows ... CREATE (a:Label {...})` | ❌ | *"UNWIND batch node patterns do not support labels"* |
| `UNWIND $rows ... CREATE (a {id:row.a, name:row.n})` | ❌ | *"UNWIND batch node supports only …"* — ids only, no extra properties. |
| `UNWIND $rows ... -[:R {w:row.w}]->` | ❌ | *"UNWIND batch requires one fixed …"* — no edge properties. |

> ⚠️ Community guidance circulating during the event said to use `MERGE` with
> `ON CREATE` / `ON UPDATE` instead of `MATCH ... CREATE`. On v0.1.0 that is
> backwards: **neither** runs. `CREATE` as a one-hop edge pattern is the only
> write that executes.

### Node identity

A node's `id` property **is** its vertex id and **must be an integer**:

```
CREATE (a:Emp {id: "eid_x"})-[:R]->(b {id: "y"})
→ "node id property must be an integer"
```

Results echo this back as `{"type":"vertex_id","value":30}`. Any application
with natural string keys needs its own string→int mapping layer.

---

## Reads

| Construct | Status | Notes |
|---|---|---|
| `MATCH (n:L {id:1}) RETURN n.p AS p` | ✅ | |
| `MATCH (n) WHERE n.v > 0 RETURN n.k AS k` | ✅ | Comparisons and `AND`/`OR` work. |
| `MATCH (a {id:1})-[:R]->(b) RETURN b.id AS i` | ✅ | |
| `MATCH (b {id:2})<-[:R]-(a) RETURN a.id AS i` | ✅ | Reverse traversal works. |
| `MATCH (a)-[:R]->(b)-[:S]->(c) RETURN c.id AS i` | ✅ | **Multi-hop reads work**, mixed relationship types included. |
| `MATCH (a)-[:R]->(b), (b)-[:S]->(c) RETURN …` | ✅ | Comma-joined patterns work. |
| `MATCH (a)-[:R*2]->(b) RETURN b.id AS i` | ✅ | Fixed-length only. |
| `MATCH (a)-[:R*1..3]->(b)` | ❌ | *"variable-length MATCH requires a fixed length"* |
| `MATCH (a)-[:R*]->(b)` | ❌ | Same. |
| `RETURN DISTINCT n.k AS k` | ✅ | |
| `RETURN n.k AS k ORDER BY n.k DESC LIMIT 2` | ✅ | |
| `OPTIONAL MATCH` | ✅ | |
| `RETURN count(*) AS c` | ✅ | |
| `RETURN count(n) AS c` | ❌ | Only `count(*)`. |
| `RETURN n` | ❌ | *"RETURN currently supports `<binding>.<property>` or `count(*)`"* |
| `WHERE n.k IN ['a','b']` | ❌ | *"WHERE currently supports boolean combinations …"* |
| `WITH n.k AS k RETURN k` | ❌ | *"WITH pass-through supports only bare identifiers"* |
| `$param` in MATCH | ✅ | Parameters work for reads. |

---

## What this implies for schema design

The two write constraints together — no labels or properties in batch writes,
integer-only ids — mean you cannot tag a node's type at bulk-insert time. Two
options follow:

1. Insert every node individually so it can carry a label (~62ms per request
   over HTTP, so ~40 minutes for 38k nodes), or
2. **Encode type in the integer id range.**

Arbiter takes (2). Each entity class owns a disjoint 1,000,000-wide band, so a
node's type is recoverable from its id with no property lookup, and a
type-constrained traversal becomes an integer range predicate the engine
evaluates directly:

```cypher
MATCH (ro:Role {id: 1000007})<-[:HAS_ROLE]-(e)-[:AUTHORED]->(a)
      -[:ABOUT_RELEASE]->(rel {id: 4000073})
WHERE a.id >= 10000000 AND a.id < 11000000     -- documents only
RETURN DISTINCT e.id AS employee, a.id AS artifact
```

Only the few hundred nodes that genuinely need properties (employees, releases,
documents) are inserted individually; the remaining ~77,000 edges go through
`UNWIND` batches. Full corpus load: **21 seconds**.

The absence of `-[:PRECEDES*]->` is the one constraint with a real modelling
cost. "Every release transitively before the current one" cannot be expressed as
a single pattern, so the ordering is read from the `PRECEDES` chain and walked a
hop at a time. The edge still carries the ordering; only the traversal sugar is
missing.

## Measured performance

| Operation | Result |
|---|---|
| Full corpus load (530 employees, 400 documents, 100 releases, 77k edges) | 21.1 s |
| Batch edge write | 500 rows/request |
| 3-hop resolution traversal | median **48 ms**, p95 **57 ms** (n=20) |
