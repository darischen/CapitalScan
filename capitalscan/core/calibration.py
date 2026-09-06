"""Empirical reliability: turning a model's probability into a claim you can print.

ADR 174. A network emits `p = 0.74` for "this name touches +3% within five
sessions". Invariant 8 says nothing carrying a probability may ship without
`n_eff` and an interval. The question this module answers is where that
interval comes from, and the answer is deliberately **not** the model.

**Why not the model.** Three seeds disagree by some amount, and that
disagreement is tempting to report. It is the wrong quantity. Seed spread
measures how sensitive the fit is to initialisation, which is a fact about
the optimiser and not about the world; three seeds that happen to agree
would produce a tight interval around a number that is simply wrong. The
same objection defeats the softmax's own width -- a confidently
miscalibrated network states a narrow distribution and is still miscalibrated.

**What replaces it.** Bucket historical predictions by their predicted
value, then look at what actually happened in each bucket. If predictions
near 0.74 resolved 0.71 of the time across a large enough sample, then 0.71
is the number to print and the sampling interval around it is the honest
width. This is histogram binning, and it fixes calibration and supplies the
interval in one pass.

Four choices in here are load-bearing:

**Equal-mass buckets, not equal-width.** Predicted probabilities pile up
near the base rate. Equal-width bins put most of the sample in two of them
and leave the tails at n=12, which is the same mistake the CRPS grid made
in ADR 170 and which cost a working model there.

**Kish `n_eff`, never the row count.** Events cluster -- one market-wide
selloff fires hundreds at once and they resolve together. `n` counts rows,
`n_eff = (sum w)^2 / sum w^2` counts independent information, and an
interval sized on `n` is too narrow by exactly the factor invariant 8
exists to catch.

**Isotonic pooling (PAVA) before anything is published.** Raw bucket rates
wobble; a reader seeing decile 7 above decile 8 loses the plot, and there is
no mechanism by which a higher prediction should mean a lower outcome.
Pool-adjacent-violators merges offending neighbours into a single block
with a pooled rate *and a pooled interval*, so a merged block correctly
reports itself as the wider, less certain thing it is.

**Piecewise-constant, not interpolated.** Interpolating between bucket
centres looks smoother and quietly breaks the one property that matters for
display: the published point must lie inside its own published interval.
Interpolation moves the point by up to the gap between adjacent rates,
which exceeds the half-width whenever buckets are well separated. Ten
distinct values is not a defect. It is the resolution the sample supports,
and saying so is the point of the module.

**Invariant 1.** No IO. The table is built from arrays by `research/`,
persisted by `jobs/`, and read back through `from_dict`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from capitalscan.core.stats import wilson_ci

# Ten is the resolution the validate sample supports: ~8,000 effective
# observations over ten buckets leaves ~800 each, a Wilson half-width near
# 0.03. Twenty buckets would halve that and double the width, buying
# precision the data does not have.
DEFAULT_BUCKETS = 10

#: What a reader must be told alongside any probability this module
#: calibrates (ADR 174).
#:
#: **It lives in `core` because both sides need it and neither may import
#: the other.** `research/predict.py` writes it into every `predictions`
#: row; `handlers/predict.py` re-exports it to the wire. A handler that
#: imported `research` to reach a string would drag the fitting stack into
#: the serving path, and no handler has ever done that.
MODEL_CAVEAT = (
    "Probabilities are calibrated on the validate split, which has been "
    "scored repeatedly during model selection, so the intervals are a lower "
    "bound on the true uncertainty. Coverage also decays with distance from "
    "the training window (2024 0.0182, 2025 0.0311, 2026 0.0480). Advisory "
    "only: this states what historically followed signals like this one, "
    "not what will happen."
)


def kish_n_eff(weights: Sequence[float]) -> float:
    """Kish effective sample size, `(sum w)^2 / sum w^2`.

    Equal weights return the count. One dominant weight returns ~1, which
    is the honest reading of "this bucket is really a single observation
    repeated". Empty or all-zero returns 0.0 rather than raising, because a
    bucket can legitimately be empty and the caller suppresses on `n_eff`.
    """
    w = np.asarray(weights, dtype=float)
    if w.size == 0:
        return 0.0
    s1 = float(w.sum())
    s2 = float((w**2).sum())
    if s2 <= 0.0 or s1 <= 0.0:
        return 0.0
    return s1 * s1 / s2


@dataclass(frozen=True)
class Bucket:
    """One band of predicted probability, and what actually happened in it.

    `p_hat` is the published probability for any prediction landing here --
    already isotonic-pooled, so it is comparable across buckets. `lo`/`hi`
    are edges in *predicted* space and exist for lookup and for display of
    what the band covers; they are not themselves a claim.

    **`n` and `n_eff` describe the same population, and after isotonic
    pooling that population is the merged block rather than this bucket.**
    Neighbouring buckets that PAVA merged therefore report identical `n`,
    `n_eff`, `p_hat` and interval, differing only in their edges. Reporting
    a bucket's own row count beside the block's effective count would let
    `n_eff` exceed `n`, which is impossible for Kish and reads as a bug.
    """

    index: int
    lo: float
    hi: float
    n: int
    n_eff: float
    p_hat: float
    ci_low: float
    ci_high: float

    @property
    def width(self) -> float:
        """Interval width. The comparator for 'which bucket is least sure'."""
        return self.ci_high - self.ci_low


@dataclass(frozen=True)
class ReliabilityTable:
    """A fitted calibration curve for one binary target.

    Built on a held-out split by `research/`, written alongside the
    predictions it calibrates, and read back at serve time. It is versioned
    with the model because a table fitted against different weights
    silently miscalibrates -- the failure mode is a plausible number, never
    an error.
    """

    target: str
    buckets: tuple[Bucket, ...]
    alpha: float = 0.05

    def lookup(self, p: float) -> Bucket:
        """The bucket a raw prediction falls in, clamped at both ends.

        A value below the lowest observed prediction gets the first bucket
        rather than an error: the model is allowed to go somewhere the
        calibration sample did not, and the correct response is the nearest
        measured evidence, not a refusal.

        **NaN gets the widest bucket**, not the nearest one. An absent
        prediction must never surface as a confident one, and the widest
        interval in the table is the weakest claim available.
        """
        if math.isnan(p):
            return max(self.buckets, key=lambda b: b.width)
        for b in self.buckets:
            if p < b.hi:
                return b
        return self.buckets[-1]

    def calibrate(self, p: float) -> float:
        """The probability to publish for a raw prediction `p`.

        Piecewise constant by design (see the module docstring): the return
        is the pooled realised rate of `p`'s bucket, which is guaranteed to
        sit inside that bucket's interval. NaN in, NaN out -- a missing
        feature must not become a number.
        """
        if math.isnan(p):
            return float("nan")
        return self.lookup(p).p_hat

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form, for `predictions.features_json` and audit."""
        return {
            "target": self.target,
            "alpha": self.alpha,
            "buckets": [
                {
                    "index": b.index,
                    "lo": b.lo,
                    "hi": b.hi,
                    "n": b.n,
                    "n_eff": b.n_eff,
                    "p_hat": b.p_hat,
                    "ci_low": b.ci_low,
                    "ci_high": b.ci_high,
                }
                for b in self.buckets
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReliabilityTable":
        return cls(
            target=str(payload["target"]),
            alpha=float(payload.get("alpha", 0.05)),
            buckets=tuple(
                Bucket(
                    index=int(b["index"]),
                    lo=float(b["lo"]),
                    hi=float(b["hi"]),
                    n=int(b["n"]),
                    n_eff=float(b["n_eff"]),
                    p_hat=float(b["p_hat"]),
                    ci_low=float(b["ci_low"]),
                    ci_high=float(b["ci_high"]),
                )
                for b in payload["buckets"]
            ),
        )


def _pool_adjacent_violators(
    blocks: list[tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    """PAVA over `(sum_w, sum_w2, sum_wy)` blocks, merging on rate order.

    Sums are pooled rather than rates averaged, so a merged block's `n_eff`
    reflects the combined sample. Averaging the two rates instead would
    keep each block's original width and understate the uncertainty that
    caused the merge in the first place.
    """
    out: list[tuple[float, float, float]] = []
    for blk in blocks:
        out.append(blk)
        while len(out) >= 2:
            w1, q1, y1 = out[-2]
            w2, q2, y2 = out[-1]
            if w1 <= 0 or w2 <= 0 or (y1 / w1) <= (y2 / w2):
                break
            out[-2:] = [(w1 + w2, q1 + q2, y1 + y2)]
    return out


def build_reliability(
    target: str,
    predicted: Sequence[float] | np.ndarray,
    realised: Sequence[float] | np.ndarray,
    weights: Sequence[float] | np.ndarray | None = None,
    n_buckets: int = DEFAULT_BUCKETS,
    alpha: float = 0.05,
) -> ReliabilityTable:
    """Fit the reliability table for one binary target.

    Args:
        target: the field this calibrates, e.g. `"p_touch_3"`. Stored so a
            table cannot be applied to the wrong head without it showing.
        predicted: raw model probabilities, in [0, 1].
        realised: the 0/1 outcomes.
        weights: cluster weights (`core.folds.cluster_weights`). Defaults
            to equal, which is correct only when events are independent --
            they are not, so callers should pass them.
        n_buckets: requested equal-mass buckets. Fewer are returned when
            ties in `predicted` collapse edges, which is normal near a hard
            base rate and not an error.

    Raises:
        ValueError: on length mismatch, probabilities outside [0, 1], or a
            target with only one outcome present. That last one matters:
            a constant target makes every rate 0 or 1 and every interval
            meaningless, and it is the shape a mis-joined label arrives in.
    """
    p = np.asarray(predicted, dtype=float)
    y = np.asarray(realised, dtype=float)
    if p.shape != y.shape:
        raise ValueError(f"predicted and realised must share a length, got {p.shape} and {y.shape}")
    w = np.ones_like(p) if weights is None else np.asarray(weights, dtype=float)
    if w.shape != p.shape:
        raise ValueError(f"weights must share a length with predicted, got {w.shape} and {p.shape}")

    ok = ~(np.isnan(p) | np.isnan(y))
    p, y, w = p[ok], y[ok], w[ok]
    if p.size and (p.min() < 0.0 or p.max() > 1.0):
        raise ValueError(f"predicted probabilities must lie in [0, 1], got [{p.min()}, {p.max()}]")
    if not np.isin(y, (0.0, 1.0)).all():
        raise ValueError("realised must be 0/1")
    if len(np.unique(y)) < 2:
        raise ValueError(f"{target}: needs both outcomes present to calibrate against")

    # Equal-MASS edges. See the module docstring for why width fails here.
    edges = np.unique(np.quantile(p, np.linspace(0.0, 1.0, n_buckets + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)

    raw: list[tuple[float, float, float]] = []
    spans: list[tuple[float, float, int]] = []
    for k in range(len(edges) - 1):
        m = idx == k
        wk = w[m]
        raw.append((float(wk.sum()), float((wk**2).sum()), float((wk * y[m]).sum())))
        spans.append((float(edges[k]), float(edges[k + 1]), int(m.sum())))

    pooled = _pool_adjacent_violators(raw)

    # Fan each pooled block back out over the buckets it absorbed, so every
    # bucket in a block reports the block's rate AND the block's wider
    # interval. A merged block that kept its members' original widths would
    # hide the very uncertainty that forced the merge.
    buckets: list[Bucket] = []
    k = 0
    for sum_w, sum_w2, sum_wy in pooled:
        n_eff = (sum_w * sum_w / sum_w2) if sum_w2 > 0 else 0.0
        p_hat = (sum_wy / sum_w) if sum_w > 0 else 0.0
        lo, hi = wilson_ci(p_hat * n_eff, n_eff, alpha=alpha) if n_eff > 0 else (0.0, 1.0)
        # Absorb this block's buckets first, so `n` can be pooled alongside
        # `n_eff`. Reporting a bucket's own row count beside the block's
        # effective count puts two different populations on one record, and
        # produces the impossible-looking `n_eff > n` -- Kish is bounded
        # above by the count of the sample it was computed over, never by
        # the count of some subset of it.
        first = k
        taken = 0.0
        while k < len(raw) and (taken < sum_w - 1e-9 or raw[k][0] == 0.0):
            taken += raw[k][0]
            k += 1
        block_n = sum(spans[j][2] for j in range(first, k))
        for j in range(first, k):
            e_lo, e_hi, _ = spans[j]
            buckets.append(
                Bucket(
                    index=len(buckets),
                    lo=e_lo,
                    hi=e_hi,
                    n=block_n,
                    n_eff=n_eff,
                    p_hat=p_hat,
                    ci_low=min(lo, p_hat),
                    ci_high=max(hi, p_hat),
                )
            )
    return ReliabilityTable(target=target, buckets=tuple(buckets), alpha=alpha)
