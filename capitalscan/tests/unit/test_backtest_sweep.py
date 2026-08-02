"""Tests for `research/backtest.py`'s exit-parameter sweep (Session 9, Task 12).

DESIGN §5.9's grid is `stop_mode` (atr, fixed, none) x `stop_atr_k` (1.0,
1.5, 2.0, 2.5) x `target_pct` (0.03, 0.04, 0.05). `stop_atr_k` only has
behavioral meaning under `stop_mode="atr"` (`fixed` uses `stop_fixed_pct`
instead, `none` places no stop at all — see `core/exits.py`), so the grid
collapses to 6 stop variants (4 atr k-values + 1 fixed + 1 none), not
3x4=12: `(4 + 1 + 1) x 3 = 18`, never `3 x 4 x 3 = 36`. Varying `stop_atr_k`
under `fixed`/`none` too would produce configs that behave identically
(same resolved stop) but hash differently, so this suite asserts the
inactive-under-fixed/none behavior explicitly (`TestInactiveStopAtrK`), not
just the count.

**Entry-reuse is NOT implemented in this task.** `run_backtest` ->
`_backtest_one_ticker` resolves entries (`research.enrich.resolve_entries`)
once per call, keyed to the `config` it is handed — there is no cache keyed
off "entry parameters" separate from "exit parameters" for a caller running
the same tickers across the 18 configs `sweep_configs` returns. Running the
sweep today means 18 full `run_backtest` passes, each re-resolving entries
from scratch; restructuring `_backtest_one_ticker` to split entry resolution
from exit resolution (so entries are computed once per event and exits are
computed 18 times against them) is out of scope for this task — see
`task-12-report.md`. `TestSweepConfigsIsAPureGenerator` below proves the one
thing that IS true at this layer: `sweep_configs` itself is a config
generator that touches no bars, no indicators, and no entry-resolution code
— it does not run anything. It does not, and cannot, prove entries are
reused across a real sweep execution, because no execution path exists yet
that would reuse them.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from capitalscan.core.config import Config, ExitParams
from capitalscan.jobs.config import config_hash
from capitalscan.research.backtest import sweep_configs


class TestSweepConfigsCount:
    def test_produces_exactly_18_configs(self):
        assert len(sweep_configs(Config())) == 18

    def test_all_config_hashes_are_distinct(self):
        configs = sweep_configs(Config())
        hashes = [config_hash(c) for c in configs]
        assert len(set(hashes)) == 18


class TestSweepConfigsGrid:
    def test_atr_stop_mode_covers_all_four_k_values(self):
        configs = sweep_configs(Config())
        atr_ks = sorted(
            {c.exits.stop_atr_k for c in configs if c.exits.stop_mode == "atr"}
        )
        assert atr_ks == [1.0, 1.5, 2.0, 2.5]

    def test_stop_mode_variant_counts_are_4_1_1(self):
        configs = sweep_configs(Config())
        atr = [c for c in configs if c.exits.stop_mode == "atr"]
        fixed = [c for c in configs if c.exits.stop_mode == "fixed"]
        none = [c for c in configs if c.exits.stop_mode == "none"]
        # 4 atr k-values x 3 targets, 1 fixed x 3 targets, 1 none x 3 targets.
        assert len(atr) == 12
        assert len(fixed) == 3
        assert len(none) == 3

    def test_target_pct_grid_is_paired_with_every_stop_variant(self):
        configs = sweep_configs(Config())
        targets = sorted({c.exits.target_pct for c in configs})
        assert targets == [0.03, 0.04, 0.05]
        # Every one of the 6 stop variants (atr x4, fixed, none) appears
        # with all 3 targets, not just some.
        for mode, k in [
            ("atr", 1.0),
            ("atr", 1.5),
            ("atr", 2.0),
            ("atr", 2.5),
            ("fixed", None),
            ("none", None),
        ]:
            if mode == "atr":
                matching = [
                    c for c in configs if c.exits.stop_mode == mode and c.exits.stop_atr_k == k
                ]
            else:
                matching = [c for c in configs if c.exits.stop_mode == mode]
            assert sorted(c.exits.target_pct for c in matching) == [0.03, 0.04, 0.05], (
                mode,
                k,
            )


class TestInactiveStopAtrK:
    """`stop_atr_k` has no effect under `fixed` (which uses
    `stop_fixed_pct`) or `none` (no stop at all — `core/exits.py`). Holding
    it at the base config's value under those modes, rather than sweeping
    it there too, is the choice that keeps the grid at 18 instead of 36 and
    keeps every hash difference tied to an actual behavioral difference.
    """

    def test_fixed_and_none_variants_hold_stop_atr_k_at_the_base_value(self):
        base = Config()
        configs = sweep_configs(base)
        for c in configs:
            if c.exits.stop_mode in ("fixed", "none"):
                assert c.exits.stop_atr_k == base.exits.stop_atr_k

    def test_a_nondefault_base_stop_atr_k_is_preserved_under_fixed_and_none(self):
        # Proves the held value is genuinely "the base's value," not a
        # hardcoded default that happens to match `Config()`.
        base = replace(Config(), exits=replace(ExitParams(), stop_atr_k=1.75))
        configs = sweep_configs(base)
        for c in configs:
            if c.exits.stop_mode in ("fixed", "none"):
                assert c.exits.stop_atr_k == 1.75


class TestStochasticThresholdsNotSwept:
    """DESIGN §5.9 does not include the stochastic exit thresholds in the
    grid. Adding them would multiply the config count and is a design
    change this task does not make (task-12-brief.md).
    """

    def test_exit_stoch_threshold_never_varies(self):
        base = Config()
        configs = sweep_configs(base)
        for c in configs:
            assert c.exits.exit_stoch_threshold == base.exits.exit_stoch_threshold
            assert c.exits.exit_stoch_threshold_short == base.exits.exit_stoch_threshold_short

    def test_a_nondefault_base_threshold_is_preserved(self):
        base = replace(
            Config(),
            exits=replace(ExitParams(), exit_stoch_threshold=75.0, exit_stoch_threshold_short=25.0),
        )
        configs = sweep_configs(base)
        for c in configs:
            assert c.exits.exit_stoch_threshold == 75.0
            assert c.exits.exit_stoch_threshold_short == 25.0


class TestSweepConfigsPreservesOtherSections:
    def test_only_exits_section_differs_from_base(self):
        base = Config()
        for c in sweep_configs(base):
            assert c.indicators == base.indicators
            assert c.signals == base.signals
            assert c.costs == base.costs
            assert c.universe == base.universe
            assert c.stats == base.stats
            assert c.splits == base.splits

    def test_base_is_not_mutated(self):
        base = Config()
        before = replace(base)
        sweep_configs(base)
        assert base == before


class TestSweepConfigsIsAPureGenerator:
    """`sweep_configs` only builds `Config` objects — it must not read bars,
    open a database connection, or resolve any entry/exit. This is the one
    reuse-adjacent claim this task can actually prove (see module docstring):
    it does NOT show entries are reused across a real sweep execution,
    because `run_backtest` has no such execution path yet.
    """

    def test_does_not_call_resolve_entries(self, monkeypatch):
        from capitalscan.research import enrich as research_enrich

        def _boom(*args, **kwargs):
            raise AssertionError("sweep_configs must not resolve entries")

        monkeypatch.setattr(research_enrich, "resolve_entries", _boom)
        sweep_configs(Config())  # must not raise

    def test_does_not_touch_the_database(self, monkeypatch):
        from capitalscan.jobs import db_io

        def _boom(*args, **kwargs):
            raise AssertionError("sweep_configs must not open a database connection")

        monkeypatch.setattr(db_io, "get_engine", _boom)
        sweep_configs(Config())  # must not raise

    def test_returns_config_instances(self):
        for c in sweep_configs(Config()):
            assert isinstance(c, Config)


class TestSweepConfigsRejectsInvalidBase:
    def test_raises_a_clear_error_when_base_is_not_a_config(self):
        with pytest.raises((TypeError, AttributeError)):
            sweep_configs(None)
