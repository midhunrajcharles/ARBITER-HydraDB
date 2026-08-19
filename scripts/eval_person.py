"""Corpus-wide evaluation: graph traversal vs BM25 retrieval on HERB person questions.

Run:  python scripts/eval_person.py
"""
from __future__ import annotations

import glob
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arbiter.herb import load_employees, load_product, load_questions
from arbiter.resolve import (
    BM25,
    extract_role,
    prf,
    resolve_person,
    resolve_person_baseline,
)

PATTERN = "worked on the previous release"


def main():
    emps = {e["employee_id"]: e for e in load_employees("data/herb/metadata/employee.json")}
    files = sorted(glob.glob("data/herb/products/*.json"))

    g_exact = b_exact = n = 0
    g_f1 = b_f1 = 0.0
    per_product = []
    t0 = time.time()

    for f in files:
        g = load_product(f)
        ans, _ = load_questions(f)
        qs = [q for q in ans if q.get("type") == "person" and PATTERN in q["question"]]
        if not qs:
            continue
        bm = BM25(g.artifacts)
        pg = pb = 0
        for q in qs:
            gt = q["ground_truth"]
            pred, _ = resolve_person(g, emps, q["question"])
            base = resolve_person_baseline(g, emps, q["question"], bm)
            _, _, f1g, eg = prf(pred, gt)
            _, _, f1b, eb = prf(base, gt)
            g_exact += eg; b_exact += eb
            g_f1 += f1g; b_f1 += f1b
            pg += eg; pb += eb
            n += 1
        per_product.append((Path(f).stem, len(qs), pg, pb))

    print(f"\nEvaluated {n} person questions across {len(per_product)} products "
          f"in {time.time()-t0:.1f}s\n")
    print(f"{'product':<24}{'n':>4}{'graph':>8}{'bm25':>7}")
    print("-" * 43)
    for name, cnt, pg, pb in per_product:
        print(f"{name:<24}{cnt:>4}{pg:>8}{pb:>7}")
    print("-" * 43)
    print(f"{'TOTAL':<24}{n:>4}{g_exact:>8}{b_exact:>7}")
    print()
    print(f"  graph traversal : {g_exact}/{n} exact ({100*g_exact/n:.1f}%)  mean F1 {g_f1/n:.3f}")
    print(f"  BM25 baseline   : {b_exact}/{n} exact ({100*b_exact/n:.1f}%)  mean F1 {b_f1/n:.3f}")
    print()
    print("  HERB paper reference: best agentic RAG ~30% accuracy; retrieval is the bottleneck.")

    Path("results").mkdir(exist_ok=True)
    Path("results/person.json").write_text(json.dumps({
        "n": n,
        "graph_exact": g_exact, "graph_mean_f1": g_f1 / n,
        "bm25_exact": b_exact, "bm25_mean_f1": b_f1 / n,
        "per_product": [{"product": p, "n": c, "graph": a, "bm25": b}
                        for p, c, a, b in per_product],
    }, indent=2), encoding="utf-8")
    print("\n  wrote results/person.json")


if __name__ == "__main__":
    main()
