"""Arbiter HTTP API.

Serves the resolver over HTTP and, deliberately, returns the Cypher it executed
alongside every answer. The traversal is the argument this project is making, so
hiding it behind a JSON blob would be the wrong product decision: a judge, or an
engineer, should be able to read the query that produced the answer and re-run it
themselves.

    uvicorn arbiter.api:app --port 8000
"""
from __future__ import annotations

import glob
import json
import random
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from arbiter.build import build_all
from arbiter.herb import load_questions
from arbiter.hydra import Hydra
from arbiter.query import Resolver

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

app = FastAPI(title="Arbiter", description="Graph-native enterprise context resolution on HydraDB")

_state: dict = {}


def state():
    if "resolver" not in _state:
        h = Hydra()
        b = build_all()
        _state["hydra"] = h
        _state["build"] = b
        _state["resolver"] = Resolver(h, b)
    return _state


class Ask(BaseModel):
    question: str
    product: str


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
def health():
    h = state()["hydra"]
    return {"hydradb_host": h.host, "ready": h.ready()}


@app.get("/api/stats")
def stats():
    b = state()["build"]
    s = b.summary()
    s["hydradb"] = {"host": state()["hydra"].host, "ready": state()["hydra"].ready()}
    return s


@app.get("/api/products")
def products():
    b = state()["build"]
    return sorted(b.products)


@app.get("/api/questions")
def questions(product: str, limit: int = 12):
    """Real HERB questions, so the demo is never driven by invented input."""
    path = ROOT / f"data/herb/products/{product}.json"
    if not path.exists():
        return JSONResponse({"error": f"unknown product {product}"}, status_code=404)
    ans, unans = load_questions(path)
    keep = [q for q in ans if q.get("type") == "person"]
    out = [{"question": q["question"], "answerable": True,
            "ground_truth": q["ground_truth"]} for q in keep[:limit]]
    out += [{"question": u, "answerable": False, "ground_truth": []}
            for u in unans[:4]]
    return out


@app.post("/api/ask")
def ask(body: Ask):
    r = state()["resolver"]
    b = state()["build"]

    ok, reason = r.can_answer(body.question, body.product)
    if not ok:
        return {
            "answered": False,
            "abstained": True,
            "reason": reason,
            "answer": [],
            "queries": [],
            "evidence": [],
        }

    family, (answer, trace) = r.route(body.question, body.product)
    if family == "unsupported":
        return {
            "answered": False, "abstained": True,
            "reason": "no traversal is registered for this question shape",
            "family": family, "answer": [], "queries": [], "evidence": [],
        }

    named = []
    for eid in answer:
        e = b.employees.get(eid, {})
        named.append({"employee_id": eid, "name": e.get("name"), "role": e.get("role")})
    return {
        "answered": True,
        "abstained": False,
        "family": family,
        "answer": named,
        "role_matched": trace.get("role_matched") or trace.get("doc_type"),
        "queries": trace.get("queries", []),
        "evidence": trace.get("evidence", []),
    }
