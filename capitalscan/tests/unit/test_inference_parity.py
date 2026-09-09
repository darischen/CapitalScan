"""The numpy forward pass must agree with torch, exactly enough.

**Why this file is the load-bearing part of `core/inference.py`.** That
module exists so the Pi can score a signal without a 2GB torch wheel, and
it does it by reimplementing the forward pass. A second implementation
that drifts from the first is worse than no second implementation: it
produces plausible numbers that are wrong, on the surface a reader
actually looks at.

So the contract is not "numpy inference works". It is "numpy inference is
the same arithmetic", and these tests are how that stays true when
`_build_module` changes.

Torch is an optional extra, so everything needing it is skipped rather
than failed when it is absent. The pure-numpy tests below still run, which
is deliberate: the overflow and averaging behaviour they pin does not
depend on torch being installed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalscan.core import inference as cinf

torch = pytest.importorskip("torch", reason="the `neural` extra is not installed")


class TestGelu:
    """`torch.nn.GELU()` defaults to the exact erf form, not tanh."""

    def test_it_matches_torch_across_the_elbow(self) -> None:
        """The tanh approximation differs by ~1e-3 near zero.

        That is a hundred times the tolerance here, so choosing the wrong
        form fails this test loudly instead of drifting into the output.
        """
        x = np.linspace(-6.0, 6.0, 401)
        mine = cinf.gelu(x)
        theirs = torch.nn.functional.gelu(torch.tensor(x)).numpy()
        assert np.allclose(mine, theirs, atol=1e-9)

    def test_it_is_not_the_tanh_approximation(self) -> None:
        """Pins the choice, so a future edit cannot quietly swap it."""
        x = np.array([1.0])
        tanh_form = torch.nn.functional.gelu(torch.tensor(x), approximate="tanh").numpy()
        assert not np.allclose(cinf.gelu(x), tanh_form, atol=1e-6)


class TestSoftmax:
    def test_it_matches_torch(self) -> None:
        rng = np.random.default_rng(11)
        x = rng.normal(size=(7, 32)) * 3.0
        mine = cinf.softmax(x, axis=-1)
        theirs = torch.softmax(torch.tensor(x), dim=-1).numpy()
        assert np.allclose(mine, theirs, atol=1e-12)

    def test_a_large_logit_does_not_overflow_to_nan(self) -> None:
        """Without the max-shift this row returns NaN, not an error.

        A NaN here reaches the screen as a missing probability rather than
        a failure, which is the quiet kind of wrong.
        """
        out = cinf.softmax(np.array([[750.0, 749.0, 0.0]]))
        assert np.all(np.isfinite(out))
        assert out.sum() == pytest.approx(1.0)


def _module(n_features: int, n_tasks: int, n_bins: int):
    from capitalscan.research import neural

    torch.manual_seed(7)
    return neural._build_module(n_features, n_tasks, n_bins)


class TestForwardParity:
    """The whole network, weights exported and re-run in numpy."""

    def test_the_pmf_matches_torch_on_random_input(self) -> None:
        from capitalscan.research import neural

        n_features, n_tasks, n_bins, rows = 23, len(neural.TASKS), 32, 40
        module = _module(n_features, n_tasks, n_bins)
        module.eval()  # dropout is the identity at inference; torch needs telling

        rng = np.random.default_rng(3)
        x = rng.normal(size=(rows, n_features))

        with torch.no_grad():
            expected = torch.softmax(module(torch.tensor(x, dtype=torch.float32)), dim=-1).numpy()

        fitted = neural.FittedModel(
            design=None,  # type: ignore[arg-type]
            grids=np.zeros((n_tasks, n_bins + 1)),
            module=module,
            seed=7,
            steps=1,
            inner_crps=0.0,
            device="cpu",
        )
        got = neural.export_weights(fitted).pmf(x)

        assert got.shape == expected.shape == (rows, n_tasks, n_bins)
        # float32 forward in torch against float64 here, so 1e-5 rather
        # than machine epsilon. Tighter than that would fail on precision
        # rather than on a real disagreement.
        assert np.allclose(got, expected, atol=1e-5)

    def test_every_row_is_a_distribution(self) -> None:
        from capitalscan.research import neural

        module = _module(23, len(neural.TASKS), 32)
        module.eval()
        fitted = neural.FittedModel(
            design=None,  # type: ignore[arg-type]
            grids=np.zeros((len(neural.TASKS), 33)),
            module=module,
            seed=7,
            steps=1,
            inner_crps=0.0,
            device="cpu",
        )
        pmf = neural.export_weights(fitted).pmf(np.random.default_rng(5).normal(size=(9, 23)))
        assert np.allclose(pmf.sum(axis=-1), 1.0)
        assert np.all(pmf >= 0.0)

    def test_the_export_reads_the_module_rather_than_a_layer_list(self) -> None:
        """A hardcoded list would export a stale architecture silently."""
        from capitalscan.research import neural

        src = __import__("inspect").getsource(neural.export_weights)
        assert "isinstance(child, nn.Linear)" in src


class TestEnsembleAveraging:
    """Mass, not logits. The two give different distributions."""

    def test_it_averages_probability_mass(self) -> None:
        a = cinf.NetworkWeights(
            trunk=(cinf.LinearLayer(np.eye(2), np.zeros(2)),),
            heads=(cinf.LinearLayer(np.array([[4.0, 0.0], [0.0, 0.0]]), np.zeros(2)),),
        )
        b = cinf.NetworkWeights(
            trunk=(cinf.LinearLayer(np.eye(2), np.zeros(2)),),
            heads=(cinf.LinearLayer(np.array([[0.0, 0.0], [0.0, 4.0]]), np.zeros(2)),),
        )
        x = np.array([[1.0, 1.0]])
        got = cinf.ensemble_pmf([a, b], x)
        assert np.allclose(got, (a.pmf(x) + b.pmf(x)) / 2.0)

    def test_an_empty_ensemble_raises_rather_than_returning_nan(self) -> None:
        """`np.mean([])` is NaN with a warning, which would ship silently."""
        with pytest.raises(ValueError, match="no members"):
            cinf.ensemble_pmf([], np.zeros((1, 2)))


class TestDesignMatrixParity:
    """The quieter of the two drift risks.

    A forward pass that disagrees tends to disagree visibly. A design
    matrix whose column order is off by one produces a full matrix of
    plausible numbers and no error at all, and every probability built on
    it is wrong. So it gets the same treatment as the network.
    """

    @staticmethod
    def _frame() -> pd.DataFrame:
        rng = np.random.default_rng(19)
        n = 30
        frame = pd.DataFrame(
            {
                "bb_pctb": rng.normal(size=n),
                "k_full": rng.normal(size=n) * 20 + 50,
                "above_sma200": rng.integers(0, 2, size=n).astype(bool),
                "days_to_earnings": rng.normal(size=n),
                "spx_ret_1d": rng.normal(size=n) / 100,
                "sector": rng.choice(["Tech", "Energy", "Health"], size=n),
                "signal_type": rng.choice(["confluence_low", "bb_lower_touch"], size=n),
            }
        )
        # Missingness in both impute columns, which is the whole reason the
        # indicator columns exist -- a frame without NaNs would pass even if
        # they were dropped.
        frame.loc[frame.index[:5], "days_to_earnings"] = np.nan
        frame.loc[frame.index[3:8], "spx_ret_1d"] = np.nan
        return frame

    def test_it_matches_the_fitted_transform(self) -> None:
        from capitalscan.research import neural

        frame = self._frame()
        numeric_cols = ["bb_pctb", "k_full", "above_sma200", "days_to_earnings", "spx_ret_1d"]
        block = frame[numeric_cols].astype(float)
        design = neural.DesignMatrix(
            columns=tuple(numeric_cols),
            mean=block.mean(),
            std=block.std().replace(0.0, 1.0),
            categorical_levels=(
                ("sector", ("Energy", "Health", "Tech")),
                ("signal_type", ("bb_lower_touch", "confluence_low")),
            ),
        )
        expected = design.transform(frame)
        got = cinf.design_matrix(
            frame,
            design.columns,
            design.mean.to_numpy(dtype=float),
            design.std.to_numpy(dtype=float),
            design.categorical_levels,
        )
        assert got.shape == expected.shape
        assert np.allclose(got, expected, equal_nan=False)
        assert isinstance(design.mean, pd.Series)  # the fitted stats stay pandas

    def test_the_impute_columns_have_not_drifted(self) -> None:
        """`core` duplicates the list so it can import without `research`.

        Duplication is fine; silent divergence is not. If `research` gains
        a third imputed column and this does not, the matrix loses a column
        and every downstream number shifts.
        """
        from capitalscan.research import neural

        assert cinf.IMPUTE_COLS == neural.IMPUTE_COLS

    def test_an_unseen_categorical_level_encodes_as_all_zero(self) -> None:
        """The honest encoding of "not a level this model was fitted on".

        `IMPUTE_COLS` are in `columns` because the transform reads their
        missingness out of the numeric block. That is not a quirk of this
        test: `fit_design` always includes them, and a caller who omitted
        them would get a `KeyError` rather than a silently short matrix,
        which is the right failure.
        """
        frame = pd.DataFrame(
            {
                "days_to_earnings": [5.0],
                "spx_ret_1d": [0.01],
                "sector": ["Utilities"],  # never in the fitted levels
            }
        )
        cols = ["days_to_earnings", "spx_ret_1d"]
        got = cinf.design_matrix(
            frame,
            cols,
            np.zeros(2),
            np.ones(2),
            [("sector", ("Tech", "Energy"))],
        )
        # Two numeric, then the sector one-hot, then the two indicators.
        assert got.shape == (1, 2 + 2 + 2)
        assert got[0, 2] == 0.0
        assert got[0, 3] == 0.0
