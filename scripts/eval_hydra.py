"""End-to-end evaluation against a live HydraDB node.

Every Arbiter answer here is produced by OpenCypher traversal executed inside
HydraDB. The BM25 column is a local retrieval baseline over the same corpus,
included so the comparison is measured rather than asserted.

Two question families are covered, and both are reported separately because they
behave differently:

  person_previous_release  "Find employee IDs of {ROLE} who worked on the
                            previous release of {PRODUCT}?"
  doc_reviewers            "Find employee IDs of the authors and key reviewers
                            of the {DOC_TYPE} for the {PRODUCT} product?"

Abstention is scored on HERB's unanswerable set, which most systems ignore.

Usage:
    python scripts/eval_hydra.py
"""
from __future__ import annotations

import glob
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arbiter.build import build_all
from arbiter.herb import load_employees, load_product, load_questions
from arbiter.hydra import Hydra
from arbiter.query import Resolver
from arbiter.resolve import BM25, prf, resolve_person_baseline

FAMILIES = {
    "person_previous_release": "worked on the previous release",
    "doc_reviewers": "authors and key reviewers",
}


def main():
    h = Hydra()
    if not h.ready():
        sys.exit("HydraDB not reachable - run: bash scripts/hydra_up.sh")

    print("building id map ...", flush=True)
    b = build_all()
    r = Resolver(h, b)
    emps = {e["employee_id"]: e for e in load_employees("data/herb/metadata/employee.json")}

    agg = defaultdict(lambda: {"n": 0, "a_exact": 0, "b_exact": 0,
                               "a_f1": 0.0, "b_f1": 0.0,
                               "a_p": 0.0, "a_r": 0.0})
    detail = []
    t0 = time.time()

    for f in sorted(glob.glob("data/herb/products/*.json")):
        product = Path(f).stem
        ans, unans = load_questions(f)
        wanted = [q for q in ans
                  if any(k in q["question"].lower() for k in FAMILIES.values())]
        if not wanted:
            continue
        pg = load_product(f)
        bm = BM25(pg.artifacts)

        for q in wanted:
            fam, (pred, trace) = r.route(q["question"], product)
            if fam == "unsupported":
                continue
            gt = q["ground_truth"]
            base = resolve_person_baseline(pg, emps, q["question"], bm)
            p, rc, f1, ex = prf(pred, gt)
            _, _, bf1, bex = prf(base, gt)
            a = agg[fam]
            a["n"] += 1; a["a_exact"] += ex; a["b_exact"] += bex
            a["a_f1"] += f1; a["b_f1"] += bf1; a["a_p"] += p; a["a_r"] += rc
            detail.append({"family": fam, "product": product,
                           "question": q["question"], "gt": gt, "pred": pred,
                           "bm25": base, "exact": ex, "f1": round(f1, 3),
                           "precision": round(p, 3), "recall": round(rc, 3),
                           "bm25_f1": round(bf1, 3),
                           "n_queries": len(trace.get("queries") or [])})

        # abstention on the unanswerable set
        for u in unans:
            ok, reason = r.can_answer(u, product)
            a = agg["abstention"]
            a["n"] += 1
            a["a_exact"] += (0 if ok else 1)   # correct = refused to answer

    elapsed = time.time() - t0

    print(f"\n{'family':<26}{'n':>5}{'exact':>8}{'F1':>8}{'prec':>7}{'rec':>7}"
          f"{'bm25 exact':>12}{'bm25 F1':>9}")
    print("-" * 82)
    for fam in ("person_previous_release", "doc_reviewers"):
        a = agg.get(fam)
        if not a or not a["n"]:
            continue
        n = a["n"]
        print(f"{fam:<26}{n:>5}{a['a_exact']:>5}/{n:<2}{a['a_f1']/n:>8.3f}"
              f"{a['a_p']/n:>7.3f}{a['a_r']/n:>7.3f}"
              f"{a['b_exact']:>9}/{n:<2}{a['b_f1']/n:>9.3f}")
    ab = agg.get("abstention")
    if ab and ab["n"]:
        print("-" * 82)
        print(f"{'abstention (unanswerable)':<26}{ab['n']:>5}"
              f"{ab['a_exact']:>5}/{ab['n']:<2}"
              f"   correctly refused: {100*ab['a_exact']/ab['n']:.1f}%")

    print(f"\nelapsed {elapsed:.1f}s")
    print("\nHERB reference: best agentic RAG ~30% accuracy; retrieval is the bottleneck.")

    (ROOT / "results").mkdir(exist_ok=True)
    out = {fam: {k: (round(v / a["n"], 3) if isinstance(v, float) else v)
                 for k, v in a.items()} for fam, a in agg.items()}
    (ROOT / "results/eval_hydra.json").write_text(
        json.dumps({"summary": out, "elapsed_s": round(elapsed, 1),
                    "detail": detail}, indent=2), encoding="utf-8")
    print("wrote results/eval_hydra.json")


if __name__ == "__main__":
    main()
