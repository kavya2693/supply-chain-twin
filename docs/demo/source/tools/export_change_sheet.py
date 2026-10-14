"""Export the planner's change scorecard (slide 07) by re-running the repo's own evaluation.

Same data, seeds and settings as `make run` (`supply-twin all`): the twin is built from data/,
the forecast-error spread comes from the fidelity backtest, and every named strategy is compared
with the current policy on the promotion quarter over the 30 evaluation seeds (1000-1029).
Also exports the store list (DC, distance, review stagger) for the 2D network scene.

Usage (from the repo root):
    PYTHONPATH=src .venv/bin/python docs/demo/source/tools/export_change_sheet.py \
        docs/demo/source/assets/change_sheet.json
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from supply_twin.cli import NAMED, _seeds
from supply_twin.fidelity import backtest
from supply_twin.harness import Evaluator, summarise
from supply_twin.sim import SCENARIOS
from supply_twin.twin import build_twin, load_sources

SCENARIO = "promo"
N_SEEDS = 30


def main(out: Path) -> None:
    t0 = time.time()
    src = load_sources(Path("data"))
    twin = build_twin(src)
    sigma = backtest(src).rate_error_sigma
    scenario = replace(SCENARIOS[SCENARIO], rate_error_sigma=sigma)
    seeds = _seeds(1000, N_SEEDS)
    ev = Evaluator(twin)
    ev.prefetch(NAMED.values(), scenario, seeds)
    current = NAMED["current policy"]
    rows = []
    for label, st in NAMED.items():
        summ = summarise(ev.runs_for(st, scenario, seeds))
        cmp = ev.compare(st, current, scenario, seeds)
        rows.append(
            {
                "strategy": label,
                "key": st.key(),
                "total_cost": summ["total_cost"][0],
                "cost_vs_current": cmp.cost_diff[0],
                "cost_vs_current_ci_half": (cmp.cost_diff[2] - cmp.cost_diff[1]) / 2,
                "fill_rate": summ["fill_rate"][0],
                "promo_fill_rate": summ["promo_fill_rate"][0],
                "truck_utilisation": summ["truck_utilisation"][0],
                "load_fill": summ["load_fill"][0],
                "beats_current": bool(cmp.accepted and st != current),
            }
        )
    stores = [
        {"store": s, "dc": str(dc), "km": float(km), "index": i}
        for i, (s, dc, km) in enumerate(zip(twin.store_ids, twin.store_dc, twin.distance_km, strict=True))
    ]
    payload = {
        "scenario": SCENARIO,
        "n_seeds": N_SEEDS,
        "seeds": [seeds[0], seeds[-1]],
        "rate_error_sigma": sigma,
        "horizon_days": scenario.horizon_days,
        "rows": rows,
        "stores": stores,
        "trucks_per_dc": {str(k): int(v) for k, v in twin.fleet.groupby("home_dc").size().items()},
        "runtime_seconds": round(time.time() - t0, 1),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"wrote {out}: {len(rows)} strategies, {len(stores)} stores in {payload['runtime_seconds']} s")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
