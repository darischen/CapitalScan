"""The headline cell grid and its identifier (ADR 101, 102, 103).

Pure functions. No IO, no database, no clock (invariant 1) — the writer in
`research/cell_stats.py` owns all of that.

**This module holds the system's second implementation of `cell_key`.** The
first is the `IMMUTABLE` SQL function of the same name, and it is the
authority. A Python copy exists because `cell_id` is derived from component
columns rather than stored (invariant 5b), and the aggregation that needs it
runs in pandas. `tests/integration/test_cell_key_sql.py` executes the SQL
function across the parameter cross-product and diffs it against this file;
`tests/unit/test_cells.py` pins the answers that test read out of Postgres.

That arrangement is deliberate. A Python replay of the SQL function's logic
would not be an independent oracle — the 2026-08-09 peak-label defect
survived exactly that pattern, because the test transcribed the statement
under test and copied its bug. Only executed parity counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from capitalscan.core.config import SplitParams, StatsParams
from capitalscan.core.types import SignalType

# ADR 102: signal type pairs *with* side rather than crossing it, which is
# what makes the grid twelve cells instead of thirty-six.
#
# **The two tuples were briefly different lengths, and are equal again**
# (ADR 108 broke the pairing 2026-08-13; ADR 144 restored it 2026-08-21).
# They are positionally paired -- index i of each is the same family read
# from the opposite side. `headline_grid` iterates them independently and
# never zips them, so an asymmetry stays expressible; the pairing is
# documentation, not a mechanism.
#
# Grid size is 4x2 + 4x2 = 16 cells, and the Benjamini-Hochberg family in
# DESIGN 6.8 is 64 tests (16 cells x 4 targets).
#
# **Two of those cells cannot fire yet, and that is deliberate.**
# `bull_close_below_lower` is dormant -- `SignalParams.enabled_signal_types`
# does not list it -- so its cells are empty until it is enabled. An empty
# cell falls below `min_n_eff` and is suppressed, and **BH corrects over
# measured cells, not over declared ones** (ADR 112 reports 48 measured of
# 56, not 56 of 56). So a dormant cell does not dilute any other cell's
# q-value. If that ever changes, this is the line to revisit: adding tests
# that cannot produce a signal would make every real cell's q-value worse
# for nothing.
#
# It is listed here rather than held back because these tuples are also the
# side lookup `handlers.enums.side_for_signal_type` reads, and every
# `SignalType` member must have a side. Classification and grid membership
# share one source, which is why a dormant type still appears.
LONG_SIGNALS: tuple[str, ...] = (
    SignalType.BB_LOWER_TOUCH.value,
    SignalType.STOCH_OVERSOLD.value,
    SignalType.CONFLUENCE_LOW.value,
    SignalType.BULL_CLOSE_BELOW_LOWER.value,
)
SHORT_SIGNALS: tuple[str, ...] = (
    SignalType.BB_UPPER_TOUCH.value,
    SignalType.STOCH_OVERBOUGHT.value,
    SignalType.CONFLUENCE_HIGH.value,
    SignalType.BEAR_CLOSE_ABOVE_UPPER.value,
)

# The number of leading `dd_buckets` labels the headline grid uses. ADR 101
# suppresses everything past the second permanently: across 59 measured
# cells, `20-35` and `35+` cleared the floor zero times.
HEADLINE_BUCKET_COUNT = 2


@dataclass(frozen=True)
class CellSpec:
    """One headline cell's three grid dimensions, and only those.

    `entry_kind`, `split_key`, `era`, `horizon_days`, and `target_pct` are
    not fields here: they vary per *query*, not per cell, and folding them
    in would turn "twelve cells" into twelve times the ladder. They enter
    at `cell_id_for`.
    """

    side: str
    signal_type: str
    dd_bucket: str


def _fm990_999(value: float) -> str:
    """`to_char(value, 'FM990.999')` as Postgres renders it.

    Three behaviours have to survive, all verified against Postgres 18:

    - `FM` suppresses padding zeros, but the pattern's literal `.` prints
      regardless, so `1.0` renders `1.` and `0.0` renders `0.`
    - rounding is half **away from zero**, not half-to-even: `0.1235`
      renders `0.124`, where Python's `round(0.1235, 3)` gives `0.123`
    - `990` guarantees one integer digit, so `0.02` keeps its leading `0`

    `Decimal(str(value))` rather than `Decimal(value)`: the float's shortest
    repr is the same text Postgres parsed into its numeric, and the binary
    expansion is not.
    """
    quantized = Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    integer_part, _, fraction = format(quantized, "f").partition(".")
    return f"{integer_part}.{fraction.rstrip('0')}"


def cell_key(
    signal_type: str | None,
    side: str | None,
    dd_bucket: str | None,
    strength: int | None,
    entry_kind: str | None,
    split: str | None,
    era: str | None,
    horizon: int | None,
    target: float | None,
) -> str:
    """Mirror of the SQL `cell_key`, argument for argument.

    Four slots coalesce: `dd_bucket` to `'all'`, `strength` to `'all'`,
    `era` to `'pooled'`. Every other null is **dropped** rather than
    emptied, because `concat_ws` omits null arguments — a null
    `signal_type` yields an eight-field key, not a nine-field key with a
    hole. That is a collision hazard and it is reproduced here on purpose:
    diverging from the SQL function on the one input nobody tries is
    exactly the failure this file's parity test exists to prevent.
    """
    parts: list[str | None] = [
        signal_type,
        side,
        dd_bucket if dd_bucket is not None else "all",
        str(strength) if strength is not None else "all",
        entry_kind,
        split,
        era if era is not None else "pooled",
        f"h{horizon}" if horizon is not None else None,
        f"t{_fm990_999(target)}" if target is not None else None,
    ]
    return "|".join(part for part in parts if part is not None)


def dd_bucket_labels(sp: StatsParams) -> tuple[str, ...]:
    """The drawdown bucket labels `StatsParams.dd_buckets` implies.

    `(0.10, 0.20, 0.35)` yields `('0-10', '10-20', '20-35', '35+')` — one
    label per threshold plus an open-ended tail. Derived rather than
    written down so the grid cannot disagree with the thresholds that
    assigned `events.dd_bucket` in the first place (invariant 9).
    """
    edges = [int(round(threshold * 100)) for threshold in sp.dd_buckets]
    labels = [f"{low}-{high}" for low, high in zip([0] + edges[:-1], edges)]
    labels.append(f"{edges[-1]}+")
    return tuple(labels)


def era_labels(sp: StatsParams, splits: SplitParams) -> tuple[str, ...]:
    """Every reporting era, in order, ending with the open one.

    Mirrors `research.enrich._era`, which stamps `events.era`. A label
    built differently here would match nothing and every era row would come
    back empty — a failure that looks like "no events in that era" rather
    than like a bug.

    The first era's lower edge is `splits.event_start`'s year rather than a
    literal, so era 1 and the event universe start together by
    construction. The last era is open-ended (`"2024+"`), matching the
    convention `dd_bucket_labels` uses for "no further boundary."
    """
    bounds = [int(bound[:4]) for bound in sp.era_bounds]
    lo = int(splits.event_start[:4])
    labels = []
    for bound_year in bounds:
        labels.append(f"{lo}-{bound_year}")
        lo = bound_year + 1
    labels.append(f"{lo}+")
    return tuple(labels)


def holdout_era(sp: StatsParams, splits: SplitParams) -> str:
    """The open era, which **is** the holdout split.

    ADR 103: era 2024+ and the holdout split both begin 2024-01-01, because
    `SplitParams.validate_end` is the last `era_bounds` entry. Reporting a
    headline cell for this era reports on holdout, which the firewall in
    DESIGN §8 forbids.
    """
    return era_labels(sp, splits)[-1]


def reported_eras(sp: StatsParams, splits: SplitParams) -> tuple[str, ...]:
    """The eras Phase 4 may report: every era except the holdout one.

    Derived rather than listed, so the exclusion is structural. A caller
    passing the holdout era explicitly is refused by `compute_grid` rather
    than quietly obeyed — an implicit exemption is the kind that gets
    widened later (ADR 103).
    """
    return era_labels(sp, splits)[:-1]


def headline_grid(sp: StatsParams) -> tuple[CellSpec, ...]:
    """The fourteen headline cells of ADR 102, in a stable order.

    Three long families and four short ones, each across two drawdown
    buckets. Deep-drawdown events are still detected, labelled, stored, and
    visible to the model layer; they simply never render as a headline cell
    (ADR 101).

    **Fourteen, not twelve** (ADR 108, amending ADR 102, 2026-08-13).
    `BEAR_CLOSE_ABOVE_UPPER` is short-side only, so the sides carry
    different family counts and the grid is 3x2 + 4x2 rather than 2x3x2.

    The new type outranks `CONFLUENCE_HIGH` (ADR 057 specificity), so bars
    firing both report the new one and are counted in its cell rather than
    the confluence cell. Measured on the train split: 409 bars fire it, 286
    of them (70%) also fire `confluence_high`. That overlap is real
    correlation, not a defect — a bar closing above the upper band has
    usually been running hot, so %K is already extreme. Neither confluence
    cell changes its render/suppress verdict as a result.
    """
    buckets = dd_bucket_labels(sp)[:HEADLINE_BUCKET_COUNT]
    return tuple(
        CellSpec(side=side, signal_type=signal_type, dd_bucket=bucket)
        for side, signals in (("long", LONG_SIGNALS), ("short", SHORT_SIGNALS))
        for signal_type in signals
        for bucket in buckets
    )


def cell_id_for(
    spec: CellSpec,
    *,
    entry_kind: str,
    split_key: str,
    horizon_days: int,
    target_pct: float,
    era: str | None = None,
) -> str:
    """`cell_key` for a headline cell.

    `strength` is always null: `core/signals.py` sets `signal_strength` to
    `len(signal_types_all)`, so it is 3 on every confluence event and 1 on
    every single-type event. There is no 1-vs-2 split to cut and ADR 102
    removed it as a dimension.

    `era` defaults to null, which the key renders `pooled` — the shape
    every headline cell uses (ADR 103). 12.4's descriptive era rows pass it
    explicitly.
    """
    return cell_key(
        spec.signal_type,
        spec.side,
        spec.dd_bucket,
        None,
        entry_kind,
        split_key,
        era,
        horizon_days,
        target_pct,
    )


def suppression_reason(n_eff: float, sp: StatsParams) -> str | None:
    """Why a cell renders no number, or `None` if it renders.

    The floor is `StatsParams.min_n_eff` and reading it from anywhere else
    is how entry and exit come to disagree inside one run (invariant 9).
    Two cells are expected to trip this at 14 and 5 against a floor of 30.
    """
    if n_eff < sp.min_n_eff:
        return f"n_eff {n_eff:.1f} below min_n_eff {sp.min_n_eff}"
    return None
