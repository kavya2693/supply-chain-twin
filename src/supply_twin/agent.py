"""Strategy agent: propose a parameter change, let the harness decide, keep only real wins.

The proposer and the judge are separate on purpose. A proposer (a deterministic neighbour
search, or optionally a language model) can suggest anything inside the parameter grid;
only the harness, on paired seeds, can move the incumbent. Because every round tests
several candidates, the significance level is split across them (Bonferroni); otherwise
a search that looks at enough candidates will eventually accept noise.

The final incumbent is then re-tested against the original plan on hold-out seeds the
search never saw, which is the number that gets reported.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from supply_twin.harness import Comparison, Evaluator
from supply_twin.sim import Scenario, Strategy

GRID: dict[str, list[float | int | bool]] = {
    "z": [0.5, 0.84, 1.28, 1.65, 2.05],
    "review_days": [1, 2, 3],
    "cover_days": [0.0, 0.5, 1.0, 2.0, 3.0],
    "extra_trucks": [-6, -4, -2, 0, 2, 4],
    "promo_aware": [False, True],
}


def snap(strategy: Strategy) -> Strategy:
    """Force every parameter onto the grid, so a proposer cannot invent a policy nobody can run."""
    out: dict[str, float | int | bool] = {}
    for name, values in GRID.items():
        v = getattr(strategy, name)
        if isinstance(values[0], bool):
            out[name] = bool(v)
        else:
            out[name] = min(values, key=lambda g: abs(float(g) - float(v)))
    return Strategy(**out)  # type: ignore[arg-type]


class Proposer(Protocol):
    name: str

    def propose(self, incumbent: Strategy, history: Sequence[Comparison]) -> list[Strategy]: ...


class NeighbourProposer:
    """One grid step up or down on each lever, plus flipping the promotion flag."""

    name = "neighbour"

    def propose(self, incumbent: Strategy, history: Sequence[Comparison]) -> list[Strategy]:
        tried = {c.candidate for c in history if c.baseline == incumbent}
        out: list[Strategy] = []
        for name, values in GRID.items():
            current = getattr(incumbent, name)
            i = values.index(current)
            for j in (i - 1, i + 1):
                if 0 <= j < len(values) and values[j] != current:
                    out.append(replace(incumbent, **{name: values[j]}))  # type: ignore[arg-type]
        return [s for s in dict.fromkeys(out) if s not in tried and s != incumbent]


@dataclass
class Round:
    incumbent: Strategy
    comparisons: list[Comparison]
    accepted: Strategy | None


@dataclass
class SearchResult:
    start: Strategy
    final: Strategy
    rounds: list[Round] = field(default_factory=list)
    holdout: Comparison | None = None
    proposer: str = ""
    correction: str = ""

    @property
    def candidates_tested(self) -> int:
        return sum(len(r.comparisons) for r in self.rounds)


def search(
    evaluator: Evaluator,
    scenario: Scenario,
    proposer: Proposer,
    search_seeds: Sequence[int],
    holdout_seeds: Sequence[int],
    start: Strategy | None = None,
    alpha: float = 0.05,
    bonferroni: bool = True,
    max_rounds: int = 8,
) -> SearchResult:
    start = start or Strategy()
    incumbent = start
    history: list[Comparison] = []
    result = SearchResult(
        start=start, final=start, proposer=proposer.name, correction="bonferroni" if bonferroni else "none"
    )
    for _ in range(max_rounds):
        candidates = [snap(c) for c in proposer.propose(incumbent, history)]
        candidates = [c for c in dict.fromkeys(candidates) if c != incumbent]
        if not candidates:
            break
        level = alpha / len(candidates) if bonferroni else alpha
        evaluator.prefetch([incumbent, *candidates], scenario, search_seeds)
        comps = [evaluator.compare(c, incumbent, scenario, search_seeds, alpha=level) for c in candidates]
        history.extend(comps)
        winners = [c for c in comps if c.accepted]
        best = min(winners, key=lambda c: c.cost_diff[0]).candidate if winners else None
        result.rounds.append(Round(incumbent=incumbent, comparisons=comps, accepted=best))
        if best is None:
            break
        incumbent = best
    result.final = incumbent
    if incumbent != start:
        result.holdout = evaluator.compare(incumbent, start, scenario, holdout_seeds, alpha=alpha)
    return result
