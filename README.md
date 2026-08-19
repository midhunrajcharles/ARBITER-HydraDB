# Arbiter

**Graph-native enterprise context resolution on HydraDB.** Answers questions about
who did what, on which release, from a 38,000-artifact enterprise corpus — by
traversal, not similarity.

Built for [Hack Hydra](https://hackhydra.hydradb.com/) 2026, Track 1 (Enterprise
Context and Ontology).

---

## Why it exists

HERB — Salesforce's enterprise RAG benchmark — asks things like *"find employee
IDs of Marketing Research Analysts who worked on the previous release of
SearchForce."* No document in the corpus contains that answer. You have to know
which releases exist, which one is *previous*, who authored the artifacts
belonging to it, and then filter by a job title stored in a different file.

The HERB paper reports the best agentic RAG systems reaching ~30% accuracy, and
names **retrieval — not reasoning — as the bottleneck.** Arbiter replaces the
retrieval step with an OpenCypher traversal executed inside HydraDB.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| [Python](https://www.python.org/downloads/) | 3.11+ | 3.12 used here |
| [Docker](https://docs.docker.com/get-docker/) | any recent | runs the HydraDB node |
| `curl`, `bash` | — | Git Bash or WSL on Windows |
| Disk | ~250 MB | HERB corpus + HydraDB store |

No API keys. No paid services. Every data source is public.

---

## Get started

### 1. Clone and install

```bash
git clone https://github.com/midhunrajcharles/arbiter.git
cd arbiter
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows
# .venv/bin/pip install -r requirements.txt       # macOS / Linux
```

### 2. Fetch the HERB corpus

HERB is **not** redistributed in this repo — it is Salesforce's, released for
research purposes only. This pulls it from the original source:

```bash
bash scripts/fetch_data.sh
```

Expected output:

```
listing files ...
downloading 33 files ...
  [1/33] ok      metadata/customers_data.json
  ...
done: 30 products, 3 metadata files
28M     data/herb
```

### 3. Start HydraDB

```bash
bash scripts/hydra_up.sh
```

Expected output:

```
STATUS Up 3 seconds
IP 172.26.200.199
```

The script is idempotent — run it any time the node needs restoring. It starts
`ghcr.io/hydra-db/hydradb:latest` with a local object store, waits for `/readyz`,
and writes the node address to `.wslip`.

### 4. Load the graph

```bash
.venv/Scripts/python scripts/load.py
```

Expected output:

```
{
  "employees": 530, "roles": 10, "products": 30, "releases": 100,
  "artifacts": 38490, "edges_total": 77144
}
...
  "timing": { "employees_s": 2.2, "releases_s": 2.3,
              "edges_s": 19.6, "total_s": 24.2 }
```

### 5. Run it

```bash
.venv/Scripts/python -m uvicorn arbiter.api:app --port 8000
```

Open <http://127.0.0.1:8000>. Pick a product, click a HERB question, and the UI
shows the answer, **the Cypher that produced it**, and every artifact the
traversal touched.

### 6. Reproduce the numbers

```bash
.venv/Scripts/python scripts/eval_hydra.py    # accuracy vs BM25, via HydraDB
.venv/Scripts/python scripts/probe_cypher.py  # what Cypher v0.1.0 executes
```

---

## The traversal

This is the whole argument, in one query:

```cypher
MATCH (ro:Role {id: 1000007})<-[:HAS_ROLE]-(e)-[:AUTHORED]->(a)
      -[:ABOUT_RELEASE]->(rel {id: 4000073})
WHERE a.id >= 10000000 AND a.id < 11000000
RETURN DISTINCT e.id AS employee, a.id AS artifact
```

Read it right to left: start from a release the question only refers to
obliquely (*"the previous"*), walk backwards to the artifacts belonging to it,
land on people through a role edge that appears in no document's text. The
`WHERE` clause is the typed-edge constraint — documents only — expressed as an
integer range, because entity type is encoded in the id (see
[`docs/hydradb-subset.md`](docs/hydradb-subset.md)).

Copy-pasteable against a loaded node:

```bash
curl -sS http://$(cat .wslip):8443/v1/graphs/default/query \
  -H "Authorization: Bearer local-development-token-32-bytes" \
  -H 'X-Graph-Namespace: default' -H 'Content-Type: application/json' \
  --data '{"cell_id":"cell-0","query":"MATCH (ro:Role {id: 1000007})<-[:HAS_ROLE]-(e)-[:AUTHORED]->(a)-[:ABOUT_RELEASE]->(rel {id: 4000073}) WHERE a.id >= 10000000 AND a.id < 11000000 RETURN DISTINCT e.id AS employee"}'
```

---

## Architecture

```mermaid
flowchart LR
    subgraph SRC["HERB corpus (fetched, not vendored)"]
        D["documents<br/>400"]
        S["slack<br/>33,618"]
        P["pull requests<br/>1,268"]
        T["transcripts + urls"]
        M["metadata<br/>employees · org · customers"]
    end

    subgraph BUILD["arbiter/build.py"]
        R["release ordering<br/>inferred from artifact ids"]
        V["review sessions<br/>parsed from doc links in Slack"]
        I["IdMap<br/>string key → integer id band"]
    end

    subgraph HDB["HydraDB — object-store-native graph engine"]
        G[("typed graph<br/>38,490 nodes · 77,144 edges")]
    end

    subgraph APP["Arbiter"]
        Q["query.py<br/>question → traversal"]
        A["FastAPI + UI<br/>answer · cypher · evidence"]
    end

    SRC --> BUILD
    BUILD -->|"UNWIND $rows batch writes"| G
    Q -->|"OpenCypher"| G
    G -->|"vertex ids"| Q
    Q --> A
```

*Figure 1. Ingestion runs once (24 s for the full corpus). At query time nothing
is retrieved or re-ranked — the question is compiled to a traversal, HydraDB
executes it, and vertex ids are mapped back to human-readable keys.*

## Graph model

| Node | Id range | Carries |
|---|---|---|
| `Role` | 1,000,000+ | — |
| `Employee` | 2,000,000+ | `key`, `name`, `role` |
| `Product` | 3,000,000+ | — |
| `Release` | 4,000,000+ | `key`, `seq` |
| `Company` / `Customer` | 5,000,000+ / 6,000,000+ | — |
| `Document` | 10,000,000+ | `key`, `dtype` |
| Slack / transcript / PR / URL | 20–50,000,000+ | — |

| Edge | From → To | Meaning |
|---|---|---|
| `HAS_ROLE` | Employee → Role | job title as an ontology edge |
| `AUTHORED` | Employee → Artifact | provenance root |
| `ABOUT_RELEASE` | Artifact → Release | which release an artifact belongs to |
| `PRECEDES` | Release → Release | temporal ordering — makes *"previous"* answerable |
| `OF_PRODUCT` | Release/Document → Product | |
| `REVIEWS` | Slack → Document | parsed from doc links posted in planning channels |
| `MENTIONS` | Artifact → Employee | `@eid_` references |
| `REPORTS_TO` | Employee → Employee | org hierarchy from `salesforce_team.json` |
| `WORKS_FOR` | Customer → Company | |

---

## API reference

| Method | Path | Returns |
|---|---|---|
| `GET` | `/` | the UI |
| `GET` | `/api/health` | HydraDB host and readiness |
| `GET` | `/api/stats` | node/edge counts |
| `GET` | `/api/products` | the 30 HERB products |
| `GET` | `/api/questions?product=X` | real HERB questions, answerable and not |
| `POST` | `/api/ask` | `{question, product}` → answer, **cypher executed**, evidence |

---

## Troubleshooting

**`HydraDB not reachable - run: bash scripts/hydra_up.sh`**
The node stopped, usually because WSL reaped the distro. Re-run `scripts/hydra_up.sh`;
it is idempotent.

**`docker: Cannot connect to the Docker daemon`**
On Windows, Docker Desktop's `com.docker.service` needs administrator rights to
start. Either launch Docker Desktop as administrator, or use the WSL path this
project uses — `scripts/hydra_up.sh` runs `dockerd` inside the Ubuntu distro and
needs no elevation.

**Node answers `/readyz` then dies on the first query**
`RUST_MIN_STACK` is unset. `scripts/hydra_up.sh` sets it to `33554432`.

**`node id property must be an integer`**
You are passing a string key. HydraDB v0.1.0 requires integer vertex ids — see
[`docs/hydradb-subset.md`](docs/hydradb-subset.md); `arbiter/schema.py:IdMap`
does the mapping.

**Answers come back empty after a reload**
The store was wiped but the graph was not reloaded. Run `scripts/load.py` again.

---

## Layout

| Path | What it does |
|---|---|
| `arbiter/herb.py` | parses HERB; infers release ordering and review sessions |
| `arbiter/schema.py` | id ranges, edge types, Cypher templates, verified constraints |
| `arbiter/build.py` | HERB → graph rows; loads into HydraDB |
| `arbiter/hydra.py` | HydraDB HTTP client (query, batch writes, typed-row unwrap) |
| `arbiter/query.py` | question → traversal; abstention logic |
| `arbiter/resolve.py` | BM25 retrieval baseline and scoring |
| `arbiter/api.py` | FastAPI app |
| `web/index.html` | single-page UI |
| `scripts/` | fetch data, start HydraDB, load, evaluate, probe Cypher |
| `docs/hydradb-subset.md` | what OpenCypher v0.1.0 actually executes |

---

## Attribution

- **HERB** — *Benchmarking Deep Search over Heterogeneous Enterprise Data*,
  Salesforce AI Research, EMNLP 2025 (industry track).
  [dataset](https://huggingface.co/datasets/Salesforce/HERB) ·
  [code](https://github.com/SalesforceAIResearch/HERB). Released for research
  purposes only; fetched at runtime, not redistributed here.
- **HydraDB** — [hydra-db/hydradb](https://github.com/hydra-db/hydradb), AGPL-3.0.
  Used as a container image; no HydraDB source is vendored into this repo.
- **LIMIT** — *On the Theoretical Limitations of Embedding-Based Retrieval*,
  Weller et al., 2025. The formal result behind why this task resists embeddings.

Third-party Python dependencies are listed in `requirements.txt`.
AI coding assistance (Claude) was used during development, as permitted by the
hackathon rules.

## License

MIT — see [LICENSE](LICENSE).
