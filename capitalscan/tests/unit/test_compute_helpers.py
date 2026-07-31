"""Unit tests for pure helpers in `jobs.compute` (BUILD session 6). No DB, no network."""

from __future__ import annotations

from datetime import date

from capitalscan.core.config import Config, SplitParams
from capitalscan.jobs.compute import (
    _dd_bucket,
    _deterministic_id,
    _quarter_end,
    _split_key,
    _tag_clusters,
)
from capitalscan.jobs.config import config_hash


class TestQuarterEnd:
    def test_q1_ends_march_31(self):
        assert _quarter_end("2026Q1") == date(2026, 3, 31)

    def test_q2_ends_june_30(self):
        assert _quarter_end("2026Q2") == date(2026, 6, 30)

    def test_q3_ends_september_30(self):
        assert _quarter_end("2026Q3") == date(2026, 9, 30)

    def test_q4_ends_december_31(self):
        assert _quarter_end("2026Q4") == date(2026, 12, 31)

    def test_leap_year_q1_still_ends_march_31(self):
        assert _quarter_end("2028Q1") == date(2028, 3, 31)


class TestDdBucket:
    def test_none_input_returns_none(self):
        assert _dd_bucket(None) is None

    def test_nan_input_returns_none(self):
        assert _dd_bucket(float("nan")) is None

    def test_below_first_threshold(self):
        assert _dd_bucket(0.05) == "0-10"

    def test_at_first_threshold_is_inclusive(self):
        assert _dd_bucket(0.10) == "0-10"

    def test_between_thresholds(self):
        assert _dd_bucket(0.15) == "10-20"

    def test_above_all_thresholds(self):
        assert _dd_bucket(0.50) == "35+"

    def test_zero_drawdown(self):
        assert _dd_bucket(0.0) == "0-10"


class TestSplitKey:
    def test_before_train_end_is_train(self):
        sp = SplitParams(train_end="2021-12-31", validate_end="2023-12-31")
        assert _split_key(date(2020, 6, 1), sp) == "train"

    def test_at_train_end_boundary_is_train(self):
        sp = SplitParams(train_end="2021-12-31", validate_end="2023-12-31")
        assert _split_key(date(2021, 12, 31), sp) == "train"

    def test_just_after_train_end_is_validate(self):
        sp = SplitParams(train_end="2021-12-31", validate_end="2023-12-31")
        assert _split_key(date(2022, 1, 1), sp) == "validate"

    def test_at_validate_end_boundary_is_validate(self):
        sp = SplitParams(train_end="2021-12-31", validate_end="2023-12-31")
        assert _split_key(date(2023, 12, 31), sp) == "validate"

    def test_after_validate_end_is_holdout(self):
        sp = SplitParams(train_end="2021-12-31", validate_end="2023-12-31")
        assert _split_key(date(2024, 1, 1), sp) == "holdout"


class TestDeterministicId:
    def test_same_inputs_produce_same_id(self):
        a = _deterministic_id("TSM", "long", date(2026, 1, 1))
        b = _deterministic_id("TSM", "long", date(2026, 1, 1))
        assert a == b

    def test_different_inputs_produce_different_ids(self):
        a = _deterministic_id("TSM", "long", date(2026, 1, 1))
        b = _deterministic_id("NVDA", "long", date(2026, 1, 1))
        assert a != b

    def test_fits_in_bigint_range(self):
        value = _deterministic_id("TSM", "long", date(2026, 1, 1))
        assert 0 <= value < 2**63


class TestTagClusters:
    def _event(self, ticker, side, signal_date):
        return {"ticker": ticker, "side": side, "signal_date": signal_date}

    def test_single_event_is_its_own_head(self):
        events = [self._event("TSM", "long", date(2026, 1, 5))]
        tagged = _tag_clusters(events, gap_days=5)
        assert tagged[0]["is_cluster_head"] is True
        assert tagged[0]["seq_in_cluster"] == 1
        assert tagged[0]["days_since_head"] == 0

    def test_events_within_gap_join_the_same_cluster(self):
        events = [
            self._event("TSM", "long", date(2026, 1, 5)),
            self._event("TSM", "long", date(2026, 1, 7)),
        ]
        tagged = _tag_clusters(events, gap_days=5)
        assert tagged[0]["cluster_id"] == tagged[1]["cluster_id"]
        assert tagged[1]["seq_in_cluster"] == 2
        assert tagged[1]["is_cluster_head"] is False
        assert tagged[1]["days_since_head"] == 2

    def test_events_beyond_gap_start_a_new_cluster(self):
        events = [
            self._event("TSM", "long", date(2026, 1, 5)),
            self._event("TSM", "long", date(2026, 1, 20)),
        ]
        tagged = _tag_clusters(events, gap_days=5)
        assert tagged[0]["cluster_id"] != tagged[1]["cluster_id"]
        assert tagged[1]["seq_in_cluster"] == 1
        assert tagged[1]["is_cluster_head"] is True

    def test_different_sides_on_the_same_ticker_cluster_independently(self):
        events = [
            self._event("TSM", "long", date(2026, 1, 5)),
            self._event("TSM", "short", date(2026, 1, 6)),
        ]
        tagged = _tag_clusters(events, gap_days=5)
        assert tagged[0]["cluster_id"] != tagged[1]["cluster_id"]
        assert tagged[0]["is_cluster_head"] is True
        assert tagged[1]["is_cluster_head"] is True

    def test_different_tickers_cluster_independently(self):
        events = [
            self._event("TSM", "long", date(2026, 1, 5)),
            self._event("NVDA", "long", date(2026, 1, 5)),
        ]
        tagged = _tag_clusters(events, gap_days=5)
        assert tagged[0]["cluster_id"] != tagged[1]["cluster_id"]

    def test_cluster_ids_are_stable_across_reruns(self):
        events_a = [self._event("TSM", "long", date(2026, 1, 5))]
        events_b = [self._event("TSM", "long", date(2026, 1, 5))]
        tagged_a = _tag_clusters(events_a, gap_days=5)
        tagged_b = _tag_clusters(events_b, gap_days=5)
        assert tagged_a[0]["cluster_id"] == tagged_b[0]["cluster_id"]


class TestConfigHash:
    def test_identical_configs_hash_identically(self):
        assert config_hash(Config()) == config_hash(Config())

    def test_different_configs_hash_differently(self):
        from capitalscan.core.config import SignalParams

        default = config_hash(Config())
        changed = config_hash(Config(signals=SignalParams(stoch_oversold=25.0)))
        assert default != changed

    def test_hash_is_short_and_hex(self):
        h = config_hash(Config())
        assert len(h) == 16
        int(h, 16)  # raises if not valid hex
