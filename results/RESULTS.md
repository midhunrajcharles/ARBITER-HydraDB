# Measured results

Produced by `python scripts/eval_hydra.py` against a live HydraDB v0.1.0 node
with the full HERB corpus loaded (30 products, 38,490 artifacts, 77,144 edges).
Raw output: `results/eval_hydra.json`.

## Accuracy

| Family | n | exact | F1 | precision | recall | BM25 exact | BM25 F1 |
|---|---|---|---|---|---|---|---|
| person_previous_release | 50 | 40/50 (80.0%) | 0.800 | 0.800 | 0.800 | 0/50 | 0.028 |
| doc_reviewers | 50 | 0/50 (0.0%) | 0.593 | 0.935 | 0.455 | 0/50 | 0.391 |

HERB paper reference: best agentic RAG ~30% accuracy; retrieval is the bottleneck.

BM25 was chosen over a dense retriever deliberately - it is the stronger baseline
on exact-token queries like role names, so beating it is a harder claim.

## Abstention

| Direction | n | correct | rate |
|---|---|---|---|
| unanswerable correctly refused | 361 | 361 | 100.0% |
| answerable correctly answered | 100 | 100 | 100.0% |

False refusals: 0.

### Why each refusal happened

| Reason | n |
|---|---|
| bug resolution outcomes are not ingested | 97 |
| no competitor entities are ingested | 70 |
| bug lifecycle state is not ingested | 36 |
| feature deferral history is not ingested | 10 |
| no traversal is registered for this shape | 148 |

213/361 (59%) are genuine graph vocabulary gaps. 148/361 (41%) are unsupported
question shapes - safe, but a coverage limit rather than a graph result.

## Performance

| Operation | Result |
|---|---|
| Full corpus load (530 employees, 400 documents, 100 releases, 77,144 edges) | 24.2 s |
| Batch edge write | 500 rows/request |
| 3-hop resolution traversal | median 48 ms, p95 57 ms (n=20) |
| Full evaluation (200 questions + 361 abstention checks) | 171.6 s |
