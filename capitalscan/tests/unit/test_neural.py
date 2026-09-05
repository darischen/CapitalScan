"""`research/neural.py`: the multi-task distributional model.

**Two tiers, because torch is optional.** Everything that pins a decision
-- the task order, the selection protocol, the train-only standardiser, the
refusal to promote -- is checked by reading the module and runs on any
machine. The handful of tests that need a network to exist are skipped
without torch, which is why the arithmetic they would otherwise cover lives
in `core/distributions.py` and is tested there unconditionally.
"""

from __future__ import annotations

import importlib.util
import inspect

import numpy as np
import pandas as pd
import pytest

from capitalscan.research import neural
from capitalscan.tests.unit._probe import code_of

#: Only the two behavioural classes at the bottom need a network to exist.
#: Everything above them reads the module, so the decisions stay pinned in
#: CI on a runner that has never installed a 2GB wheel -- which is the
#: whole point of keeping the arithmetic in `core/distributions.py`.
needs_torch = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None,
    reason="optional extra: uv sync --extra neural",
)


class TestTheTaskSet:
    def test_the_four_labels_are_adr_113s(self) -> None:
        assert neural.TASKS == (("terminal", 5), ("terminal", 10), ("peak", 5), ("peak", 10))

    def test_the_order_is_fixed(self) -> None:
        """A head index means nothing unless the order does. A saved model
        read back against a reordered TASKS would silently answer the wrong
        question for every task."""
        assert isinstance(neural.TASKS, tuple)
        src = inspect.getsource(neural)
        assert "TASKS.index(" in src, "heads must be addressed by position in TASKS"

    def test_every_task_has_a_label(self) -> None:
        from capitalscan.research import train

        for family, horizon in neural.TASKS:
            assert train.label_for(family, horizon)


class TestItDoesNotPromote:
    """ADR 067: fitting and promoting are separate acts."""

    def test_it_writes_no_predictions(self) -> None:
        code = code_of(neural)
        for forbidden in ("INSERT", "predictions", "to_sql", "run_sync"):
            assert forbidden not in code, f"{forbidden} makes a fitter a promoter"

    def test_it_performs_no_io(self) -> None:
        code = code_of(neural)
        for forbidden in ("read_sql", "requests", "sqlalchemy", "get_engine"):
            assert forbidden not in code

    def test_predict_still_returns_not_found(self) -> None:
        """The gate has not passed: coverage is 17/20, not 20/20. This is
        the test that must be edited deliberately if that ever changes."""
        # `capitalscan.handlers` re-exports the function under the module's
        # own name, so `from ... import predict` binds the function.
        from capitalscan.handlers.predict import predict as predict_fn

        assert "NotFound" in code_of(predict_fn)


class TestTheSelectionProtocol:
    """It has to match the incumbent's, or the comparison is between two
    procedures rather than two models."""

    def test_it_uses_the_whole_walk_forward_ladder(self) -> None:
        src = inspect.getsource(neural.fold_ladder)
        assert "walk_forward_folds" in src

    def test_the_folds_are_purged_and_embargoed(self) -> None:
        assert "_fold_masks" in inspect.getsource(neural.fold_ladder)

    def test_the_step_count_is_a_median_not_a_mean(self) -> None:
        """A mean lets one fold that never converged drag the budget."""
        src = inspect.getsource(neural.fit)
        assert "statistics.median" in src
        assert "statistics.mean" not in src

    def test_it_refits_on_all_of_train_after_selecting(self) -> None:
        """Reusing a fold's model would score something fitted on a strict
        subset of the data the incumbent got."""
        src = inspect.getsource(neural.fit)
        assert src.index("ladder") < src.index("resolved]")

    def test_the_ladder_uses_the_stricter_purge(self) -> None:
        """Two tasks are 5-day and two are 10-day. Purging every task at 10
        removes more training rows near each boundary, never fewer."""
        assert "fold_ladder(train_frame, 10, calendar)" in inspect.getsource(neural.fit)


class TestTheDesignMatrix:
    def _frame(self, n: int = 200, seed: int = 0) -> pd.DataFrame:
        from capitalscan.research import features as feat

        rng = np.random.default_rng(seed)
        # Categoricals are driven off `feat.CATEGORICAL_COLS`, not a
        # hardcoded `== "sector"`. The production code had exactly that
        # hardcoding and it silently dropped `signal_type`; a fixture with
        # the same assumption could not have caught it.
        levels = {
            "sector": ["Technology", "Energy", "Utilities"],
            "signal_type": ["confluence_low", "confluence_high", "stoch_oversold"],
        }
        data: dict[str, object] = {}
        for col in feat.FEATURE_COLS:
            if col in feat.CATEGORICAL_COLS:
                data[col] = rng.choice(levels.get(col, [f"{col}_a", f"{col}_b"]), size=n)
            elif col in feat.BOOL_FEATURE_COLS:
                data[col] = rng.random(n) > 0.5
            else:
                data[col] = rng.normal(size=n)
        return pd.DataFrame(data)

    def test_statistics_come_from_train_only(self) -> None:
        """Standardising a later frame on its own mean and scale would
        remove the regime shift the study exists to measure."""
        train_frame = self._frame(seed=1)
        shifted = self._frame(seed=2)
        shifted["bb_pctb"] = shifted["bb_pctb"] * 50 + 100

        design = neural.fit_design(train_frame)
        out = design.transform(shifted)
        column = list(design.columns).index("bb_pctb")
        assert abs(out[:, column].mean()) > 5, "a shifted frame must not standardise to zero"

    def test_missingness_survives_as_an_indicator(self) -> None:
        """Invariant 4 forbids inventing a value and forgetting you did.
        The indicator is what makes the imputation honest."""
        frame = self._frame()
        frame.loc[:9, "days_to_earnings"] = np.nan
        design = neural.fit_design(frame)
        out = design.transform(frame)
        assert out.shape[1] == design.n_features
        indicator = out[:, -len(neural.IMPUTE_COLS)]
        assert indicator[:10].sum() == 10
        assert indicator[10:].sum() == 0

    def test_the_width_accounts_for_every_categorical_and_the_indicators(self) -> None:
        """Every categorical, not just `sector`.

        Until 2026-09-04 `transform` one-hot encoded `frame["sector"]` by
        name while `_numeric_block` excluded *all* categoricals, so a second
        categorical was dropped from one side and never added to the other.
        Adding `signal_type` produced two arms with byte-identical inputs
        and a delta of exactly 0.000 on all twenty heads. This asserts the
        width accounts for each categorical's own level count.
        """
        design = neural.fit_design(self._frame())
        widths = sum(len(levels) for _, levels in design.categorical_levels)
        assert design.n_features == len(design.columns) + widths + len(neural.IMPUTE_COLS)

    def test_every_categorical_feature_is_encoded(self) -> None:
        """The guard against the silent dropper returning."""
        from capitalscan.research import features as feat

        design = neural.fit_design(self._frame())
        encoded = {col for col, _ in design.categorical_levels}
        expected = {c for c in feat.CATEGORICAL_COLS if c in feat.FEATURE_COLS}
        assert encoded == expected, f"unencoded categoricals: {expected - encoded}"

    def test_no_categorical_leaks_into_the_numeric_block(self) -> None:
        from capitalscan.research import features as feat

        design = neural.fit_design(self._frame())
        assert not set(design.columns) & set(feat.CATEGORICAL_COLS)


class TestTheFanIsMonotoneWithoutSorting:
    def test_a_fan_read_off_a_cdf_cannot_cross(self) -> None:
        """DESIGN §7.4 sorts because twenty independent heads cross. This
        architecture has nothing to repair, and sorting anyway would hide a
        bug rather than fix one."""
        assert "sort_quantiles" not in code_of(neural)

    def test_quantiles_come_from_the_shared_distribution_module(self) -> None:
        src = inspect.getsource(neural.Ensemble)
        assert "dist.quantiles_from_pmf" in src
        assert "dist.exceedance" in src


class TestTheEnsembleAveragesDistributions:
    def test_it_averages_pmfs_not_quantiles(self) -> None:
        """Different operations, and only the first yields a distribution.
        Averaged quantiles have no CDF behind them, so `exceedance` and
        `p_touch_*` would have nothing to read."""
        src = inspect.getsource(neural.Ensemble.predict_pmf)
        assert "predict_pmf" in src and "mean" in src
        fan_src = inspect.getsource(neural.Ensemble.fan)
        assert fan_src.index("predict_pmf") < fan_src.index("quantiles_from_pmf")

    def test_several_seeds_by_default(self) -> None:
        """Session 23 measured an unseeded A/B giving 17/20 and 18/20 from
        identical code. One seed is an anecdote."""
        assert len(neural.DEFAULT_SEEDS) >= 3


@needs_torch
class TestTheObjective:
    def test_summed_crps_is_lower_for_a_sharper_correct_forecast(self) -> None:
        import torch

        grids = torch.stack([torch.linspace(-1.0, 1.0, neural.N_BINS + 1)])
        targets = torch.zeros(4, 1)
        weights = torch.ones(4)
        sharp = torch.full((4, 1, neural.N_BINS), -8.0)
        sharp[:, 0, neural.N_BINS // 2] = 8.0
        diffuse = torch.zeros(4, 1, neural.N_BINS)
        assert float(neural.summed_crps(sharp, targets, grids, weights)) < float(
            neural.summed_crps(diffuse, targets, grids, weights)
        )

    def test_it_sums_over_every_task(self) -> None:
        """A loss reading one head would train one head."""
        import torch

        grids = torch.stack([torch.linspace(-1.0, 1.0, neural.N_BINS + 1)] * 2)
        targets = torch.zeros(3, 2)
        weights = torch.ones(3)
        one_bad = torch.zeros(3, 2, neural.N_BINS)
        one_bad[:, 1, 0] = 12.0
        both_flat = torch.zeros(3, 2, neural.N_BINS)
        assert float(neural.summed_crps(one_bad, targets, grids, weights)) > float(
            neural.summed_crps(both_flat, targets, grids, weights)
        )

    def test_weights_reach_the_loss(self) -> None:
        """DESIGN §7.5's `1/|cluster|` must not stop at the scorer."""
        assert "weights).sum() / weights.sum()" in inspect.getsource(neural.summed_crps)


@needs_torch
class TestTheModule:
    def test_it_emits_one_distribution_per_task(self) -> None:
        import torch

        module = neural._build_module(12, len(neural.TASKS), neural.N_BINS)
        out = module(torch.zeros(7, 12))
        assert out.shape == (7, len(neural.TASKS), neural.N_BINS)

    def test_the_trunk_is_shared(self) -> None:
        """The whole hypothesis. Four independent networks would be four
        independent fits with extra steps."""
        module = neural._build_module(12, len(neural.TASKS), neural.N_BINS)
        assert hasattr(module, "trunk")
        assert len(module.heads) == len(neural.TASKS)
        trunk_params = sum(p.numel() for p in module.trunk.parameters())
        head_params = sum(p.numel() for p in module.heads.parameters())
        assert trunk_params > head_params, "the shared body must carry the capacity"
