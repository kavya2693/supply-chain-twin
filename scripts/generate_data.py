"""Write the three source tables the twin joins: fleet (FMS), merchandise (MMS) and orders (OMS).

Seeded, so every run produces the same files. Sized like a mid-size regional grocery and
household chain: 2 distribution centres, 36 stores, 120 SKUs, two years of daily order lines,
and a truck fleet sized so the base plan keeps trucks busy most of the shift.

Patterns put in on purpose, because the twin has to cope with them:
- weekly profile (weekend peak), annual seasonality per category, slow growth
- public holidays and the stock-up days before them
- historical category promotions with uplift that differs by category
- over-dispersed (negative binomial) daily noise
- occasional bulk orders (outliers) and store feed outages (missing days)
- missing unit costs in MMS and missing truck capacities in FMS
- store backroom capacity in the location master, about five days of trade
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from supply_twin.calendar import HISTORY_DAYS, HISTORY_START, holiday_eve, is_holiday

CATEGORIES = {
    # name: (weekend lift, seasonal amplitude, seasonal peak day-of-year, promo uplift)
    "beverages": (1.30, 0.35, 200, 1.9),
    "snacks": (1.35, 0.15, 340, 1.7),
    "dairy": (1.10, 0.05, 30, 1.4),
    "pantry": (1.15, 0.10, 90, 1.5),
    "frozen": (1.20, 0.25, 210, 1.6),
    "household": (1.05, 0.08, 120, 1.5),
    "personal_care": (1.05, 0.05, 150, 1.3),
    "baby": (1.00, 0.03, 60, 1.2),
}


def _skus(rng: np.random.Generator, n: int) -> pd.DataFrame:
    cats = rng.choice(list(CATEGORIES), size=n)
    unit_cost = np.round(rng.lognormal(mean=1.3, sigma=0.55, size=n), 2)
    margin = rng.uniform(0.22, 0.45, size=n)
    case_pack = rng.choice([6, 12, 24], size=n, p=[0.3, 0.5, 0.2])
    df = pd.DataFrame(
        {
            "sku_id": [f"SKU{i:04d}" for i in range(n)],
            "category": cats,
            "unit_cost": unit_cost,
            "unit_price": np.round(unit_cost / (1 - margin), 2),
            "case_pack": case_pack,
        }
    )
    # Item master gaps: some new items have no landed cost yet.
    missing = rng.random(n) < 0.03
    df.loc[missing, "unit_cost"] = np.nan
    return df


def _stores(rng: np.random.Generator, n: int) -> pd.DataFrame:
    dc = np.where(np.arange(n) < n * 0.55, "DC1", "DC2")
    tier = rng.choice(["small", "medium", "large"], size=n, p=[0.3, 0.45, 0.25])
    return pd.DataFrame(
        {
            "store_id": [f"S{i:03d}" for i in range(n)],
            "dc_id": dc,
            "size_tier": tier,
            "distance_km": np.round(rng.gamma(shape=3.0, scale=28.0, size=n) + 12, 1),
        }
    )


def _fleet(rng: np.random.Generator, n: int) -> pd.DataFrame:
    kind = rng.choice(["rigid_10t", "rigid_18t"], size=n, p=[0.6, 0.4])
    capacity = np.where(kind == "rigid_10t", 420, 700).astype(float)
    df = pd.DataFrame(
        {
            "truck_id": [f"T{i:03d}" for i in range(n)],
            "home_dc": np.where(np.arange(n) < n * 0.55, "DC1", "DC2"),
            "truck_type": kind,
            "capacity_cases": capacity,
            "cost_per_km": np.where(kind == "rigid_10t", 1.05, 1.40),
            "daily_fixed_cost": np.where(kind == "rigid_10t", 210.0, 290.0),
            "status": rng.choice(["active", "maintenance", "retired"], size=n, p=[0.88, 0.08, 0.04]),
        }
    )
    # Telematics feeds lose the capacity field for a few vehicles; type tells you the answer.
    df.loc[rng.random(n) < 0.06, "capacity_cases"] = np.nan
    return df


def _orders(rng: np.random.Generator, skus: pd.DataFrame, stores: pd.DataFrame) -> pd.DataFrame:
    days = [HISTORY_START + dt.timedelta(days=d) for d in range(HISTORY_DAYS)]
    n_d, n_s, n_k = len(days), len(stores), len(skus)
    cat_names = skus["category"].to_numpy()

    tier_scale = stores["size_tier"].map({"small": 0.6, "medium": 1.0, "large": 1.6}).to_numpy()
    popularity = rng.lognormal(mean=3.4, sigma=0.8, size=n_k)
    base = tier_scale[:, None] * popularity[None, :]

    day_factor = np.ones((n_d, n_k))
    doy = np.array([d.timetuple().tm_yday for d in days])
    weekend = np.array([d.weekday() >= 4 for d in days])  # Friday to Sunday peak
    for c, (wk, amp, peak, _) in CATEGORIES.items():
        idx = cat_names == c
        season = 1 + amp * np.cos(2 * np.pi * (doy - peak) / 365.25)
        lift = np.where(weekend, wk, 1.0)
        day_factor[:, idx] = (season * lift)[:, None]
    growth = 1 + 0.04 * np.arange(n_d) / 365.25
    hol = np.array([1.8 if is_holiday(d) else (1.35 if holiday_eve(d) else 1.0) for d in days])
    day_factor *= (growth * hol)[:, None]

    # Two-week category promotions, about one per category per quarter.
    promo = np.zeros((n_d, n_k), dtype=bool)
    for c, (_, _, _, uplift) in CATEGORIES.items():
        idx = cat_names == c
        for start in rng.choice(np.arange(0, n_d - 14, 7), size=8, replace=False):
            promo[start : start + 14, idx] = True
            day_factor[start : start + 14, idx] *= uplift

    mu = base[None, :, :] * day_factor[:, None, :]

    # Backrooms are built for roughly five days of average trade, give or take.
    daily_cases = (mu / skus["case_pack"].to_numpy()[None, None, :]).mean(axis=0).sum(axis=1)
    stores["backroom_cases"] = np.round(daily_cases * 5.0 * rng.uniform(0.85, 1.15, size=n_s), -1)
    dispersion = 4.0
    p = dispersion / (dispersion + mu)
    units = rng.negative_binomial(dispersion, p).astype(np.int64)

    # Bulk buys from small businesses: rare, large.
    bulk = rng.random(units.shape) < 0.0008
    units[bulk] = units[bulk] * rng.integers(6, 15, size=int(bulk.sum())) + 20

    # Store POS feed outages: whole store-days missing from the OMS extract.
    outage = rng.random((n_d, n_s)) < 0.006
    units[outage] = -1

    d_idx, s_idx, k_idx = np.nonzero(units > 0)
    return pd.DataFrame(
        {
            "order_date": pd.to_datetime(np.array(days, dtype="datetime64[D]")[d_idx]),
            "store_id": stores["store_id"].to_numpy()[s_idx],
            "sku_id": skus["sku_id"].to_numpy()[k_idx],
            "units": units[d_idx, s_idx, k_idx],
            "on_promo": promo[d_idx, k_idx],
        }
    )


def generate(out: Path, seed: int = 7, n_stores: int = 36, n_skus: int = 120, n_trucks: int = 28) -> None:
    rng = np.random.default_rng(seed)
    out.mkdir(parents=True, exist_ok=True)
    skus = _skus(rng, n_skus)
    stores = _stores(rng, n_stores)
    fleet = _fleet(rng, n_trucks)
    orders = _orders(rng, skus, stores)
    skus.to_csv(out / "mms_items.csv", index=False)
    stores.to_csv(out / "mms_locations.csv", index=False)
    fleet.to_csv(out / "fms_fleet.csv", index=False)
    orders.to_csv(out / "oms_order_lines.csv.gz", index=False, compression="gzip")
    print(
        f"wrote {len(skus)} items, {len(stores)} stores, {len(fleet)} trucks, "
        f"{len(orders):,} order lines ({orders['units'].sum():,} units) to {out}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    generate(args.out, seed=args.seed)


if __name__ == "__main__":
    main()
