"""Unit tests for `jobs.ingest.validate_bars` (DESIGN §2.3), no DB or network."""

from __future__ import annotations

import pandas as pd

from capitalscan.jobs.ingest import validate_bars


def _bar(
    ticker="TSM", ts="2020-01-02", open_=100.0, high=101.0, low=99.0, close=100.5, volume=1_000_000
):
    return {
        "ticker": ticker,
        "ts": pd.Timestamp(ts),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "adj_close": close,
        "volume": volume,
        "adj_factor": 1.0,
    }


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class TestStructuralRejects:
    def test_high_less_than_low_is_rejected(self):
        df = _frame([_bar(high=98.0, low=99.0)])
        clean, rejects = validate_bars(df)
        assert clean.empty
        assert rejects[0]["rule"] == "high_lt_low"
        assert rejects[0]["severity"] == "reject"

    def test_close_outside_high_low_is_rejected(self):
        df = _frame([_bar(close=200.0)])
        clean, rejects = validate_bars(df)
        assert clean.empty
        assert rejects[0]["rule"] == "close_outside_range"

    def test_open_outside_high_low_is_rejected(self):
        df = _frame([_bar(open_=50.0)])
        clean, rejects = validate_bars(df)
        assert clean.empty
        assert rejects[0]["rule"] == "open_outside_range"

    def test_valid_bar_passes_through_clean(self):
        df = _frame([_bar()])
        clean, rejects = validate_bars(df)
        assert len(clean) == 1
        assert rejects == []


class TestFlags:
    def test_zero_volume_is_flagged_not_rejected(self):
        df = _frame([_bar(volume=0)])
        clean, rejects = validate_bars(df)
        assert len(clean) == 1  # kept
        assert rejects[0]["rule"] == "zero_or_null_volume"
        assert rejects[0]["severity"] == "flag"

    def test_null_volume_is_flagged(self):
        df = _frame([_bar(volume=None)])
        clean, rejects = validate_bars(df)
        assert len(clean) == 1
        assert rejects[0]["rule"] == "zero_or_null_volume"

    def test_price_below_one_dollar_is_flagged(self):
        df = _frame([_bar(open_=0.5, high=0.6, low=0.4, close=0.5)])
        clean, rejects = validate_bars(df)
        assert len(clean) == 1
        assert any(r["rule"] == "price_below_min" for r in rejects)

    def test_identical_close_for_five_days_is_flagged(self):
        rows = [_bar(ts=f"2020-01-{2 + i:02d}", close=100.0) for i in range(6)]
        df = _frame(rows)
        clean, rejects = validate_bars(df)
        assert len(clean) == 6  # kept, just flagged
        flat_flags = [r for r in rejects if r["rule"] == "identical_close_run"]
        assert len(flat_flags) >= 1

    def test_four_identical_closes_does_not_trigger_flat_run_flag(self):
        rows = [_bar(ts=f"2020-01-{2 + i:02d}", close=100.0) for i in range(4)]
        df = _frame(rows)
        _, rejects = validate_bars(df)
        assert not any(r["rule"] == "identical_close_run" for r in rejects)


class TestLargeMoves:
    def test_large_unexplained_move_is_flagged_not_rejected(self):
        # +45% move that isn't split-ratio shaped and has no corporate action.
        rows = [
            _bar(ts="2020-01-02", open_=100, high=101, low=99, close=100),
            _bar(ts="2020-01-03", open_=144, high=146, low=143, close=145),
        ]
        df = _frame(rows)
        clean, rejects = validate_bars(df)
        assert len(clean) == 2
        assert any(r["rule"] == "large_unexplained_return" for r in rejects)

    def test_move_explained_by_corporate_action_is_flagged_and_kept(self):
        rows = [
            _bar(ts="2020-01-02", open_=100, high=101, low=99, close=100),
            _bar(ts="2020-01-03", open_=49, high=51, low=48, close=50),  # ~2-for-1 split
        ]
        df = _frame(rows)
        actions = pd.DataFrame(
            [
                {
                    "ticker": "TSM",
                    "ex_date": pd.Timestamp("2020-01-03").date(),
                    "action_type": "split",
                    "ratio": 2.0,
                }
            ]
        )
        clean, rejects = validate_bars(df, corporate_actions=actions)
        assert len(clean) == 2
        assert any(r["rule"] == "large_return_explained_by_split" for r in rejects)
        assert not any(r["rule"] == "unexplained_split_like_move" for r in rejects)

    def test_split_like_move_without_corporate_action_is_rejected(self):
        rows = [
            _bar(ts="2020-01-02", open_=100, high=101, low=99, close=100),
            _bar(
                ts="2020-01-03", open_=49, high=51, low=48, close=50
            ),  # looks like 2-for-1, no action on file
        ]
        df = _frame(rows)
        clean, rejects = validate_bars(df, corporate_actions=None)
        assert len(clean) == 1  # the split-like row was dropped
        assert any(
            r["rule"] == "unexplained_split_like_move" and r["severity"] == "reject"
            for r in rejects
        )

    def test_small_move_is_not_flagged(self):
        rows = [
            _bar(ts="2020-01-02", open_=100, high=101, low=99, close=100),
            _bar(ts="2020-01-03", open_=101, high=103, low=100, close=102),
        ]
        df = _frame(rows)
        _, rejects = validate_bars(df)
        assert rejects == []


class TestEmptyInput:
    def test_empty_frame_returns_empty(self):
        df = pd.DataFrame(columns=["ticker", "ts", "open", "high", "low", "close", "volume"])
        clean, rejects = validate_bars(df)
        assert clean.empty
        assert rejects == []


class TestMultiTicker:
    def test_tickers_are_validated_independently(self):
        rows = [
            _bar(ticker="TSM", ts="2020-01-02", close=100.0),
            _bar(ticker="TSM", ts="2020-01-03", high=98.0, low=99.0),  # bad
            _bar(ticker="NVDA", ts="2020-01-02", open_=200.0, high=201.0, low=199.0, close=200.0),
        ]
        df = _frame(rows)
        clean, rejects = validate_bars(df)
        assert set(clean["ticker"]) == {"TSM", "NVDA"}
        assert len(clean) == 2
        assert any(r["ticker"] == "TSM" and r["rule"] == "high_lt_low" for r in rejects)


class TestFabricatedFlatBars:
    """A zero-volume bar repeating the prior close is a carried-forward price.

    **Found in production, 2026-08-27.** AVB stored four consecutive bars at
    exactly 65.9005 with volume 0 for 2026-08-17..20, spanning its 2.793
    split. Yahoo has since served four distinct real closes for those
    sessions, so the flat run was vendor filler, not the market.

    Both existing rules saw it and both are `flag`: `zero_or_null_volume`
    fired on every one of those bars, on every run, and `identical_close_run`
    fires once a run is long enough. Neither clears `keep`, so the bars were
    written anyway and the indicators read a fake flat line across a split.

    Neither signal is conclusive alone, which is why they stay flags. CCZ,
    GJS and SBNY have genuine zero-volume sessions, and a flat close on real
    volume is an ordinary quiet day. The **conjunction** is not ordinary: a
    bar with no trades whose close equals the previous close contains no
    observation at all. Invariant 4 says an absent value is dropped and
    logged, never carried forward, so this rejects.
    """

    def test_zero_volume_repeating_the_prior_close_is_rejected(self):
        rows = [
            _bar(ts="2020-01-02", close=100.0, volume=1_000_000),
            _bar(ts="2020-01-03", close=100.0, volume=0),
        ]
        clean, rejects = validate_bars(_frame(rows))
        assert len(clean) == 1, "the real bar survives"
        assert clean.iloc[0]["volume"] == 1_000_000
        hard = [r for r in rejects if r["severity"] == "reject"]
        assert [r["rule"] for r in hard] == ["flat_zero_volume_bar"]

    def test_a_whole_fabricated_run_is_rejected_and_the_real_bars_kept(self):
        """AVB's exact shape: one real bar, four filler, one real."""
        rows = [_bar(ts="2020-01-02", close=100.0, volume=1_000_000)]
        rows += [
            _bar(ts=d, close=100.0, volume=0)
            for d in ("2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08")
        ]
        rows.append(_bar(ts="2020-01-09", close=101.0, volume=900_000))
        clean, rejects = validate_bars(_frame(rows))
        assert len(clean) == 2
        assert sorted(clean["close"].tolist()) == [100.0, 101.0]
        hard = [r for r in rejects if r["severity"] == "reject"]
        assert len(hard) == 4
        assert {r["rule"] for r in hard} == {"flat_zero_volume_bar"}

    def test_zero_volume_alone_is_still_only_flagged(self):
        """Illiquid names really do have no-trade sessions. Rejecting on
        volume alone would throw away real data for CCZ, GJS and SBNY."""
        rows = [
            _bar(ts="2020-01-02", close=100.0, volume=1_000_000),
            _bar(ts="2020-01-03", close=100.8, volume=0),
        ]
        clean, rejects = validate_bars(_frame(rows))
        assert len(clean) == 2, "kept: the price moved, so it is an observation"
        assert not [r for r in rejects if r["severity"] == "reject"]
        assert "zero_or_null_volume" in {r["rule"] for r in rejects}

    def test_a_flat_close_on_real_volume_is_untouched(self):
        """An ordinary quiet day. No flag, no reject."""
        rows = [
            _bar(ts="2020-01-02", close=100.0, volume=1_000_000),
            _bar(ts="2020-01-03", close=100.0, volume=800_000),
        ]
        clean, rejects = validate_bars(_frame(rows))
        assert len(clean) == 2
        assert not [r for r in rejects if r["severity"] == "reject"]

    def test_the_first_bar_of_a_batch_is_never_rejected_for_flatness(self):
        """It has no predecessor in the batch, so `close == prior` cannot be
        evaluated. Guessing from outside the frame would make the rule depend
        on fetch boundaries."""
        clean, rejects = validate_bars(_frame([_bar(volume=0)]))
        assert len(clean) == 1
        assert not [r for r in rejects if r["severity"] == "reject"]

    def test_tickers_do_not_bleed_into_each_other(self):
        """The comparison is per ticker. B's first bar must not be measured
        against A's last."""
        rows = [
            _bar(ticker="AAA", ts="2020-01-02", close=100.0, volume=1_000_000),
            _bar(ticker="BBB", ts="2020-01-02", close=100.0, volume=0),
        ]
        clean, rejects = validate_bars(_frame(rows))
        assert len(clean) == 2
        assert not [r for r in rejects if r["severity"] == "reject"]
