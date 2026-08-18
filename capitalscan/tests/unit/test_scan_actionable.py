"""`cscan scan --actionable` — the OR the other two filters cannot express.

**What it is.** `--confluence-only` and `--bear-close-only` compose as an
AND, so no combination of them produces:

    confluence_low  OR  (confluence_high AND bear_close_above_upper)

A long needs confluence on its own; a short needs confluence *plus* the
close-confirmed reversal. The asymmetry is deliberate and is ADR 016's
position that the short side is not a mirror of the long (user's request,
2026-08-17).

**The side-separation guarantee this leans on.** `core.signals.detect`
builds `fired` as a dict keyed by `Side` and emits one hit per side, so
`signal_types_all` never mixes long and short types. That is why
`--bear-close-only` already excludes every `confluence_low` row without
saying so, and why the second clause above needs no explicit side check.
It is asserted here rather than left incidental, because the filter's
correctness depends on it and nothing else pins it.

`cli.scan` is called as a plain function rather than through Typer's
runner, matching `test_path_cli.py` and `test_backtest_cli.py`.
"""

from __future__ import annotations

import pandas as pd
import pytest
import typer

from capitalscan.core.config import SignalParams
from capitalscan.core.types import Side, SignalType
from capitalscan.jobs import cli, compute

# One row per case, with `signal_types_all` as `compute.scan` returns it.
_ROWS = [
    ("LONGC", ["confluence_low", "bb_lower_touch", "stoch_oversold"], "long"),
    ("LONGB", ["bb_lower_touch"], "long"),
    ("SHRTC", ["confluence_high", "bb_upper_touch", "stoch_overbought"], "short"),
    ("SHRTX", ["bear_close_above_upper", "confluence_high", "bb_upper_touch"], "short"),
    ("SHRTR", ["bear_close_above_upper", "bb_upper_touch"], "short"),
]


@pytest.fixture
def stub_scan(monkeypatch, tmp_path):
    frame = pd.DataFrame(
        [
            {
                "ticker": t,
                "signal_date": pd.Timestamp("2026-08-05").date(),
                "signal_type": types[0],
                "signal_types_all": types,
                "signal_strength": len(types),
                "side": side,
                "bb_upper": 105.0,
                "bb_upper_sameday": 106.0,
                "bb_mid": 100.0,
                "bb_lower": 95.0,
                "bb_pctb": 0.5,
                "k_full": 50.0,
                "k_fast": 50.0,
                "k_cross_up": False,
                "k_cross_down": False,
                "dd_bucket": "0-10",
                "trend_state": "above_sma200",
            }
            for t, types, side in _ROWS
        ]
    )
    monkeypatch.setattr(compute, "scan", lambda **kw: frame.copy())
    monkeypatch.chdir(tmp_path)
    return frame


def _scan(**kw):
    """`cli.scan` with every option named.

    Called as a plain function, Typer does not bind defaults: any omitted
    parameter arrives as an `OptionInfo` object, which is truthy and is not
    a string, so `date_cls.fromisoformat(date_)` raises `TypeError`. Naming
    all of them is what the other CLI test modules do for the same reason.
    """
    args = dict(
        ticker=None,
        universe="trade",
        start=None,
        end=None,
        date_=None,
        confluence_only=False,
        bear_close_only=False,
        actionable=False,
    )
    args.update(kw)
    return cli.scan(**args)


def _tickers_from_csv(tmp_path) -> list[str]:
    (csv,) = sorted((tmp_path / "reports").glob("*.csv"))
    return sorted(pd.read_csv(csv)["Ticker"].tolist())


def test_actionable_keeps_longs_on_confluence_alone(stub_scan, tmp_path):
    _scan(actionable=True)
    assert "LONGC" in _tickers_from_csv(tmp_path)


def test_actionable_drops_a_long_band_touch_without_confluence(stub_scan, tmp_path):
    _scan(actionable=True)
    assert "LONGB" not in _tickers_from_csv(tmp_path)


def test_actionable_drops_a_short_confluence_with_no_reversal(stub_scan, tmp_path):
    """The whole point of the flag. `SHRTC` is a perfectly good
    `confluence_high` and is excluded because nothing confirmed it."""
    _scan(actionable=True)
    assert "SHRTC" not in _tickers_from_csv(tmp_path)


def test_actionable_keeps_a_short_confluence_that_a_reversal_confirms(stub_scan, tmp_path):
    _scan(actionable=True)
    assert "SHRTX" in _tickers_from_csv(tmp_path)


def test_actionable_drops_a_bare_reversal_with_no_confluence(stub_scan, tmp_path):
    """`bear_close_above_upper` fires without any stochastic extreme, so on
    its own it is a detection, not a call to act. `--bear-close-only`
    remains the way to see those."""
    _scan(actionable=True)
    assert "SHRTR" not in _tickers_from_csv(tmp_path)


def test_actionable_selects_exactly_the_two_intended_rows(stub_scan, tmp_path):
    """The set, not just its members — a filter that is too *wide* passes
    every membership test above."""
    _scan(actionable=True)
    assert _tickers_from_csv(tmp_path) == ["LONGC", "SHRTX"]


def test_actionable_is_not_the_same_set_as_either_existing_filter(stub_scan, tmp_path):
    """Guards against a rewrite that quietly collapses `--actionable` into
    one of the two flags it exists to differ from."""
    _scan(confluence_only=True)
    confluence = _tickers_from_csv(tmp_path)
    assert confluence != ["LONGC", "SHRTX"]
    assert "SHRTC" in confluence


@pytest.mark.parametrize("other", ["confluence_only", "bear_close_only"])
def test_actionable_refuses_to_compose_with_the_and_filters(stub_scan, other):
    """Intersecting an OR-of-two with an AND-of-two-others yields a set
    nobody can state in a sentence. Exit 2, not a silent surprise."""
    with pytest.raises(typer.Exit) as exc:
        _scan(actionable=True, **{other: True})
    assert exc.value.exit_code == 2


def test_the_two_and_filters_still_compose_with_each_other(stub_scan, tmp_path):
    """`--actionable`'s guard must not have broken the documented AND."""
    _scan(confluence_only=True, bear_close_only=True)
    assert _tickers_from_csv(tmp_path) == ["SHRTX"]


# ---------------------------------------------------------------------------
# The guarantee `--actionable`'s short clause depends on
# ---------------------------------------------------------------------------


def test_a_hit_never_mixes_long_and_short_types():
    """`detect` emits one hit per side, so `bear_close_above_upper` and
    `confluence_low` can never share a row.

    Measured on 2,541 bear rows in the live population: zero carry
    `confluence_low`, and they span exactly one `side`. Asserted here so it
    stays a property of the code rather than an observation about one
    table.
    """
    bar = pd.Series(
        {"open": 101.0, "high": 106.0, "low": 94.0, "close": 100.0, "ticker": "TSM"},
        name=pd.Timestamp("2026-08-05"),
    )
    ind = pd.Series(
        {
            "bb_lower": 95.0,
            "bb_mid": 100.0,
            "bb_upper": 105.0,
            "bb_pctb": 0.5,
            "k_full": 15.0,
            "k_fast": 15.0,
            "d_full": 15.0,
            "atr_14": 2.0,
        }
    )
    from capitalscan.core import signals as sig

    # `detect` reads the close-confirmed flag off the *bar*, not from a
    # keyword: `core/signals.py` attaches it under `BEAR_CLOSE_FIELD` and
    # `_bear_close_flag` defaults it to False when absent. Passing it any
    # other way would be testing a signature that does not exist.
    bar[sig.BEAR_CLOSE_FIELD] = True
    hits = sig.detect(bar, ind, SignalParams())

    assert len(hits) == 2, "a bar touching both bands must emit one hit per side"
    for hit in hits:
        types = set(hit.signal_types_all)
        if hit.side is Side.SHORT:
            assert SignalType.CONFLUENCE_LOW not in types
            assert SignalType.BB_LOWER_TOUCH not in types
        else:
            assert SignalType.BEAR_CLOSE_ABOVE_UPPER not in types
            assert SignalType.CONFLUENCE_HIGH not in types
