"""Trade-universe health filter. Pure functions, no IO (DESIGN §3.1).

The five ADR 014 criteria are returned **separately** rather than reduced to
one boolean, and `is_tradeable` accepts a subset of required criteria. That
pair is what makes the ablation study a config change rather than a code
change (DESIGN §3.10).

Three-valued by design. A criterion is `None` when the data needed to judge
it is absent — fewer than four reported quarters for revenue growth, a
sector with too few members for a median, a name inside its SMA warmup.
`is_tradeable` treats `None` as failing, but the two stay distinguishable so
the audit log can separate "failed the filter" from "could not be judged"
(DESIGN §4.6).

Every criterion is evaluated on information available at t-1. This module
never sees a clock; the caller supplies the as-of row.
"""

from __future__ import annotations

import pandas as pd

from capitalscan.core.config import UniverseParams
from capitalscan.core.signals import _isnan

# The five ADR 014 criteria, matching the `universe` table's crit_* columns
# exactly (DESIGN §2.5). No translation layer.
CRITERIA: tuple[str, ...] = (
    "crit_mcap",
    "crit_above_sma200",
    "crit_sma200_slope",
    "crit_rel_return",
    "crit_rev_growth",
)


def _cmp(left: object, right: object, strict: bool = True) -> bool | None:
    """`left > right`, or None when either side is missing."""
    if _isnan(left) or _isnan(right):
        return None
    lhs, rhs = float(left), float(right)  # type: ignore[arg-type]
    return lhs > rhs if strict else lhs >= rhs


def evaluate_criteria(
    ind_row: pd.Series,
    mcap: float | None,
    sector_median_return: float | None,
    rev_growth_positive: bool | None,
    up: UniverseParams,
) -> dict[str, bool | None]:
    """The five ADR 014 criteria, judged independently.

    `ind_row` supplies `close`, `sma_200`, and `sma200_slope_60` from the
    indicators table, plus `rel_return_756d` — the trailing 3-year total
    return, which the universe job computes and attaches because it is a
    cross-sectional quantity rather than a per-bar indicator.

    `mcap` is point-in-time: the caller resolves it from the latest filing
    with `filed_on < as_of` (DESIGN §2.4). A ticker with no filing yet gets
    `None`, not a backfilled current share count.
    """
    return {
        "crit_mcap": _cmp(mcap, up.min_mcap_usd, strict=False),
        "crit_above_sma200": _cmp(ind_row.get("close"), ind_row.get("sma_200")),
        "crit_sma200_slope": _cmp(ind_row.get("sma200_slope_60"), 0.0),
        "crit_rel_return": _cmp(ind_row.get("rel_return_756d"), sector_median_return),
        "crit_rev_growth": rev_growth_positive,
    }


def is_tradeable(
    criteria: dict[str, bool | None],
    required: set[str] | None = None,
) -> bool:
    """Whether a name is in the trade universe, given the criteria that count.

    `required` defaults to all five. Passing a subset is the ablation arm:
    dropping `crit_rev_growth` measures what that criterion is worth without
    touching this function. An empty set admits everything, which is the
    no-filter control.

    An unknown criterion name raises rather than being ignored, so a typo in
    an ablation config fails loudly instead of silently widening the universe.
    """
    names = set(CRITERIA) if required is None else required
    unknown = names - set(CRITERIA)
    if unknown:
        raise KeyError(f"unknown criteria: {sorted(unknown)}")
    # `is True` rather than truthiness: None must fail, not raise or pass.
    return all(criteria.get(name) is True for name in names)
