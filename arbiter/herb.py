"""HERB -> typed graph rows.

HERB (Salesforce, EMNLP 2025 industry track) is the benchmark the Hack Hydra
organizers link for Track 1. The subset fetched by `scripts/fetch_data.sh` and
parsed here measures 530 employees, 30 products and 38,490 artifacts across
Slack, documents, meeting transcripts, PRs and URLs, with answerable and
unanswerable question sets. Those three figures are the loader's own counts,
recorded in `results/load.json`; nothing here restates them from the paper.

The paper's headline finding is that RETRIEVAL, not reasoning, is the
bottleneck, and that strong agentic RAG systems reach roughly 30% accuracy.
That is the baseline this project is aimed at.

Release ordering is the load-bearing structure. Document ids are
`{release_slug}_{document_type_slug}[_final]`, so stripping the document type
recovers which release an artifact belongs to, and the earliest artifact date
per release recovers their order. Without that ordering the phrase "the
previous release" is unanswerable - which is precisely why similarity search
fails on these questions and traversal does not.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

EID = re.compile(r"eid_[0-9a-f]{8}")
# Slack messages that circulate a document embed its id in a link:
#   <https://sf-internal.slack.com/archives/docs/onforcex_market_research_report|Market Research Report>
DOCLINK = re.compile(r"archives/docs/([A-Za-z0-9_\-]+)")


class ProductGraph:
    def __init__(self, product):
        self.product = product
        self.artifacts = []
        self.authored = []
        self.mentions = []
        self.about_release = []
        self.releases = {}
        self._release_dates = {}
        # review sessions: a document is circulated in a planning channel and the
        # ensuing same-day conversation IS the review. `reviews` links each of
        # those slack utterances to the document under discussion.
        self.reviews = []
        self.doc_types = {}   # document id -> 'Market Research Report' etc
        self._slack_by_session = {}   # (channel, yyyymmdd) -> [slack ids]
        self._doc_shared_in = {}      # (channel, yyyymmdd) -> {doc_ids}

    # -- release inference -------------------------------------------------
    def note_release(self, slug, ts):
        if not slug:
            return
        self.releases.setdefault(
            slug,
            {"release_id": self.product + ":" + slug, "name": slug, "product": self.product},
        )
        if ts:
            self._release_dates.setdefault(slug, []).append(ts)

    def link_release(self, artifact_id, slug):
        if slug:
            self.about_release.append(
                {"artifact_id": artifact_id, "release_id": self.product + ":" + slug}
            )

    def slug_from_doc(self, doc):
        did = doc.get("id") or ""
        dtype = doc.get("type") or ""
        type_slug = dtype.lower().replace(" ", "_")
        if type_slug and type_slug in did:
            return did.split(type_slug)[0].strip("_") or None
        return None

    def slug_from_text(self, text):
        """Match a known release token inside a channel name, id, or URL."""
        low = (text or "").lower()
        best = None
        for slug in self.releases:
            token = slug.split("_")[-1]
            if token and token in low:
                if best is None or len(slug) > len(best):
                    best = slug
        return best

    def add(self, aid, kind, ts, text, author=None, slug=None):
        if not aid:
            return
        self.artifacts.append(
            {
                "artifact_id": aid,
                "kind": kind,
                "ts": ts or "",
                "text": (text or "")[:4000],
                "product": self.product,
                "release_id": (self.product + ":" + slug) if slug else "",
            }
        )
        if author and author.startswith("eid_"):
            self.authored.append({"employee_id": author, "artifact_id": aid})
        for m in set(EID.findall(text or "")):
            self.mentions.append({"artifact_id": aid, "employee_id": m})
        self.link_release(aid, slug)

    def note_session(self, channel, slack_id, text):
        """Group slack utterances into (channel, day) review sessions.

        A document review in HERB looks like: someone posts the doc link into a
        planning channel, and the same day's replies are the review. Grouping by
        (channel, day) recovers that unit without guessing at thread semantics,
        and any doc id linked inside the session names what was reviewed.
        """
        if not slack_id:
            return
        day = slack_id.split("-")[0]
        key = (channel, day)
        self._slack_by_session.setdefault(key, []).append(slack_id)
        for doc_id in DOCLINK.findall(text or ""):
            self._doc_shared_in.setdefault(key, set()).add(doc_id)

    def close_sessions(self):
        """Emit REVIEWS edges: every utterance in a session -> the doc reviewed."""
        for key, docs in self._doc_shared_in.items():
            for slack_id in self._slack_by_session.get(key, []):
                for doc_id in docs:
                    self.reviews.append({"artifact_id": slack_id, "document_id": doc_id})

    def order_releases(self):
        """Order by earliest artifact date. This produces the PRECEDES chain."""
        ordered = sorted(
            self.releases.values(),
            key=lambda r: min(self._release_dates.get(r["name"], ["9999"])),
        )
        for i, rel in enumerate(ordered):
            rel["seq"] = i
        return ordered

    def precedes(self):
        ordered = self.order_releases()
        return [
            {"earlier": ordered[i]["release_id"], "later": ordered[i + 1]["release_id"]}
            for i in range(len(ordered) - 1)
        ]


def load_product(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    g = ProductGraph(Path(path).stem)

    # Pass 1: documents establish the release vocabulary and their dates.
    for doc in data.get("documents") or []:
        g.note_release(g.slug_from_doc(doc), doc.get("date"))

    for doc in data.get("documents") or []:
        if doc.get("id"):
            g.doc_types[doc["id"]] = doc.get("type") or ""
        g.add(
            doc.get("id"),
            "document",
            doc.get("date"),
            doc.get("content", ""),
            author=doc.get("author"),
            slug=g.slug_from_doc(doc),
        )

    # Pass 2: everything else attaches to a release by token match.
    for t in data.get("meeting_transcripts") or []:
        tid = t.get("id") or ""
        g.add(tid, "transcript", t.get("date"), t.get("transcript", ""),
              slug=g.slug_from_text(tid))
        for pid in t.get("participants") or []:
            if isinstance(pid, str) and pid.startswith("eid_"):
                g.authored.append({"employee_id": pid, "artifact_id": tid})

    for pr in data.get("prs") or []:
        pid = pr.get("id") or ""
        body = (pr.get("title") or "") + "\n" + (pr.get("summary") or "")
        user = (pr.get("user") or {}).get("login")
        g.add(pid, "pr", pr.get("created_at"), body, author=user,
              slug=g.slug_from_text(pid + " " + (pr.get("link") or "")))
        for rv in pr.get("reviews") or []:
            login = (rv.get("user") or {}).get("login")
            if login and login.startswith("eid_"):
                g.authored.append({"employee_id": login, "artifact_id": pid})

    for msg in data.get("slack") or []:
        m = (msg.get("Message") or {}).get("User") or {}
        aid = msg.get("id") or m.get("utterranceID") or ""
        chan = ((msg.get("Channel") or {}).get("name")) or ""
        text = m.get("text", "")
        g.add(aid, "slack", m.get("timestamp"), text,
              author=m.get("userId"), slug=g.slug_from_text(chan))
        g.note_session(chan, aid, text)
        for reply in msg.get("ThreadReplies") or []:
            if not isinstance(reply, dict):
                continue
            ru = reply.get("User") or reply
            rid = ru.get("utterranceID") or ""
            if rid:
                rtext = ru.get("text", "")
                g.add(rid, "slack", ru.get("timestamp"), rtext,
                      author=ru.get("userId"), slug=g.slug_from_text(chan))
                g.note_session(chan, rid, rtext)

    g.close_sessions()

    for u in data.get("urls") or []:
        uid = u.get("id") or ""
        g.add(uid, "url", None, u.get("description", ""),
              slug=g.slug_from_text(uid + " " + (u.get("link") or "")))

    return g


def load_questions(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return (data.get("answerable_questions") or [],
            data.get("unanswerable_questions") or [])


def load_employees(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data.values()) if isinstance(data, dict) else data
