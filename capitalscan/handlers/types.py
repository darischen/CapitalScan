"""Result shapes for the seven tools (session 15.1, DESIGN §10.1).

Three consumers will call the handler layer: the web frontend (sessions 17
and 18), the MCP server (session 16), and the chat layer (session 18). ADR
027 requires the MCP server to wrap "the same tools", which only means
anything if the tools have one contract. These are it.

Four rules, each of which is a *type* here rather than a convention:

1. **No IO vocabulary.** Nothing in this module or any handler imports
   `rich`, `fastapi`, `starlette`, or an HTTP client. A handler that
   formats has the wrong shape, and a handler that prints has no shape at
   all. `test_handlers_contract.py` asserts the import set.
2. **`meta` on every result.** `config_hash`, `as_of`, `staleness_days`,
   and `run_id` where one applies. DESIGN §11.2 renders a staleness banner
   above `MonitoringThresholds.stale_after_days`, which it cannot do if the
   handler does not say.
3. **Unions, not nullable fields.** `get_stats` returns `CellStats |
   Suppressed`; `predict` returns `Prediction | NotFound`. A caller that
   forgets to check gets a type error from mypy rather than a `None` that
   renders as an empty cell three layers away.
4. **Invariant 8 as a type.** Any field expressing a probability sits in
   the same object as `n_eff`, `ci_low`, `ci_high`, and `q_value`.
   `test_handlers_contract.py` walks `RESULT_TYPES` annotations and
   enforces it, so a new probability field on any result fails the fast
   tier unless it brings its companions.

**Why rule 4 is name-based rather than a wrapper type.** An `Estimate`
dataclass bundling value-plus-companions would make the rule unbreakable,
and would also mean every field carries four repeated companions that are
identical across a whole `CellStats` row - `p_hit` and `edge` share one
`n_eff`, because they are two readings of one sample. Bundling would
duplicate that four times per row and invite them to disagree. The
companions belong to the *object*, so the check is on the object.

Everything is a frozen dataclass. Nothing here is mutated after
construction, and freezing is what lets a result be cached, hashed, and
compared by value in the determinism tests.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from typing import Any

# The four fields invariant 8 requires alongside any probability. Named once
# here and read by the structural test, so widening the requirement is a
# one-line change that immediately fails every type that has not caught up.
COMPANION_FIELDS: tuple[str, ...] = ("n_eff", "ci_low", "ci_high", "q_value")

# Names that *look* like probabilities under the prefix rule and are not.
#
# A p-value is a property of the test, not an estimate of a rate. It has no
# confidence interval and requiring one would recurse: the interval would
# itself be an estimate needing an interval. `q_value` is excluded for the
# same reason and is additionally a companion, so including it would make
# every object require a companion for its own companion.
_NOT_PROBABILITY: frozenset[str] = frozenset(
    {
        "p_value_randomization",
        "p_value_parametric",
        "q_value",
    }
)

# Prefixes and exact names that mark a field as a probability.
#
# `edge` is `p_hit - baseline`, a difference of two probabilities, and is
# included deliberately: ADR 112 measured every edge interval spanning zero,
# so an `edge` rendered without its interval is the single most misleading
# number this system can emit.
_PROBABILITY_PREFIXES: tuple[str, ...] = ("p_hit", "p_touch", "p_adverse")
_PROBABILITY_NAMES: frozenset[str] = frozenset({"baseline", "edge", "earnings_frac"})


def is_probability_field(name: str) -> bool:
    """Whether a field name expresses a probability, for the rule-4 check."""
    if name in _NOT_PROBABILITY:
        return False
    if name in _PROBABILITY_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in _PROBABILITY_PREFIXES)


def probability_fields(cls: type) -> tuple[str, ...]:
    """Every probability-expressing field on a result dataclass."""
    return tuple(f.name for f in fields(cls) if is_probability_field(f.name))


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Meta:
    """Provenance and freshness, attached to every result.

    `as_of` is the most recent **bar** date, not the current date, and
    `staleness_days` is the gap between them. That distinction is the whole
    point: a database whose last ingest was three weeks ago should report
    twenty-one days of staleness, not silently answer as though it were
    current. Computing it from the clock alone would report zero forever.

    `run_id` is present when the result traces to one job (a `cell_stats`
    row carries the run that wrote it) and absent when it does not (a
    screener page is a query, not a run).

    No wall-clock timestamp field. Session 15's gate item 10 requires two
    identical calls against an unchanged database to return identical
    results, and a `generated_at` would break that by construction. The
    only clock dependence left is `staleness_days`, which advances once a
    day and is the number the reader is asking for.
    """

    config_hash: str
    as_of: date | None = None
    staleness_days: int | None = None
    run_id: str | None = None
    split: str | None = None
    stale: bool = False


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CellStats:
    """One measured cell. Every probability here shares one `n_eff`.

    `survives_fdr` is stored rather than left for the caller to derive from
    `q_value < fdr_alpha`. Three consumers would each write that comparison,
    and one of them would use `<=`. It is also the field that carries ADR
    112's result onto every surface: on the live config it is `False` for
    every cell that returns.
    """

    cell_id: str
    signal_type: str | None
    side: str | None
    dd_bucket: str | None
    signal_strength: int | None
    entry_kind: str | None
    split_key: str | None
    era: str | None
    horizon_days: int | None
    target_pct: float | None
    arm: str
    n_events: int | None
    n_tickers: int | None
    # --- invariant 8 companions -------------------------------------------
    n_eff: int | None
    ci_low: float | None
    ci_high: float | None
    q_value: float | None
    # --- probabilities ----------------------------------------------------
    p_hit: float | None
    baseline: float | None
    edge: float | None
    earnings_frac: float | None
    # --- descriptive ------------------------------------------------------
    p_value_randomization: float | None
    mean_ret: float | None
    median_ret: float | None
    mean_mfe: float | None
    mean_mae: float | None
    capture_ratio: float | None
    mean_cofire: float | None
    exit_mix: dict[str, Any] | None
    survives_fdr: bool
    meta: Meta


@dataclass(frozen=True)
class Suppressed:
    """A cell that exists and has too little data to report.

    Not an error and not an empty `CellStats`. ADR 112 measured 100 of 224
    train cells landing here, so this is the *common* return of `get_stats`
    rather than an edge case, and it carries the counts that explain it.

    It deliberately carries no probability field at all. A `Suppressed` with
    a greyed-out `p_hit` would be read off the screen by someone in a hurry,
    which is exactly what suppression exists to prevent.
    """

    cell_id: str
    reason: str
    n_events: int | None
    n_eff: int | None
    min_n_eff: int
    meta: Meta


# ---------------------------------------------------------------------------
# screen_signals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenRow:
    """One event on the screener.

    Per the screener column contract (ADR 114), the statistical fields are
    not columns on this row. They sit in `stats`, populated only when the
    caller asks, and they arrive as a `CellStats | Suppressed` union so a
    suppressed cell cannot be rendered as a blank number.
    """

    ticker: str
    signal_date: date
    signal_type: str
    signal_types_all: tuple[str, ...]
    signal_strength: int
    side: str | None
    sector: str | None
    bb_pctb: float | None
    k_full: float | None
    k_fast: float | None
    k_cross_up: bool | None
    dd_52w: float | None
    dd_bucket: str | None
    above_sma200: bool | None
    cofire_count: int | None
    cell_id: str | None
    stats: CellStats | Suppressed | None


@dataclass(frozen=True)
class ScreenResult:
    rows: tuple[ScreenRow, ...]
    # What the filter matched before `limit` truncated it. A page showing
    # 200 of 640 rows and saying only "200" is a page that quietly lies
    # about how much fired.
    total_matched: int
    limit: int
    with_stats: bool
    meta: Meta


# ---------------------------------------------------------------------------
# get_indicators
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndicatorPoint:
    ts: date
    values: dict[str, float | None]


@dataclass(frozen=True)
class IndicatorSeries:
    ticker: str
    fields: tuple[str, ...]
    points: tuple[IndicatorPoint, ...]
    meta: Meta


# ---------------------------------------------------------------------------
# get_events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventRow:
    id: int
    ticker: str
    signal_date: date
    signal_type: str
    signal_types_all: tuple[str, ...]
    signal_strength: int
    side: str | None
    cluster_id: int | None
    seq_in_cluster: int | None
    is_cluster_head: bool | None
    bb_pctb: float | None
    k_full: float | None
    k_fast: float | None
    dd_52w: float | None
    dd_bucket: str | None
    above_sma200: bool | None
    entry_kind: str | None
    entry_date: date | None
    entry_price: float | None
    exit_date: date | None
    exit_price: float | None
    exit_reason: str | None
    holding_days: int | None
    gross_ret: float | None
    net_ret: float | None
    mfe: float | None
    mae: float | None
    era: str | None
    split_key: str | None


@dataclass(frozen=True)
class EventList:
    ticker: str | None
    rows: tuple[EventRow, ...]
    total_matched: int
    limit: int
    cluster_head_only: bool
    meta: Meta


# ---------------------------------------------------------------------------
# predict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Prediction:
    """The Phase 6 contract, defined and never returned in Phase 5.

    `predict` returns `NotFound` for every input today. This shape exists so
    Phase 6 inherits a decided contract rather than negotiating one, and so
    the change is visible as a diff when it happens.

    The four companions are fields here for the same reason they are on
    `CellStats`: ADR 093 conditions the model on a cell, and a fan of
    quantiles with no statement of how much data stands behind it is the
    exact object ADR 112 argues against shipping. `predictions.cell_n_eff`
    already stores the number; this is where it surfaces.
    """

    ticker: str
    as_of: date
    model_version: str
    cell_id: str | None
    q05: float | None
    q25: float | None
    q50: float | None
    q75: float | None
    q95: float | None
    p_touch_2: float | None
    p_touch_3: float | None
    p_touch_5: float | None
    p_touch_10: float | None
    p_adverse_3: float | None
    p_adverse_5: float | None
    # --- invariant 8 companions, from the conditioning cell ----------------
    n_eff: int | None
    ci_low: float | None
    ci_high: float | None
    q_value: float | None
    meta: Meta


@dataclass(frozen=True)
class NotFound:
    """No answer exists, and the reason is a fact rather than a failure.

    `predict` returns this for every input in Phase 5 because no model
    exists (ADR 093 is Provisional; ADR 113 opens Phase 6). Returning a
    plausible-looking distribution instead would be forgotten and then
    trusted, which is the whole hazard.
    """

    what: str
    reason: str
    meta: Meta


# ---------------------------------------------------------------------------
# explain_signal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Explanation:
    """Why a signal fired, and what cell it lands in.

    No SHAP field. DESIGN §10.1 lists a top-5 attribution, and it is a Phase
    6 field because it is an attribution *of a model*. It is absent here
    rather than present and empty: an empty list reads as "nothing
    contributed", which is a claim, and a missing field reads as "no model",
    which is the truth.
    """

    ticker: str
    signal_date: date
    signal_type: str
    signal_types_all: tuple[str, ...]
    side: str | None
    features: dict[str, Any]
    cell_id: str | None
    cell: CellStats | Suppressed | None
    meta: Meta


# ---------------------------------------------------------------------------
# get_universe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UniverseRow:
    ticker: str
    name: str | None
    sector: str | None
    industry: str | None
    as_of: date | None
    in_train: bool | None
    in_trade: bool | None
    mcap_usd: float | None
    mcap_rank: int | None
    adv_20d_usd: float | None
    criteria: dict[str, bool | None]
    is_active: bool | None
    delisted_on: date | None


@dataclass(frozen=True)
class UniverseResult:
    as_of: date | None
    rows: tuple[UniverseRow, ...]
    n_train: int
    n_trade: int
    meta: Meta


# Every type the structural contract test walks. A result type absent from
# this tuple is a result type nobody checks, so adding one and forgetting
# the tuple is itself a failure mode - `test_handlers_contract.py` compares
# this against the module's own dataclasses rather than trusting the list.
RESULT_TYPES: tuple[type, ...] = (
    CellStats,
    Suppressed,
    ScreenRow,
    ScreenResult,
    IndicatorPoint,
    IndicatorSeries,
    EventRow,
    EventList,
    Prediction,
    NotFound,
    Explanation,
    UniverseRow,
    UniverseResult,
    Meta,
)
