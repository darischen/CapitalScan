"""Score events from a saved artifact, without fitting and without torch.

ADR 181. `run_predict` fits (24 model fits, ~11 minutes) and then scores.
This scores only, in milliseconds, from what that run wrote.

**It reuses `FittedPredictor.apply` rather than reimplementing it.**
`apply` decides which head backs which field, reads the calibration table,
and attaches the interval and `n_eff` that invariant 8 requires. Writing a
second copy of that against the same tables is how the two would come to
disagree about what `p_touch_3` means -- so instead this supplies an object
with the `Ensemble` interface `apply` expects, backed by numpy.

**Importing `research.predict` does not require torch.** `neural` imports
torch only inside `_require_torch`, on demand, and nothing here reaches
that path: every forward pass goes through `core.inference`. That is what
lets this module run on the Pi.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from capitalscan.core import distributions as dist
from capitalscan.core import inference as cinf
from capitalscan.jobs import artifact as art


@dataclass(frozen=True)
class NumpyEnsemble:
    """The `Ensemble` surface `FittedPredictor.apply` uses, in numpy.

    Three members: `grids`, `predict_pmf`, `fan`. `apply` touches nothing
    else, and a test asserts that, so this cannot silently fall behind a
    method `apply` starts calling.

    **Not a subclass of `neural.Ensemble`.** Subclassing would inherit
    `predict_pmf`, and an override missed during a refactor would fall
    through to the torch path and fail on the Pi with an import error
    rather than here with a clear one.
    """

    artifact: art.Artifact

    @property
    def grids(self) -> np.ndarray:
        return self.artifact.grids

    def _design(self, frame: pd.DataFrame) -> np.ndarray:
        return cinf.design_matrix(
            frame,
            self.artifact.columns,
            self.artifact.mean,
            self.artifact.std,
            self.artifact.categorical_levels,
        )

    def predict_pmf(self, frame: pd.DataFrame) -> np.ndarray:
        """`(rows, tasks, bins)`, averaged across seeds as mass."""
        return cinf.ensemble_pmf(self.artifact.members, self._design(frame))

    def fan(
        self,
        frame: pd.DataFrame,
        family: str,
        horizon: int,
        taus: "list[float] | tuple[float, ...] | None" = None,
    ) -> dict[float, np.ndarray]:
        """Mirrors `neural.Ensemble.fan`, including its TAUS default.

        `TASKS` and `TAUS` are imported here rather than duplicated: they
        are ordering contracts, and a second copy that drifted would map a
        field onto the wrong head with no error at all.
        """
        from capitalscan.research import neural, train

        index = neural.TASKS.index((family, horizon))
        pmf = self.predict_pmf(frame)[:, index, :]
        return dist.quantiles_from_pmf(pmf, self.grids[index], taus or train.TAUS)


def load_predictor(config_hash: str, git_sha: str, path: Path = art.DEFAULT_PATH):
    """A `FittedPredictor` backed by the saved artifact.

    Raises `art.StaleArtifact` when the artifact does not match, which the
    caller must not swallow: a scorer that refits on the hot path has
    given up the thing this exists for, and one that scores anyway is
    publishing numbers from a model whose features have moved.
    """
    from capitalscan.research import predict as rp

    loaded = art.load(config_hash, git_sha, path)
    return rp.FittedPredictor(
        ensemble=NumpyEnsemble(loaded),  # type: ignore[arg-type]
        tables=loaded.tables,
        model_version=loaded.model_version,
        n_train=loaded.n_train,
        n_calibrate=loaded.n_calibrate,
        trained_signal_types=loaded.trained_signal_types,
    )
