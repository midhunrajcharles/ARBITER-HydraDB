# First measured result (SearchForce, HERB)

Question class: "Find employee IDs of {ROLE} who worked on the previous release of SearchForce?"

| System | Exact match | Mean F1 |
|---|---|---|
| Graph traversal (HydraDB model) | 4/5 | 0.80 |
| BM25 retrieval baseline | 0/5 | 0.00 |

Reference point: HERB (Salesforce, EMNLP 2025 industry) reports the best agentic
RAG systems reach ~30% accuracy on this benchmark and names retrieval, not
reasoning, as the bottleneck.

Why the graph wins this class: answering requires resolving which releases
PRECEDE the current one (transitively), reverse-traversing to the people who
authored documents about them, then joining against a role attribute stored in
a separate metadata file. The question's embedding is nowhere near the answer's
embedding - no market research report states the author's job title or the word
"previous".

Known limitation, not hidden: the 5th question (QA Specialists) fails. Those
employees have ZERO authored edges - they appear only inside document text, not
in structured fields. That class needs extraction on top of traversal. Traversal
still does the useful work by narrowing 1,393 artifacts to the 9 that matter.

BM25 was chosen over a dense retriever deliberately: it is the stronger baseline
on exact-token queries like role names, so beating it is a harder claim.
