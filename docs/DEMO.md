# Demo script — 3:00 hard cap

Hack Hydra requires a demo video of **3 minutes or less** covering the problem,
the solution, a working demo, and how HydraDB is used. All four are named
requirements; all four appear below.

Record with the best microphone available. Narrate over the product, never over
slides. Do not speed up audio to fit — if it runs long, cut a beat.

---

## 0:00–0:15 — The problem, shown not stated

**On screen:** the HERB question, large, in the Arbiter UI.

> "Find employee IDs of Marketing Research Analysts who worked on the previous
> release of SearchForce."

**Say:**
> "This is a question from HERB, Salesforce's enterprise search benchmark. Thirty
> eight thousand artifacts — Slack, documents, pull requests, meeting transcripts.
> And not one of them contains this answer."

---

## 0:15–0:35 — Why retrieval fails

**On screen:** scroll the corpus stats in the header — 38,490 artifacts, 77,144 edges.

**Say:**
> "No market research report says the words 'previous release'. None of them
> states its author's job title. So the question's embedding isn't near the
> answer's embedding — which is exactly the limitation the LIMIT paper proves
> formally. HERB measures the consequence: the best agentic RAG systems get about
> thirty percent, and the paper says retrieval, not reasoning, is the bottleneck."

---

## 0:35–1:15 — The wow: click, answer, proof

**On screen:** click the question. Answer renders.

**Say:**
> "Arbiter answers it by traversal instead."

Point at the **EXACT MATCH** badge and the two employee cards.

> "Two people. Exact match against HERB's ground truth."

**Then scroll to the Cypher panel — this is the moment.**

> "And here's the query that produced it, running inside HydraDB."

Read the shape aloud, right to left:

> "Start at the release the question only refers to obliquely — 'the previous
> one'. Walk backwards to the artifacts that belong to it. Land on the people who
> authored them. Filter by a role edge that appears in no document's text.
> That `WHERE` clause is the type constraint — documents only — and it's an
> integer range because entity type is encoded in the node id."

**Scroll to the evidence table.**

> "Every artifact the traversal touched, with its kind and release. The answer
> comes with its own proof."

---

## 1:15–1:35 — Do it again, live (proves it isn't hardcoded)

**On screen:** switch product to **TrendForce**, click a different role.

**Say:**
> "Different product, different role, same traversal. Nothing here is
> special-cased."

*(This 20 seconds is the most important in the video — it's what separates a demo
from a screenshot.)*

---

## 1:35–2:05 — Abstention, the metric nobody reports

**On screen:** click an **UNANSWERABLE** question (competitor products).

**Say:**
> "HERB also ships six hundred and ninety nine unanswerable questions. Watch."

The abstain card renders.

> "Arbiter refuses. Not because a confidence score dropped below a threshold —
> because there is no path. There are no competitor entities in the graph, so the
> traversal has nowhere to go. A retrieval system still returns its top-k here,
> and a language model will happily write something plausible from it. That's
> where enterprise search actually hurts."

---

## 2:05–2:35 — HydraDB, specifically

**On screen:** `docs/hydradb-subset.md`, then the load timing in `results/load.json`.

**Say:**
> "On HydraDB: this is the open-source engine — Rust, object-store native,
> OpenCypher over Bolt and HTTP. We probed which OpenCypher it actually executes
> in v0.1.0 rather than assuming, and documented it. That mattered: batch writes
> take no labels and no properties, and node ids must be integers. So we encode
> entity type in the integer id range, which makes the type constraint a range
> predicate the engine answers from the id alone."

> "Full corpus — five hundred and thirty employees, four hundred documents,
> seventy seven thousand edges — loads in twenty four seconds. The resolution
> traversal runs in a median of forty eight milliseconds."

---

## 2:35–3:00 — The number, and the honest limit

**On screen:** `results/eval_hydra.json` summary table.

**Say:**
> "Across thirty products: [X] percent exact match on the release-participation
> questions, against zero percent for a BM25 baseline on the identical corpus —
> and BM25 is the *stronger* baseline for exact-token queries like role names."

> "One family we don't win: QA Specialists have no authored edges at all — they
> exist only inside document prose. That needs extraction layered on traversal,
> and it's in the README as future work. Traversal still narrows thirteen hundred
> artifacts to the nine that matter."

> "Repo and setup are linked below. Everything is public data, no API keys."

---

## Checklist before uploading

- [ ] Under 3:00
- [ ] Problem stated before solution
- [ ] Something working on screen inside 90 seconds *(here: 0:35)*
- [ ] HydraDB usage explicitly covered *(2:05 segment — a named requirement)*
- [ ] Second live run with different input *(1:15 — proves it isn't hardcoded)*
- [ ] Limitation stated out loud
- [ ] "Hack Hydra" named in the first seconds
- [ ] Real data on screen, nothing mocked
- [ ] Uploaded **unlisted**, not private; **not** flagged as made for kids
- [ ] Link opens in a private browser window
- [ ] Captions burned in (judges often watch muted)
