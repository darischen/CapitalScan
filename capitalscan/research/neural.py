"""A multi-task distributional model for ADR 113's four labels.

**One trunk, four heads, trained jointly on summed CRPS.** This is the one
thing a gradient-boosted tree structurally cannot do, and on this
population it is the difference between the directional heads losing to a
constant and beating it.

ADR 113 fits twenty independent boosters. `fwd_ret_5d`, `fwd_ret_10d`,
`peak_ret_5d` and `peak_ret_10d` are four views of the *same price path
after the same signal*, so anything the features say about one says
something about the other three, and twenty independent fits throw that
away twenty times. The effective sample after cluster weighting is near
8,000; the measured failure across Sessions 22-23 is a directional signal
too weak to separate from noise. Multi-task learning is the textbook
response to exactly that shape -- the auxiliary labels regularise the
shared representation, so the trunk has to explain four outcomes with one
set of features instead of memorising one.

The arrangement is favourable here rather than incidental. The peak heads
are the strong ones (10-20% over baseline) and the terminal q50 heads are
the weak ones (negative). **The tasks that work supervise the
representation the failing task has to use.**

**Measured on validate, 2026-09-03, against the ADR 063 LightGBM
incumbent** (three seeds, ensembled, same purged walk-forward selection
protocol, same baselines, same cluster weighting, scored by the same
`promotion.score_family`):

| | incumbent | multi-task |
|---|---|---|
| heads beating the global constant | 18/20 | 18/20 |
| heads beating the incumbent | -- | 16/20 (14 beyond seed spread) |
| coverage within 5 points | 14/20 | **17/20** |
| `terminal_h5_q50` | **-0.43%** | **+0.43%** |
| `terminal_h10_q50` | **-0.64%** | **+0.78%** |

**Nothing here promotes anything.** Fitting and promoting are separate acts
(ADR 067). Coverage is 17/20, not 20/20, so DESIGN §7.7 check 3 still
fails and the gate does not pass. `handlers/predict.py` still returns
`NotFound`, `predictions` stays empty, and `v_screen_live` -- which already
`LEFT JOIN`s that table -- shows nothing. Selection among several
challengers on the split the gate scores is itself a form of fitting, and
only the holdout can price it. The holdout is untouched.

**torch is an optional dependency** (`uv sync --extra neural`). Everything
importable without it lives in `core/distributions.py`, which is where the
grids, the CDF inversion and the CRPS live and where they are tested. This
module imports torch lazily so the package, the CLI and the fast test tier
all import cleanly on a machine that has never installed it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

import numpy as np
import pandas as pd

from capitalscan.core import distributions as dist
from capitalscan.core import folds as core_folds
from capitalscan.research import features as feat
from capitalscan.research import train

#: The four labels, in a fixed order so a saved model's head index means
#: the same thing on every load.
TASKS: tuple[tuple[str, int], ...] = (("terminal", 5), ("terminal", 10), ("peak", 5), ("peak", 10))

#: Bins per task. Enough resolution to read tau=0.05 without the
#: interpolation dominating, few enough that each bin keeps real mass.
N_BINS = 32

#: Written here rather than in `core/config.py` for the same reason
#: `train.LGBM_PARAMS` is: these are not sweepable *study* parameters,
#: changing them does not move `config_hash`, and invariant 9 governs
#: thresholds the engine compares against. Seeded, because Session 23
#: measured an unseeded A/B producing 17/20 and 18/20 from identical code.
TRAINING: dict[str, Any] = {
    "width": 384,
    "dropout": 0.15,
    "lr": 1e-3,
    "weight_decay": 1e-4,
    "batch_size": 1024,
    "max_steps": 6000,
    "patience": 25,
    "eval_every": 25,
}

#: Three, and the ensemble of them is what would ship. Odd seeds do not
#: matter; that there are several does. The measured per-head spread on
#: this model is 0.25-1.57 points of improvement, which is the size of most
#: of the effects being claimed.
DEFAULT_SEEDS: tuple[int, ...] = (20260903, 20260904, 20260905)

#: Fallback when no fold produced a usable step count. Mirrors
#: `promotion.DEFAULT_ROUNDS`, which exists for the same reason.
DEFAULT_STEPS = 300

#: The two features carrying NaNs that a network cannot consume the way
#: LightGBM does. Mean-imputed **with a companion indicator column**, so
#: the fact of missingness survives into the matrix. That is what keeps
#: this inside invariant 4, which forbids inventing a value and then
#: forgetting you invented it -- not imputation as such.
IMPUTE_COLS: tuple[str, ...] = ("days_to_earnings", "spx_ret_1d")


def _require_torch():
    """Import torch on demand, with a message naming the extra.

    A bare `ModuleNotFoundError: torch` from inside a research module reads
    like a broken install rather than an unselected optional dependency.
    """
    try:
        import torch
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise ModuleNotFoundError(
            "capitalscan.research.neural needs PyTorch, which is an optional "
            "dependency. Install it with `uv sync --extra neural`. The pure "
            "distribution arithmetic in capitalscan.core.distributions needs "
            "no such thing and is where the grids and CRPS live."
        ) from exc
    return torch


@dataclass(frozen=True)
class DesignMatrix:
    """A fitted encoding of the feature frame, and the statistics behind it.

    The statistics are carried rather than recomputed because they come
    from **train only**. Standardising a later frame on its own mean and
    scale would quietly remove the regime shift this study exists to
    measure -- validate's returns are 12-28% more dispersed than train's,
    and a per-frame standardiser would hide exactly that.
    """

    columns: tuple[str, ...]
    mean: pd.Series
    std: pd.Series
    #: One entry per categorical, in `feat.CATEGORICAL_COLS` order, each
    #: mapping the column to its train-observed levels.
    #:
    #: **Was `sector_levels` until 2026-09-04, and that was a silent
    #: dropper.** `transform` one-hot encoded `frame["sector"]` by name
    #: while `_numeric_block` excluded *every* categorical, so a second
    #: categorical was removed from the numeric side and never encoded on
    #: the other -- it vanished with no error. Adding `signal_type` (ADR
    #: 173) produced two arms with byte-identical inputs: the same step
    #: counts [406, 404, 372] and a delta of exactly 0.000 on all twenty
    #: heads. Only that impossible-looking zero exposed it.
    categorical_levels: tuple[tuple[str, tuple[str, ...]], ...]

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        numeric = _numeric_block(frame, self.columns)
        indicators = [numeric[c].isna().to_numpy(float)[:, None] for c in IMPUTE_COLS]
        scaled = ((numeric - self.mean) / self.std).fillna(0.0)

        # Every categorical, by name from the fitted encoding -- not one
        # hardcoded column. A level unseen in train encodes as all-zero,
        # which is the honest representation of "not a level this model
        # was fitted on" and is what an unknown sector already did.
        blocks = [scaled.to_numpy(float)]
        for col, levels in self.categorical_levels:
            values = frame[col].to_numpy()
            blocks.append(np.stack([(values == lv).astype(float) for lv in levels], axis=1))
        blocks.extend(indicators)
        return np.concatenate(blocks, axis=1)

    @property
    def n_features(self) -> int:
        widths = sum(len(levels) for _, levels in self.categorical_levels)
        return len(self.columns) + widths + len(IMPUTE_COLS)


def _numeric_block(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    block = frame[list(columns)].copy()
    for col in block.columns:
        if block[col].dtype == bool:
            block[col] = block[col].astype(float)
    return block.astype(float)


def fit_design(train_frame: pd.DataFrame) -> DesignMatrix:
    """Learn the encoding from train, once."""
    columns = tuple(c for c in feat.FEATURE_COLS if c not in feat.CATEGORICAL_COLS)
    numeric = _numeric_block(train_frame, columns)
    levels = tuple(
        (col, tuple(sorted(train_frame[col].dropna().unique())))
        for col in feat.CATEGORICAL_COLS
        if col in feat.FEATURE_COLS
    )
    return DesignMatrix(
        columns=columns,
        mean=numeric.mean(),
        std=numeric.std().replace(0.0, 1.0),
        categorical_levels=levels,
    )


def _build_module(n_features: int, n_tasks: int, n_bins: int):
    """Shared trunk, one softmax head per task.

    Wider than a single-task trunk would be (384) because it now carries
    four tasks. Capacity is the one hyperparameter multi-task genuinely
    changes: too narrow and the tasks compete for it, which shows as *every*
    head getting worse rather than one.
    """
    torch = _require_torch()
    import torch.nn as nn

    width = int(TRAINING["width"])
    drop = float(TRAINING["dropout"])

    class MultiHeadCRPS(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.trunk = nn.Sequential(
                nn.Linear(n_features, width),
                nn.GELU(),
                nn.Dropout(drop),
                nn.Linear(width, width),
                nn.GELU(),
                nn.Dropout(drop),
                nn.Linear(width, width // 2),
                nn.GELU(),
            )
            self.heads = nn.ModuleList([nn.Linear(width // 2, n_bins) for _ in range(n_tasks)])

        def forward(self, x: Any) -> Any:
            hidden = self.trunk(x)
            return torch.stack([head(hidden) for head in self.heads], dim=1)

    return MultiHeadCRPS()


def summed_crps(logits, targets, grids, weights):
    """Summed CRPS across the tasks. The training objective.

    **Summed, not weighted.** The four labels are in the same units
    (fractional return) and their CRPS values are the same order of
    magnitude, so a plain sum already balances them. A learned weighting
    would be four more parameters fitted on the same thin population, for a
    balance that is not measurably wrong.

    **CRPS, not cross-entropy.** Cross-entropy treats the bins as unordered
    labels, so the neighbouring bin costs exactly what the far tail costs.
    These bins are ordered returns. `core.distributions.crps` carries the
    same integral, in numpy, and is what the result is scored with.
    """
    torch = _require_torch()

    total = None
    for k in range(logits.shape[1]):
        pmf = torch.softmax(logits[:, k, :], dim=1)
        cdf = torch.cumsum(pmf, dim=1)
        step = (grids[k, 1:] - grids[k, :-1]).view(1, -1)
        indicator = (targets[:, k].view(-1, 1) <= grids[k, 1:].view(1, -1)).to(cdf.dtype)
        per_row = (((cdf - indicator) ** 2) * step).sum(dim=1)
        total = per_row if total is None else total + per_row
    return (total * weights).sum() / weights.sum()


def fold_ladder(
    frame: pd.DataFrame, horizon: int, calendar: Sequence[date]
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Every purged, embargoed walk-forward fold inside train.

    **The ladder, not one split, and the difference was measured.** An
    earlier version selected its step count on a single inner split --
    train's last year -- and selected badly: 2021 is calm, validate
    (2022-2023) is not, so a model tuned to stop when it fits 2021 keeps
    fitting until its fan is too narrow for the later regime. The selected
    model scored **-5.9%** on `terminal_h5_q05` where a shorter fit scored
    +1.6%.

    `promotion._fit_and_predict` already had the right protocol: the median
    `best_iteration` across the whole ladder. Copying LightGBM's model but
    not its selection procedure compares two procedures, not two models.
    """
    years = pd.to_datetime(frame["signal_date"]).dt.year
    out: list[tuple[np.ndarray, np.ndarray]] = []
    for fold in core_folds.walk_forward_folds(int(years.min()), int(years.max())):
        tr_mask, va_mask = train._fold_masks(frame, fold, calendar, horizon_days=horizon)
        if tr_mask.sum() and va_mask.sum():
            out.append((tr_mask, va_mask))
    return out


@dataclass
class FittedModel:
    """One seed's fitted network, plus everything needed to read it."""

    design: DesignMatrix
    grids: np.ndarray
    module: Any
    seed: int
    steps: int
    inner_crps: float
    device: str

    def predict_pmf(self, frame: pd.DataFrame) -> np.ndarray:
        """`(rows, tasks, bins)` of probability mass."""
        torch = _require_torch()
        x = torch.tensor(self.design.transform(frame), dtype=torch.float32, device=self.device)
        self.module.eval()
        with torch.no_grad():
            logits = self.module(x)
            return np.asarray(torch.softmax(logits, dim=2).cpu().numpy(), dtype=float)


@dataclass
class Ensemble:
    """Several seeds, averaged as distributions.

    **Averaging pmfs, not quantiles.** They are different operations and
    only the first produces a distribution. Averaging five quantile vectors
    gives five numbers with no CDF behind them, so `exceedance` and
    `p_touch_*` would have nothing to read.

    Averaging is not a way of reporting the seed spread; it is the model
    that would ship. The spread is reported separately, by the caller, from
    the members.
    """

    members: list[FittedModel] = field(default_factory=list)

    @property
    def grids(self) -> np.ndarray:
        return self.members[0].grids

    def predict_pmf(self, frame: pd.DataFrame) -> np.ndarray:
        # `np.asarray` is not decoration: numpy's stubs type `np.mean` as
        # `Any` under the numpy that resolves for Python 3.11, which is what
        # CI runs, and mypy's `no-any-return` fires there and not on 3.14.
        stacked = np.mean([m.predict_pmf(frame) for m in self.members], axis=0)
        return np.asarray(stacked, dtype=float)

    def fan(
        self, frame: pd.DataFrame, family: str, horizon: int, taus: Sequence[float] | None = None
    ) -> dict[float, np.ndarray]:
        """The five quantiles for one task, ready for `score_family`.

        Monotone across tau by construction, so nothing is sorted. DESIGN
        §7.4's `sort_quantiles` repairs independently fitted heads that
        cross; a fan read off one CDF cannot cross, and sorting it would
        hide a bug rather than fix one.
        """
        index = TASKS.index((family, horizon))
        pmf = self.predict_pmf(frame)[:, index, :]
        return dist.quantiles_from_pmf(pmf, self.grids[index], taus or train.TAUS)

    def exceedance(
        self, frame: pd.DataFrame, family: str, horizon: int, threshold: float
    ) -> np.ndarray:
        """`P(Y > threshold)`, which fills `Prediction.p_touch_*`.

        The twenty-head architecture cannot answer this without fitting
        more heads; a predicted CDF answers it by being read the other way
        round.
        """
        index = TASKS.index((family, horizon))
        pmf = self.predict_pmf(frame)[:, index, :]
        return dist.exceedance(pmf, self.grids[index], threshold)


def _labels_matrix(frame: pd.DataFrame) -> np.ndarray:
    return np.stack(
        [
            pd.to_numeric(frame[train.label_for(f, h)], errors="coerce").to_numpy(float)
            for f, h in TASKS
        ],
        axis=1,
    )


def _run(module_factory, x, y, w, n_steps, seed, watch, grids_t, device):
    torch = _require_torch()

    # **Seed BEFORE the module is built, not after.** Until 2026-09-04 this
    # read `module_factory()` first and seeded second, so the initial
    # weights were drawn from whatever RNG state happened to be current and
    # `DEFAULT_SEEDS` controlled only the batch shuffle. Two runs of
    # identical code with identical seeds chose different step counts --
    # [364, 384, 372] against [437, 323, 510] -- and the "seed spread" the
    # session reported was measuring shuffles across three *unseeded*
    # initialisations, which is not what it claimed to measure.
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    # cuDNN picks algorithms by benchmarking unless told not to; two runs
    # can otherwise pick different kernels with different float ordering.
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

    module = module_factory().to(device)
    optimiser = torch.optim.AdamW(
        module.parameters(), lr=float(TRAINING["lr"]), weight_decay=float(TRAINING["weight_decay"])
    )
    batch = int(TRAINING["batch_size"])
    patience = int(TRAINING["patience"])
    eval_every = int(TRAINING["eval_every"])

    generator = torch.Generator().manual_seed(seed)
    order = torch.randperm(len(x), generator=generator).to(device)
    cursor = step = bad = 0
    best, best_step = float("inf"), 0

    while step < n_steps:
        if cursor + batch > len(order):
            order = torch.randperm(len(x), generator=generator).to(device)
            cursor = 0
        idx = order[cursor : cursor + batch]
        cursor += batch

        module.train()
        optimiser.zero_grad()
        summed_crps(module(x[idx]), y[idx], grids_t, w[idx]).backward()
        optimiser.step()
        step += 1

        if watch is not None and step % eval_every == 0:
            module.eval()
            with torch.no_grad():
                value = float(summed_crps(module(watch[0]), watch[1], grids_t, watch[2]))
            if value < best - 1e-9:
                best, best_step, bad = value, step, 0
            else:
                bad += 1
                if bad >= patience:
                    break

    module.eval()
    return module, best_step, best


def fit(
    train_frame: pd.DataFrame,
    calendar: Sequence[date],
    seeds: Sequence[int] = DEFAULT_SEEDS,
    n_bins: int = N_BINS,
    device: str | None = None,
) -> Ensemble:
    """Fit one network per seed and return them as an ensemble.

    **Steps, not epochs, and this was measured too.** One pass over 158k
    rows at batch 1024 is 154 optimiser steps. Selecting on whole epochs
    made every configuration report its best score at epoch 1 and stop --
    not because nothing was learned, but because the optimum sat *inside*
    the first pass. A patience whose unit is coarser than the thing it
    watches always returns the first value.

    **Two fits per seed, deliberately.** The ladder fits exist only to
    produce a step count; the final fit is the model, and it sees every
    train row -- which is what `promotion._fit_and_predict` does with
    LightGBM's median CV rounds. Reusing a fold's model would score
    something fitted on a strict subset of the data the incumbent got.

    The ladder uses the **10-day** purge for every task even though two
    tasks are 5-day. That is the safe direction: it removes more training
    rows near each boundary, never fewer.
    """
    torch = _require_torch()
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")

    design = fit_design(train_frame)
    y = _labels_matrix(train_frame)
    w = np.asarray(core_folds.cluster_weights(list(train_frame["cluster_id"])))
    x = design.transform(train_frame)

    grids = np.stack([dist.crps_grid(y[:, k], n_bins) for k in range(len(TASKS))])
    grids_t = torch.tensor(grids, dtype=torch.float32, device=device)

    resolved = ~np.isnan(y).any(axis=1)
    ladder = [(a & resolved, b & resolved) for a, b in fold_ladder(train_frame, 10, calendar)]

    def tensor(values):
        return torch.tensor(values, dtype=torch.float32, device=device)

    def factory():
        return _build_module(design.n_features, len(TASKS), n_bins)

    ensemble = Ensemble()
    for seed in seeds:
        picks, inners = [], []
        for tr_mask, va_mask in ladder:
            watch = (tensor(x[va_mask]), tensor(y[va_mask]), tensor(w[va_mask]))
            _, step, value = _run(
                factory,
                tensor(x[tr_mask]),
                tensor(y[tr_mask]),
                tensor(w[tr_mask]),
                int(TRAINING["max_steps"]),
                seed,
                watch,
                grids_t,
                device,
            )
            if step > 0:
                # Rescaled to the full frame so the final fit makes a
                # comparable number of passes over a larger training set.
                picks.append(step * float(resolved.sum()) / float(tr_mask.sum()))
                inners.append(value)

        steps = int(round(statistics.median(picks))) if picks else DEFAULT_STEPS
        module, _, _ = _run(
            factory,
            tensor(x[resolved]),
            tensor(y[resolved]),
            tensor(w[resolved]),
            max(steps, 1),
            seed,
            None,
            grids_t,
            device,
        )
        ensemble.members.append(
            FittedModel(
                design=design,
                grids=grids,
                module=module,
                seed=seed,
                steps=steps,
                inner_crps=float(statistics.median(inners)) if inners else float("nan"),
                device=device,
            )
        )
    return ensemble
