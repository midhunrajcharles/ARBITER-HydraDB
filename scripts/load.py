"""Load the HERB-derived graph into HydraDB.

Usage:
    python scripts/load.py              # full corpus (30 products)
    python scripts/load.py SearchForce  # one product, for a fast iteration loop
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arbiter.build import build_all, load_into_hydra
from arbiter.hydra import Hydra


def main():
    products = sys.argv[1:] or None

    print("building graph from HERB ...", flush=True)
    t0 = time.time()
    b = build_all(products=products)
    summary = b.summary()
    print(json.dumps(summary, indent=2), flush=True)
    print(f"built in {time.time()-t0:.1f}s\n", flush=True)

    h = Hydra()
    if not h.ready():
        sys.exit("HydraDB not reachable - run: bash scripts/hydra_up.sh")
    print(f"loading into HydraDB at {h.host} ...", flush=True)

    stats = load_into_hydra(b, h, verbose=True)
    print("\n" + json.dumps(stats, indent=2), flush=True)

    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results/load.json").write_text(
        json.dumps({"summary": summary, "load": stats}, indent=2), encoding="utf-8")

    # persist the id map so query time can translate vertex ids back to keys
    idmap = {
        "forward": {f"{k[0]}|{k[1]}": v for k, v in b.ids._fwd.items()},
    }
    (ROOT / "results/idmap.json").write_text(json.dumps(idmap), encoding="utf-8")
    print("\nwrote results/load.json and results/idmap.json", flush=True)


if __name__ == "__main__":
    main()
