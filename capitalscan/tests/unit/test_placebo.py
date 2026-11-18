"""The placebo inflation factor behind ADR 209's cell_stats intervals.

Synthetic panels only, no database. Every call runs with
`placebo_workers = 1` except one, which drives the process pool.
"""

import dataclasses
import logging

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.cells import headline_grid
from capitalscan.core.config import DEFAULT_CONFIG, VarianceParams
from capitalscan.research import placebo

SP = DEFAULT_CONFIG.stats
H = 5


def _vp(**kw: object) -> VarianceParams:
    base = {
        "placebo_draws": 200,
        "placebo_window": ("2012-01-02", "2017-12-29"),
        "placebo_workers": 1,
    }
    base.update(kw)
    return dataclasses.replace(VarianceParams(), **base)  # type: ignore[arg-type]


def _panel(n_tickers: int, start: str, end: str, seed: int, common: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    if common:
        r = np.repeat(rng.normal(0, 0.02, size=(len(dates), 1)), n_tickers, axis=1)
    else:
        r = rng.normal(0, 0.02, size=(len(dates), n_tickers))
    close = 100.0 * np.exp(np.cumsum(r, axis=0))
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    frame = pd.DataFrame(close, index=dates, columns=tickers)
    long = frame.stack().reset_index()
    long.columns = ["ts", "ticker", "adj_close"]
    return long[["ticker", "ts", "adj_close"]]


def _events(dates: pd.DatetimeIndex, tickers: list[str], cluster: list[str]) -> pd.DataFrame:
    spec = headline_grid(SP)[0]
    return pd.DataFrame(
        {
            "ticker": tickers,
            "signal_date": dates,
            "signal_type": spec.signal_type,
            "side": spec.side,
            "dd_bucket": spec.dd_bucket,
            "era": "e1",
            "is_cluster_head": True,
            "cluster_id": cluster,
            "fwd_ret_5d": 0.0,
        }
    )


def _iid_events(panel: pd.DataFrame, n: int, seed: int, vp: VarianceParams) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(*vp.placebo_window)
    tickers = sorted(panel["ticker"].unique())
    d = days[rng.integers(0, len(days), size=n)]
    t = [tickers[i] for i in rng.integers(0, len(tickers), size=n)]
    return _events(d, t, [f"c{i}" for i in range(n)])


def _pooled(events: pd.DataFrame) -> dict[str, pd.Series]:
    return {"pooled": pd.Series(True, index=events.index)}


def test_null_edge_mean_zero() -> None:
    panel = _panel(3, "2014-01-01", "2015-12-31", seed=1)
    dates, tickers, d = placebo.null_edge_matrix(panel, 0.02, 1, H, ("2014-01-01", "2015-12-31"))
    assert d.dtype == np.float32
    assert d.shape == (len(dates), len(tickers)) == (len(dates), 3)
    frame = pd.DataFrame(d.astype(np.float64), index=dates, columns=tickers)
    means = frame.groupby(dates.year).mean()
    assert np.nanmax(np.abs(means.to_numpy())) < 1e-6
    # the last `H` days have no forward window
    assert np.isnan(d[-H:]).all()


def test_iid_panel_factor_near_one() -> None:
    vp = _vp()
    panel = _panel(60, "2011-06-01", "2018-06-29", seed=2)
    events = _iid_events(panel, 1500, seed=3, vp=vp)
    out = placebo.measure_inflation(panel, events, SP, vp, H, _pooled(events))
    assert 1.0 <= out["pooled"] <= 1.15
    # unfloored, the ratio itself sits near one rather than being lifted to it
    raw = placebo.measure_inflation(
        panel, events, SP, dataclasses.replace(vp, inflation_floor=0.0), H, _pooled(events)
    )
    assert 0.85 <= raw["pooled"] <= 1.15


def test_common_shock_panel_factor_floor() -> None:
    vp = _vp(placebo_draws=50)
    panel = _panel(20, "2011-06-01", "2018-06-29", seed=4, common=True)
    days = pd.bdate_range(*vp.placebo_window)
    rng = np.random.default_rng(5)
    fire_days = days[np.sort(rng.choice(len(days), size=40, replace=False))]
    tickers = sorted(panel["ticker"].unique())
    d = np.repeat(fire_days, len(tickers))
    t = tickers * len(fire_days)
    c = [f"d{i}" for i in range(len(fire_days)) for _ in tickers]
    events = _events(pd.DatetimeIndex(d), t, c)
    out = placebo.measure_inflation(panel, events, SP, vp, H, _pooled(events))
    assert np.isfinite(out["pooled"])
    assert out["pooled"] >= vp.inflation_floor


def test_sparse_draws_skipped(caplog: pytest.LogCaptureFixture) -> None:
    vp = _vp(placebo_draws=20)
    panel = _panel(10, "2011-06-01", "2018-06-29", seed=6)
    events = _iid_events(panel, 10, seed=7, vp=vp)
    with caplog.at_level(logging.WARNING, logger=placebo.logger.name):
        out = placebo.measure_inflation(panel, events, SP, vp, H, _pooled(events))
    assert out == {"pooled": vp.inflation_floor}
    assert "pooled" in caplog.text and "floor" in caplog.text


def test_era_inherits_pooled_within_5pct() -> None:
    factors = {"pooled": 1.30, "e1": 1.33, "e2": 1.50, "e3": 1.26}
    out = placebo.inherit_pooled(factors, 0.05)
    assert out == {"pooled": 1.30, "e1": 1.30, "e2": 1.50, "e3": 1.30}

    # end to end: an era scope covering the same events as pooled inherits it
    vp = _vp(placebo_draws=30)
    panel = _panel(30, "2011-06-01", "2018-06-29", seed=8)
    events = _iid_events(panel, 400, seed=9, vp=vp)
    scopes = {**_pooled(events), "e1": events["era"] == "e1"}
    got = placebo.measure_inflation(panel, events, SP, vp, H, scopes)
    assert got["e1"] == got["pooled"]


def test_deterministic() -> None:
    vp = _vp(placebo_draws=40)
    panel = _panel(30, "2011-06-01", "2018-06-29", seed=10)
    events = _iid_events(panel, 400, seed=11, vp=vp)
    a = placebo.measure_inflation(panel, events, SP, vp, H, _pooled(events))
    b = placebo.measure_inflation(panel, events, SP, vp, H, _pooled(events))
    assert a == b


def test_pool_matches_serial() -> None:
    vp = _vp(placebo_draws=20)
    panel = _panel(20, "2011-06-01", "2018-06-29", seed=12)
    events = _iid_events(panel, 300, seed=13, vp=vp)
    serial = placebo.measure_inflation(panel, events, SP, vp, H, _pooled(events))
    pooled = placebo.measure_inflation(
        panel, events, SP, dataclasses.replace(vp, placebo_workers=2), H, _pooled(events)
    )
    assert serial == pooled


def test_scope_with_no_event_in_window_raises() -> None:
    """A scope whose events all fall outside the placebo window would floor
    to a factor that reads as measured. It raises, naming the scope."""
    vp = _vp(placebo_draws=20)
    panel = _panel(10, "2011-06-01", "2018-06-29", seed=14)
    inside = _iid_events(panel, 50, seed=15, vp=vp)
    outside = _events(
        pd.bdate_range("2018-01-02", periods=5), ["T000"] * 5, [f"x{i}" for i in range(5)]
    )
    events = pd.concat([inside, outside.assign(era="late")], ignore_index=True)
    scopes = {**_pooled(events), "late": events["era"] == "late"}
    with pytest.raises(ValueError, match="late"):
        placebo.measure_inflation(panel, events, SP, vp, H, scopes)


def test_scope_with_no_events_floors() -> None:
    vp = _vp(placebo_draws=20)
    panel = _panel(10, "2011-06-01", "2018-06-29", seed=16)
    events = _iid_events(panel, 50, seed=17, vp=vp)
    scopes = {**_pooled(events), "empty": events["era"] == "none"}
    out = placebo.measure_inflation(panel, events, SP, vp, H, scopes)
    assert out["empty"] == vp.inflation_floor
