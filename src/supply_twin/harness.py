"""Evaluation harness: run strategies over many seeds and decide whether a change is real.

Two choices carry the weight here:

- Common random numbers. A candidate and its baseline run on the same seeds, so they face
  the same customers, and the comparison is a paired difference per seed. That removes most
  of the demand noise from the comparison and lets 20 seeds detect effects that 200 unpaired
  runs would miss.
- A two-sided acceptance rule. A candidate must cut total cost (operating cost plus lost
  margin) with the whole confidence interval below zero, and must not lose more than a
  fixed margin of fill rate. A cheaper plan that quietly empties shelves is rejected even
  if lost margin happens to be priced low.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from supply_twin.sim import Scenario, Strategy, simulate
from supply_twin.twin import TwinState

FILL_NON_INFERIORITY = 0.002  # accept at most 0.2 points of fill rate loss

_WORKER_TWIN: TwinState | None = None


def _init_worker(twin: TwinState) -> None:
    global _WORKER_TWIN
    _WORKER_TWIN = twin


def _run_one(args: tuple[Strategy, Scenario, int]) -> dict[str, float]:
    assert _WORKER_TWIN is not None
    strategy, scenario, seed = args
    return simulate(_WORKER_TWIN, strategy, scenario, seed).metrics()


def mean_ci(values: Sequence[float] | np.ndarray, level: float = 0.95) -> tuple[float, float, float]:
    """Mean and t-interval. Returns (mean, low, high)."""
    x = np.asarray(values, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 2:
        m = float(x.mean()) if len(x) else float("nan")
        return m, m, m
    m = float(x.mean())
    half = float(stats.t.ppf(0.5 + level / 2, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x)))
    return m, m - half, m + half


@dataclass
class Comparison:
    candidate: Strategy
    baseline: Strategy
    n_seeds: int
    alpha: float
    cost_diff: tuple[float, float, float]  # candidate minus baseline, (mean, low, high)
    fill_diff: tuple[float, float, float]
    util_diff: tuple[float, float, float]
    p_value: float

    @property
    def cost_better(self) -> bool:
        return self.cost_diff[2] < 0

    @property
    def fill_ok(self) -> bool:
        return self.fill_diff[1] > -FILL_NON_INFERIORITY

    @property
    def accepted(self) -> bool:
        return self.cost_better and self.fill_ok

    def row(self) -> dict[str, object]:
        return {
            "candidate": self.candidate.key(),
            "cost_diff": self.cost_diff[0],
            "cost_diff_low": self.cost_diff[1],
            "cost_diff_high": self.cost_diff[2],
            "fill_diff_pts": 100 * self.fill_diff[0],
            "fill_diff_low_pts": 100 * self.fill_diff[1],
            "util_diff_pts": 100 * self.util_diff[0],
            "p_value": self.p_value,
            "alpha": self.alpha,
            "accepted": self.accepted,
        }


class Evaluator:
    """Runs and caches simulations; the cache keys on (strategy, scenario, seed)."""

    def __init__(self, twin: TwinState, workers: int | None = None) -> None:
        self.twin = twin
        self.workers = workers if workers is not None else min(4, os.cpu_count() or 1)
        self._cache: dict[tuple[Strategy, Scenario, int], dict[str, float]] = {}
        self.runs = 0

    def _ensure(self, jobs: Iterable[tuple[Strategy, Scenario, int]]) -> None:
        todo = [j for j in dict.fromkeys(jobs) if j not in self._cache]
        if not todo:
            return
        self.runs += len(todo)
        if self.workers <= 1 or len(todo) < 8:
            for st, sc, seed in todo:
                self._cache[(st, sc, seed)] = simulate(self.twin, st, sc, seed).metrics()
            return
        with ProcessPoolExecutor(self.workers, initializer=_init_worker, initargs=(self.twin,)) as pool:
            for job, res in zip(todo, pool.map(_run_one, todo, chunksize=4), strict=True):
                self._cache[job] = res

    def runs_for(self, strategy: Strategy, scenario: Scenario, seeds: Sequence[int]) -> pd.DataFrame:
        self._ensure((strategy, scenario, s) for s in seeds)
        return pd.DataFrame([self._cache[(strategy, scenario, s)] for s in seeds], index=list(seeds))

    def prefetch(self, strategies: Iterable[Strategy], scenario: Scenario, seeds: Sequence[int]) -> None:
        self._ensure((st, scenario, s) for st in strategies for s in seeds)

    def compare(
        self,
        candidate: Strategy,
        baseline: Strategy,
        scenario: Scenario,
        seeds: Sequence[int],
        alpha: float = 0.05,
    ) -> Comparison:
        self.prefetch([candidate, baseline], scenario, seeds)
        c = self.runs_for(candidate, scenario, seeds)
        b = self.runs_for(baseline, scenario, seeds)
        d_cost = (c["total_cost"] - b["total_cost"]).to_numpy()
        level = 1 - alpha
        p = 1.0 if np.allclose(d_cost, 0) else float(stats.ttest_1samp(d_cost, 0.0).pvalue)
        return Comparison(
            candidate=candidate,
            baseline=baseline,
            n_seeds=len(seeds),
            alpha=alpha,
            cost_diff=mean_ci(d_cost, level),
            fill_diff=mean_ci((c["fill_rate"] - b["fill_rate"]).to_numpy(), level),
            util_diff=mean_ci((c["truck_utilisation"] - b["truck_utilisation"]).to_numpy(), level),
            p_value=p,
        )


SUMMARY_METRICS = [
    "fill_rate",
    "promo_fill_rate",
    "total_cost",
    "operating_cost",
    "lost_margin",
    "truck_utilisation",
    "load_fill",
]


def summarise(runs: pd.DataFrame) -> dict[str, tuple[float, float, float]]:
    return {m: mean_ci(runs[m].to_numpy()) for m in SUMMARY_METRICS}
