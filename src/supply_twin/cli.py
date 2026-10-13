"""Command line: build the twin, check it, evaluate strategies, run the strategy agent.

`supply-twin all` is the one-command run; it writes everything the README quotes to outputs/.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

from supply_twin.agent import NeighbourProposer, Proposer, SearchResult, search
from supply_twin.fidelity import backtest
from supply_twin.harness import Evaluator, summarise
from supply_twin.llm import LLMProposer, ollama_complete
from supply_twin.sim import SCENARIOS, Scenario, Strategy
from supply_twin.twin import Sources, TwinState, build_twin, load_sources

NAMED = {
    "current policy": Strategy(),
    "lean stock (z=0.84, cover 0d)": Strategy(z=0.84, cover_days=0.0),
    "high service (z=2.05, cover 2d)": Strategy(z=2.05, cover_days=2.0),
    "order every 2 days": Strategy(review_days=2),
    "promo-aware ordering": Strategy(promo_aware=True),
    "fleet -4 trucks": Strategy(extra_trucks=-4),
    "fleet +4 trucks": Strategy(extra_trucks=4),
}


def _seeds(start: int, n: int) -> list[int]:
    return list(range(start, start + n))


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


def cmd_quality(src: Sources, out: Path) -> TwinState:
    twin = build_twin(src)
    q: dict[str, object] = dict(twin.quality)
    q["holiday_factor"] = round(twin.holiday_factor, 3)
    q["holiday_eve_factor"] = round(twin.eve_factor, 3)
    q["dispersion_k"] = round(twin.dispersion, 3)
    q["promo_uplift_by_category"] = {k: round(v, 3) for k, v in twin.promo_uplift.items()}
    _write_json(out / "data_quality.json", q)
    print("twin built:", json.dumps({k: v for k, v in q.items() if not isinstance(v, dict)}))
    return twin


def cmd_fidelity(src: Sources, out: Path) -> float:
    fid = backtest(src)
    _write_json(out / "fidelity.json", asdict(fid))
    print(
        "fidelity (store x SKU x week WAPE): "
        + ", ".join(f"{k} {v:.1%}" for k, v in fid.wape_store_sku_week.items())
        + f"; forecast-error sigma {fid.rate_error_sigma:.3f}"
    )
    return fid.rate_error_sigma


def cmd_evaluate(ev: Evaluator, sigma: float, seeds: list[int], out: Path) -> pd.DataFrame:
    rows = []
    for sc_name, sc in SCENARIOS.items():
        scenario = replace(sc, rate_error_sigma=sigma)
        ev.prefetch(NAMED.values(), scenario, seeds)
        base = ev.runs_for(NAMED["current policy"], scenario, seeds)
        for label, st in NAMED.items():
            runs = ev.runs_for(st, scenario, seeds)
            summ = summarise(runs)
            cmp = ev.compare(st, NAMED["current policy"], scenario, seeds)
            row: dict[str, object] = {"scenario": sc_name, "strategy": label, "n_seeds": len(seeds)}
            for m, (mean, lo, hi) in summ.items():
                row[m] = mean
                row[f"{m}_ci_half"] = (hi - lo) / 2
            row["cost_vs_current"] = cmp.cost_diff[0]
            row["cost_vs_current_ci_half"] = (cmp.cost_diff[2] - cmp.cost_diff[1]) / 2
            row["fill_vs_current_pts"] = 100 * cmp.fill_diff[0]
            row["beats_current"] = cmp.accepted and st != NAMED["current policy"]
            rows.append(row)
        print(
            f"evaluated {len(NAMED)} strategies on '{sc_name}' x {len(seeds)} seeds "
            f"(current policy fill {base['fill_rate'].mean():.4f})"
        )
    table = pd.DataFrame(rows)
    table.to_csv(out / "strategy_table.csv", index=False)
    return table


def _search_rows(res: SearchResult) -> list[dict[str, object]]:
    rows = []
    for i, r in enumerate(res.rounds, start=1):
        for c in r.comparisons:
            row = {"round": i, "incumbent": r.incumbent.key(), **c.row()}
            row["chosen"] = r.accepted == c.candidate
            rows.append(row)
    return rows


def _search_summary(res: SearchResult, search_seeds: int, ev_runs: int) -> dict[str, object]:
    first_round_gain = 0.0
    search_gain = 0.0
    for r in res.rounds:
        chosen = [c for c in r.comparisons if c.candidate == r.accepted]
        if chosen:
            search_gain += chosen[0].cost_diff[0]
    if res.rounds and res.rounds[0].accepted is not None:
        first_round_gain = next(
            c.cost_diff[0] for c in res.rounds[0].comparisons if c.candidate == res.rounds[0].accepted
        )
    h = res.holdout
    return {
        "proposer": res.proposer,
        "correction": res.correction,
        "search_seeds": search_seeds,
        "rounds": len(res.rounds),
        "candidates_tested": res.candidates_tested,
        "steps_accepted": sum(r.accepted is not None for r in res.rounds),
        "start": res.start.key(),
        "final": res.final.key(),
        "search_cost_gain_sum_of_steps": search_gain,
        "first_step_gain": first_round_gain,
        "holdout": None if h is None else h.row() | {"n_seeds": h.n_seeds},
        "simulation_runs_total": ev_runs,
    }


def cmd_search(
    ev: Evaluator,
    scenario: Scenario,
    proposer: Proposer,
    search_seeds: list[int],
    holdout_seeds: list[int],
    bonferroni: bool,
    out: Path,
    tag: str,
) -> SearchResult:
    t0 = time.time()
    res = search(ev, scenario, proposer, search_seeds, holdout_seeds, bonferroni=bonferroni)
    pd.DataFrame(_search_rows(res)).to_csv(out / f"search_{tag}.csv", index=False)
    summary = _search_summary(res, len(search_seeds), ev.runs)
    summary["wall_seconds"] = round(time.time() - t0, 1)
    _write_json(out / f"search_{tag}.json", summary)
    h = res.holdout
    hold = (
        "no change accepted"
        if h is None
        else f"hold-out cost {h.cost_diff[0]:+,.0f} fill {100 * h.fill_diff[0]:+.2f} pts"
    )
    print(f"search [{tag}] {res.start.key()} -> {res.final.key()} | {hold}")
    return res


def cmd_robustness(
    ev: Evaluator, res: SearchResult, tuned_on: str, sigma: float, seeds: list[int], out: Path, tag: str
) -> None:
    """Re-test the searched plan on every scenario, including the ones it was not tuned on."""
    rows = []
    for name, sc in SCENARIOS.items():
        scenario = replace(sc, rate_error_sigma=sigma)
        c = ev.compare(res.final, res.start, scenario, seeds)
        runs = ev.runs_for(res.final, scenario, seeds)
        rows.append(
            {
                "scenario": name,
                "tuned_on": name == tuned_on,
                **c.row(),
                "final_fill_rate": runs["fill_rate"].mean(),
                "final_promo_fill_rate": runs["promo_fill_rate"].mean(),
                "final_truck_utilisation": runs["truck_utilisation"].mean(),
                "final_late_loads": runs["late_loads"].mean(),
            }
        )
        print(
            f"robustness [{name}] cost {c.cost_diff[0]:+,.0f} fill {100 * c.fill_diff[0]:+.2f} pts "
            f"-> {'holds' if c.accepted else 'does not hold'}"
        )
    pd.DataFrame(rows).to_csv(out / f"search_{tag}_robustness.csv", index=False)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="supply-twin", description=__doc__)
    ap.add_argument("command", choices=["quality", "fidelity", "evaluate", "search", "all"])
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--out", type=Path, default=Path("outputs"))
    ap.add_argument("--seeds", type=int, default=30, help="seeds per strategy in the evaluation table")
    ap.add_argument("--search-seeds", type=int, default=20)
    ap.add_argument("--holdout-seeds", type=int, default=30)
    ap.add_argument("--scenario", default="promo", choices=sorted(SCENARIOS))
    ap.add_argument("--proposer", default="neighbour", choices=["neighbour", "llm"])
    ap.add_argument("--model", default="llama3.2")
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    src = load_sources(args.data)
    twin = cmd_quality(src, args.out)
    if args.command == "quality":
        return
    sigma = cmd_fidelity(src, args.out)
    if args.command == "fidelity":
        return
    ev = Evaluator(twin, workers=args.workers)
    eval_seeds = _seeds(1000, args.seeds)
    search_seeds = _seeds(0, args.search_seeds)
    holdout_seeds = _seeds(5000, args.holdout_seeds)
    scenario = replace(SCENARIOS[args.scenario], rate_error_sigma=sigma)

    if args.command in ("evaluate", "all"):
        cmd_evaluate(ev, sigma, eval_seeds, args.out)
    if args.command in ("search", "all"):
        proposer: Proposer
        proposer = LLMProposer(ollama_complete(args.model)) if args.proposer == "llm" else NeighbourProposer()
        tag = f"{args.proposer}_{args.scenario}"
        res = cmd_search(ev, scenario, proposer, search_seeds, holdout_seeds, True, args.out, tag)
        if res.final != res.start:
            # The check most likely to embarrass the result: the plan was tuned on one
            # scenario, so test it on the others before anyone rolls it out.
            cmd_robustness(ev, res, args.scenario, sigma, holdout_seeds, args.out, tag)
        if isinstance(proposer, LLMProposer):
            _write_json(args.out / f"search_{tag}_replies.json", proposer.replies)
            print(f"llm proposer fell back to neighbours {proposer.fallbacks} time(s)")


if __name__ == "__main__":
    main()
