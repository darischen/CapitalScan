"""Tests for `research/candidates.py` — DESIGN §5.2 steps 3-5 (Session 9, Task 3).

Controller Ruling C3: pairing bars to indicators is date-based (the latest
indicator row strictly before the bar's own date), never positional. See
`capitalscan.jobs.compute.run_events` (compute.py:731-738) for the shipped
precedent this module follows.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from capitalscan.core.config import SignalParams, SplitParams
from capitalscan.research.candidates import apply_eligibility, debounce, scan_candidates


def _bars(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def _indicators(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


SP = SignalParams()


# ---------------------------------------------------------------------------
# scan_candidates: the t-1 pairing guarantee
# ---------------------------------------------------------------------------


class TestScanCandidatesReadsTMinus1:
    def test_uses_the_prior_dated_indicator_row_not_the_bars_own_date(self):
        """Construct indicators so t-1's row fires a signal and t's own
        (same-date) row would not. The only bar in the frame is dated
        2026-07-30; if `scan_candidates` wrongly paired it with the
        2026-07-30 indicator row (bb_lower=50, k_full=90 — no touch, not
        oversold) it would produce zero candidates. Pairing with the
        2026-07-29 row (bb_lower=95, k_full=15 — touch, oversold) must
        produce a CONFLUENCE_LOW hit instead.
        """
        bars = _bars(
            [
                {
                    "ticker": "TSM",
                    "ts": "2026-07-30",
                    "open": 96.0,
                    "high": 96.0,
                    "low": 94.0,
                    "close": 95.0,
                    "volume": 1_000_000,
                },
            ]
        )
        indicators = _indicators(
            [
                {
                    "ticker": "TSM",
                    "ts": "2026-07-29",
                    "bb_lower": 95.0,
                    "bb_upper": 999.0,
                    "k_full": 15.0,
                },
                {
                    "ticker": "TSM",
                    "ts": "2026-07-30",
                    "bb_lower": 50.0,
                    "bb_upper": 999.0,
                    "k_full": 90.0,
                },
            ]
        )

        candidates, rejects = scan_candidates(bars, indicators, SP)

        assert rejects == []
        assert len(candidates) == 1
        row = candidates.iloc[0]
        assert row["ticker"] == "TSM"
        assert row["signal_type"] == "confluence_low"
        # touch_level must come from the t-1 row's bb_lower (95.0), never
        # the bar's own-date row's bb_lower (50.0) — pinning the value, not
        # just the fact that *a* signal fired.
        assert row["touch_level"] == pytest.approx(95.0)

    def test_a_bar_with_no_prior_indicator_row_produces_nothing(self):
        """The earliest bar in the frame has no t-1 row at all (Ruling C3:
        skip, don't fabricate a pairing)."""
        bars = _bars(
            [
                {
                    "ticker": "TSM",
                    "ts": "2026-07-29",
                    "open": 96.0,
                    "high": 96.0,
                    "low": 94.0,
                    "close": 95.0,
                    "volume": 1_000_000,
                },
            ]
        )
        indicators = _indicators(
            [
                {
                    "ticker": "TSM",
                    "ts": "2026-07-29",
                    "bb_lower": 95.0,
                    "bb_upper": 999.0,
                    "k_full": 15.0,
                },
            ]
        )

        candidates, rejects = scan_candidates(bars, indicators, SP)

        assert candidates.empty
        assert rejects == []

    def test_gap_between_frames_does_not_shift_the_pairing(self):
        """Regression for the failure mode Ruling C3 exists to prevent: an
        indicator gap (ticker under 280 bars, or a write-window narrower
        than the read-window) must not silently shift every later pairing
        by one row. Bar dated 2026-08-03 has no indicator row for
        2026-08-02 (a gap), but the 2026-07-29 row is still the correct,
        nearest-prior pairing. A positional `iloc[i-1]` implementation
        would instead pair this bar with indicators.iloc[0] — coincidentally
        the same row here, so the test also checks a second, later bar
        where positional pairing diverges.
        """
        bars = _bars(
            [
                {
                    "ticker": "TSM",
                    "ts": "2026-07-29",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1_000_000,
                },
                {
                    "ticker": "TSM",
                    "ts": "2026-08-03",
                    "open": 96.0,
                    "high": 96.0,
                    "low": 94.0,
                    "close": 95.0,
                    "volume": 1_000_000,
                },
            ]
        )
        # Only one indicator row exists (the gap), dated before both bars.
        # k_full is neutral (50) so only the band-touch condition, which
        # depends on each bar's own `low`, decides whether a hit fires —
        # isolating the pairing question from the stochastic condition.
        indicators = _indicators(
            [
                {
                    "ticker": "TSM",
                    "ts": "2026-07-28",
                    "bb_lower": 95.0,
                    "bb_upper": 999.0,
                    "k_full": 50.0,
                },
            ]
        )

        candidates, rejects = scan_candidates(bars, indicators, SP)

        # Both bars pair with the single 2026-07-28 row (date-based rule).
        # A positional `iloc[i-1]` scheme would skip i=0 (the 2026-07-29
        # bar) and pair the 2026-08-03 bar with... nothing, since the
        # indicators frame has only one row at position 0 and there is no
        # position 1 to feed i=1. That silently drops the second bar's
        # signal instead of pairing it correctly with 2026-07-28.
        assert rejects == []
        assert len(candidates) == 1  # only the 2026-08-03 bar touches/oversolds
        assert candidates.iloc[0]["signal_date"] == date(2026, 8, 3)


class TestScanCandidatesNullIndicators:
    def test_a_null_required_field_drops_the_row_and_records_a_reject(self):
        """Invariant 4: never fill a null, drop the row and log why."""
        bars = _bars(
            [
                {
                    "ticker": "TSM",
                    "ts": "2026-07-30",
                    "open": 96.0,
                    "high": 96.0,
                    "low": 94.0,
                    "close": 95.0,
                    "volume": 1_000_000,
                },
            ]
        )
        indicators = _indicators(
            [
                {
                    "ticker": "TSM",
                    "ts": "2026-07-29",
                    "bb_lower": np.nan,
                    "bb_upper": 999.0,
                    "k_full": 15.0,
                },
            ]
        )

        candidates, rejects = scan_candidates(bars, indicators, SP)

        assert candidates.empty
        assert len(rejects) == 1
        assert rejects[0]["ticker"] == "TSM"
        assert rejects[0]["signal_date"] == date(2026, 7, 30)
        assert "bb_lower" in rejects[0]["reason"]

    def test_the_null_row_is_never_filled_or_substituted(self):
        """A stochastic-only condition could theoretically still fire on
        `k_full` alone even with `bb_lower` null (the null doesn't block
        `_breach`, it just always returns False). This asserts the scan
        still refuses to produce *any* candidate for that bar rather than
        emitting a partial one — matching `run_events`'s blanket
        required-field check (compute.py:739-743)."""
        bars = _bars(
            [
                {
                    "ticker": "TSM",
                    "ts": "2026-07-30",
                    "open": 96.0,
                    "high": 96.0,
                    "low": 200.0,
                    "close": 200.0,
                    "volume": 1_000_000,
                },
            ]
        )
        indicators = _indicators(
            [
                # k_full alone would fire STOCH_OVERSOLD if it were checked.
                {
                    "ticker": "TSM",
                    "ts": "2026-07-29",
                    "bb_lower": np.nan,
                    "bb_upper": np.nan,
                    "k_full": 5.0,
                },
            ]
        )

        candidates, rejects = scan_candidates(bars, indicators, SP)

        assert candidates.empty
        assert len(rejects) == 1


# ---------------------------------------------------------------------------
# apply_eligibility
# ---------------------------------------------------------------------------


def _candidate_row(**overrides) -> dict:
    row = {
        "ticker": "TSM",
        "signal_date": date(2026, 7, 30),
        "signal_type": "confluence_low",
        "signal_types_all": ["confluence_low"],
        "signal_strength": 1,
        "side": "long",
        "touch_level": 95.0,
    }
    row.update(overrides)
    return row


class TestApplyEligibility:
    def test_keeps_a_row_inside_the_window_and_in_trade(self):
        candidates = pd.DataFrame([_candidate_row()])
        universe_flags = pd.DataFrame(columns=["ticker", "as_of", "in_trade"])
        sp = SplitParams(event_start="2010-01-01")

        kept, rejects = apply_eligibility(candidates, universe_flags, sp, today=date(2026, 8, 1))

        assert len(kept) == 1
        assert rejects == []

    def test_drops_a_row_before_event_start(self):
        candidates = pd.DataFrame([_candidate_row(signal_date=date(2009, 12, 31))])
        universe_flags = pd.DataFrame(columns=["ticker", "as_of", "in_trade"])
        sp = SplitParams(event_start="2010-01-01")

        kept, rejects = apply_eligibility(candidates, universe_flags, sp, today=date(2026, 8, 1))

        assert kept.empty
        assert len(rejects) == 1
        assert rejects[0]["reason"] == "outside_event_window"

    def test_drops_a_row_after_today(self):
        candidates = pd.DataFrame([_candidate_row(signal_date=date(2026, 8, 2))])
        universe_flags = pd.DataFrame(columns=["ticker", "as_of", "in_trade"])
        sp = SplitParams(event_start="2010-01-01")

        kept, rejects = apply_eligibility(candidates, universe_flags, sp, today=date(2026, 8, 1))

        assert kept.empty
        assert len(rejects) == 1
        assert rejects[0]["reason"] == "outside_event_window"

    def test_drops_a_row_not_in_trade_on_that_date(self):
        candidates = pd.DataFrame([_candidate_row(signal_date=date(2026, 7, 30))])
        universe_flags = pd.DataFrame(
            [{"ticker": "TSM", "as_of": date(2026, 7, 1), "in_trade": False}]
        )
        sp = SplitParams(event_start="2010-01-01")

        kept, rejects = apply_eligibility(candidates, universe_flags, sp, today=date(2026, 8, 1))

        assert kept.empty
        assert len(rejects) == 1
        assert rejects[0]["reason"] == "not_in_trade"

    def test_fail_open_when_no_universe_evaluation_exists_yet(self):
        """v1 semantics from compute.py:624-635: no evaluation on or before
        the date means in-trade defaults True."""
        candidates = pd.DataFrame([_candidate_row(signal_date=date(2026, 7, 30))])
        universe_flags = pd.DataFrame(columns=["ticker", "as_of", "in_trade"])
        sp = SplitParams(event_start="2010-01-01")

        kept, rejects = apply_eligibility(candidates, universe_flags, sp, today=date(2026, 8, 1))

        assert len(kept) == 1
        assert rejects == []

    def test_empty_candidates_returns_empty_frame_and_no_rejects(self):
        candidates = pd.DataFrame(
            columns=[
                "ticker",
                "signal_date",
                "signal_type",
                "signal_types_all",
                "signal_strength",
                "side",
                "touch_level",
            ]
        )
        universe_flags = pd.DataFrame(columns=["ticker", "as_of", "in_trade"])
        sp = SplitParams(event_start="2010-01-01")

        kept, rejects = apply_eligibility(candidates, universe_flags, sp, today=date(2026, 8, 1))

        assert kept.empty
        assert rejects == []


# ---------------------------------------------------------------------------
# debounce
# ---------------------------------------------------------------------------


class TestDebounce:
    def test_collapses_two_lower_bound_touches_on_one_ticker_one_day_to_one_row(self):
        """Two candidate rows sharing (ticker, signal_date, bound=lower) —
        e.g. a duplicate upstream bar row producing two hits for the same
        slot — must collapse to one. `debounce_key` groups on the LONG/
        SHORT side, which maps to Bound.LOWER/Bound.UPPER (core.signals)."""
        candidates = pd.DataFrame(
            [
                _candidate_row(signal_type="bb_lower_touch", side="long"),
                _candidate_row(signal_type="confluence_low", side="long"),
            ]
        )

        out = debounce(candidates)

        assert len(out) == 1

    def test_keeps_a_lower_and_an_upper_touch_on_the_same_day_as_two(self):
        candidates = pd.DataFrame(
            [
                _candidate_row(side="long", touch_level=95.0),
                _candidate_row(side="short", touch_level=105.0, signal_type="bb_upper_touch"),
            ]
        )

        out = debounce(candidates)

        assert len(out) == 2

    def test_keeps_the_same_bound_on_two_different_days_as_two(self):
        candidates = pd.DataFrame(
            [
                _candidate_row(signal_date=date(2026, 7, 30)),
                _candidate_row(signal_date=date(2026, 7, 31)),
            ]
        )

        out = debounce(candidates)

        assert len(out) == 2

    def test_empty_frame_returns_empty_frame(self):
        candidates = pd.DataFrame(
            columns=[
                "ticker",
                "signal_date",
                "signal_type",
                "signal_types_all",
                "signal_strength",
                "side",
                "touch_level",
            ]
        )

        out = debounce(candidates)

        assert out.empty
