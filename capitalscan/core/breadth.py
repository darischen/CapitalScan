"""The ranking gate: when `p_touch` may be used to choose between names.

ADR 176. `p_touch` is calibrated in every regime measured -- bias +0.000 --
and its ability to *rank* is not. Universe breadth separates the two:

    breadth < 0.68     AUC 0.6255, Brier skill +5.62%   (n=6,078)
    breadth >= 0.68    AUC 0.5154, Brier skill -0.56%   (n=3,037)

AUC 0.515 is a coin flip, and negative skill means the base rate beats the
model. Worse, the low band inverts there: predictions under 0.40 resolved
at 0.458 while those from 0.40 to 0.55 resolved at 0.417. So above the
floor a low `p_touch` is not evidence against a name, which is the specific
mistake a reader would otherwise make.

**A gate, not a suppression, and the difference is the whole design.**
`core.cells.suppression_reason` withholds a number because a thin cell's
number would be untrustworthy. Here the number is trustworthy and only its
*ordering* is not. Withholding it would discard something correct; showing
it without the warning would invite a comparison it cannot support.

**Invariant 1.** No IO, no clock. The caller supplies the reading.
"""

from __future__ import annotations

#: What the gate means, in the words a surface should use. Kept here so the
#: web copy, the MCP tool description and the CLI cannot drift apart.
GATE_OPEN_LABEL = "Ranking usable"
GATE_CLOSED_LABEL = "Ranking unreliable"

GATE_CLOSED_DETAIL = (
    "Market breadth is above the level where this model's ranking has held. "
    "Measured on validate, discrimination falls to AUC 0.515 here against "
    "0.626 below it, which is close to a coin flip. The probabilities are "
    "still calibrated and can be read as long-run frequencies, but they "
    "should not be used to choose one name over another, and a low value is "
    "not evidence against a name."
)

GATE_OPEN_DETAIL = (
    "Market breadth is in the range where this model's ranking has held: "
    "AUC 0.626 with Brier skill +5.6%, measured on 6,078 validate events. "
    "That is a modest edge found by searching a split that has been examined "
    "many times, so it is a lead the forward log is still confirming."
)


def ranking_gate_open(breadth: float | None, floor: float) -> bool | None:
    """Whether `p_touch` may be used to rank names at this breadth reading.

    Returns `None` for a missing reading rather than defaulting either way.
    Defaulting open would let a stale or failed breadth job silently restore
    ranking; defaulting closed would hide a working model whenever the
    market data lagged. Neither is a decision code should make on the
    reader's behalf, so the surface renders "unknown" and says why.

    Args:
        breadth: fraction of the universe with its 20-day average at or
            above its 200-day, or None when not computed.
        floor: `StatsParams.breadth_rank_floor`. Sweepable, because 0.68 is
            where the drop sits on one split rather than a law.
    """
    if breadth is None:
        return None
    return breadth < floor


def gate_label(open_: bool | None) -> str:
    """The short display string for a gate state."""
    if open_ is None:
        return "Ranking state unknown"
    return GATE_OPEN_LABEL if open_ else GATE_CLOSED_LABEL


def gate_detail(open_: bool | None) -> str:
    """The sentence a surface shows beside the label."""
    if open_ is None:
        return (
            "Market breadth has not been computed for this date, so whether "
            "this model's ranking is reliable here is unknown. Treat the "
            "probabilities as calibrated frequencies and do not rank on them."
        )
    return GATE_OPEN_DETAIL if open_ else GATE_CLOSED_DETAIL
