![Arbiter — resolving a HERB question by traversal](arbiter-verified.png)

**Arbiter** answers enterprise questions about *who did what, on which release* by
traversing a 38,490-artifact corpus instead of ranking its text. Built for
**[Hack Hydra](https://hackhydra.hydradb.com/) 2026, Track 1 (Enterprise Context and
Ontology)**, Arbiter demonstrates how an **OpenCypher traversal executed inside
[HydraDB](https://github.com/hydra-db/hydradb)** answers questions that no document in
the corpus contains — and shows you the query and every artifact it landed on, every
time.

---

## Try Arbiter Live

The console is deployed here:
**[arbiter-nine-mu.vercel.app](https://arbiter-nine-mu.vercel.app)**

> **Read this before you click.** The hosted page is the **UI only**. Arbiter's API
> parses the HERB corpus at boot and holds an open connection to a HydraDB node, so it
> cannot run as a serverless function — a cold start would re-parse 28 MB per
> invocation and could not reach a `localhost` engine. Until `ARBITER_API_URL` points
> at a stateful host, every view renders **"The Arbiter API is not answering"** and
> names what to configure.
>
> That is the honest state, not a broken one — and it is the same rule the whole
> project runs on: an outage must never render as an empty result.

**For the full experience, run it locally.** [Get started](#get-started) takes about
five minutes and needs no API keys, no accounts and no paid services.

### Runtime notes

| Operation | Measured |
|---|---|
| Full corpus load — 530 employees, 400 documents, 100 releases, 77,144 edges | **24.2 s**, once |
| 3-hop resolution traversal | **median 48 ms**, p95 57 ms (n=20) |
| Full evaluation — 200 questions + 361 abstention checks | **171.6 s** |
| Corpus fetch — one-time download from Salesforce | ~28 MB |

*Nothing is retrieved or re-ranked at query time. The question compiles to a
traversal, HydraDB executes it, and vertex ids map back to human-readable keys.*

---

## The problem, in one question

HERB — Salesforce's enterprise RAG benchmark — asks things like *"find employee IDs of
Marketing Research Analysts who worked on the previous release of SearchForce."*

**No document in the corpus contains that answer.** You have to know which releases
exist, which one is *previous*, who authored the artifacts belonging to it, and then
filter by a job title stored in an entirely different file.

So search hands back a market research report that matches every word of the question
— and belongs to the release before it, or to a different product altogether. It reads
as correct, so nobody checks. The HERB paper reports the best agentic RAG systems
reaching **~30% accuracy**, and names **retrieval — not reasoning — as the
bottleneck.**

Arbiter replaces the retrieval step entirely.

---

## High-Level System Overview

Arbiter is four components, each doing one job:

- **Ingestion (`arbiter/herb.py`, `arbiter/build.py`)** — parses eight HERB sources and
  *infers* the two relationships HERB never states outright: release ordering (from
  artifact id structure) and review sessions (parsed from document links posted in
  Slack planning channels). Emits integer-keyed rows.
- **HydraDB — the graph engine** — an object-store-native OpenCypher database. Holds
  36,518 nodes and 77,144 edges. **Every traversal runs inside it**; Arbiter never
  pulls a subgraph into Python to walk it.
- **Resolver (`arbiter/query.py`)** — compiles a question into a traversal, or
  **refuses**. If the graph has no vocabulary for the question, it says so instead of
  guessing.
- **Console (`web/index.html`)** — a single-page UI that renders the answer, **the
  Cypher that produced it**, and the evidence path. Also ships as a standalone
  deployable frontend (`frontend/`, ES modules, no build step).

## Deployment Surfaces

| Component | Technology | Deployment | Purpose |
|---|---|---|---|
| **Graph engine** | HydraDB v0.1.0 | Docker container, local object store | Stores the graph; executes every OpenCypher traversal |
| **API** | FastAPI + Uvicorn | Stateful host (local, VM or container) | Compiles questions into traversals; serves evidence |
| **Console** | Vanilla ES modules, one 2D canvas | Same-origin with API, or Vercel | Answer · Cypher · evidence · graph · data browser |
| **Corpus** | HERB (Salesforce AI Research) | Fetched at runtime, **not vendored** | 38,490 artifacts across 30 products |

The graph node runs from `ghcr.io/hydra-db/hydradb:latest` with a local object store —
no HydraDB source is vendored into this repo.

```mermaid
flowchart LR
  subgraph ingest["Ingestion — runs once"]
    HERB["HERB corpus<br/>38,490 artifacts · 8 sources"] --> BUILD["arbiter/build.py<br/>parse · infer · mint integer ids"]
  end

  BUILD -->|"batch write<br/>36,518 nodes · 77,144 edges"| HYDRA[("HydraDB v0.1.0<br/>OpenCypher engine<br/>object-store native")]

  subgraph query["Query time"]
    UI["Console<br/>ES modules · 2D canvas"] -->|"HTTP /api/ask"| API["FastAPI<br/>arbiter/query.py<br/>question becomes a traversal, or a refusal"]
    API -->|"OpenCypher over HTTP"| HYDRA
    HYDRA -->|"vertex ids"| API
    API -->|"answer + Cypher + evidence"| UI
  end
```

<p align="center"><strong>Figure 1.</strong> Ingestion runs once. At query time, OpenCypher goes over HTTP to HydraDB and vertex ids come back.</p>

---

## How the Traversal Works

Answering *"who worked on the previous release"* is not one lookup — it is a chain of
four hops, three of which touch relationships that appear in no document's text.
Below, we walk through each stage.

### 1. The question becomes a path, not a query string

This is the whole argument, in one query:

```cypher
MATCH (ro:Role {id: 1000007})<-[:HAS_ROLE]-(e)-[:AUTHORED]->(a)
      -[:ABOUT_RELEASE]->(rel {id: 4000073})
WHERE a.id >= 10000000 AND a.id < 11000000
RETURN DISTINCT e.id AS employee, a.id AS artifact
```

Read it right to left: start from a release the question only refers to obliquely
(*"the previous"*), walk backwards to the artifacts belonging to it, land on people
through a role edge that appears in no document's text.

The `WHERE` clause is the typed-edge constraint — documents only — expressed as an
integer range, because entity type is encoded in the id itself. HydraDB v0.1.0's only
batch write form accepts ids alone, so the id band *is* the label (see
[`docs/hydradb-subset.md`](docs/hydradb-subset.md)).

Copy-pasteable against a loaded node:

```bash
curl -sS http://$(cat .wslip):8443/v1/graphs/default/query \
  -H "Authorization: Bearer local-development-token-32-bytes" \
  -H 'X-Graph-Namespace: default' -H 'Content-Type: application/json' \
  --data '{"cell_id":"cell-0","query":"MATCH (ro:Role {id: 1000007})<-[:HAS_ROLE]-(e)-[:AUTHORED]->(a)-[:ABOUT_RELEASE]->(rel {id: 4000073}) WHERE a.id >= 10000000 AND a.id < 11000000 RETURN DISTINCT e.id AS employee"}'
```

### 2. The answer is a path, and every hop is walkable

We treat the traversal as the deliverable, not a hidden implementation detail. Each
evidence row carries the **whole path** that produced it — `role → employee → artifact
→ release` — and every hop is a link into the node it landed on.

Opening a hop shows what HydraDB actually stores for that vertex: the edges the
ontology permits, how many of each are populated, the properties on the node, and
**every query the page issued to build it**. Nothing on that page is inferred from the
answer you came from; it is all read back out of the graph.

![Node inspector — a hop from the evidence path, with its permitted edges and every query the page ran](evidence-path.png)

### 3. Every answer is scored against HERB ground truth — including the misses

This is where most demos stop showing you things. Arbiter renders the ground-truth
comparison inline, in the answer header, and a partial answer says so on its face
rather than being quietly rounded up to a win.

Below: 4 of 11 ground-truth IDs matched — the header reads **7 missing · 0 extra**.
Precision held; recall did not. The UI says so.

![Ground truth comparison showing 7 missing and 0 extra against 11 ground-truth IDs](arbiter-groundtruth.png)

### 4. When the graph cannot answer, it refuses

An enterprise system that invents an answer is worse than one that declines. HERB
includes 361 questions the corpus genuinely cannot support — about bug lifecycle
states, competitor entities and feature deferral history that Arbiter never ingests.

Arbiter refuses **361 of 361**, with **zero false refusals** across 100 answerable
questions. Each refusal names its own reason rather than returning an empty list:

| Reason for refusal | n |
|---|---|
| No traversal is registered for this question shape | 148 |
| Bug resolution outcomes are not ingested | 97 |
| No competitor entities are ingested | 70 |
| Bug lifecycle state is not ingested | 36 |
| Feature deferral history is not ingested | 10 |

213 of 361 (59%) are genuine graph vocabulary gaps. 148 (41%) are unsupported question
shapes — safe, but a coverage limit rather than a graph result. We report the split
because the two mean different things.

### 5. Explore the graph yourself

The graph view seeds from a product and expands one hop per click. The panel shows the
node's stored properties, every branch it walked with the row count each returned, and
a **`capped at 60`** badge on any direction that hit the per-direction limit — so a
node with more neighbours than are drawn says so, rather than looking complete.

![Graph explorer — force-directed canvas with node inspector and the 21 queries the panel ran](graph-tab.png)

### 6. Browse the rows the answers came from

The data browser is the audit trail. Search any entity, open any node, see every
neighbour grouped by edge type — with the count query and the fetch query printed
underneath.

It also shows you why this task resists embeddings. Search `Ian Smith` and you get
**ten different employees**, each with a distinct id, and roles ranging from Software
Engineer to Marketing Manager to QA Specialist. A name is not an identity here. Only
the edges disambiguate.

![Data browser — ten distinct employees all named Ian Smith](data-tab.png)

<p align="center">
  <img src="arbiter-375.png" alt="Arbiter console at 375px width" width="300">
</p>
<p align="center"><strong>Figure 2.</strong> The console is responsive down to 375px, keyboard-navigable, and honours <code>prefers-reduced-motion</code>.</p>

---

## Measured Results

Produced by `python scripts/eval_hydra.py` against a live HydraDB v0.1.0 node with the
full HERB corpus loaded. Raw output: [`results/eval_hydra.json`](results/eval_hydra.json).

The graph covers all 30 products. The evaluation covers the **20 distinct products**
for which HERB defines these two question families (10 products each).

### Accuracy

| Family | n | Arbiter exact | Arbiter F1 | precision | recall | BM25 exact | BM25 F1 |
|---|---|---|---|---|---|---|---|
| `person_previous_release` | 50 | **40/50 (80.0%)** | 0.800 | 0.800 | 0.800 | 0/50 | 0.028 |
| `doc_reviewers` | 50 | 0/50 (0.0%) | **0.593** | 0.935 | 0.455 | 0/50 | 0.391 |

**HERB paper reference: best agentic RAG ~30% accuracy.**

Two things we will not round off. On `doc_reviewers`, Arbiter scores **zero exact
matches** — it rarely lands the whole set — but at precision 0.935 it is almost never
wrong about the reviewers it *does* return; the recall of 0.455 is the real gap.

And BM25 was chosen over a dense retriever **deliberately**: it is the *stronger*
baseline on exact-token queries like role names, so beating it is a harder claim than
beating an embedding model would have been.

### Abstention

| Direction | n | correct | rate |
|---|---|---|---|
| Unanswerable, correctly refused | 361 | 361 | **100.0%** |
| Answerable, correctly answered | 100 | 100 | **100.0%** |

False refusals: **0**.

Reproduce both tables:

```bash
.venv/Scripts/python scripts/eval_hydra.py     # accuracy vs BM25, via HydraDB
.venv/Scripts/python scripts/probe_cypher.py   # what Cypher v0.1.0 actually executes
```

---

## Get Started

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| [Python](https://www.python.org/downloads/) | 3.11+ | 3.12 used here |
| [Docker](https://docs.docker.com/get-docker/) | any recent | runs the HydraDB node |
| `curl`, `bash` | — | Git Bash or WSL on Windows |
| Disk | ~250 MB | HERB corpus + HydraDB store |

**No API keys. No paid services. Every data source is public.**

### 1. Clone and install

```bash
git clone https://github.com/midhunrajcharles/arbiter.git
cd arbiter
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows
# .venv/bin/pip install -r requirements.txt       # macOS / Linux
```

### 2. Fetch the HERB corpus

HERB is **not** redistributed in this repo — it is Salesforce's, released for research
purposes only. This pulls it from the original source:

```bash
bash scripts/fetch_data.sh
```

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

```
STATUS Up 3 seconds
IP 172.26.200.199
```

Idempotent — run it any time the node needs restoring. It starts
`ghcr.io/hydra-db/hydradb:latest` with a local object store, waits for `/readyz`, and
writes the node address to `.wslip`.

### 4. Load the graph

```bash
.venv/Scripts/python scripts/load.py
```

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

Open <http://127.0.0.1:8000>. Pick a product, click a HERB question, and the UI shows
the answer, **the Cypher that produced it**, and every artifact the traversal touched.

### 6. Reproduce the numbers

See [Measured Results](#measured-results) above.

---

## Schema

The entity-relationship model. Both artifacts below are generated from
`arbiter/schema.py` by `scripts/gen_dbml.py`, **never hand-edited**, so they cannot
drift from the loader. Structure comes from the code; edge counts come from
`results/ontology.json`, a dump of the live `/api/ontology` endpoint.

<!-- BEGIN GENERATED ERD -->
```mermaid
erDiagram
    ROLE {
        integer id PK
        varchar key
    }
    EMPLOYEE {
        integer id PK
        varchar key
        varchar name
        varchar role FK
        integer manager_id FK
    }
    PRODUCT {
        integer id PK
        varchar key
    }
    RELEASE {
        integer id PK
        varchar key
        integer seq
        integer product_id FK
        integer precedes_id FK
    }
    COMPANY {
        integer id PK
        varchar key
    }
    CUSTOMER {
        integer id PK
        varchar key
        integer company_id FK
    }
    ARTIFACT {
        integer id PK
        varchar kind
    }
    DOCUMENT {
        integer id PK
        varchar key
        varchar dtype
        integer product_id FK
    }
    EMPLOYEE }o--|| ROLE : "HAS_ROLE 530"
    EMPLOYEE ||--o{ ARTIFACT : "AUTHORED 45,192"
    ARTIFACT }o--|| RELEASE : "ABOUT_RELEASE 15,326"
    RELEASE ||--o| RELEASE : "PRECEDES 70"
    RELEASE }o--|| PRODUCT : "OF_PRODUCT 100"
    ARTIFACT }o--o{ EMPLOYEE : "MENTIONS 12,960"
    EMPLOYEE }o--|| EMPLOYEE : "REPORTS_TO 295"
    ARTIFACT }o--|| CUSTOMER : "REPORTED_BY declared only"
    CUSTOMER }o--|| COMPANY : "WORKS_FOR 120"
    ARTIFACT }o--o{ DOCUMENT : "REVIEWS 2,551"
```
<!-- END GENERATED ERD -->

*Figure 3. Entity type is carried by the integer id band rather than a label, because
HydraDB v0.1.0's only batch write form accepts ids alone. `PRECEDES` is the edge that
orders releases, and so the only reason "the previous release" is answerable at all.
`REPORTED_BY` is declared by the ontology but never emitted by the loader — HERB
provides no artifact-to-customer link.*

- [`schema.dbml`](schema.dbml) — the same schema in [DBML](https://dbml.dbdiagram.io/docs/).
  Paste it into [dbdiagram.io](https://dbdiagram.io/d) for an interactive, laid-out diagram.
- Regenerate after any schema change: `python scripts/gen_dbml.py`
- Check for staleness (CI-friendly, exits 1 if out of date): `python scripts/gen_dbml.py --check`

### Graph model

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
On Windows, Docker Desktop's `com.docker.service` needs administrator rights to start.
Either launch Docker Desktop as administrator, or use the WSL path this project uses —
`scripts/hydra_up.sh` runs `dockerd` inside the Ubuntu distro and needs no elevation.

**Node answers `/readyz` then dies on the first query**
`RUST_MIN_STACK` is unset. `scripts/hydra_up.sh` sets it to `33554432`.

**`node id property must be an integer`**
You are passing a string key. HydraDB v0.1.0 requires integer vertex ids — see
[`docs/hydradb-subset.md`](docs/hydradb-subset.md); `arbiter/schema.py:IdMap` does the
mapping.

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

- **HERB** — *Benchmarking Deep Search over Heterogeneous Enterprise Data*, Salesforce
  AI Research, EMNLP 2025 (industry track).
  [dataset](https://huggingface.co/datasets/Salesforce/HERB) ·
  [code](https://github.com/SalesforceAIResearch/HERB). Released for research purposes
  only; fetched at runtime, not redistributed here.
- **HydraDB** — [hydra-db/hydradb](https://github.com/hydra-db/hydradb), AGPL-3.0. Used
  as a container image; no HydraDB source is vendored into this repo.
- **LIMIT** — *On the Theoretical Limitations of Embedding-Based Retrieval*, Weller et
  al., 2025. The formal result behind why this task resists embeddings.

Third-party Python dependencies are listed in `requirements.txt`.

## License

MIT — see [LICENSE](LICENSE).

---

**Built on [HydraDB](https://github.com/hydra-db/hydradb)** — the graph lives in an
OpenCypher database and every traversal executes inside it. Originally built for
[Hack Hydra](https://hackhydra.hydradb.com/) 2026, Track 1 (Enterprise Context and
Ontology).
