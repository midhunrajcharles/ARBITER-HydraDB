"""The Arbiter graph schema, designed against HydraDB v0.1.0's *verified*
executable OpenCypher subset.

We probed the engine rather than trusting the docs, and the subset is narrower
than "practical OpenCypher subset" suggests. Everything below is what the engine
actually accepts (see `results/cypher_support.json` and `docs/hydradb-subset.md`).

Verified constraints that shaped this design
--------------------------------------------
1. `CREATE` only executes as a ONE-HOP EDGE PATTERN. A bare `CREATE (n)` is
   rejected. Every node must therefore be born attached to an edge.
2. A node's `id` property IS the vertex id and MUST BE AN INTEGER. String keys
   like "eid_3bd7cd36" are rejected outright.
3. `UNWIND $rows AS row CREATE (a {id: row.a})-[:REL]->(b {id: row.b})` is the
   only batch write form. It accepts NO labels, NO extra node properties and NO
   edge properties - ids only.
4. `MERGE` is not executable at all, and `MERGE ... ON CREATE/ON MATCH` is
   explicitly rejected. (Worth noting: community guidance said to prefer MERGE
   with ON CREATE. On v0.1.0 that is exactly backwards - it does not run.)
5. `RETURN` supports `<binding>.<property>` or `count(*)`. `RETURN n` fails.
6. Variable-length paths require a FIXED length: `-[:R*2]->` works, `-[:R*1..3]->`
   and `-[:R*]->` do not.
7. `WHERE ... IN [...]`, `WITH` (beyond bare identifiers) and `count(n)` are
   not supported. `count(*)` is.
8. Multi-hop READ patterns DO work, in both directions, including comma-joined
   patterns. This is what lets the resolution traversal run server-side.

Two consequences drive the whole model:

* **Type is encoded in the integer id range.** Because batch writes cannot carry
  labels, we cannot tag a node's type at bulk-insert time. Instead each entity
  class owns a disjoint id range, so a node's type is recoverable from its id
  with no property lookup and no label. This also gives the typed-edge
  constraint (`AUTHORED` -> document only) for free: it becomes an integer range
  check the engine can answer from the id alone.

* **Roles are first-class nodes.** Constraint 1 means a node cannot be created
  without an edge, so employees are created via `(:Employee)-[:HAS_ROLE]->(:Role)`.
  That turns what would have been a string attribute into a real ontology edge,
  which is the correct modelling for an enterprise-ontology task anyway.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Integer id ranges. Type is a function of the id - no label required.
# ---------------------------------------------------------------------------
BASE = {
    "role":       1_000_000,
    "employee":   2_000_000,
    "product":    3_000_000,
    "release":    4_000_000,
    "company":    5_000_000,
    "customer":   6_000_000,
    # artifact kinds get their own ranges so that constraining a traversal to
    # documents is an id-range test rather than a property filter
    "document":  10_000_000,
    "slack":     20_000_000,
    "transcript": 30_000_000,
    "pr":        40_000_000,
    "url":       50_000_000,
}
SPAN = 1_000_000
ARTIFACT_KINDS = ("document", "slack", "transcript", "pr", "url")


def kind_of(node_id: int) -> str | None:
    """Recover an entity's type from its integer id alone."""
    for name, base in BASE.items():
        if base <= node_id < base + SPAN:
            return name
    return None


class IdMap:
    """Assigns stable integer ids per entity type and remembers the mapping.

    The reverse map matters as much as the forward one: query results come back
    as `{"type":"vertex_id","value":10004}` and have to be turned back into
    "chprox_market_research_report_final" for a human-readable answer.
    """

    def __init__(self) -> None:
        self._fwd: dict[tuple[str, str], int] = {}
        self._rev: dict[int, tuple[str, str]] = {}
        self._next: dict[str, int] = {k: 0 for k in BASE}

    def get(self, kind: str, key: str) -> int:
        k = (kind, key)
        if k not in self._fwd:
            n = self._next[kind]
            self._next[kind] = n + 1
            if n >= SPAN:
                raise ValueError(f"id space exhausted for {kind}")
            nid = BASE[kind] + n
            self._fwd[k] = nid
            self._rev[nid] = k
        return self._fwd[k]

    def key(self, node_id: int) -> str | None:
        e = self._rev.get(node_id)
        return e[1] if e else None

    def entry(self, node_id: int):
        return self._rev.get(node_id)

    def count(self, kind: str) -> int:
        return self._next[kind]

    def __len__(self) -> int:
        return len(self._fwd)


# ---------------------------------------------------------------------------
# Edge types
# ---------------------------------------------------------------------------
HAS_ROLE = "HAS_ROLE"            # Employee  -> Role
AUTHORED = "AUTHORED"            # Employee  -> Artifact
ABOUT_RELEASE = "ABOUT_RELEASE"  # Artifact  -> Release
PRECEDES = "PRECEDES"            # Release   -> Release   (temporal ordering)
OF_PRODUCT = "OF_PRODUCT"        # Release   -> Product
MENTIONS = "MENTIONS"            # Artifact  -> Employee
REPORTS_TO = "REPORTS_TO"        # Employee  -> Employee  (org hierarchy)
REPORTED_BY = "REPORTED_BY"      # Artifact  -> Customer
WORKS_FOR = "WORKS_FOR"          # Customer  -> Company
REVIEWS = "REVIEWS"              # Artifact(slack) -> Artifact(document)

EDGE_TYPES = [
    HAS_ROLE, AUTHORED, ABOUT_RELEASE, PRECEDES,
    OF_PRODUCT, MENTIONS, REPORTS_TO, REPORTED_BY, WORKS_FOR, REVIEWS,
]

# ---------------------------------------------------------------------------
# Query templates (all verified executable on v0.1.0)
# ---------------------------------------------------------------------------

# Bulk edge insert - the only batch write form the engine accepts.
BATCH_EDGE = "UNWIND $rows AS row CREATE (a {{id: row.a}})-[:{rel}]->(b {{id: row.b}})"

# Property-carrying create. One hop, labelled, one request each (~62ms).
CREATE_EMPLOYEE = (
    'CREATE (e:Employee {{id: {eid}, key: "{key}", name: "{name}", role: "{role}"}})'
    '-[:HAS_ROLE]->(r:Role {{id: {rid}}})'
)
CREATE_RELEASE = (
    'CREATE (rel:Release {{id: {relid}, key: "{key}", seq: {seq}}})'
    '-[:OF_PRODUCT]->(p:Product {{id: {pid}}})'
)

CREATE_DOCUMENT = (
    'CREATE (d:Document {{id: {did}, key: "{key}", dtype: "{dtype}"}})'
    '-[:OF_PRODUCT]->(p:Product {{id: {pid}}})'
)

# The resolution traversal, server-side. Two hops plus a reverse hop.
#   role -> employees with that role -> artifacts they authored
# The document constraint is applied as an id-range predicate on the artifact.
TRAVERSE_ROLE_AUTHORS = (
    "MATCH (r:Role {{id: {rid}}})<-[:HAS_ROLE]-(e)-[:AUTHORED]->(a) "
    "RETURN e.id AS employee, a.id AS artifact"
)

# Artifacts belonging to a release.
TRAVERSE_RELEASE_ARTIFACTS = (
    "MATCH (a)-[:ABOUT_RELEASE]->(rel {{id: {relid}}}) RETURN a.id AS artifact"
)

# The release ordering chain. Fixed-length only, so "everything before current"
# is walked one hop at a time rather than with `-[:PRECEDES*]->`.
TRAVERSE_PRECEDES = (
    "MATCH (earlier)-[:PRECEDES]->(later {{id: {relid}}}) RETURN earlier.id AS earlier"
)

COUNT_AUTHORED = (
    "MATCH (e {{id: {eid}}})-[:AUTHORED]->(a) RETURN count(*) AS c"
)
