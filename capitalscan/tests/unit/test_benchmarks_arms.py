"""Contract for `research/benchmarks.py` — the windowing layer of Session 13.

`core/arms.py` owns the arithmetic and `test_arms.py` covers it. What is
tested here is *which rows*: which days form the window, who is in the
universe on each of them, which events become entries, and how a random
entry is drawn so the null is reproducible. Every case runs on in-memory
frames, so nothing here reads the database.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from capitalscan.core import arms as core_arms
from capitalscan.core.config import BenchmarkParams, Config
from capitalscan.core.types import Side
from capitalscan.research import benchmarks
from capitalscan.research.benchmarks import ArmWindow, Entry

CONFIG_HASH = "1835688bf7d760ba"
OTHER_HASH = "6ffb4a9286aac960"
CFG = Config()
BM = BenchmarkParams()


def _dates(n, start=date(2020, 1, 1)):
    """`n` consecutive weekdays, which is close enough to a trading calendar
    for the windowing rules under test."""
    out = []
    day = start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return tuple(out)


def _window(n_days, members=None, dates=None):
    days = dates or _dates(n_days)
    return ArmWindow(
        split_key="train",
        dates=days,
        members=tuple(members or [("AAA", "BBB")] * n_days),
        delisted_on={},
    )


def _panel(ticker, window, prices, indicator_value=None):
    """A dense panel where every bar is a flat OHLC at the given price."""
    frame = pd.DataFrame(
        {
            "ts": [pd.Timestamp(d) for d in window.dates],
            "open": prices,
            "high": [p * 1.02 for p in prices],
            "low": [p * 0.98 for p in prices],
            "close": prices,
            "adj_close": prices,
        }
    )
    indicators = pd.DataFrame({"ts": frame["ts"]})
    for column in benchmarks._PANEL_INDICATORS:
        indicators[column] = indicator_value
    return benchmarks._build_panel(ticker, frame, indicators, window.index_of())


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------


def test_split_bounds_do_not_overlap_between_train_and_validate():
    """ADR 019's splits are by date, and an off-by-one at the boundary puts
    the same day in two splits."""
    _, train_end = benchmarks.split_bounds(CFG, "train")
    validate_start, _ = benchmarks.split_bounds(CFG, "validate")
    assert validate_start > train_end


def test_split_bounds_reject_an_unknown_split():
    with pytest.raises(ValueError, match="unknown split_key"):
        benchmarks.split_bounds(CFG, "everything")


def test_the_window_exposes_every_ticker_that_was_ever_a_member():
    window = _window(3, members=[("AAA",), ("AAA", "BBB"), ("BBB",)])
    assert window.tickers == ("AAA", "BBB")


# --------------------------------------------------------------------------
# Universe restriction (ADR 012)
# --------------------------------------------------------------------------


def test_an_event_outside_the_trade_universe_that_day_is_dropped():
    """ADR 012: the signal arm has to trade the names buy-and-hold holds, or
    the comparison is between two universes rather than two entry rules."""
    window = _window(3, members=[("AAA",), ("AAA",), ("AAA",)])
    events = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB"],
            "signal_date": [window.dates[0], window.dates[0]],
        }
    )
    kept = benchmarks.restrict_to_universe(events, window)
    assert list(kept["ticker"]) == ["AAA"]


def test_an_event_on_a_day_the_ticker_had_not_yet_joined_is_dropped():
    window = _window(2, members=[("AAA",), ("AAA", "BBB")])
    events = pd.DataFrame({"ticker": ["BBB", "BBB"], "signal_date": list(window.dates)})
    kept = benchmarks.restrict_to_universe(events, window)
    assert list(kept["signal_date"]) == [window.dates[1]]


# --------------------------------------------------------------------------
# Entries and positions
# --------------------------------------------------------------------------


def test_an_entry_fills_at_the_next_open_not_the_signal_bar():
    """The population is `entry_kind = 'next_open'`, so the signal on day s
    fills at day s+1's open. A caller resolving off the signal bar shifts
    every exit in the arm by one bar."""
    window = _window(8)
    panels = {"AAA": _panel("AAA", window, [100.0] * 8)}
    positions = benchmarks.build_positions(
        [Entry("AAA", window.dates[0])], panels, window, CFG, Side.LONG
    )
    assert len(positions) == 1
    assert positions[0].entry_idx == 1


def test_a_signal_on_the_last_bar_never_fills_and_is_dropped():
    """No next bar to fill against. Dropped, never fabricated."""
    window = _window(4)
    panels = {"AAA": _panel("AAA", window, [100.0] * 4)}
    positions = benchmarks.build_positions(
        [Entry("AAA", window.dates[-1])], panels, window, CFG, Side.LONG
    )
    assert positions == []


def test_an_entry_on_a_ticker_with_no_panel_is_dropped():
    window = _window(6)
    panels = {"AAA": _panel("AAA", window, [100.0] * 6)}
    assert (
        benchmarks.build_positions([Entry("ZZZ", window.dates[0])], panels, window, CFG, Side.LONG)
        == []
    )


def test_a_flat_series_times_out_at_max_hold_days():
    """Nothing triggers, so `resolve_exit` falls through to TIMEOUT on the
    last bar of the window — `max_hold_days` bars after the fill."""
    window = _window(10)
    panels = {"AAA": _panel("AAA", window, [100.0] * 10)}
    positions = benchmarks.build_positions(
        [Entry("AAA", window.dates[0])], panels, window, CFG, Side.LONG
    )
    assert positions[0].exit_idx - positions[0].entry_idx == CFG.exits.max_hold_days


def test_build_positions_calls_the_shared_exit_resolver(monkeypatch):
    """13.2 acceptance, stated as "assert the same function is called rather
    than comparing outputs". `resolve_exit_for_entry` is the pinned wrapper
    around `core.exits.resolve_exit` (invariant 2); a second exit
    implementation anywhere in this module would not touch it."""
    calls = []
    real = benchmarks.resolve_exit_for_entry

    def _spy(*args, **kwargs):
        calls.append(args[0]["entry_date"])
        return real(*args, **kwargs)

    monkeypatch.setattr(benchmarks, "resolve_exit_for_entry", _spy)
    window = _window(10)
    panels = {"AAA": _panel("AAA", window, [100.0] * 10)}
    benchmarks.build_positions([Entry("AAA", window.dates[0])], panels, window, CFG, Side.LONG)
    assert calls == [window.dates[1]]


def test_the_exit_cache_does_not_change_the_answer():
    """The null resolves the same entry bar across replications, so the
    cache is worth minutes. It is keyed on what determines the answer, and
    this is the assertion that it is."""
    window = _window(12)
    panels = {"AAA": _panel("AAA", window, [100.0 + i for i in range(12)])}
    entries = [Entry("AAA", window.dates[0]), Entry("AAA", window.dates[0])]
    cached = benchmarks.build_positions(entries, panels, window, CFG, Side.LONG, {})
    uncached = benchmarks.build_positions(entries, panels, window, CFG, Side.LONG, None)
    assert [p.returns for p in cached] == [p.returns for p in uncached]


def test_a_missing_interior_bar_is_padded_with_a_zero_return():
    """A ticker short one calendar day must still span the shared calendar,
    and the gap gets 0.0 — no bar means no observed price change. Filling it
    from a neighbour would invent one (invariant 4)."""
    window = _window(9)
    prices = [100.0] * 9
    frame = pd.DataFrame(
        {
            "ts": [pd.Timestamp(d) for d in window.dates],
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "adj_close": prices,
        }
    ).drop(index=3)  # the ticker has no bar on calendar day 3
    indicators = pd.DataFrame({"ts": frame["ts"]})
    for column in benchmarks._PANEL_INDICATORS:
        indicators[column] = np.nan
    panel = benchmarks._build_panel("AAA", frame, indicators, window.index_of())

    positions = benchmarks.build_positions(
        [Entry("AAA", window.dates[0])], {"AAA": panel}, window, CFG, Side.LONG
    )
    position = positions[0]
    assert len(position.returns) == position.exit_idx - position.entry_idx + 1
    assert position.returns[3 - position.entry_idx] == 0.0


# --------------------------------------------------------------------------
# The random-entry null (13.2, ADR 061)
# --------------------------------------------------------------------------


def _pools():
    return {
        ("AAA", 2018): _dates(200, date(2018, 1, 1)),
        ("AAA", 2019): _dates(200, date(2019, 1, 1)),
        ("BBB", 2018): _dates(200, date(2018, 1, 1)),
    }


def _signal_entries():
    """AAA fires 12 times in 2018, 3 in 2019; BBB fires once in 2018."""
    out = [Entry("AAA", date(2018, 1, 1) + timedelta(days=7 * i)) for i in range(12)]
    out += [Entry("AAA", date(2019, 1, 1) + timedelta(days=30 * i)) for i in range(3)]
    out += [Entry("BBB", date(2018, 6, 1))]
    return out


def test_two_draws_at_the_same_config_hash_are_identical():
    """13.2 acceptance, verified on every replication rather than a sample.
    ADR 061 seeds the null from `config_hash` so it reproduces within a
    config."""
    pools, entries = _pools(), _signal_entries()
    for replication in range(1, 201):
        first = benchmarks.random_entries(entries, pools, CONFIG_HASH, replication)
        second = benchmarks.random_entries(entries, pools, CONFIG_HASH, replication)
        assert first == second, replication


def test_two_different_config_hashes_produce_different_nulls():
    """The other half of ADR 061's seeding rule. A fixed constant seed makes
    every config share one null, and it runs without complaint."""
    pools, entries = _pools(), _signal_entries()
    differ = sum(
        benchmarks.random_entries(entries, pools, CONFIG_HASH, r)
        != benchmarks.random_entries(entries, pools, OTHER_HASH, r)
        for r in range(1, 21)
    )
    assert differ == 20


def test_two_replications_of_one_config_differ():
    """A null whose 200 replications are identical has no distribution, and
    its 97.5th percentile is its median."""
    pools, entries = _pools(), _signal_entries()
    first = benchmarks.random_entries(entries, pools, CONFIG_HASH, 1)
    second = benchmarks.random_entries(entries, pools, CONFIG_HASH, 2)
    assert first != second


def test_the_null_matches_the_firing_rate_per_ticker_year():
    """13.2 acceptance, on a fixture where a uniform rate would give a
    visibly different count. AAA fires 12 times in 2018 and 3 in 2019; a
    uniform rate over three ticker-years would give ~5 each."""
    pools, entries = _pools(), _signal_entries()
    drawn = benchmarks.random_entries(entries, pools, CONFIG_HASH, 7)
    counts: dict[tuple[str, int], int] = {}
    for entry in drawn:
        key = (entry.ticker, entry.signal_date.year)
        counts[key] = counts.get(key, 0) + 1
    assert counts == {("AAA", 2018): 12, ("AAA", 2019): 3, ("BBB", 2018): 1}


def test_a_ticker_year_that_never_fired_draws_nothing():
    """A uniform rate would place entries in every ticker-year with a pool.
    The empirical rate does not."""
    pools = {**_pools(), ("CCC", 2018): _dates(200, date(2018, 1, 1))}
    drawn = benchmarks.random_entries(_signal_entries(), pools, CONFIG_HASH, 3)
    assert not any(e.ticker == "CCC" for e in drawn)


def test_a_pool_smaller_than_the_firing_count_draws_the_whole_pool():
    """Fewer, never with replacement — that would enter the same day twice."""
    pools = {("AAA", 2018): _dates(4, date(2018, 1, 1))}
    entries = [Entry("AAA", date(2018, 1, 1)) for _ in range(12)]
    drawn = benchmarks.random_entries(entries, pools, CONFIG_HASH, 1)
    assert len(drawn) == 4
    assert len({e.signal_date for e in drawn}) == 4


def test_a_ticker_year_with_no_pool_at_all_is_skipped():
    drawn = benchmarks.random_entries(_signal_entries(), {}, CONFIG_HASH, 1)
    assert drawn == ()


def test_firing_rate_counts_by_ticker_and_year():
    counts = benchmarks.firing_rate_by_ticker_year(_signal_entries())
    assert counts == {("AAA", 2018): 12, ("AAA", 2019): 3, ("BBB", 2018): 1}


def test_eligible_days_exclude_the_tail_where_no_exit_could_resolve():
    """A draw on the last two bars fills with no forward window and gets
    dropped in `build_positions`, silently shrinking that replication."""
    window = _window(10)
    panels = {"AAA": _panel("AAA", window, [100.0] * 10)}
    window = _window(10, members=[("AAA",)] * 10, dates=window.dates)
    pools = benchmarks.eligible_days(panels, window, CFG)
    assert len(pools[("AAA", 2020)]) == 8


def test_eligible_days_exclude_days_outside_the_trade_universe():
    window = _window(10, members=[()] * 5 + [("AAA",)] * 5)
    panels = {"AAA": _panel("AAA", window, [100.0] * 10)}
    pools = benchmarks.eligible_days(panels, window, CFG)
    assert set(pools[("AAA", 2020)]) == set(window.dates[5:8])


# --------------------------------------------------------------------------
# Trim signals (13.3)
# --------------------------------------------------------------------------


def _trim_events(window, signal_type, k_full):
    return pd.DataFrame(
        {
            "ticker": ["AAA"],
            "signal_date": [window.dates[1]],
            "signal_type": [signal_type],
            "k_full": [k_full],
        }
    )


def test_a_confluence_high_event_trims_regardless_of_k_full():
    window = _window(5)
    signals = benchmarks.trim_signals(
        _trim_events(window, "confluence_high", 10.0), pd.DataFrame(), window, BM
    )
    assert signals == {"AAA": [(1, "trim")]}


def test_a_high_k_full_event_trims_even_without_confluence():
    window = _window(5)
    signals = benchmarks.trim_signals(
        _trim_events(window, "bb_upper_touch", 85.0), pd.DataFrame(), window, BM
    )
    assert signals == {"AAA": [(1, "trim")]}


def test_the_trim_threshold_comes_from_config_not_a_literal():
    """13.3 rule: the `%K` threshold is a `BenchmarkParams` field, so moving
    it moves the trim rule. A literal 80.0 would ignore this."""
    window = _window(5)
    events = _trim_events(window, "bb_upper_touch", 70.0)
    assert benchmarks.trim_signals(events, pd.DataFrame(), window, BM) == {}
    lenient = BenchmarkParams(trim_stoch_threshold=65.0)
    assert benchmarks.trim_signals(events, pd.DataFrame(), window, lenient) == {
        "AAA": [(1, "trim")]
    }


def test_the_trim_threshold_is_independent_of_the_exit_stochastic_threshold():
    """ADR 092's pattern: one is exit policy for an open sleeve position,
    the other is a trim rule for a held core one. Sweeping the exit must not
    move the trim rule."""
    assert BM.trim_stoch_threshold == CFG.exits.exit_stoch_threshold
    window = _window(5)
    events = _trim_events(window, "bb_upper_touch", 70.0)
    swept = Config(exits=type(CFG.exits)(exit_stoch_threshold=65.0))
    assert swept.exits.exit_stoch_threshold == 65.0
    assert benchmarks.trim_signals(events, pd.DataFrame(), window, BM) == {}


def test_a_confluence_low_event_becomes_a_redeploy():
    window = _window(5)
    redeploy = pd.DataFrame(
        {
            "ticker": ["AAA"],
            "signal_date": [window.dates[3]],
            "signal_type": ["confluence_low"],
            "k_full": [5.0],
        }
    )
    signals = benchmarks.trim_signals(
        _trim_events(window, "confluence_high", 90.0), redeploy, window, BM
    )
    assert signals == {"AAA": [(1, "trim"), (3, "redeploy")]}


def test_a_plain_oversold_event_is_not_a_redeploy():
    """DESIGN §6.5 names `CONFLUENCE_LOW` specifically, not any long signal."""
    window = _window(5)
    redeploy = pd.DataFrame(
        {
            "ticker": ["AAA"],
            "signal_date": [window.dates[3]],
            "signal_type": ["stoch_oversold"],
            "k_full": [5.0],
        }
    )
    assert benchmarks.trim_signals(pd.DataFrame(), redeploy, window, BM) == {}


def test_a_signal_on_a_day_outside_the_calendar_is_dropped():
    window = _window(5)
    events = pd.DataFrame(
        {
            "ticker": ["AAA"],
            "signal_date": [date(1999, 1, 4)],
            "signal_type": ["confluence_high"],
            "k_full": [90.0],
        }
    )
    assert benchmarks.trim_signals(events, pd.DataFrame(), window, BM) == {}


# --------------------------------------------------------------------------
# DCA schedules (13.4)
# --------------------------------------------------------------------------


def test_month_starts_are_the_first_trading_day_of_each_month():
    dates = (date(2020, 1, 2), date(2020, 1, 31), date(2020, 2, 3), date(2020, 3, 2))
    assert benchmarks.month_start_indices(dates) == (0, 2, 3)


def test_the_train_split_takes_n_from_its_own_realized_count():
    """The window that *defines* the historical rate realizes it, so `N` is
    the realized count there and no database read is needed — asserted by
    passing `None` for the engine, which would raise if it were touched."""
    window = _window(5)
    assert benchmarks.expected_deployment_days(None, CONFIG_HASH, CFG, window, 42) == 42


def test_a_zero_signal_window_still_yields_a_usable_tranche_count():
    """`N = 0` would divide by zero. One deployment is the floor."""
    window = _window(5)
    assert benchmarks.expected_deployment_days(None, CONFIG_HASH, CFG, window, 0) == 1


def test_signal_days_are_distinct_calendar_days_not_events():
    """Six names firing on one day is one deployment day for DCA. Counting
    events instead would make `N` the event count and shrink every
    tranche."""
    positions = [
        core_arms.Position("AAA", Side.LONG, 3, 4, 1.0, 1.0, (0.0, 0.0)),
        core_arms.Position("BBB", Side.LONG, 3, 4, 1.0, 1.0, (0.0, 0.0)),
        core_arms.Position("CCC", Side.LONG, 7, 8, 1.0, 1.0, (0.0, 0.0)),
    ]
    assert benchmarks.signal_day_indices(positions) == (3, 7)


# --------------------------------------------------------------------------
# Core purchases for the wash-sale test (13.5)
# --------------------------------------------------------------------------


def test_a_core_purchase_is_recorded_on_the_day_a_ticker_joins():
    window = _window(4, members=[("AAA",), ("AAA",), ("AAA", "BBB"), ("AAA", "BBB")])
    panels = {"AAA": None, "BBB": None}
    purchases = benchmarks.core_purchases(window, panels)
    assert purchases == [("AAA", window.dates[0]), ("BBB", window.dates[2])]


def test_a_ticker_rejoining_the_universe_is_bought_again():
    """Two stints, two purchases. One purchase would understate the
    wash-sale exposure of a name that left and came back."""
    window = _window(4, members=[("AAA",), (), ("AAA",), ("AAA",)])
    purchases = benchmarks.core_purchases(window, {"AAA": None})
    assert purchases == [("AAA", window.dates[0]), ("AAA", window.dates[2])]


def test_a_universe_member_with_no_panel_is_not_a_purchase():
    window = _window(2, members=[("AAA", "ZZZ")] * 2)
    assert benchmarks.core_purchases(window, {"AAA": None}) == [("AAA", window.dates[0])]


def _tax_trade(ticker, entry, exit_, pnl):
    return core_arms.Trade(
        ticker=ticker,
        side=Side.LONG,
        entry_date=entry,
        exit_date=exit_,
        entry_price=100.0,
        exit_price=100.0,
        realized_return=pnl / 1000.0,
        notional=1000.0,
        pnl=pnl,
    )


def test_the_arms_own_re_entry_counts_as_a_wash_sale_purchase():
    """ADR 032 says "including the core position", not "only". The sleeve
    buying back a name it just took a loss in is the textbook wash sale, and
    passing only the core purchases would miss every one of them."""
    trades = [
        _tax_trade("AAA", date(2020, 3, 2), date(2020, 3, 6), -500.0),
        _tax_trade("AAA", date(2020, 3, 16), date(2020, 3, 20), 100.0),
    ]
    row = benchmarks._tax_row(trades, [], -0.04, BM)
    assert row["wash_sale_flagged"] is True


def test_a_trade_never_flags_itself_through_the_arms_own_purchase_list():
    """Its own entry is in the list now, so the self-exclusion in
    `wash_sale_flags` is what stops a five-day hold flagging every loss."""
    trades = [_tax_trade("AAA", date(2020, 3, 2), date(2020, 3, 6), -500.0)]
    row = benchmarks._tax_row(trades, [], -0.05, BM)
    assert row["wash_sale_flagged"] is False


def test_a_core_purchase_still_flags_on_its_own():
    """13.5 acceptance survives the change: the core position alone triggers
    the flag where the sleeve alone would not."""
    trades = [_tax_trade("AAA", date(2020, 3, 2), date(2020, 3, 6), -500.0)]
    row = benchmarks._tax_row(trades, [("AAA", date(2020, 3, 25))], -0.05, BM)
    assert row["wash_sale_flagged"] is True


# --------------------------------------------------------------------------
# Row shaping
# --------------------------------------------------------------------------


def test_every_written_row_carries_run_id_and_git_sha():
    """Invariant 6."""
    rows = [
        benchmarks.BenchmarkRow(core_arms.ARM_SIGNAL, {"total_ret": 0.1}),
        benchmarks.BenchmarkRow(core_arms.ARM_RANDOM, {"total_ret": 0.05}, replication=1),
    ]
    frame = benchmarks._to_frame(rows, CONFIG_HASH, "train", "run-1", "abc123")
    assert (frame["run_id"] == "run-1").all()
    assert (frame["git_sha"] == "abc123").all()
    assert list(frame.columns) == benchmarks.BENCHMARK_COLUMNS


def test_the_replication_column_is_null_on_every_summary_arm():
    """DESIGN §6.10: `replication` is null except for the random-entry arm.
    A populated value elsewhere would make the null's distribution query
    pick up rows that are not part of it."""
    rows = [
        benchmarks.BenchmarkRow(core_arms.ARM_BUY_HOLD, {}),
        benchmarks.BenchmarkRow(core_arms.ARM_SIGNAL, {}),
        benchmarks.BenchmarkRow(core_arms.ARM_TRIM, {}),
        benchmarks.BenchmarkRow(core_arms.ARM_RANDOM, {}, replication=1),
    ]
    frame = benchmarks._to_frame(rows, CONFIG_HASH, "train", "run-1", "abc")
    assert frame.loc[frame["arm"] != core_arms.ARM_RANDOM, "replication"].isna().all()
    assert frame.loc[frame["arm"] == core_arms.ARM_RANDOM, "replication"].tolist() == [1]


def test_subset_rows_carry_a_distinguishable_era_marker():
    """13.6 acceptance: the subset rows have to be queryable separately from
    the pooled ones."""
    rows = [
        benchmarks.BenchmarkRow(core_arms.ARM_SIGNAL, {}),
        benchmarks.BenchmarkRow(core_arms.ARM_SIGNAL, {}, era=benchmarks.HIGH_BREADTH_ERA),
    ]
    frame = benchmarks._to_frame(rows, CONFIG_HASH, "train", "run-1", "abc")
    assert set(frame["era"]) == {benchmarks.POOLED_ERA, benchmarks.HIGH_BREADTH_ERA}


def test_the_subset_marker_is_not_a_split_key():
    """`split_key` names which dates an event may be measured on, and the
    subset shares its parent's dates exactly. Overloading it would make the
    holdout firewall query ambiguous."""
    rows = [benchmarks.BenchmarkRow(core_arms.ARM_SIGNAL, {}, era=benchmarks.HIGH_BREADTH_ERA)]
    frame = benchmarks._to_frame(rows, CONFIG_HASH, "train", "run-1", "abc")
    assert frame["split_key"].tolist() == ["train"]
