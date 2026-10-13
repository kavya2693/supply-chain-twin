from __future__ import annotations

import numpy as np
import pytest

from supply_twin.harness import FILL_NON_INFERIORITY, Comparison, Evaluator, mean_ci
from supply_twin.sim import SCENARIOS, Strategy

BASE = SCENARIOS["base"]
SEEDS = [0, 1, 2, 3, 4]


def _cmp(cost: tuple[float, float, float], fill: tuple[float, float, float]) -> Comparison:
    return Comparison(Strategy(z=2.05), Strategy(), 20, 0.05, 1_000_000.0, cost, fill, (0, 0, 0), 0.01)


def test_mean_ci_matches_t_interval() -> None:
    m, lo, hi = mean_ci([1.0, 2.0, 3.0, 4.0])
    assert m == pytest.approx(2.5)
    assert hi - m == pytest.approx(3.182446 * np.std([1, 2, 3, 4], ddof=1) / 2, rel=1e-5)
    assert lo == pytest.approx(2 * m - hi)


@pytest.mark.parametrize(
    ("cost", "fill", "expected"),
    [
        ((-20_000, -25_000, -15_000), (0, -0.0005, 0.0005), True),  # clear win, fill held
        ((-20_000, -40_000, 5_000), (0, -0.0005, 0.0005), False),  # interval crosses zero
        ((-6_000, -8_000, -4_000), (0, -0.0005, 0.0005), False),  # real but below the 0.5% floor
        ((-90_000, -95_000, -85_000), (-0.004, -0.005, -0.003), False),  # cheaper by emptying shelves
        (
            (-90_000, -95_000, -85_000),
            (-0.001, -FILL_NON_INFERIORITY + 1e-4, 0),
            True,
        ),  # fill loss inside margin
    ],
)
def test_acceptance_rule_on_known_cases(cost, fill, expected) -> None:
    assert _cmp(cost, fill).accepted is expected


def test_harness_never_accepts_a_strategy_against_itself(twin) -> None:
    ev = Evaluator(twin, workers=1)
    c = ev.compare(Strategy(), Strategy(), BASE, SEEDS)
    assert c.cost_diff == (0.0, 0.0, 0.0)
    assert not c.accepted


def test_harness_rejects_a_deliberately_broken_policy(twin) -> None:
    ev = Evaluator(twin, workers=1)
    broken = Strategy(z=0.5, cover_days=0.0, extra_trucks=-6)
    c = ev.compare(broken, Strategy(), BASE, SEEDS)
    assert not c.accepted
    assert c.fill_diff[0] < 0


def test_cache_avoids_rerunning(twin) -> None:
    ev = Evaluator(twin, workers=1)
    ev.runs_for(Strategy(), BASE, SEEDS)
    first = ev.runs
    ev.runs_for(Strategy(), BASE, SEEDS)
    assert ev.runs == first == len(SEEDS)
