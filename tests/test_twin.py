from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from supply_twin.twin import Sources, build_twin


def test_join_repairs_and_counts_bad_source_rows(sources: Sources) -> None:
    items = sources.items.copy()
    items.loc[0, "unit_cost"] = np.nan
    fleet = sources.fleet.copy()
    fleet.loc[0, "capacity_cases"] = np.nan
    fleet.loc[0, "status"] = "active"
    orphan = pd.DataFrame(
        {
            "order_date": [pd.Timestamp("2025-06-01")],
            "store_id": ["S999"],
            "sku_id": ["SKU0000"],
            "units": [5],
            "on_promo": [False],
        }
    )
    orders = pd.concat([sources.orders, orphan], ignore_index=True)
    twin = build_twin(Sources(items=items, locations=sources.locations, fleet=fleet, orders=orders))

    assert twin.quality["order_lines_orphan_key"] == 1
    assert twin.quality["items_missing_cost"] >= 1
    assert not np.isnan(twin.unit_cost).any()
    assert (twin.unit_cost < twin.unit_price).all()
    assert not twin.fleet["capacity_cases"].isna().any()
    assert (twin.fleet["status"] == "active").all()


def test_feed_outage_is_missing_not_zero(sources: Sources, twin) -> None:
    # Delete the last four weeks of one store from the extract. If those days were read as
    # zero sales the store's rate would collapse; read as missing, it barely moves.
    cut = sources.orders["order_date"].dt.date > twin.cutoff - dt.timedelta(days=28)
    gone = sources.orders[~(cut & (sources.orders["store_id"] == "S000"))]
    holed = build_twin(Sources(sources.items, sources.locations, sources.fleet, gone))
    ratio = holed.base_rate[0].sum() / twin.base_rate[0].sum()
    assert 0.85 < ratio < 1.15
    assert holed.quality["store_days_missing"] >= twin.quality["store_days_missing"] + 28


def test_calendar_effects_recovered_from_history(twin, categories) -> None:
    assert 1.5 < twin.holiday_factor < 2.1  # generator uses 1.8
    for c, (*_, uplift) in categories.items():
        if c in twin.promo_uplift:
            assert abs(twin.promo_uplift[c] - uplift) < 0.3, c
    for w in twin.weekday_idx.values():
        assert w[4:].mean() >= w[:4].mean()  # Friday to Sunday is the peak
