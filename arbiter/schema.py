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
# Machine-readable ontology declaration.
#
# The endpoints of each edge used to live only in the trailing comments above,
# which meant anything needing them (the API, the diagrams) had to restate them
# and could drift. This block is the single source: `scripts/gen_dbml.py` and
# `arbiter.api:/api/ontology` both read it, so schema.dbml, the README ER
# diagram and the running app cannot disagree with the loader.
# ---------------------------------------------------------------------------

# entity -> band key + (column, dbml_type, key, note). `key` is "pk"/"fk"/"".
ENTITIES = {
    "Role":     {"band": "role", "columns": [
        ("id", "integer", "pk", "vertex id"),
        ("key", "varchar", "", "role name, e.g. Marketing Research Analyst")]},
    "Employee": {"band": "employee", "columns": [
        ("id", "integer", "pk", "vertex id"),
        ("key", "varchar", "", "HERB employee_id, e.g. eid_3bd7cd36"),
        ("name", "varchar", "", ""),
        ("role", "varchar", "fk", "denormalised; edge of record is HAS_ROLE"),
        ("manager_id", "integer", "fk", "FK Employee.id via REPORTS_TO; graph edge, not a stored column")]},
    "Product":  {"band": "product", "columns": [
        ("id", "integer", "pk", "vertex id"),
        ("key", "varchar", "", "product name, e.g. SearchForce")]},
    "Release":  {"band": "release", "columns": [
        ("id", "integer", "pk", "vertex id"),
        ("key", "varchar", "", "release_id"),
        ("seq", "integer", "", "ordinal within the product"),
        ("product_id", "integer", "fk", "FK Product.id via OF_PRODUCT"),
        ("precedes_id", "integer", "fk", "FK Release.id via PRECEDES; graph edge, not a stored column")]},
    "Company":  {"band": "company", "columns": [
        ("id", "integer", "pk", "vertex id"),
        ("key", "varchar", "", "company name")]},
    "Customer": {"band": "customer", "columns": [
        ("id", "integer", "pk", "vertex id"),
        ("key", "varchar", "", "customer id"),
        ("company_id", "integer", "fk", "FK Company.id via WORKS_FOR")]},
    "Artifact": {"band": None, "columns": [
        ("id", "integer", "pk", "vertex id; band encodes kind"),
        ("kind", "varchar", "", "slack | transcript | pr | url")]},
    "Document": {"band": "document", "columns": [
        ("id", "integer", "pk", "vertex id"),
        ("key", "varchar", "", "document id"),
        ("dtype", "varchar", "", "document type"),
        ("product_id", "integer", "fk", "FK Product.id via OF_PRODUCT")]},
}

# edge -> (source entity, target entity, cardinality), per build.py
EDGE_ENDPOINTS = {
    HAS_ROLE:      ("Employee", "Role",     "many-1"),
    REPORTS_TO:    ("Employee", "Employee", "many-1"),
    WORKS_FOR:     ("Customer", "Company",  "many-1"),
    OF_PRODUCT:    ("Release",  "Product",  "many-1"),
    PRECEDES:      ("Release",  "Release",  "1-1"),
    AUTHORED:      ("Employee", "Artifact", "1-many"),
    ABOUT_RELEASE: ("Artifact", "Release",  "many-1"),
    REVIEWS:       ("Artifact", "Document", "many-many"),
    MENTIONS:      ("Artifact", "Employee", "many-many"),
    REPORTED_BY:   ("Artifact", "Customer", "many-1"),
}

# REPORTED_BY is declared by the ontology but the loader never emits it - HERB
# provides no artifact -> customer link. Kept because the constant is part of
# the modelled schema, and flagged so nothing reports it as populated.
UNPOPULATED = frozenset({REPORTED_BY})

# The column each edge leaves from. Self-relationships need a dedicated
# column because DBML cannot Ref a table to itself on the same column.
EDGE_SOURCE_COLUMN = {
    REPORTS_TO: "manager_id",
    PRECEDES:   "precedes_id",
    OF_PRODUCT: "product_id",
    WORKS_FOR:  "company_id",
}

DBML_OP = {"many-1": ">", "1-many": "<", "1-1": "-", "many-many": "<>"}
MERMAID_OP = {"many-1": "}o--||", "1-many": "||--o{",
              "1-1": "||--o|", "many-many": "}o--o{"}


# Properties actually readable off a node in the graph, per entity.
#
# Probed against a live v0.1.0 node, not assumed: Employee, Release and Document
# are created one request each with properties attached, so they carry them.
# Role and Product nodes are only ever born as the far end of a batch-written
# edge, which carries ids alone - so in the graph they have an id and nothing
# else. Their human-readable name exists only in the loader's IdMap, and any
# viewer must label it as resolver-side rather than pretending it was stored.
STORED_PROPS = {
    "Employee": ["key", "name", "role"],
    "Release":  ["key", "seq"],
    "Document": ["key", "dtype"],
    "Product":  [],
    "Role":     [],
}

# Which column a text search filters on. STARTS WITH only: the engine rejects
# CONTAINS ("WHERE currently supports boolean combinations of ...").
SEARCH_PROP = {"Employee": "name", "Release": "key", "Document": "key",
               "Product": None, "Role": None}

# id band -> entity name. The artifact kinds collapse onto one entity.
ENTITY_OF_KIND = {
    "role": "Role", "employee": "Employee", "product": "Product",
    "release": "Release", "company": "Company", "customer": "Customer",
    "document": "Document",
    "slack": "Artifact", "transcript": "Artifact", "pr": "Artifact", "url": "Artifact",
}


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
