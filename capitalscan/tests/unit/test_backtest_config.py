"""Tests for `research/backtest.py`'s config contract (Session 9, Task 2).

`config_hash`'s determinism and field-sensitivity are already covered by
`TestConfigHash` in `test_compute_helpers.py` (imports `config_hash` from
`jobs/config.py` directly). This file does not repeat that coverage — it
only checks the two things genuinely new here: that `research/backtest.py`
re-exports the same objects (not a parallel copy), and `split_key_for`'s
boundary behaviour plus its raise below `event_start`.
"""

from __future__ import annotations

from datetime import date

import pytest

from capitalscan.core.config import Config, SplitParams, UniverseParams
from capitalscan.research.backtest import BacktestConfig, config_hash, split_key_for


class TestBacktestConfigAlias:
    def test_backtest_config_is_the_core_config_type(self):
        # Ruling C1: BacktestConfig is `Config`, not a second dataclass.
        assert BacktestConfig is Config

    def test_backtest_config_constructs_like_config(self):
        cfg = BacktestConfig()
        assert cfg == Config()


class TestConfigHashReexport:
    def test_reexported_hash_is_the_same_function(self):
        from capitalscan.jobs.config import config_hash as jobs_config_hash

        assert config_hash is jobs_config_hash

    def test_reexported_hash_still_works(self):
        assert config_hash(Config()) == config_hash(Config())


class TestUniverseMinMcapThreshold:
    def test_default_min_mcap_usd_is_100_billion(self):
        """Session 9 config-thresholds task, Change 2 (user's decision):
        ADR 014's mega-cap threshold moves from a fixed $200B nominal to
        $100B nominal, widening the candidate pool. See `UniverseParams`'s
        docstring and `docs/DECISIONS.md` ADR 014's dated note for why."""
        assert UniverseParams().min_mcap_usd == 100e9


class TestSplitKeyFor:
    def test_train_end_boundary_is_train(self):
        sp = SplitParams(train_end="2021-12-31", validate_end="2023-12-31")
        assert split_key_for(date(2021, 12, 31), sp) == "train"

    def test_day_after_train_end_is_validate(self):
        sp = SplitParams(train_end="2021-12-31", validate_end="2023-12-31")
        assert split_key_for(date(2022, 1, 1), sp) == "validate"

    def test_validate_end_boundary_is_validate(self):
        sp = SplitParams(train_end="2021-12-31", validate_end="2023-12-31")
        assert split_key_for(date(2023, 12, 31), sp) == "validate"

    def test_day_after_validate_end_is_holdout(self):
        sp = SplitParams(train_end="2021-12-31", validate_end="2023-12-31")
        assert split_key_for(date(2024, 1, 1), sp) == "holdout"

    def test_event_start_boundary_is_train(self):
        sp = SplitParams(
            event_start="2010-01-01", train_end="2021-12-31", validate_end="2023-12-31"
        )
        assert split_key_for(date(2010, 1, 1), sp) == "train"

    def test_a_date_before_event_start_raises(self):
        sp = SplitParams(
            event_start="2010-01-01", train_end="2021-12-31", validate_end="2023-12-31"
        )
        with pytest.raises(ValueError):
            split_key_for(date(2009, 12, 31), sp)

    def test_the_raise_names_the_offending_date(self):
        sp = SplitParams(event_start="2010-01-01")
        with pytest.raises(ValueError, match="2009-06-15"):
            split_key_for(date(2009, 6, 15), sp)
