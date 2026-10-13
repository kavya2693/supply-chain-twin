"""Does the twin's demand look like the demand that actually happened?

Build the twin from history that stops 13 weeks early, ask it for expected demand over
those 13 weeks (promotions as they were actually scheduled), and compare with the order
lines it never saw. Two naive references set the bar: the recent 8-week average, and the
same weekday 52 weeks earlier.

The spread of the store-SKU error measured here is what the simulator then injects as
forecast error, so the twin is exactly as wrong in simulation as it was in the backtest.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from supply_twin.calendar import HISTORY_END
from supply_twin.twin import Sources, build_twin, to_cube


@dataclass
class Fidelity:
    holdout_days: int
    wape_store_sku_week: dict[str, float]
    wape_store_category_week: dict[str, float]
    bias: dict[str, float]
    rate_error_sigma: float
    promo_wape: dict[str, float]


def _wape(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.abs(actual - pred).sum() / actual.sum())


def backtest(src: Sources, holdout_days: int = 91) -> Fidelity:
    cutoff = HISTORY_END - dt.timedelta(days=holdout_days)
    twin = build_twin(src, cutoff=cutoff)
    start = cutoff + dt.timedelta(days=1)
    days = [start + dt.timedelta(days=i) for i in range(holdout_days)]
    actual, observed, promo = to_cube(src.orders, twin.store_ids, twin.sku_ids, start, holdout_days)

    cal = np.stack([twin.day_multiplier(d) for d in days])
    uplift = np.array([twin.promo_uplift[str(c)] for c in twin.categories])
    mult = cal * np.where(promo, uplift[None, :], 1.0)
    pred_twin = twin.base_rate[None] * mult[:, None, :]

    hist_start = cutoff - dt.timedelta(days=364 + 7)
    hist, hist_obs, _ = to_cube(src.orders, twin.store_ids, twin.sku_ids, hist_start, 364 + 7 + 1)
    recent = np.where(hist_obs[-56:, :, None], hist[-56:], np.nan)
    pred_recent = np.broadcast_to(np.nanmean(recent, axis=0)[None], actual.shape)
    # Same weekday 52 weeks earlier: hold-out day i sits at index 8 + i of `hist`.
    pred_lastyear = hist[8 : 8 + holdout_days]

    preds = {"twin": pred_twin, "recent_8wk_mean": pred_recent, "same_day_last_year": pred_lastyear}
    mask = observed[:, :, None] & np.ones_like(actual, dtype=bool)
    weeks = holdout_days // 7

    def weekly(x: np.ndarray) -> np.ndarray:
        x = np.where(mask, x, 0.0)[: weeks * 7]
        return x.reshape(weeks, 7, *x.shape[1:]).sum(axis=1)

    cats = np.unique(twin.categories)

    def by_cat(x: np.ndarray) -> np.ndarray:
        return np.stack([x[..., twin.categories == c].sum(axis=-1) for c in cats], axis=-1)

    a_w = weekly(actual)
    promo_weeks = np.asarray(promo[: weeks * 7].reshape(weeks, 7, -1).any(axis=1))
    promo_cells = np.broadcast_to(promo_weeks[:, None, :], a_w.shape)
    sku_wape, cat_wape, bias, promo_wape = {}, {}, {}, {}
    for name, p in preds.items():
        p_w = weekly(p)
        sku_wape[name] = _wape(a_w, p_w)
        cat_wape[name] = _wape(by_cat(a_w), by_cat(p_w))
        bias[name] = float(p_w.sum()) / float(a_w.sum()) - 1
        promo_wape[name] = _wape(a_w[promo_cells], p_w[promo_cells]) if promo_cells.any() else float("nan")

    tot_a = np.where(mask, actual, 0).sum(axis=0)
    tot_p = np.where(mask, pred_twin, 0).sum(axis=0)
    ratio = np.log(np.maximum(tot_a, 1) / np.maximum(tot_p, 1))
    return Fidelity(
        holdout_days=holdout_days,
        wape_store_sku_week=sku_wape,
        wape_store_category_week=cat_wape,
        bias=bias,
        rate_error_sigma=float(ratio.std()),
        promo_wape=promo_wape,
    )
