# Arbiter — Hack Hydra 2026, Track 1

**Enterprise context resolution by graph traversal, not similarity.**

Repo: https://github.com/midhunrajcharles/arbiter · Demo video: `<youtube url>` · Track: Enterprise Context and Ontology

---

## Inspiration & Problem

HERB — Salesforce's enterprise search benchmark, and one of the datasets the Hack
Hydra organisers link for this track — asks questions like:

> *Find employee IDs of Marketing Research Analysts who worked on the previous
> release of SearchForce.*

We went looking for the document that answers it. There isn't one. No market
research report contains the phrase "previous release." None of them states its
author's job title — that lives in a separate metadata file. The corpus has
38,490 artifacts across Slack, documents, pull requests and meeting transcripts,
and the answer is in **none** of them individually.

It only exists in the *relationships between* them: which releases exist, which
one came before the current one, who authored the artifacts belonging to it, and
what their role is.

The HERB paper measures the consequence precisely — the best agentic RAG systems
reach roughly **30% accuracy**, and the authors name **retrieval, not reasoning,
as the bottleneck.** That is the same wall the LIMIT paper (Weller et al., 2025)
proves formally: for some query–answer pairs, no embedding puts them near each
other, regardless of model quality.

So we stopped trying to retrieve the answer and traversed to it instead.

## What it does

Arbiter loads an enterprise corpus into HydraDB as a typed graph and answers
questions by executing OpenCypher traversals inside the database. Every answer
comes back with the query that produced it and every artifact the traversal
touched.

- **Resolves multi-hop questions** that span Slack, documents and metadata
- **Shows its work** — the Cypher, and the evidence path, for every answer
- **Abstains when there is no path**, rather than returning a plausible guess

## Results

The graph is built from **all 30 HERB products**. The evaluation covers the
**20 distinct products** for which HERB actually defines these two question
families (10 products each). Every Arbiter answer is produced by a live HydraDB
traversal; BM25 runs over the identical corpus.

| Question family | n | Arbiter exact | Arbiter F1 | precision | recall | BM25 exact | BM25 F1 |
|---|---|---|---|---|---|---|---|
| "who worked on the previous release" | 50 | **40/50 (80.0%)** | **0.800** | 0.800 | 0.800 | 0/50 (0.0%) | 0.028 |
| "authors and key reviewers of {doc}" | 50 | 0/50 (0.0%) | **0.593** | **0.935** | 0.455 | 0/50 (0.0%) | 0.391 |

*Table 1. Accuracy by question family. Every Arbiter answer is produced by a live
HydraDB traversal; BM25 runs over the identical corpus.*

Reference point: **~30%** is where the HERB paper puts the best agentic RAG
systems on this benchmark.

Read the second row honestly: we score **no exact matches** on the reviewer
family, because HERB's ground truth there is broader than the review-thread
structure supports. What we do get is **0.935 precision** — when Arbiter names a
reviewer, it is almost always right — at 0.455 recall. F1 0.593 against BM25's
0.391 on the same questions.

### Abstention

HERB ships 699 unanswerable questions. Almost nobody reports on them.

| Direction | n | correct | rate |
|---|---|---|---|
| Unanswerable questions correctly **refused** | 361 | 361 | **100.0%** |
| Answerable questions correctly **answered** (not wrongly refused) | 100 | 100 | **100.0%** |

*Table 2. Abstention measured in both directions. False refusals: **0**. Refusing
everything would score 100% on the first row and 0% on the second, which is why
both are reported.*

And the honest breakdown of *why* each refusal happened:

| Reason | n | What it means |
|---|---|---|
| bug resolution outcomes are not ingested | 97 | the graph genuinely lacks the concept |
| no competitor entities are ingested | 70 | the graph genuinely lacks the concept |
| bug lifecycle state is not ingested | 36 | the graph genuinely lacks the concept |
| feature deferral history is not ingested | 10 | the graph genuinely lacks the concept |
| no traversal is registered for this shape | 148 | **we simply don't support it yet** |

*Table 3. 213 of 361 refusals (59%) are the graph reporting a real vocabulary
gap. The remaining 148 (41%) are Arbiter declining a question shape it has no
traversal for. Both are safe failures — neither invents an answer — but they are
not the same claim, so they are not merged.*

We chose BM25 rather than a dense retriever on purpose. BM25 is the *stronger*
baseline for exact-token queries like role names, so beating it is a harder claim
than beating a vector index would have been.

## How we used HydraDB

| Feature | How we used it | Why it mattered |
|---|---|---|
| OpenCypher multi-hop traversal | The 3-hop resolution query runs server-side: role → employees → artifacts → release | This *is* the product. There is no similarity formulation of "the previous release" |
| Integer vertex ids | Entity type encoded in disjoint 1M-wide id bands | Makes the typed-edge constraint (documents only) an integer range predicate the engine answers from the id alone — no property lookup, no label |
| `UNWIND $rows` batch writes | 77,144 edges in 500-row batches | Full corpus loads in **24.2 s** instead of the ~40 min single-writes would have taken |
| Reverse traversal `<-[:HAS_ROLE]-` | Start from the Role node and walk backwards to people | Lets one query serve every role without a per-role index |
| Property nodes on one-hop CREATE | Employees, releases and documents carry properties | v0.1.0 cannot create a bare node, so roles became first-class nodes — better ontology, forced by the engine |
| Local object-store backend | Node runs from a local store, no cloud dependency | Whole project reproduces offline after one dataset fetch |

**We also mapped what OpenCypher v0.1.0 actually executes**, because the README's
"practical OpenCypher subset" turned out to be much narrower than it sounds, and
building against assumptions would have cost us the weekend. Documented in
[`docs/hydradb-subset.md`](docs/hydradb-subset.md) and reproducible with
`scripts/probe_cypher.py`. Findings that changed our design:

- `CREATE` only executes as a **one-hop edge pattern** — a bare `CREATE (n)` is rejected
- Node `id` **must be an integer**; string keys are rejected outright
- `UNWIND` batch writes accept **ids only** — no labels, no node properties, no edge properties
- `MERGE` does not execute at all, and `MERGE ... ON CREATE/ON MATCH` is explicitly rejected
- Variable-length paths need a **fixed** length — `-[:R*2]->` works, `-[:R*]->` does not
- Multi-hop **reads** work in both directions, which is what makes server-side resolution possible

Worth flagging for other teams: guidance circulating during the event said to
prefer `MERGE` with `ON CREATE` over `MATCH ... CREATE`. On v0.1.0 **neither**
runs — the one-hop `CREATE` edge pattern is the only executable write.

## The traversal

```cypher
MATCH (ro:Role {id: 1000007})<-[:HAS_ROLE]-(e)-[:AUTHORED]->(a)
      -[:ABOUT_RELEASE]->(rel {id: 4000073})
WHERE a.id >= 10000000 AND a.id < 11000000
RETURN DISTINCT e.id AS employee, a.id AS artifact
```

*Figure 1. The resolution traversal. Read right to left: start from a release the
question only refers to obliquely, walk backwards through the artifacts belonging
to it, land on people via a role edge that appears in no document's text.*

Median latency **48 ms**, p95 **57 ms** (n=20).

## Instant setup

```bash
git clone https://github.com/midhunrajcharles/arbiter.git && cd arbiter
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
bash scripts/fetch_data.sh      # pulls HERB from Salesforce's HuggingFace repo
bash scripts/hydra_up.sh        # starts HydraDB, waits for /readyz
.venv/Scripts/python scripts/load.py
.venv/Scripts/python -m uvicorn arbiter.api:app --port 8000
```

No API keys. No paid services. Every data source is public.

## Challenges we ran into

**HydraDB's executable Cypher subset is narrower than documented.** Our first
schema used `MERGE ... ON CREATE SET` for idempotent upserts and `UNWIND $rows`
with labels and properties for bulk loading. Neither executes on v0.1.0. We
rewrote the schema around the one write form that does work, which is what
produced the integer-id-range design — and that turned out better than the
original, because type constraints became range predicates instead of property
filters.

**Docker Desktop's backend service needs administrator rights**, which we didn't
have. `com.docker.service` sat in `Stopped`, so the Linux engine never appeared
and the container could not start. We moved the whole node into a WSL Ubuntu
distro running `dockerd` directly — no elevation needed. `scripts/hydra_up.sh`
encodes that, plus the detail that WSL reaps a distro once its last process
exits, taking the graph node with it.

**Our first traversal over-predicted.** Unconstrained, it returned three people
where ground truth had two. Every false positive turned out to be someone who had
only ever posted in a Slack channel loosely associated with the release, never
authored a release document. Constraining the edge to
`AUTHORED → Artifact{kind:document}` took F1 from 0.80 to 1.00 on that question
with no other change. The corpus encodes authority in artifact type; we just
weren't reading it.

**`OF_PRODUCT` collided.** Adding Document→Product edges silently broke release
lookup, because the release query started returning documents with no `seq`
property. Fixed by constraining to the Release id band — the same range
discipline used everywhere else.

## What's real, and what isn't

Everything reported above is real and reproducible: real HERB data fetched from
Salesforce's HuggingFace repo, real HydraDB queries against a running node, real
ground-truth comparison. Nothing is mocked and no numbers are estimated.

The honest limits:

- We cover **two question families** of the five HERB defines. The remaining
  families (PR links, company bug aggregations, free-text content) are not
  implemented, and the router **abstains** on them rather than guessing. That
  accounts for 148 of the 361 refusals in Table 3 - safe, but it is coverage we
  do not have, not a graph result.
- **QA Specialist questions fail.** Those employees have *zero* authored edges —
  they appear only inside document prose, never in a structured field. That class
  needs extraction layered on top of traversal. Traversal still does useful work
  there by narrowing 1,393 artifacts to the 9 that matter.
- The `REVIEWS` edge has **high precision (0.935) and partial recall (0.455)**.
  It captures reviewers who participated in the document's review session; HERB's
  ground truth for that family is broader than the review-thread structure
  supports, so we win on F1 but score zero exact matches.

## What we learned

That the interesting constraint wasn't the benchmark, it was the engine. Being
forced to model without labels in batch writes pushed entity type into the id
space, and that accidentally produced a better design than the one we set out to
build — type checks became integer comparisons the engine resolves without
touching a property.

And that abstention deserves to be a first-class result. A traversal that lands
on nothing is *information*. A retriever's top-k is never empty, so the "I don't
know" has to be manufactured downstream by a model that has every incentive to
answer anyway.

## What's next

1. **Extraction on top of traversal** for the QA and free-text families — use the
   graph to narrow to the handful of relevant artifacts, then read only those.
2. **Push aggregation server-side** once HydraDB supports `count(n)` and
   `WITH` — the "who resolved the most bugs" family is a pure graph aggregation
   we currently cannot express.
3. **Contribute the subset findings upstream** as a docs PR to `hydra-db/hydradb`;
   the constraint list cost us hours and would cost the next team the same.

## Who this helps

Anyone who has asked an internal search tool a question about their own company
and got back ten documents that each contain a third of the answer. The failure
isn't that the model is too small. It's that the answer was never in any single
document — it was in how they relate, and flat retrieval throws exactly that away.

---

**Built with:** HydraDB (OpenCypher, Bolt/HTTP, object-store native) · Python 3.12 ·
FastAPI · httpx · Docker · WSL2

**Data:** [HERB](https://huggingface.co/datasets/Salesforce/HERB), Salesforce AI
Research, EMNLP 2025 industry track — fetched at runtime, not redistributed.

**AI assistance:** Claude was used during development, as the rules permit.
