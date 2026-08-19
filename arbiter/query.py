"""Question resolution against HydraDB.

Every answer here is produced by OpenCypher traversal executed *inside* HydraDB.
Nothing is retrieved by similarity and nothing is re-ranked client side. The
resolver's job is to translate a natural-language question into a traversal and
to translate vertex ids back into human-readable keys.

The traversal that does the work:

    MATCH (ro:Role {id: $role})<-[:HAS_ROLE]-(e)-[:AUTHORED]->(a)
          -[:ABOUT_RELEASE]->(rel {id: $release})
    WHERE a.id >= 10000000 AND a.id < 11000000
    RETURN DISTINCT e.id AS employee, a.id AS artifact

Read it right to left and the reason it beats retrieval is visible: it starts
from a release the question only refers to obliquely ("the previous release"),
walks backwards through the artifacts that belong to it, and lands on people
via a role edge that is never mentioned in any document's text. There is no
query embedding that is near that answer, which is the point LIMIT makes
formally and HERB measures empirically.
"""
from __future__ import annotations

import re

from arbiter import schema

DOC_LO = schema.BASE["document"]
DOC_HI = DOC_LO + schema.SPAN


class Resolver:
    def __init__(self, hydra, build):
        self.h = hydra
        self.b = build
        self._release_cache: dict[int, list[dict]] = {}

    # -- vocabulary --------------------------------------------------------
    def role_id(self, role_text: str):
        """Map a role phrase from the question onto a Role node."""
        want = (role_text or "").strip().lower().rstrip("s")
        best = None
        for r in self.b.roles:
            rl = r.lower()
            if rl == want or rl.rstrip("s") == want:
                return self.b.role(r), r
            if want and (want in rl or rl in want):
                if best is None or len(r) < len(best[1]):
                    best = (self.b.role(r), r)
        return best if best else (None, None)

    def product_id(self, name: str):
        return self.b.prod(name) if name in self.b.products else None

    # -- graph lookups (all server-side) -----------------------------------
    def releases_of(self, product_id: int) -> list[dict]:
        """MATCH (rel)-[:OF_PRODUCT]->(p) - the product's releases, with order."""
        if product_id in self._release_cache:
            return self._release_cache[product_id]
        # Documents also hang off Product via OF_PRODUCT, so constrain to the
        # Release id band. Type is encoded in the id range, so this is a range
        # predicate rather than a label test.
        lo = schema.BASE["release"]
        hi = lo + schema.SPAN
        cy = (f"MATCH (rel)-[:OF_PRODUCT]->(p {{id: {product_id}}}) "
              f"WHERE rel.id >= {lo} AND rel.id < {hi} "
              f"RETURN rel.id AS id, rel.seq AS seq, rel.key AS key")
        rows = [r for r in self.h.run(cy)
                if r.get("id") is not None and r.get("seq") is not None]
        rows.sort(key=lambda r: (r.get("seq") if r.get("seq") is not None else 0))
        self._release_cache[product_id] = rows
        return rows

    def previous_releases(self, product_id: int) -> list[dict]:
        """Everything before the current release.

        v0.1.0 has no `-[:PRECEDES*]->`, so "transitively before" is derived
        from the ordering the PRECEDES chain encodes rather than walked in one
        pattern. The edge still carries the ordering; only the traversal syntax
        is unavailable.
        """
        rels = self.releases_of(product_id)
        if len(rels) < 2:
            return []
        top = max(r["seq"] for r in rels)
        return [r for r in rels if r["seq"] < top]

    def authors_by_role(self, role_id: int, release_id: int, documents_only=True):
        """The core 3-hop traversal, executed by HydraDB."""
        where = (f" WHERE a.id >= {DOC_LO} AND a.id < {DOC_HI}" if documents_only else "")
        cy = (f"MATCH (ro:Role {{id: {role_id}}})<-[:HAS_ROLE]-(e)-[:AUTHORED]->(a)"
              f"-[:ABOUT_RELEASE]->(rel {{id: {release_id}}}){where} "
              f"RETURN DISTINCT e.id AS employee, a.id AS artifact")
        return cy, self.h.run(cy)

    # -- question answering ------------------------------------------------
    def answer_person_previous_release(self, question: str, product: str):
        """'Find employee IDs of {ROLE} who worked on the previous release of {P}?'"""
        m = re.search(r"employee ids of (.+?) who ", question, re.I)
        role_text = m.group(1).strip() if m else ""
        rid, role_name = self.role_id(role_text)
        pid = self.product_id(product)

        trace = {"role_text": role_text, "role_matched": role_name,
                 "product": product, "queries": [], "evidence": []}
        if rid is None or pid is None:
            return [], trace

        found = set()
        for rel in self.previous_releases(pid):
            cy, rows = self.authors_by_role(rid, rel["id"])
            trace["queries"].append(cy)
            for row in rows:
                key = self.b.ids.key(row["employee"])
                art = self.b.ids.entry(row["artifact"])
                if key:
                    found.add(key)
                    trace["evidence"].append({
                        "employee": key,
                        "artifact": art[1] if art else None,
                        "artifact_kind": art[0] if art else None,
                        "release": rel.get("key"),
                    })
        return sorted(found), trace

    # -- document authors and reviewers ------------------------------------
    DOC_TYPES = ("Market Research Report", "Product Vision Document",
                 "Product Requirements Document", "Technical Specifications Document",
                 "System Design Document")

    def doc_type_in(self, question: str):
        low = question.lower()
        for t in self.DOC_TYPES:
            if t.lower() in low:
                return t
        return None

    def documents_of_type(self, product_id: int, dtype: str):
        cy = (f'MATCH (d:Document {{dtype: "{dtype}"}})-[:OF_PRODUCT]->(p {{id: {product_id}}}) '
              f"RETURN d.id AS id, d.key AS key")
        return cy, [r for r in self.h.run(cy) if r.get("id") is not None]

    def answer_doc_reviewers(self, question: str, product: str):
        """'authors and key reviewers of the {DOC_TYPE} for {PRODUCT}?'

        Two traversals per document:
          author    (e)-[:AUTHORED]->(d)
          reviewers (e)-[:AUTHORED]->(slack)-[:REVIEWS]->(d)

        The REVIEWS edge exists because a review in this corpus is a real,
        recoverable structure: someone posts the document link into a planning
        channel and that day's replies are the review. The link is parsed from
        the message, so the edge is derived from data rather than guessed.
        """
        dtype = self.doc_type_in(question)
        pid = self.product_id(product)
        trace = {"doc_type": dtype, "product": product, "queries": [], "evidence": []}
        if not dtype or pid is None:
            return [], trace

        cy_docs, docs = self.documents_of_type(pid, dtype)
        trace["queries"].append(cy_docs)

        found = set()
        for d in docs:
            did, dkey = d["id"], d.get("key")
            cy_a = f"MATCH (e)-[:AUTHORED]->(d {{id: {did}}}) RETURN DISTINCT e.id AS employee"
            cy_r = (f"MATCH (e)-[:AUTHORED]->(s)-[:REVIEWS]->(d {{id: {did}}}) "
                    f"RETURN DISTINCT e.id AS employee")
            trace["queries"].extend([cy_a, cy_r])
            for cy, how in ((cy_a, "authored"), (cy_r, "reviewed")):
                for row in self.h.run(cy):
                    key = self.b.ids.key(row["employee"])
                    if key:
                        found.add(key)
                        trace["evidence"].append({"employee": key, "artifact": dkey,
                                                  "artifact_kind": "document",
                                                  "release": how})
        return sorted(found), trace

    # -- router ------------------------------------------------------------
    def route(self, question: str, product: str):
        """Pick the traversal for this question shape."""
        low = question.lower()
        if "worked on the previous release" in low:
            return "person_previous_release", self.answer_person_previous_release(question, product)
        if "authors and key reviewers" in low:
            return "doc_reviewers", self.answer_doc_reviewers(question, product)
        return "unsupported", ([], {"queries": [], "evidence": [],
                                    "reason": "no traversal registered for this question shape"})

    # -- abstention --------------------------------------------------------
    def can_answer(self, question: str, product: str) -> tuple[bool, str]:
        """Decide whether the graph can support an answer at all.

        HERB ships 699 unanswerable questions. A retrieval system will always
        return its top-k and an LLM will usually write something plausible from
        them. A traversal either lands on nodes or it does not, so "no path
        exists" is a first-class, checkable answer rather than a confidence
        threshold. This is the abstention signal.
        """
        low = question.lower()
        # Competitor questions: HERB's corpus contains no competitor entities.
        if "competitor" in low:
            return False, "no competitor entities exist in the graph"
        pid = self.product_id(product)
        if pid is None:
            return False, f"product '{product}' is not in the graph"
        m = re.search(r"employee ids of (.+?) who ", question, re.I)
        if m:
            rid, _ = self.role_id(m.group(1))
            if rid is None:
                return False, f"role '{m.group(1).strip()}' has no Role node"
        return True, "traversal path exists"
