"""A fitted network's forward pass, in numpy.

**Why this exists: so the Pi can score a signal without torch.** The
poller runs there, and a fire should carry a prediction by the time it
reaches the screen. Training needs torch; *reading* a fitted model does
not. The network is a three-layer MLP with linear heads, so the forward
pass is four matrix multiplies, a GELU and a softmax — about thirty lines
against a 2GB ARM wheel the Pi has no business carrying.

**This module performs no IO**, per invariant 1. It takes arrays and
returns arrays. `jobs/` owns loading the file they came from.

**It is not a reimplementation of the model.** It is the same arithmetic
against the same weights, and `test_inference_parity.py` asserts the two
agree to 1e-5 on random input. A second implementation that drifts from
the first is worse than no second implementation, so the parity test is
the load-bearing part of this file, not a nicety.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

#: Matches `torch.nn.GELU()`'s default, which is the exact erf form rather
#: than the tanh approximation. The two differ by ~1e-3 at the elbow — far
#: above the 1e-5 the parity test holds to, so picking the wrong one would
#: fail loudly rather than drift. That is deliberate.
_SQRT2 = math.sqrt(2.0)


def gelu(x: np.ndarray) -> np.ndarray:
    """Exact GELU: `x * Phi(x)`, with `Phi` the standard normal CDF.

    `math.erf` rather than `scipy.special.erf` because scipy is not a
    dependency and this must run wherever numpy does. `np.vectorize` is
    slow per element and irrelevant here: the Pi scores a few hundred rows
    at a time, not a training batch.
    """
    erf = np.vectorize(math.erf, otypes=[np.float64])
    out: np.ndarray = x * 0.5 * (1.0 + erf(x / _SQRT2))
    return out


def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Row-wise softmax, shifted by the row max before exponentiating.

    The shift is not an optimisation. Without it a logit around 750
    overflows `exp` to `inf` and the row comes back as NaN, which would
    reach the screen as a missing probability rather than an error.
    """
    shifted = x - np.max(x, axis=axis, keepdims=True)
    exp = np.exp(shifted)
    out: np.ndarray = exp / np.sum(exp, axis=axis, keepdims=True)
    return out


@dataclass(frozen=True)
class LinearLayer:
    """One `nn.Linear`'s weight and bias.

    `weight` is stored in torch's `(out, in)` orientation and applied as
    `x @ weight.T + bias`, so an export can hand over `state_dict` arrays
    untransposed. Transposing on export and again on load is how the two
    implementations would silently disagree.
    """

    weight: np.ndarray
    bias: np.ndarray

    def __call__(self, x: np.ndarray) -> np.ndarray:
        out: np.ndarray = x @ self.weight.T + self.bias
        return out


@dataclass(frozen=True)
class NetworkWeights:
    """One seed's trunk and heads, enough to reproduce `forward`.

    Dropout is absent by design: it is the identity at inference, and
    carrying a rate that is never applied invites someone to apply it.
    """

    trunk: tuple[LinearLayer, ...]
    heads: tuple[LinearLayer, ...]

    def logits(self, x: np.ndarray) -> np.ndarray:
        """`(rows, tasks, bins)`, matching the torch module's `forward`."""
        hidden = x
        for layer in self.trunk:
            hidden = gelu(layer(hidden))
        return np.stack([head(hidden) for head in self.heads], axis=1)

    def pmf(self, x: np.ndarray) -> np.ndarray:
        """`(rows, tasks, bins)` of probability mass."""
        return softmax(self.logits(x), axis=-1)


def ensemble_pmf(members: Sequence[NetworkWeights], x: np.ndarray) -> np.ndarray:
    """The seed-averaged pmf.

    **Averaged as probability mass, not as logits.** Averaging logits then
    softmaxing is a different distribution — sharper, and no longer the
    mean of the members' beliefs. `research.neural.Ensemble` averages mass;
    so does this, and the parity test covers the ensemble rather than only
    a single member for exactly that reason.
    """
    if not members:
        raise ValueError("an ensemble with no members cannot produce a pmf")
    return np.mean([m.pmf(x) for m in members], axis=0)


#: The columns whose missingness is carried as its own indicator column,
#: mirroring `research.neural.IMPUTE_COLS`. Duplicated rather than imported
#: because `research` pulls sqlalchemy and torch, and this module has to
#: stay importable with numpy alone. `test_inference_parity.py` asserts the
#: two lists agree, so the duplication cannot drift.
IMPUTE_COLS: tuple[str, ...] = ("days_to_earnings", "spx_ret_1d")


def design_matrix(
    frame: Any,
    columns: Sequence[str],
    mean: np.ndarray,
    std: np.ndarray,
    categorical_levels: Sequence[tuple[str, Sequence[str]]],
) -> np.ndarray:
    """`research.neural.DesignMatrix.transform`, without torch or pandas state.

    **The second place two implementations could disagree**, and a quieter
    one than the forward pass: a column order that differs by one produces
    a full matrix of plausible numbers rather than an error. So this is
    parity-tested against `DesignMatrix.transform` exactly as the network
    is against torch.

    `mean` and `std` are the *train* statistics, carried rather than
    recomputed. Standardising a serving frame on its own moments would
    erase the regime difference the model is supposed to see, and it would
    do so invisibly.

    Takes a DataFrame because that is what the caller has; it reads columns
    and does no IO, so invariant 1 holds.
    """
    block = frame[list(columns)].copy()
    for col in block.columns:
        if block[col].dtype == bool:
            block[col] = block[col].astype(float)
    numeric = block.astype(float)

    # Built before scaling: `fillna(0.0)` below erases the missingness that
    # these columns exist to record.
    indicators = [numeric[c].isna().to_numpy(dtype=float)[:, None] for c in IMPUTE_COLS]

    scaled = ((numeric - mean) / std).fillna(0.0)
    blocks: list[np.ndarray] = [scaled.to_numpy(dtype=float)]

    # A level unseen in train encodes as all-zero, which is the honest
    # representation of "not a level this model was fitted on".
    for col, levels in categorical_levels:
        values = frame[col].to_numpy()
        blocks.append(np.stack([(values == lv).astype(float) for lv in levels], axis=1))

    blocks.extend(indicators)
    return np.concatenate(blocks, axis=1)
