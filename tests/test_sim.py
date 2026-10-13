from __future__ import annotations

from dataclasses import replace

from supply_twin.sim import SCENARIOS, Strategy, simulate

BASE = SCENARIOS["base"]
PROMO = replace(SCENARIOS["promo"], promo_categories=("beverages", "snacks", "dairy", "pantry"))


def test_same_seed_same_result(twin) -> None:
    a = simulate(twin, Strategy(), PROMO, 3).metrics()
    b = simulate(twin, Strategy(), PROMO, 3).metrics()
    assert a == b


def test_strategies_face_identical_customers(twin) -> None:
    # Common random numbers: the harness's paired comparison depends on this.
    a = simulate(twin, Strategy(), BASE, 5)
    b = simulate(twin, Strategy(z=2.05, review_days=3, extra_trucks=-2), BASE, 5)
    assert a.demand_units == b.demand_units


def test_accounting_is_conserved(twin) -> None:
    r = simulate(twin, Strategy(), BASE, 1)
    assert 0 < r.sold_units <= r.demand_units
    assert 0 <= r.load_fill <= 1
    assert r.total_cost == r.operating_cost + r.lost_margin


def test_short_fleet_shows_up_as_late_loads_and_lost_sales(twin) -> None:
    full = simulate(twin, Strategy(), BASE, 2)
    short = simulate(twin, Strategy(extra_trucks=-6), BASE, 2)
    assert short.truck_utilisation > full.truck_utilisation
    assert short.late_loads >= full.late_loads
    assert short.fill_rate <= full.fill_rate


def test_promo_aware_ordering_protects_promo_fill(twin) -> None:
    blind = simulate(twin, Strategy(), PROMO, 4)
    aware = simulate(twin, Strategy(promo_aware=True), PROMO, 4)
    assert aware.promo_fill_rate > blind.promo_fill_rate


def test_less_frequent_orders_fill_trucks_but_overflow_backrooms(twin) -> None:
    daily = simulate(twin, Strategy(review_days=1), BASE, 6)
    every3 = simulate(twin, Strategy(review_days=3), BASE, 6)
    assert every3.load_fill > daily.load_fill
    assert every3.overflow_cost > daily.overflow_cost
