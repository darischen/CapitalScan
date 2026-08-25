"""Purged walk-forward cross-validation (DESIGN §7.5, ADR 065).

Pure computation. No IO, no clock, no calendar lookup — invariant 1 applies
here as everywhere in `core/`, so the trading calendar arrives as an
argument rather than being read.

**Why not random K-fold.** An event on 2018-03-05 and one on 2018-03-07
share overlapping forward windows: they are two views of one market move.
A random split puts one on each side, the model is scored on outcomes it
has already seen, and the fold score comes back excellent. Nothing raises.
That is the failure this module exists to prevent, and it is invisible in
every metric that would normally catch a modelling error.

**Purge and embargo are asymmetric on purpose.**

    train ────────────────────┤ purge ├──┤ embargo ├──────── validate
                              boundary

*Purge* looks backwards from the boundary into **train**: a training event
whose forward window reaches past the boundary carries a label made partly
of the period the fold is meant to predict.

*Embargo* looks forwards into **validate**: a validation event in the
fold's first sessions is close enough in time to the last training events
to be the same move, even though no window formally overlaps.

DESIGN §7.5 measures the two together at ~10 events per fold. Small, and
the point is that the alternative is not "slightly optimistic" but
"unfalsifiable".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

#: DESIGN §7.5. Years of history before the first fold validates.
DEFAULT_MIN_TRAIN_YEARS = 5

#: Trading days dropped from the start of each validation window.
DEFAULT_EMBARGO_DAYS = 5


@dataclass(frozen=True)
class Fold:
    """One walk-forward fold, in years.

    Years rather than dates because the ladder is defined in years and a
    fold is a coarse object; the fine boundary work is `purge` and
    `embargo`, which take real dates.
    """

    train_start: int
    train_end: int
    validate: int


def walk_forward_folds(
    first_year: int,
    last_year: int,
    min_train_years: int = DEFAULT_MIN_TRAIN_YEARS,
) -> tuple[Fold, ...]:
    """The expanding-window ladder of DESIGN §7.5.

    **Expanding, never sliding.** Dropping early years to hold the training
    span fixed would discard data a population this thin cannot spare, and
    the argument for a sliding window — that old regimes mislead — is the
    same argument `era` was excluded as a feature for.

    Returns empty rather than one degenerate fold when the span is too
    short. A single fold is not cross-validation, and reporting it as such
    overstates what was checked.
    """
    first_validate = first_year + min_train_years
    if last_year < first_validate:
        return ()
    return tuple(
        Fold(train_start=first_year, train_end=year - 1, validate=year)
        for year in range(first_validate, last_year + 1)
    )


def purge(
    signal_dates: Sequence[date],
    boundary: date,
    horizon_days: int,
) -> list[bool]:
    """Keep-mask for training events, by whether their window clears the fold.

    An event at `d` resolves over roughly `[d, d + horizon_days]`. If that
    reaches the boundary, part of its label is drawn from the period the
    fold predicts, so it is dropped.

    **`horizon_days` must be the longest horizon trained**, not the
    shortest. ADR 113 fits h=5 and h=10 from one frame; purging at 5 leaves
    every 10-day label leaking, and the leak lands in exactly half the
    heads while the other half look clean.

    **Calendar days, deliberately, and this over-purges slightly.** A
    10-session window spans about 14 calendar days, so bounding by 10
    calendar days would under-purge. Over-purging costs a handful of
    training events; under-purging costs the guarantee. Where the trading
    calendar matters the caller passes a session count converted at the
    call site.
    """
    cutoff = boundary - timedelta(days=horizon_days)
    return [d < cutoff for d in signal_dates]


def embargo(
    signal_dates: Sequence[date],
    boundary: date,
    calendar: Sequence[date],
    embargo_days: int = DEFAULT_EMBARGO_DAYS,
) -> list[bool]:
    """Keep-mask for validation events, dropping the fold's first sessions.

    **Trading days, not calendar days.** Five sessions across a weekend is
    a week; five calendar days is three sessions. Counting the wrong unit
    under-embargoes every fold whose start falls near a holiday, which is
    most of them — 1 January is a holiday in every year this ladder covers.

    `calendar` is the sequence of trading days, ascending, supplied by the
    caller because `core/` may not read one.

    `embargo_days=0` is the ablation arm and keeps everything, so measuring
    what the embargo is worth needs no second code path.
    """
    if embargo_days <= 0:
        return [True] * len(signal_dates)

    sessions = [d for d in calendar if d >= boundary]
    if not sessions:
        return [True] * len(signal_dates)
    # The last session still inside the embargo. Anything on or before it
    # is dropped; `embargo_days` counts sessions, so index `n-1`.
    last_embargoed = sessions[min(embargo_days, len(sessions)) - 1]
    return [d > last_embargoed for d in signal_dates]


def cluster_weights(cluster_ids: Sequence[str | None]) -> list[float]:
    """Sample weights of `1 / |cluster|` (DESIGN §7.5).

    Four events from one cluster are four views of one move. Unweighted,
    that move counts four times, and the effective sample the
    hyperparameters were chosen against (~8,000) is not the row count.

    Weights sum to the number of distinct clusters, which is what makes
    this a de-duplication rather than a rescaling: each cluster contributes
    one event's worth of evidence.

    **A NULL `cluster_id` weighs 1.0, not `1/|NULL group|`.** ADR 151
    leaves the column NULL on rows the backtest has not tagged, and
    treating those as one shared cluster would crush every untagged event
    to near zero — silently, since the weights are never inspected.

    **Co-firing is not corrected here.** DESIGN §7.5 is explicit: the
    randomization null handles it at the reporting layer, and correcting
    twice understates confidence. The signature takes cluster ids and
    nothing else, which is the cheapest way to stop a caller adding it.
    """
    sizes = Counter(cid for cid in cluster_ids if cid is not None)
    return [1.0 if cid is None else 1.0 / sizes[cid] for cid in cluster_ids]
