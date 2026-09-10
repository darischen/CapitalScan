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

**Interpolated between block anchors, and the interval comes with it**
(ADR 192, amending ADR 174; this module argued the opposite until
2026-09-10 and the original text is worth keeping):

    Piecewise-constant, not interpolated. Interpolating between bucket
    centres looks smoother and quietly breaks the one property that
    matters for display: the published point must lie inside its own
    published interval. Interpolation moves the point by up to the gap
    between adjacent rates, which exceeds the half-width whenever buckets
    are well separated. Ten distinct values is not a defect. It is the
    resolution the sample supports, and saying so is the point of the
    module.

That objection is correct about interpolating the *point alone*, which is
what it assumed. `band()` interpolates `p_hat`, `ci_low` and `ci_high`
together, and since `ci_low <= p_hat <= ci_high` holds at every anchor, a
convex combination preserves it everywhere. The property is kept, not
traded.

What the original text got right and this does not overturn: the *level* is
supported to about a bucket half-width, ~2.7pp at `n_eff` around 1,300.
Interpolation does not buy precision in the level and does not claim to --
the interval says so, and it widens where the blocks are least sure.

What forced the change is **ranking**, which the level's own unreliability
makes the durable output (`CLAUDE.md`, and ADR 179 on the base rate moving
36.5-65.0% year to year). Measured on serving 2026-09-09: 498 predictions
carried **498 distinct raw scores and 9 distinct published values**, and a
single pooled block put **142 tickers on exactly 0.4780** spanning raw
0.375 to 0.495. Within that block the model's ordering was not coarse, it
was *gone* -- and a reader comparing two rows saw a tie the model never
expressed. Ten distinct values is a defect once it destroys the one output
the module can defend.

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
#: **Measured against the live forward log on 2026-09-08**, not asserted.
#: Across 4,020 resolved in-population predictions the ordering held across
#: all eight probability buckets (41.6% to 87.1% realised) while the shipped
#: value fell below the bucket's own 95% interval in six of the eight. The
#: cause is a base rate that will not sit still: the isotonic tables are
#: anchored to the validate split's 43.2%, the last twelve months average
#: about 49.5%, and the month-to-month range is 36.5% to 65.0%. That swing
#: is larger than the model's entire Brier skill of 0.079, so the ordering
#: is the durable part of the output and the level is not.
#:
#: Saying so is the point. A number carrying an interval that has been
#: measured to miss reads as more trustworthy than one that admits it,
#: which is the worse failure. → RESULTS.md 2026-09-08.
MODEL_CAVEAT = (
    "Use these to rank signals, not to read an exact chance. Measured "
    "against live results through 2026-09-08, the ordering held across "
    "every probability band, but the stated percentage ran low in six of "
    "eight bands. The reason is the market, not the signal: how often any "
    "signal reaches +3% has ranged from 37% to 65% month to month over the "
    "past year, while these numbers are anchored to a 43% period. Expect "
    "the figure to understate in a rising market and overstate in a "
    "falling one. Calibration also uses the validate split, which model "
    "selection has scored repeatedly, so the intervals are a lower bound "
    "on the true uncertainty, and accuracy decays with distance from the "
    "training window (2024 0.0182, 2025 0.0311, 2026 0.0480). Advisory "
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

    def _anchors(self) -> tuple[tuple[float, float, float, float, float], ...]:
        """One `(x, p_hat, lo, hi, n_eff)` per isotonic **block**, ascending.

        **Blocks, not buckets.** PAVA merges violating neighbours, and
        every bucket in a merged block reports the block's rate. Anchoring
        per bucket would place two anchors at the same height and
        interpolate a flat segment between them -- reproducing exactly the
        tie this exists to remove. On 2026-09-09 the largest block spanned
        two buckets and 142 tickers.

        `x` is the block's midpoint in predicted space. The outer blocks
        are half-open, so they borrow the median finite block width rather
        than trying to average an infinity.

        Anchors are forced strictly ascending. Ties in `x` would make the
        interpolation weight undefined, and PAVA guarantees ascending
        `p_hat` between blocks but says nothing about their spans.
        """
        blocks: list[list[Bucket]] = []
        for b in self.buckets:
            if blocks and blocks[-1][0].p_hat == b.p_hat:
                blocks[-1].append(b)
            else:
                blocks.append([b])

        widths = [
            blk[-1].hi - blk[0].lo
            for blk in blocks
            if math.isfinite(blk[0].lo) and math.isfinite(blk[-1].hi)
        ]
        fallback = (sorted(widths)[len(widths) // 2] / 2.0) if widths else 0.05

        out: list[tuple[float, float, float, float, float]] = []
        for blk in blocks:
            lo_edge, hi_edge = blk[0].lo, blk[-1].hi
            if math.isfinite(lo_edge) and math.isfinite(hi_edge):
                x = (lo_edge + hi_edge) / 2.0
            elif math.isfinite(hi_edge):
                x = hi_edge - fallback
            elif math.isfinite(lo_edge):
                x = lo_edge + fallback
            else:
                x = blk[0].p_hat
            head = blk[0]
            if out and x <= out[-1][0]:
                x = math.nextafter(out[-1][0], math.inf)
            out.append((x, head.p_hat, head.ci_low, head.ci_high, head.n_eff))
        return tuple(out)

    def band(self, p: float) -> tuple[float, float, float, float]:
        """`(p_hat, ci_low, ci_high, n_eff)` for a raw prediction `p`.

        **The point and its interval are interpolated together**, which is
        what keeps `ci_low <= p_hat <= ci_high` true: the relation holds at
        every anchor, and a convex combination of two anchors preserves it.
        Interpolating the point alone would not, and that is the objection
        the module docstring used to make against doing this at all.

        Outside the outermost anchors the nearest block's values are used
        unchanged rather than extrapolated. The model is allowed to go
        where the calibration sample did not; inventing a rate out there
        would be the one thing this module exists to refuse.

        `n_eff` takes the **smaller** of the two bracketing blocks, because
        an interpolated point is supported by neither block alone and the
        weaker claim is the honest one.
        """
        anchors = self._anchors()
        if not anchors:
            return (float("nan"), 0.0, 1.0, 0.0)
        if math.isnan(p):
            widest = max(self.buckets, key=lambda b: b.width)
            return (float("nan"), widest.ci_low, widest.ci_high, widest.n_eff)

        if p <= anchors[0][0]:
            _, y, lo, hi, n = anchors[0]
            return (y, lo, hi, n)
        if p >= anchors[-1][0]:
            _, y, lo, hi, n = anchors[-1]
            return (y, lo, hi, n)

        for left, right in zip(anchors, anchors[1:], strict=False):
            if left[0] <= p <= right[0]:
                span = right[0] - left[0]
                t = 0.0 if span <= 0 else (p - left[0]) / span
                return (
                    left[1] + t * (right[1] - left[1]),
                    left[2] + t * (right[2] - left[2]),
                    left[3] + t * (right[3] - left[3]),
                    min(left[4], right[4]),
                )
        _, y, lo, hi, n = anchors[-1]
        return (y, lo, hi, n)

    def calibrate(self, p: float) -> float:
        """The probability to publish for a raw prediction `p`.

        Interpolated between block anchors (ADR 192) so two raw scores that
        differ produce published values that differ -- the ordering the
        model expressed survives to the page. NaN in, NaN out: a missing
        feature must not become a number.
        """
        if math.isnan(p):
            return float("nan")
        return self.band(p)[0]

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
