"""Tests for `research/harness.py` — DESIGN §5.10, Session 9 Task 10.

Five checks, five test classes. Per CONSTRAINTS.md's "this session has
already hit that exact defect once, in the determinism test": every check
gets a test proving it catches a genuine violation, not only a fixture that
happens to pass clean. `TestNoLookahead` additionally needs a large,
market-shaped fixture (see `_synthetic_bars_with_bands`) because TESTS.md
§3.1's four bounds are calibrated to real drift-vs-range ratios and a
handful of hand-picked rows cannot exercise them meaningfully.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from capitalscan.core.config import Config
from capitalscan.core.types import Side
from capitalscan.research import candidates as research_candidates
from capitalscan.research import harness
from capitalscan.research.harness import run_harness

# ---------------------------------------------------------------------------
# Shared fixtures / builders
# ---------------------------------------------------------------------------


def _bars_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def _event_row(**overrides) -> dict:
    """A minimal, internally-consistent `events` row: `entry_price` is a
    real TOUCH fill (equal to `touch_level`) with `CostParams()` slippage
    (3.0 bps) baked in on top, `exit_price` is a plain TARGET fill, and
    `gross_ret` is the real `realized_return` for those two prices — so an
    unmodified row passes every check, and each test overrides exactly the
    field its own violation needs.
    """
    from capitalscan.core.returns import realized_return

    entry_raw = 95.0
    slip = entry_raw * 3.0 / 1e4  # CostParams().slippage_bps
    entry_price = entry_raw + slip  # long: adverse = higher
    exit_price = 98.8
    row = {
        "ticker": "TSM",
        "signal_date": date(2026, 1, 8),
        "signal_type": "bb_lower_touch",
        "side": "long",
        "entry_kind": "touch",
        "entry_date": date(2026, 1, 8),
        "entry_price": entry_price,
        "exit_date": date(2026, 1, 9),
        "exit_price": exit_price,
        "exit_reason": "target",
        "gross_ret": realized_return(entry_price, exit_price, Side.LONG),
        "cluster_id": 1,
        "seq_in_cluster": 1,
        "is_cluster_head": True,
        "days_since_head": 0,
    }
    row.update(overrides)
    return row


def _events(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _daily_bars(ticker: str, day_specs: dict) -> pd.DataFrame:
    """`day_specs`: `{date: (open, high, low, close)}`."""
    rows = []
    for d, (o, h, low, c) in day_specs.items():
        rows.append(
            {
                "ticker": ticker,
                "ts": d,
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "adj_close": c,
                "volume": 1_000_000,
            }
        )
    return _bars_frame(rows)


CONFIG = Config()  # default ExitParams.max_hold_days=5, CostParams.slippage_bps=3.0


# ---------------------------------------------------------------------------
# Entry sanity
# ---------------------------------------------------------------------------


class TestEntrySanity:
    def test_passes_when_every_entry_price_is_within_its_bar_range(self):
        bars = {
            "TSM": _daily_bars(
                "TSM",
                {
                    date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5),
                    date(2026, 1, 9): (96.5, 99.0, 96.0, 98.8),
                },
            )
        }
        events = _events([_event_row()])

        report = run_harness(events, bars, CONFIG)

        assert report.entry_sanity.passed
        assert report.entry_sanity.violations == []

    def test_catches_an_entry_price_planted_outside_the_bar_range(self):
        """Genuine violation: `entry_price` set to a value no combination of
        `[low, high]` and slippage reversal can explain — 150 against a bar
        whose high is 97."""
        bars = {
            "TSM": _daily_bars(
                "TSM",
                {date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5)},
            )
        }
        events = _events([_event_row(entry_price=150.0)])

        report = run_harness(events, bars, CONFIG)

        assert not report.entry_sanity.passed
        assert len(report.entry_sanity.violations) == 1
        assert report.entry_sanity.violations[0]["reason"] == "entry_price_outside_bar_range"

    def test_next_open_entry_is_checked_against_the_fill_bar_not_the_signal_bar(self):
        """A NEXT_OPEN entry fills on t+1 — CONSTRAINTS.md item 3. Planting
        an entry price that is valid for the *signal* bar's range but
        invalid for the *fill* bar's range must still be caught, proving
        the check reads `entry_date`, not `signal_date`."""
        bars = {
            "TSM": _daily_bars(
                "TSM",
                {
                    date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5),  # signal bar
                    date(2026, 1, 9): (150.0, 151.0, 149.0, 150.5),  # fill bar, far away
                },
            )
        }
        entry_raw = 96.5  # inside the signal bar's range, NOT the fill bar's
        slip = entry_raw * 3.0 / 1e4
        events = _events(
            [
                _event_row(
                    entry_kind="next_open",
                    entry_date=date(2026, 1, 9),
                    entry_price=entry_raw + slip,
                )
            ]
        )

        report = run_harness(events, bars, CONFIG)

        assert not report.entry_sanity.passed

    def test_null_entry_price_is_skipped_not_flagged(self):
        """An unfilled position (pre-2024 hourly kind, or terminal NEXT_OPEN)
        carries a null `entry_price` — invariant 4 forbids fabricating one
        to check, so it must not count as a violation."""
        bars = {"TSM": _daily_bars("TSM", {date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5)})}
        events = _events([_event_row(entry_price=float("nan"), entry_date=None)])

        report = run_harness(events, bars, CONFIG)

        assert report.entry_sanity.passed
        assert report.entry_sanity.detail["n_priced"] == 0
        assert report.entry_sanity.detail["n_validated"] == 0

    def test_ticker_entirely_absent_from_bars_by_ticker_is_a_violation(self):
        """Same condition `no_bar_for_entry_date` already covers when a
        ticker is present but the specific date is missing — this is the
        coarser case, a ticker with no entry in `bars_by_ticker` at all."""
        events = _events([_event_row()])

        report = run_harness(events, {}, CONFIG)

        assert not report.entry_sanity.passed
        assert report.entry_sanity.violations[0]["reason"] == "no_bar_for_entry_date"


# ---------------------------------------------------------------------------
# Exit sanity
# ---------------------------------------------------------------------------


class TestExitSanity:
    def test_passes_when_every_exit_price_is_within_its_bar_range(self):
        bars = {
            "TSM": _daily_bars(
                "TSM",
                {
                    date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5),
                    date(2026, 1, 9): (96.5, 99.0, 96.0, 98.8),
                },
            )
        }
        events = _events([_event_row()])

        report = run_harness(events, bars, CONFIG)

        assert report.exit_sanity.passed

    def test_catches_an_exit_price_planted_outside_the_bar_range(self):
        """Unlike entry, exit carries no slippage (`core/exits.py` prices
        exits directly off bar levels) — 200 against a bar whose high is
        99 has no cost-model explanation and must be flagged."""
        bars = {
            "TSM": _daily_bars(
                "TSM",
                {
                    date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5),
                    date(2026, 1, 9): (96.5, 99.0, 96.0, 98.8),
                },
            )
        }
        events = _events([_event_row(exit_price=200.0)])

        report = run_harness(events, bars, CONFIG)

        assert not report.exit_sanity.passed
        assert report.exit_sanity.violations[0]["reason"] == "exit_price_outside_bar_range"

    def test_null_exit_price_is_skipped_not_flagged(self):
        bars = {"TSM": _daily_bars("TSM", {date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5)})}
        events = _events([_event_row(exit_price=float("nan"), exit_date=None)])

        report = run_harness(events, bars, CONFIG)

        assert report.exit_sanity.passed
        assert report.exit_sanity.detail["n_priced"] == 0
        assert report.exit_sanity.detail["n_validated"] == 0

    def test_ticker_entirely_absent_from_bars_by_ticker_is_a_violation(self):
        events = _events([_event_row()])

        report = run_harness(events, {}, CONFIG)

        assert not report.exit_sanity.passed
        assert report.exit_sanity.violations[0]["reason"] == "no_bar_for_exit_date"


# ---------------------------------------------------------------------------
# Return identity
# ---------------------------------------------------------------------------


class TestReturnIdentity:
    def test_passes_when_gross_ret_matches_realized_return(self):
        bars = {"TSM": _daily_bars("TSM", {date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5)})}
        events = _events([_event_row()])

        report = run_harness(events, bars, CONFIG)

        assert report.return_identity.passed

    def test_catches_a_gross_ret_that_disagrees_with_realized_return(self):
        bars = {"TSM": _daily_bars("TSM", {date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5)})}
        events = _events([_event_row(gross_ret=0.5)])  # nowhere near the real ~3.9%

        report = run_harness(events, bars, CONFIG)

        assert not report.return_identity.passed
        assert report.return_identity.violations[0]["reason"] == "gross_ret_mismatch"

    def test_both_sides_null_is_not_a_violation(self):
        """An unresolved position: `entry_price`/`exit_price`/`gross_ret`
        are all NaN. `realized_return` recomputes NaN too, so this is the
        honest matching answer, not a mismatch (module docstring's
        argument against a NaN-always-passes OR NaN-always-fails
        shortcut)."""
        bars = {"TSM": _daily_bars("TSM", {date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5)})}
        events = _events(
            [
                _event_row(
                    entry_price=float("nan"),
                    entry_date=None,
                    exit_price=float("nan"),
                    exit_date=None,
                    gross_ret=float("nan"),
                )
            ]
        )

        report = run_harness(events, bars, CONFIG)

        assert report.return_identity.passed

    def test_one_side_null_the_other_not_is_a_violation(self):
        """`entry_price` is null (position never filled), so the recomputed
        `gross_ret` is NaN — but the row's stored `gross_ret` claims a real
        0.04 return anyway. A recomputation that disagrees about whether
        the position even resolved is exactly the case a naive NaN-tolerant
        comparison would hide."""
        bars = {"TSM": _daily_bars("TSM", {date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5)})}
        events = _events(
            [_event_row(entry_price=float("nan"), entry_date=None, gross_ret=0.04)]
        )

        report = run_harness(events, bars, CONFIG)

        assert not report.return_identity.passed
        assert report.return_identity.violations[0]["reason"] == "gross_ret_mismatch"


# ---------------------------------------------------------------------------
# Non-overlap
# ---------------------------------------------------------------------------


class TestNonOverlap:
    def test_passes_when_cluster_heads_are_further_apart_than_max_hold_days(self):
        trading_days = {
            date(2026, 1, d): (100.0, 101.0, 99.0, 100.5) for d in range(5, 26)
        }
        bars = {"TSM": _daily_bars("TSM", trading_days)}
        events = _events(
            [
                _event_row(signal_date=date(2026, 1, 5), cluster_id=1, is_cluster_head=True),
                _event_row(signal_date=date(2026, 1, 20), cluster_id=2, is_cluster_head=True),
            ]
        )

        report = run_harness(events, bars, CONFIG)

        assert report.non_overlap.passed

    def test_catches_two_cluster_heads_whose_windows_overlap(self):
        """Genuine violation: two `is_cluster_head=True` rows on the same
        ticker only 2 trading bars apart, well inside
        `ExitParams().max_hold_days == 5` — a tagger bug (or a caller that
        marked both heads by hand) that `tag_clusters` itself would never
        produce."""
        trading_days = {
            date(2026, 1, d): (100.0, 101.0, 99.0, 100.5) for d in range(5, 12)
        }
        bars = {"TSM": _daily_bars("TSM", trading_days)}
        events = _events(
            [
                _event_row(signal_date=date(2026, 1, 5), cluster_id=1, is_cluster_head=True),
                _event_row(signal_date=date(2026, 1, 7), cluster_id=2, is_cluster_head=True),
            ]
        )

        report = run_harness(events, bars, CONFIG)

        assert not report.non_overlap.passed
        v = report.non_overlap.violations[0]
        assert v["reason"] == "cluster_head_windows_overlap"
        assert v["ticker"] == "TSM"

    def test_non_head_rows_are_ignored(self):
        """A cluster's non-head member overlapping the head is expected
        (that is what a cluster *is*) and must not be flagged."""
        trading_days = {
            date(2026, 1, d): (100.0, 101.0, 99.0, 100.5) for d in range(5, 12)
        }
        bars = {"TSM": _daily_bars("TSM", trading_days)}
        events = _events(
            [
                _event_row(signal_date=date(2026, 1, 5), cluster_id=1, is_cluster_head=True),
                _event_row(
                    signal_date=date(2026, 1, 7),
                    cluster_id=1,
                    is_cluster_head=False,
                    seq_in_cluster=2,
                ),
            ]
        )

        report = run_harness(events, bars, CONFIG)

        assert report.non_overlap.passed

    def test_a_long_head_and_a_short_head_overlapping_is_not_a_violation(self):
        """Review Finding 1: `tag_clusters` keys clusters by `(ticker,
        side)` (Ruling C5) — a long cluster and a short cluster on one
        ticker are different positions and never merge, even on identical
        dates. A stock touching both bands in a choppy window legitimately
        produces a long head and a short head with overlapping windows;
        that is normal mega-cap behavior the tagger itself produces, not a
        defect, and grouping this check by ticker alone (the pre-fix
        version) incorrectly flagged it."""
        trading_days = {
            date(2026, 1, d): (100.0, 101.0, 99.0, 100.5) for d in range(5, 12)
        }
        bars = {"TSM": _daily_bars("TSM", trading_days)}
        events = _events(
            [
                _event_row(
                    signal_date=date(2026, 1, 5),
                    side="long",
                    cluster_id=1,
                    is_cluster_head=True,
                ),
                _event_row(
                    signal_date=date(2026, 1, 6),
                    side="short",
                    cluster_id=2,
                    is_cluster_head=True,
                ),
            ]
        )

        report = run_harness(events, bars, CONFIG)

        assert report.non_overlap.passed

    def test_same_side_overlap_is_still_caught_after_the_ticker_side_fix(self):
        """The converse of the test above: grouping by `(ticker, side)`
        must not accidentally stop catching the same-side violation
        `test_catches_two_cluster_heads_whose_windows_overlap` already
        covers — restated here explicitly on `side="long"` to make the fix
        direction unambiguous."""
        trading_days = {
            date(2026, 1, d): (100.0, 101.0, 99.0, 100.5) for d in range(5, 12)
        }
        bars = {"TSM": _daily_bars("TSM", trading_days)}
        events = _events(
            [
                _event_row(
                    signal_date=date(2026, 1, 5),
                    side="long",
                    cluster_id=1,
                    is_cluster_head=True,
                ),
                _event_row(
                    signal_date=date(2026, 1, 7),
                    side="long",
                    cluster_id=2,
                    is_cluster_head=True,
                ),
            ]
        )

        report = run_harness(events, bars, CONFIG)

        assert not report.non_overlap.passed
        assert report.non_overlap.violations[0]["reason"] == "cluster_head_windows_overlap"

    def test_a_ticker_with_heads_but_no_bar_data_is_a_violation_not_a_silent_skip(self):
        """Review Finding 2: a ticker with `is_cluster_head` events but no
        entry in `bars_by_ticker` at all used to be silently skipped —
        `non_overlap.passed` could read `True` while this ticker was never
        actually gap-tested. Matches `_check_entry_sanity`/
        `_check_exit_sanity`'s `no_bar_for_*` precedent for the identical
        missing-bar-data condition."""
        events = _events(
            [
                _event_row(signal_date=date(2026, 1, 5), cluster_id=1, is_cluster_head=True),
                _event_row(signal_date=date(2026, 1, 7), cluster_id=2, is_cluster_head=True),
            ]
        )

        report = run_harness(events, {}, CONFIG)

        assert not report.non_overlap.passed
        reasons = {v["reason"] for v in report.non_overlap.violations}
        assert "no_bars_for_ticker" in reasons
        assert report.non_overlap.detail["n_groups_no_bars"] == 1


# ---------------------------------------------------------------------------
# No look-ahead
# ---------------------------------------------------------------------------


def _synthetic_bars_with_bands(
    seed: int = 7,
    n_days: int = 300,
    n_tickers: int = 3,
    theta: float = 0.15,
    sigma: float = 2.0,
    bb_window: int = 5,
    bb_mult: float = 3.5,
    intraday_vol: float = 0.012,
) -> dict[str, pd.DataFrame]:
    """A synthetic OHLC + Bollinger-band universe shaped like real mega-cap
    data closely enough to exercise TESTS.md §3.1's four bounds: an
    Ornstein-Uhlenbeck price path (mean-reverting, so the band is touched
    intermittently rather than during one long drawdown run) under a
    rolling band computed the normal way (`core.config.IndicatorParams`'s
    own `bb_window`/`bb_std` shape, just narrower here so the band moves
    fast enough — day-to-day band drift versus a bar's own range is the
    ratio TESTS.md §3.1 says drives the whole test, not the specific
    window length), with the stochastic conditions held neutral (`k_full
    = 50.0` throughout, `SignalParams.stoch_oversold/overbought` both at
    their defaults of 20/80) so only the band-touch condition — the one
    TESTS.md §3.1's own worked example is about — decides the event set.

    These parameters were tuned empirically (not derived from a formula)
    against `_event_set`/`_jaccard` directly until all four TESTS.md §3.1
    bounds held with margin; see the Task 10 report for the search. A
    fixed seed keeps the result reproducible.
    """
    rng = np.random.default_rng(seed)
    out: dict[str, pd.DataFrame] = {}
    dates = pd.bdate_range("2015-01-05", periods=n_days)
    for i in range(n_tickers):
        ticker = f"SYN{i}"
        price = 100.0
        closes = []
        for _ in range(n_days):
            price = price + theta * (100.0 - price) + sigma * rng.normal()
            closes.append(price)
        closes = np.array(closes)
        opens = closes + rng.normal(0, sigma * 0.3, n_days)
        extra = np.abs(rng.normal(0, intraday_vol, n_days)) * closes
        highs = np.maximum(opens, closes) + extra
        lows = np.minimum(opens, closes) - extra

        frame = pd.DataFrame(
            {
                "ticker": ticker,
                "ts": dates,
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "adj_close": closes,
                "volume": 1_000_000,
            }
        )
        roll = frame["close"].rolling(bb_window)
        mid = roll.mean()
        std = roll.std(ddof=0)
        frame["bb_mid"] = mid
        frame["bb_upper"] = mid + bb_mult * std
        frame["bb_lower"] = mid - bb_mult * std
        band_width = frame["bb_upper"] - frame["bb_lower"]
        frame["bb_pctb"] = (frame["close"] - frame["bb_lower"]) / band_width
        frame["bb_width_pct"] = (frame["bb_upper"] - frame["bb_lower"]) / frame["bb_mid"]
        frame["k_full"] = 50.0
        frame["d_full"] = 50.0
        frame["k_fast"] = 50.0
        frame["k_cross_up"] = False
        frame["k_cross_down"] = False
        frame["atr_14"] = 1.0
        out[ticker] = frame.dropna(subset=["bb_lower", "bb_upper"]).reset_index(drop=True)
    return out


class TestNoLookahead:
    def test_passes_on_a_market_shaped_fixture_that_genuinely_reads_indicators(self):
        bars = _synthetic_bars_with_bands()

        report = run_harness(_events([]), bars, CONFIG)

        assert report.no_lookahead.passed, report.no_lookahead.violations
        jac = report.no_lookahead.detail["jaccard_by_shift"]
        jc = report.no_lookahead.detail["jaccard_control"]
        assert jc < 0.15
        assert jac[1] < 0.80
        assert jac[1] > jac[2] > jac[5] > jac[20]
        assert jac[5] < 0.50
        assert jac[20] < 2 * jc

    def test_catches_a_blind_detector_that_ignores_indicators(self, monkeypatch):
        """TESTS.md §3.1's own guard, ported to the harness: a detector that
        fires identically regardless of what indicator row it is handed
        must fail the ladder, because shifting the (unread) indicators
        cannot change its output at all. Monkeypatches
        `research.candidates.scan_candidates` — the one entry point
        `_check_no_lookahead` calls (module docstring) — rather than
        writing a second detection path."""
        bars = _synthetic_bars_with_bands()

        def blind_scan(bars_df, indicators_df, sp):
            # Fires on every single bar, regardless of `indicators_df` —
            # the defect this test exists to catch.
            rows = [
                {
                    "ticker": t,
                    "signal_date": d,
                    "signal_type": "bb_lower_touch",
                    "signal_types_all": ["bb_lower_touch"],
                    "signal_strength": 1,
                    "side": "long",
                    "touch_level": 100.0,
                }
                for t, d in zip(bars_df["ticker"], bars_df["ts"].dt.date)
            ]
            return pd.DataFrame(rows, columns=research_candidates._CANDIDATE_COLUMNS), []

        # `harness.py` imports `scan_candidates` by name at module scope
        # (`from capitalscan.research.candidates import scan_candidates`),
        # so the reference to patch is the one bound inside `harness`, not
        # the original on `research_candidates` — patching the source
        # module would leave `harness`'s already-bound name untouched.
        monkeypatch.setattr(harness, "scan_candidates", blind_scan)

        report = run_harness(_events([]), bars, CONFIG)

        assert not report.no_lookahead.passed
        # A blind detector's shifted event sets are identical to its base
        # set (nothing about the shift changed anything), so the
        # "material change" bound is the one that fires here.
        reasons = {v["bound"] for v in report.no_lookahead.violations}
        assert "shift1_material_change" in reasons

    def test_no_bars_supplied_is_a_violation_not_a_silent_pass(self):
        """A check that reports PASS when it never actually ran is the
        exact failure mode CONSTRAINTS.md warns the determinism test
        already hit once — an empty `bars_by_ticker` must not read as
        'no look-ahead detected'."""
        report = run_harness(_events([]), {}, CONFIG)

        assert not report.no_lookahead.passed
        assert report.no_lookahead.violations[0]["reason"] == "no_bars_supplied"


# ---------------------------------------------------------------------------
# Empty events
# ---------------------------------------------------------------------------


class TestEmptyEvents:
    def test_a_genuinely_empty_events_frame_vacuously_passes_the_four_events_checks(self):
        """`TestNoLookahead` already passes `_events([])` incidentally
        (that check does not read `events` at all), but nothing directly
        asserted the other four checks' behavior on a truly empty frame —
        called out explicitly since an empty `events` means "nothing to
        check," not "checked and clean," and the two must still resolve to
        the same `passed=True` verdict with a `detail` that says zero rows
        were examined."""
        bars = {"TSM": _daily_bars("TSM", {date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5)})}
        report = run_harness(_events([]), bars, CONFIG)

        assert report.entry_sanity.passed
        assert report.entry_sanity.detail == {"n_priced": 0, "n_validated": 0}
        assert report.exit_sanity.passed
        assert report.exit_sanity.detail == {"n_priced": 0, "n_validated": 0}
        assert report.return_identity.passed
        assert report.return_identity.detail == {"n_checked": 0}
        assert report.non_overlap.passed
        assert report.non_overlap.detail == {
            "n_heads": 0,
            "n_pairs_checked": 0,
            "n_groups_no_bars": 0,
        }


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------


class TestHarnessReportShape:
    def test_all_passed_is_false_if_any_single_check_fails(self):
        """A failing entry-sanity check must not be masked by four passing
        checks — `all_passed` is the AND of all five, and each check's own
        `passed` stays independently readable.

        The planted `entry_price` (150.0) sits outside the bar's
        `[low, high]` — an entry-sanity violation — but `gross_ret` is
        recomputed from that *same* `entry_price`/`exit_price` pair, so
        return-identity still agrees with itself and exit-sanity is
        untouched (exit_price is unchanged and still inside its own bar).
        This isolates the one check that should fail."""
        from capitalscan.core.returns import realized_return

        bars = {
            "TSM": _daily_bars(
                "TSM",
                {
                    date(2026, 1, 8): (96.0, 97.0, 94.0, 96.5),
                    date(2026, 1, 9): (96.5, 99.0, 96.0, 98.8),
                },
            )
        }
        bad_entry, exit_price = 150.0, 98.8
        events = _events(
            [
                _event_row(
                    entry_price=bad_entry,
                    exit_price=exit_price,
                    gross_ret=realized_return(bad_entry, exit_price, Side.LONG),
                )
            ]
        )

        report = run_harness(events, bars, CONFIG)

        assert not report.entry_sanity.passed
        assert not report.all_passed
        # The other checks on this same, otherwise-clean row still ran and
        # still report their own independent verdicts — a single bad row
        # anywhere must not hide what the other four checks found.
        assert report.exit_sanity.passed
        assert report.return_identity.passed
