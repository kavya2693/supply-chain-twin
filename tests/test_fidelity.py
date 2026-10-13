from __future__ import annotations

from supply_twin.fidelity import backtest


def test_twin_beats_naive_references_on_held_out_quarter(sources) -> None:
    fid = backtest(sources)
    cat = fid.wape_store_category_week
    assert cat["twin"] < cat["recent_8wk_mean"]
    assert cat["twin"] < cat["same_day_last_year"]
    assert 0 < fid.rate_error_sigma < 0.5
