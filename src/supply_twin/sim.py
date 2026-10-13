"""Discrete-event simulation of one planning horizon on the twin (SimPy).

Each day, in simulated hours:

  05:00-19:00  trucks leave the DC with yesterday's store orders, one load at a time
  21:00        each store sells against what is on the shelf (unmet demand is lost)
  21:30        stores due for review place an order (order-up-to with safety stock)

A truck is a token in a per-DC SimPy store. A load waits for a free truck, and a truck only
departs inside the shift window, so a short fleet shows up the way it does in a real
network: deliveries slip past the evening trade and the shelf runs empty the next day.
All SKUs of a store are handled as one vector, so a run of 36 stores x 120 SKUs x 91 days
takes a fraction of a second.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import asdict, dataclass

import numpy as np
import simpy

from supply_twin.twin import TwinState

SHIFT_START, SHIFT_END = 5.0, 19.0
SALES_HOUR, REVIEW_HOUR = 21.0, 21.5
SPEED_KMH = 50.0
LOAD_HOURS, UNLOAD_HOURS = 0.75, 1.0
DRIVER_COST_PER_HOUR = 28.0
HOLDING_RATE_PER_YEAR = 0.25


@dataclass(frozen=True)
class Strategy:
    """The levers a planner can pull. Defaults are the current operating policy."""

    z: float = 1.28  # safety factor on lead-time demand
    review_days: int = 1  # how often each store places an order
    cover_days: float = 1.0  # stock held above the reorder point, in days of demand
    extra_trucks: int = 0  # trucks added (or removed if negative) across the network
    promo_aware: bool = False  # does the store order with the promotion calendar in hand

    def key(self) -> str:
        return (
            f"z={self.z:g} review={self.review_days}d cover={self.cover_days:g}d "
            f"trucks{self.extra_trucks:+d} promo_aware={'yes' if self.promo_aware else 'no'}"
        )

    def as_dict(self) -> dict[str, float | int | bool]:
        return asdict(self)


@dataclass(frozen=True)
class Scenario:
    name: str = "base"
    horizon_days: int = 91
    promo_categories: tuple[str, ...] = ()
    promo_start_day: int = 35
    promo_days: int = 14
    trucks_down_share: float = 0.0  # share of the fleet out of service for the whole horizon
    # Store-SKU demand differs from the twin's estimate by a lognormal factor with this
    # spread. The CLI sets it from the hold-out backtest, so the simulated customers are
    # exactly as unpredictable as the real history showed the estimates to be.
    rate_error_sigma: float = 0.15


SCENARIOS = {
    "base": Scenario("base"),
    "promo": Scenario("promo", promo_categories=("beverages", "snacks")),
    "fleet_outage": Scenario("fleet_outage", trucks_down_share=0.2),
}


@dataclass
class RunResult:
    demand_units: float
    sold_units: float
    promo_demand_units: float
    promo_sold_units: float
    holding_cost: float
    transport_cost: float
    fleet_fixed_cost: float
    lost_margin: float
    truck_hours: float
    truck_hours_available: float
    loads: int
    cases_shipped: float
    load_capacity: float
    late_loads: int

    @property
    def fill_rate(self) -> float:
        return self.sold_units / self.demand_units

    @property
    def promo_fill_rate(self) -> float:
        return self.promo_sold_units / self.promo_demand_units if self.promo_demand_units else float("nan")

    @property
    def operating_cost(self) -> float:
        return self.holding_cost + self.transport_cost + self.fleet_fixed_cost

    @property
    def total_cost(self) -> float:
        """Operating cost plus the margin lost on unmet demand: the number strategies compete on."""
        return self.operating_cost + self.lost_margin

    @property
    def truck_utilisation(self) -> float:
        return self.truck_hours / self.truck_hours_available

    @property
    def load_fill(self) -> float:
        return self.cases_shipped / self.load_capacity if self.load_capacity else float("nan")

    def metrics(self) -> dict[str, float]:
        return {
            "fill_rate": self.fill_rate,
            "promo_fill_rate": self.promo_fill_rate,
            "total_cost": self.total_cost,
            "operating_cost": self.operating_cost,
            "holding_cost": self.holding_cost,
            "transport_cost": self.transport_cost,
            "fleet_fixed_cost": self.fleet_fixed_cost,
            "lost_margin": self.lost_margin,
            "truck_utilisation": self.truck_utilisation,
            "load_fill": self.load_fill,
            "late_loads": float(self.late_loads),
        }


def _fleet_for(twin: TwinState, strategy: Strategy, scenario: Scenario) -> dict[str, list[dict[str, float]]]:
    fleet = twin.fleet.sort_values("truck_id")
    per_dc: dict[str, list[dict[str, float]]] = {}
    for dc, grp in fleet.groupby("home_dc"):
        per_dc[str(dc)] = [
            {"capacity": float(cap), "cost_per_km": float(km), "fixed": float(fixed)}
            for cap, km, fixed in zip(
                grp["capacity_cases"].to_numpy(dtype=float),
                grp["cost_per_km"].to_numpy(dtype=float),
                grp["daily_fixed_cost"].to_numpy(dtype=float),
                strict=True,
            )
        ]
    dcs = sorted(per_dc)
    # Fleet changes go to DCs in turn; added trucks are the DC's most common type.
    for i in range(abs(strategy.extra_trucks)):
        dc = dcs[i % len(dcs)]
        if strategy.extra_trucks > 0:
            caps = [t["capacity"] for t in per_dc[dc]]
            common = max(set(caps), key=caps.count)
            template = next(t for t in per_dc[dc] if t["capacity"] == common)
            per_dc[dc].append(dict(template))
        elif len(per_dc[dc]) > 1:
            per_dc[dc].pop()
    if scenario.trucks_down_share > 0:
        for dc in dcs:
            down = math.floor(len(per_dc[dc]) * scenario.trucks_down_share)
            per_dc[dc] = per_dc[dc][: max(1, len(per_dc[dc]) - down)]
    return per_dc


def simulate(twin: TwinState, strategy: Strategy, scenario: Scenario, seed: int) -> RunResult:
    """Run one horizon. Demand depends only on (twin, scenario, seed), never on the strategy,
    so two strategies run with the same seed face identical customers."""
    n_days = scenario.horizon_days
    n_s, n_k = twin.base_rate.shape
    start = twin.cutoff + dt.timedelta(days=1)
    # A week past the horizon so the last reviews still see a full protection window.
    days = [start + dt.timedelta(days=i) for i in range(n_days + 7)]

    cal = np.stack([twin.day_multiplier(d) for d in days])  # (day, sku)
    promo_on = np.zeros(n_days + 7, dtype=bool)
    promo_on[scenario.promo_start_day : scenario.promo_start_day + scenario.promo_days] = bool(
        scenario.promo_categories
    )
    uplift = twin.promo_vector(list(scenario.promo_categories))
    promo_sku = uplift > 1.0
    true_mult = cal * np.where(promo_on[:, None], uplift[None, :], 1.0)
    planned_mult = true_mult if strategy.promo_aware else cal

    rng = np.random.default_rng(seed)
    error = rng.lognormal(-0.5 * scenario.rate_error_sigma**2, scenario.rate_error_sigma, size=(n_s, n_k))
    mu = (twin.base_rate * error)[None] * true_mult[:n_days, None, :]
    k = twin.dispersion
    demand = rng.negative_binomial(k, k / (k + mu)).astype(float)  # (day, store, sku)

    fleet = _fleet_for(twin, strategy, scenario)
    env = simpy.Environment()
    trucks = {dc: simpy.Store(env) for dc in fleet}
    for dc, ts in fleet.items():
        for t in ts:
            trucks[dc].put(t)
    n_trucks = sum(len(ts) for ts in fleet.values())

    lead_days = 1
    horizon = lead_days + strategy.review_days
    rate = twin.base_rate
    # Reorder point and order-up-to level from the planned demand over the protection window.
    window_mean = np.stack([planned_mult[d : d + horizon].sum(axis=0) for d in range(n_days)])  # (day, sku)
    daily_mean = np.stack([planned_mult[d : d + horizon].mean(axis=0) for d in range(n_days)])

    def targets(day: int) -> tuple[np.ndarray, np.ndarray]:
        m = rate * window_mean[day][None, :]
        sd = np.sqrt(m + m**2 / (k * horizon))
        reorder = m + strategy.z * sd
        up_to = reorder + strategy.cover_days * rate * daily_mean[day][None, :]
        return reorder, up_to

    _, s0 = targets(0)
    on_hand = s0.copy()
    on_order = np.zeros_like(on_hand)

    stats = {
        "sold": 0.0,
        "promo_sold": 0.0,
        "holding": 0.0,
        "transport": 0.0,
        "lost_margin": 0.0,
        "truck_hours": 0.0,
        "loads": 0,
        "cases": 0.0,
        "capacity": 0.0,
        "late": 0,
    }
    margin = twin.unit_price - twin.unit_cost
    hold_per_unit_day = twin.unit_cost * HOLDING_RATE_PER_YEAR / 365.0

    def deliver(store: int, units: np.ndarray, release: float) -> simpy.events.ProcessGenerator:
        dc = str(twin.store_dc[store])
        cases_vec = units / twin.case_pack
        remaining = float(cases_vec.sum())
        total = remaining
        yield env.timeout(max(0.0, release - env.now))
        while remaining > 1e-9:
            truck = yield trucks[dc].get()
            hour = env.now % 24
            if hour >= SHIFT_END or hour < SHIFT_START:
                wait = (SHIFT_START - hour) % 24
                yield env.timeout(wait)
            load = min(truck["capacity"], remaining)
            share = load / total
            km = float(twin.distance_km[store])
            drive = km / SPEED_KMH
            departure = env.now
            if departure - release >= 24:
                stats["late"] += 1
            yield env.timeout(LOAD_HOURS + drive + UNLOAD_HOURS)
            arrived = units * share
            on_hand[store] += arrived
            on_order[store] -= arrived
            yield env.timeout(drive)
            trip = env.now - departure
            stats["truck_hours"] += trip
            stats["transport"] += 2 * km * truck["cost_per_km"] + trip * DRIVER_COST_PER_HOUR
            stats["loads"] += 1
            stats["cases"] += load
            stats["capacity"] += truck["capacity"]
            remaining -= load
            yield trucks[dc].put(truck)

    def store_day(day: int) -> simpy.events.ProcessGenerator:
        yield env.timeout(day * 24 + SALES_HOUR - env.now)
        dem = demand[day]
        sold = np.minimum(on_hand, dem)
        on_hand[:] -= sold
        stats["sold"] += float(sold.sum())
        stats["lost_margin"] += float(((dem - sold) * margin[None, :]).sum())
        if promo_on[day]:
            stats["promo_sold"] += float(sold[:, promo_sku].sum())
        stats["holding"] += float((on_hand * hold_per_unit_day[None, :]).sum())

        yield env.timeout(REVIEW_HOUR - SALES_HOUR)
        reorder, up_to = targets(day)
        position = on_hand + on_order
        for s in range(n_s):
            # Stores are staggered across the review cycle so the DC sees an even load.
            if (day + s) % strategy.review_days:
                continue
            need = position[s] < reorder[s]
            if not need.any():
                continue
            qty = np.where(need, up_to[s] - position[s], 0.0)
            qty = np.ceil(qty / twin.case_pack) * twin.case_pack
            on_order[s] += qty
            env.process(deliver(s, qty, release=(day + 1) * 24 + SHIFT_START))

    def clock() -> simpy.events.ProcessGenerator:
        for day in range(n_days):
            yield env.process(store_day(day))

    env.process(clock())
    env.run(until=n_days * 24)

    fixed = sum(t["fixed"] for ts in fleet.values() for t in ts) * n_days
    in_window = promo_on[:n_days]
    promo_dem = float(demand[in_window][:, :, promo_sku].sum()) if in_window.any() else 0.0
    return RunResult(
        demand_units=float(demand.sum()),
        sold_units=stats["sold"],
        promo_demand_units=promo_dem,
        promo_sold_units=stats["promo_sold"],
        holding_cost=stats["holding"],
        transport_cost=stats["transport"],
        fleet_fixed_cost=fixed,
        lost_margin=stats["lost_margin"],
        truck_hours=stats["truck_hours"],
        truck_hours_available=n_trucks * (SHIFT_END - SHIFT_START) * n_days,
        loads=int(stats["loads"]),
        cases_shipped=stats["cases"],
        load_capacity=stats["capacity"],
        late_loads=int(stats["late"]),
    )
