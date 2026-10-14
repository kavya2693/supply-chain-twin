"""Export the strategy search (slide 06) from the repo's own outputs, no re-run needed.

Reads outputs/search_neighbour_promo.csv (one row per candidate tested) and
outputs/search_neighbour_promo.json (hold-out confirmation) written by `make run`.

Usage (from the repo root):
    python3 docs/demo/source/tools/export_search_walk.py docs/demo/source/assets/search_walk.json
"""

import json
import sys
from pathlib import Path

import pandas as pd

TAG = "neighbour_promo"


def lever(incumbent: str, candidate: str) -> str:
    """The one parameter that differs between incumbent and candidate, as 'name=value'."""
    a, b = incumbent.split(), candidate.split()
    diff = [y for x, y in zip(a, b, strict=True) if x != y]
    return diff[0] if diff else ""


def main(out: Path) -> None:
    rows = pd.read_csv(f"outputs/search_{TAG}.csv")
    summary = json.loads(Path(f"outputs/search_{TAG}.json").read_text())
    cands = [
        {
            "round": int(r["round"]),
            "lever": lever(r["incumbent"], r["candidate"]),
            "diff": float(r["cost_diff"]),
            "low": float(r["cost_diff_low"]),
            "high": float(r["cost_diff_high"]),
            "base": float(r["baseline_cost"]),
            "accepted": bool(r["accepted"]),
            "chosen": bool(r["chosen"]),
        }
        for _, r in rows.iterrows()
    ]
    steps = []
    for rnd, g in rows.groupby("round"):
        chosen = g[g["chosen"]]
        step = lever(chosen["incumbent"].iloc[0], chosen["candidate"].iloc[0]) if len(chosen) else None
        steps.append(
            {
                "round": int(rnd),
                "incumbent_cost": float(g["baseline_cost"].iloc[0]),
                "chosen": step,
                "tested": len(g),
                "accepted": int(g["accepted"].sum()),
            }
        )
    h = summary["holdout"]
    payload = {
        "search_seeds": summary["search_seeds"],
        "rounds": summary["rounds"],
        "candidates_tested": summary["candidates_tested"],
        "start": summary["start"],
        "final": summary["final"],
        "holdout": {
            "n_seeds": h["n_seeds"],
            "baseline_cost": h["baseline_cost"],
            "cost_diff": h["cost_diff"],
            "fill_diff_pts": round(h["fill_diff_pts"], 3),
            "util_diff_pts": round(h["util_diff_pts"], 2),
        },
        "steps": steps,
        "candidates": cands,
    }
    out.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"wrote {out}: {len(cands)} candidates over {len(steps)} rounds")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
