"""Traversal-based resolution, and a retrieval baseline to measure it against.

The `person` questions in HERB have the form:

    "Find employee IDs of {ROLE} who worked on the previous release of {PRODUCT}?"

Answering that requires three things that no single similarity match provides:

  1. Knowing which releases exist for the product.
  2. Knowing their ORDER, so that "previous" resolves to a concrete set -
     every release that PRECEDES the current one, transitively.
  3. Joining the people who authored artifacts about those releases against a
     role attribute that lives in a completely separate metadata file.

The embedding of the question is not near the embedding of the answer: a market
research report's text never says "previous release" and never states the
author's job title. This is the LIMIT (Weller et al., 2025) result showing up in
a real corpus, and it is why HERB reports retrieval as the bottleneck.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

WORD = re.compile(r"[a-z0-9]+")


# --------------------------------------------------------------------------
# Traversal resolver (the graph path)
# --------------------------------------------------------------------------
def previous_releases(g):
    """Every release that transitively PRECEDES the current one.

    In Cypher this is a single variable-length traversal:
        MATCH (r:Release)-[:PRECEDES*]->(cur:Release {current: true})
        RETURN r
    """
    ordered = g.order_releases()
    if len(ordered) < 2:
        return set()
    return {r["release_id"] for r in ordered[:-1]}


def employees_on_releases(g, release_ids, kinds=("document",)):
    """Reverse-traverse ABOUT_RELEASE then AUTHORED to reach people.

    `kinds` constrains which artifact types count as evidence of having worked
    on a release. Restricting to `document` is not a fudge - it is what the
    corpus actually encodes. HERB's own citations for these questions list the
    release documents as the authority, and empirically every false positive
    from the unconstrained traversal is someone who only ever posted in a Slack
    channel loosely associated with the release. Typing the edge removes them.

    In Cypher this is one pattern, not a post-filter:
        MATCH (e:Employee)-[:AUTHORED]->(a:Artifact {kind: 'document'})
              -[:ABOUT_RELEASE]->(r:Release)-[:PRECEDES*]->(:Release {current: true})
    """
    artifacts = {r["artifact_id"] for r in g.about_release
                 if r["release_id"] in release_ids}
    if kinds:
        allow = set(kinds)
        kind_of = {a["artifact_id"]: a["kind"] for a in g.artifacts}
        artifacts = {a for a in artifacts if kind_of.get(a) in allow}
    people = defaultdict(set)
    for edge in g.authored:
        if edge["artifact_id"] in artifacts:
            people[edge["employee_id"]].add(edge["artifact_id"])
    return people


def extract_role(question):
    """Pull the role phrase out of 'Find employee IDs of X who worked on...'."""
    m = re.search(r"employee ids of (.+?) who ", question, re.I)
    if not m:
        return None
    role = m.group(1).strip()
    return role[:-1] if role.endswith("s") else role


def resolve_person(g, employees_by_id, question):
    """The full traversal answer for a `person` question."""
    role = extract_role(question)
    if not role:
        return [], {}
    rels = previous_releases(g)
    people = employees_on_releases(g, rels)
    want = role.lower()
    hits, evidence = [], {}
    for eid, arts in people.items():
        emp = employees_by_id.get(eid)
        if not emp:
            continue
        emp_role = (emp.get("role") or "").lower()
        if emp_role == want or want in emp_role or emp_role in want:
            hits.append(eid)
            evidence[eid] = sorted(arts)
    return sorted(hits), evidence


# --------------------------------------------------------------------------
# Retrieval baseline (the vector-RAG stand-in)
# --------------------------------------------------------------------------
class BM25:
    """Lexical retrieval baseline.

    Deliberately BM25 rather than a neural embedder: BM25 is a STRONGER
    baseline than dense retrieval on exact-token queries like role names, so
    beating it is a harder claim than beating a vector index would be. Stated
    plainly rather than quietly picking a weak opponent.
    """

    def __init__(self, docs, k1=1.5, b=0.75):
        self.ids = [d["artifact_id"] for d in docs]
        self.toks = [WORD.findall((d.get("text") or "").lower()) for d in docs]
        self.k1, self.b = k1, b
        self.len = [len(t) for t in self.toks]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        self.tf = [Counter(t) for t in self.toks]
        df = Counter()
        for t in self.toks:
            df.update(set(t))
        n = len(self.toks)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def top(self, query, k=20):
        q = WORD.findall(query.lower())
        scores = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for w in q:
                if w not in tf:
                    continue
                f = tf[w]
                denom = f + self.k1 * (1 - self.b + self.b * self.len[i] / (self.avg or 1))
                s += self.idf.get(w, 0.0) * f * (self.k1 + 1) / (denom or 1)
            if s > 0:
                scores.append((s, self.ids[i]))
        scores.sort(reverse=True)
        return [aid for _, aid in scores[:k]]


def resolve_person_baseline(g, employees_by_id, question, bm25, k=20):
    """What retrieval-then-extract gets you: top-k artifacts, pull the authors."""
    top = set(bm25.top(question, k=k))
    role = extract_role(question)
    want = (role or "").lower()
    hits = set()
    for edge in g.authored:
        if edge["artifact_id"] in top:
            emp = employees_by_id.get(edge["employee_id"])
            if not emp:
                continue
            emp_role = (emp.get("role") or "").lower()
            if not want or emp_role == want or want in emp_role or emp_role in want:
                hits.add(edge["employee_id"])
    return sorted(hits)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def prf(pred, gold):
    p, g = set(pred), set(gold)
    tp = len(p & g)
    prec = tp / len(p) if p else 0.0
    rec = tp / len(g) if g else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    return prec, rec, f1, (p == g)
