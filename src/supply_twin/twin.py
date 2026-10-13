"""Join the fleet (FMS), merchandise (MMS) and order (OMS) tables into one twin state.

The twin state is everything the simulator needs and nothing it does not: per store-SKU
demand rates with calendar multipliers estimated from order history, item economics from
the item master, store-to-DC routing and distances from the location master, and the
dispatchable fleet per DC. Every repair made on the way in (imputed costs, dropped trucks,
clipped bulk orders, missing store-days) is counted in a data-quality report, because a
twin built quietly on bad joins produces confident nonsense.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from supply_twin.calendar import HISTORY_END, holiday_eve, is_holiday


@dataclass
class Sources:
    items: pd.DataFrame
    locations: pd.DataFrame
    fleet: pd.DataFrame
    orders: pd.DataFrame


@dataclass
class TwinState:
    store_ids: list[str]
    sku_ids: list[str]
    categories: np.ndarray  # (sku,) category name
    store_dc: np.ndarray  # (store,) DC id
    distance_km: np.ndarray  # (store,)
    backroom_cases: np.ndarray  # (store,) storage the store has before stock spills over
    unit_cost: np.ndarray  # (sku,)
    unit_price: np.ndarray  # (sku,)
    case_pack: np.ndarray  # (sku,)
    base_rate: np.ndarray  # (store, sku) de-seasonalised units per day
    weekday_idx: dict[str, np.ndarray]  # category -> (7,)
    month_idx: dict[str, np.ndarray]  # category -> (12,)
    holiday_factor: float
    eve_factor: float
    promo_uplift: dict[str, float]  # category -> multiplier
    dispersion: float  # negative binomial k
    fleet: pd.DataFrame  # dispatchable trucks: truck_id, home_dc, capacity_cases, cost_per_km, ...
    cutoff: dt.date
    quality: dict[str, int | float] = field(default_factory=dict)

    def day_multiplier(self, day: dt.date) -> np.ndarray:
        """Expected demand multiplier per SKU on a calendar day, promotions excluded."""
        out = np.empty(len(self.sku_ids))
        for c in np.unique(self.categories):
            m = self.categories == c
            out[m] = self.weekday_idx[c][day.weekday()] * self.month_idx[c][day.month - 1]
        if is_holiday(day):
            out *= self.holiday_factor
        elif holiday_eve(day):
            out *= self.eve_factor
        return out

    def promo_vector(self, promo_categories: list[str]) -> np.ndarray:
        """Per-SKU uplift if the given categories are on promotion, else 1."""
        out = np.ones(len(self.sku_ids))
        for c in promo_categories:
            out[self.categories == c] = self.promo_uplift[c]
        return out


def load_sources(data_dir: Path) -> Sources:
    return Sources(
        items=pd.read_csv(data_dir / "mms_items.csv"),
        locations=pd.read_csv(data_dir / "mms_locations.csv"),
        fleet=pd.read_csv(data_dir / "fms_fleet.csv"),
        orders=pd.read_csv(data_dir / "oms_order_lines.csv.gz", parse_dates=["order_date"]),
    )


def _clean_items(items: pd.DataFrame, q: dict[str, int | float]) -> pd.DataFrame:
    items = items.copy()
    missing = items["unit_cost"].isna()
    q["items_missing_cost"] = int(missing.sum())
    # Price is complete in the item master; the category's median cost-to-price ratio
    # recovers a plausible landed cost for items that have not been costed yet.
    ratio = (items["unit_cost"] / items["unit_price"]).groupby(items["category"]).transform("median")
    items.loc[missing, "unit_cost"] = (items.loc[missing, "unit_price"] * ratio[missing]).round(2)
    return items


def _clean_fleet(fleet: pd.DataFrame, q: dict[str, int | float]) -> pd.DataFrame:
    fleet = fleet.copy()
    q["trucks_in_source"] = len(fleet)
    missing = fleet["capacity_cases"].isna()
    q["trucks_missing_capacity"] = int(missing.sum())
    by_type = fleet.groupby("truck_type")["capacity_cases"].transform("median")
    fleet.loc[missing, "capacity_cases"] = by_type[missing]
    active = fleet["status"] == "active"
    q["trucks_not_dispatchable"] = int((~active).sum())
    return fleet[active].reset_index(drop=True)


def to_cube(
    orders: pd.DataFrame, store_ids: list[str], sku_ids: list[str], start: dt.date, n_days: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Order lines to dense arrays: units (day, store, sku), observed (day, store), promo (day, sku).

    The OMS extract only carries non-zero lines, so a store-day with no lines at all is a
    feed outage, not a day of zero sales, and is marked unobserved.
    """
    d_i = (orders["order_date"] - pd.Timestamp(start)).dt.days.to_numpy()
    keep = (d_i >= 0) & (d_i < n_days)
    orders, d_i = orders[keep], d_i[keep]
    s_i = orders["store_id"].map({s: i for i, s in enumerate(store_ids)}).to_numpy()
    k_i = orders["sku_id"].map({k: i for i, k in enumerate(sku_ids)}).to_numpy()
    units = np.zeros((n_days, len(store_ids), len(sku_ids)))
    units[d_i, s_i, k_i] = orders["units"].to_numpy()
    observed = np.zeros((n_days, len(store_ids)), dtype=bool)
    observed[d_i, s_i] = True
    promo = np.zeros((n_days, len(sku_ids)), dtype=bool)
    promo[d_i, k_i] = orders["on_promo"].to_numpy()
    return units, observed, promo


def build_twin(src: Sources, cutoff: dt.date = HISTORY_END, rate_window_days: int = 56) -> TwinState:
    """Estimate the twin from order history up to and including `cutoff`."""
    q: dict[str, int | float] = {}
    items = _clean_items(src.items, q)
    fleet = _clean_fleet(src.fleet, q)
    locs = src.locations

    orders = src.orders[src.orders["order_date"].dt.date <= cutoff]
    known_store = orders["store_id"].isin(locs["store_id"])
    known_sku = orders["sku_id"].isin(items["sku_id"])
    q["order_lines"] = len(orders)
    q["order_lines_orphan_key"] = int((~(known_store & known_sku)).sum())
    orders = orders[known_store & known_sku]

    store_ids = locs["store_id"].tolist()
    sku_ids = items["sku_id"].tolist()
    start = orders["order_date"].min().date()
    n_days = (cutoff - start).days + 1
    days = [start + dt.timedelta(days=i) for i in range(n_days)]

    units, observed, promo = to_cube(orders, store_ids, sku_ids, start, n_days)
    q["store_days_missing"] = int((~observed).sum())

    # Bulk business buys are real orders but not repeatable demand; cap them so one
    # 400-unit order does not set a store's reorder point for the next quarter.
    obs3 = np.broadcast_to(observed[:, :, None], units.shape)
    masked = np.where(obs3, units, np.nan)
    cap = np.nanpercentile(masked, 99.5, axis=0) * 1.5 + 5
    clipped = units > cap[None]
    q["order_units_clipped_as_bulk"] = int(clipped.sum())
    units = np.minimum(units, cap[None])

    cats = items["category"].to_numpy()
    weekday = np.array([d.weekday() for d in days])
    month = np.array([d.month for d in days])
    hol = np.array([is_holiday(d) for d in days])
    eve = np.array([holiday_eve(d) for d in days]) & ~hol
    plain = ~hol & ~eve

    level = np.nanmean(masked.clip(max=cap[None]), axis=0)  # (store, sku)
    y = units / np.maximum(level[None], 1e-6)

    weekday_idx: dict[str, np.ndarray] = {}
    month_idx: dict[str, np.ndarray] = {}
    promo_uplift: dict[str, float] = {}
    for c in np.unique(cats):
        kc = cats == c
        yc = np.where(observed[:, :, None], y[:, :, kc], np.nan)  # (day, store, sku_c)
        pc = promo[:, kc][:, None, :]
        base_days = plain[:, None, None] & ~pc
        overall = np.nanmean(np.where(base_days, yc, np.nan))
        w = np.array(
            [np.nanmean(np.where(base_days & (weekday == i)[:, None, None], yc, np.nan)) for i in range(7)]
        )
        w = w / overall
        y_w = yc / w[weekday][:, None, None]
        m = np.array(
            [
                np.nanmean(np.where(base_days & (month == i + 1)[:, None, None], y_w, np.nan))
                for i in range(12)
            ]
        )
        m = m / np.nanmean(m)
        y_wm = y_w / m[month - 1][:, None, None]
        on = np.nanmean(np.where(plain[:, None, None] & pc, y_wm, np.nan))
        off = np.nanmean(np.where(plain[:, None, None] & ~pc, y_wm, np.nan))
        weekday_idx[str(c)] = w
        month_idx[str(c)] = m
        promo_uplift[str(c)] = float(on / off)

    # Calendar multiplier per (day, sku) without holidays, used to isolate the holiday lift.
    cal = np.empty((n_days, len(sku_ids)))
    for c in np.unique(cats):
        kc = cats == c
        cal[:, kc] = (weekday_idx[str(c)][weekday] * month_idx[str(c)][month - 1])[:, None]
    promo_mult = np.where(promo, np.array([promo_uplift[str(c)] for c in cats])[None, :], 1.0)
    y_adj = np.where(observed[:, :, None], y / (cal * promo_mult)[:, None, :], np.nan)
    base_level = np.nanmean(y_adj[plain])
    holiday_factor = float(np.nanmean(y_adj[hol]) / base_level)
    eve_factor = float(np.nanmean(y_adj[eve]) / base_level)

    hol_mult = np.where(hol, holiday_factor, np.where(eve, eve_factor, 1.0))
    full_mult = cal * promo_mult * hol_mult[:, None]  # (day, sku)

    recent = slice(n_days - rate_window_days, n_days)
    deseason = np.where(observed[recent, :, None], units[recent] / full_mult[recent, None, :], np.nan)
    base_rate = np.nanmean(deseason, axis=0)

    # Over-dispersion from the last half year: var = mu + mu^2 / k.
    tail = slice(n_days - 182, n_days)
    mu = base_rate[None] * full_mult[tail, None, :]
    resid = np.where(observed[tail, :, None], (units[tail] - mu) ** 2 - mu, np.nan)
    excess = float(np.nanmean(resid))
    dispersion = float(np.clip(np.nanmean(mu**2) / max(excess, 1e-6), 0.5, 50.0))

    q["stores"] = len(store_ids)
    q["skus"] = len(sku_ids)
    q["trucks_dispatchable"] = len(fleet)

    loc = locs.set_index("store_id").loc[store_ids]
    return TwinState(
        store_ids=store_ids,
        sku_ids=sku_ids,
        categories=cats,
        store_dc=loc["dc_id"].to_numpy(),
        distance_km=loc["distance_km"].to_numpy(dtype=float),
        backroom_cases=loc["backroom_cases"].to_numpy(dtype=float),
        unit_cost=items["unit_cost"].to_numpy(dtype=float),
        unit_price=items["unit_price"].to_numpy(dtype=float),
        case_pack=items["case_pack"].to_numpy(dtype=float),
        base_rate=base_rate,
        weekday_idx=weekday_idx,
        month_idx=month_idx,
        holiday_factor=holiday_factor,
        eve_factor=eve_factor,
        promo_uplift=promo_uplift,
        dispersion=dispersion,
        fleet=fleet,
        cutoff=cutoff,
        quality=q,
    )
