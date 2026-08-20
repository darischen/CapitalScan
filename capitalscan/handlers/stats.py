"""`get_stats` — one cell, or the reason it reports nothing (DESIGN §10.1).

**This is the handler ADR 112 shapes most.** 100 of 224 train cells suppress
at `n_eff < 30`, and 0 of the 124 that do not survive FDR correction. So
`Suppressed` is the *common* return, and every `CellStats` that comes back
carries `survives_fdr=False` and a q-value near 1.

Two rules the code enforces rather than the caller remembering:

- **A suppressed cell never becomes a broader one.** DESIGN §10.2's system
  prompt asks the chat layer not to substitute; asking is not a guarantee,
  so this handler has no widening path at all. There is nowhere in it that
  drops a predicate and retries.
- **Suppression is read, never recomputed.** `research/cell_stats.py`
  decided it at write time against `StatsParams.min_n_eff` and stored the
  reason. Re-deciding here would be a second implementation of the rule,
  and the two would disagree the first time `min_n_eff` moved.

**Deviation from DESIGN §10.1: no `ticker` parameter.** The spec's signature
lists one. `cell_stats` has no ticker dimension - ADR 102 fixed the grid at
side x signal type x drawdown bucket, and ADR 104 made the breadth
denominator the train universe rather than any single name. A `ticker`
argument could therefore only be accepted and ignored, which is the worst of
the three options. Per-ticker history is `get_events(ticker=...)`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine

from capitalscan.core.cells import cell_key
from capitalscan.core.config import ExitParams, StatsParams
from capitalscan.handlers import _db, enums
from capitalscan.handlers.types import CellStats, Meta, Suppressed
from capitalscan.handlers.validate import validated

# Every column `_CELL_COLUMNS` names, selected explicitly rather than with a
# star. A `SELECT *` here would silently pick up any column a later
# migration adds to `cell_stats`, and `_to_cell_stats` would ignore it - so
# the new column would exist, be queried, and never surface, which is the
# quietest way to lose a measurement.
_CELL_COLUMNS = """
    cell_id, run_id, config_hash, signal_type, side, dd_bucket, signal_strength,
    entry_kind, split_key, era, horizon_days, target_pct, arm,
    n_events, n_eff, n_tickers, mean_cofire,
    p_hit, baseline_empirical, edge, ci_low, ci_high,
    q_value, p_value_randomization,
    mean_ret, median_ret, mean_mfe, mean_mae, capture_ratio,
    exit_mix, earnings_frac, suppressed, suppress_reason
"""

_SELECT_CELL = f"""
SELECT {_CELL_COLUMNS}
FROM cell_stats
WHERE config_hash = :config_hash
  AND cell_id = :cell_id
  AND arm = :arm
LIMIT 1
"""


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _i(value: Any) -> int | None:
    return None if value is None else int(value)


def to_cell_stats(row: dict[str, Any], meta: Meta, sp: StatsParams) -> CellStats | Suppressed:
    """Map one `cell_stats` row to the union `get_stats` returns.

    Shared with `screen_signals`, which attaches the same object to a row
    when the caller asks for statistics. One mapping, so the two surfaces
    cannot report the same cell differently - which is the whole reason ADR
    027 wants the consumers to share a layer.
    """
    cell_meta = Meta(
        config_hash=meta.config_hash,
        as_of=meta.as_of,
        staleness_days=meta.staleness_days,
        run_id=row.get("run_id") or meta.run_id,
        split=row.get("split_key") or meta.split,
        stale=meta.stale,
    )
    if bool(row.get("suppressed")):
        return Suppressed(
            cell_id=row["cell_id"],
            # The stored reason, never a fresh one. `research/cell_stats.py`
            # wrote it against the `min_n_eff` in force at compute time, and
            # a reason regenerated here would quote today's floor against
            # yesterday's counts.
            reason=row.get("suppress_reason") or "suppressed",
            n_events=_i(row.get("n_events")),
            n_eff=_i(row.get("n_eff")),
            min_n_eff=sp.min_n_eff,
            meta=cell_meta,
        )

    q = _f(row.get("q_value"))
    return CellStats(
        cell_id=row["cell_id"],
        signal_type=row.get("signal_type"),
        side=row.get("side"),
        dd_bucket=row.get("dd_bucket"),
        signal_strength=_i(row.get("signal_strength")),
        entry_kind=row.get("entry_kind"),
        split_key=row.get("split_key"),
        era=row.get("era"),
        horizon_days=_i(row.get("horizon_days")),
        target_pct=_f(row.get("target_pct")),
        arm=row.get("arm") or "signal",
        n_events=_i(row.get("n_events")),
        n_tickers=_i(row.get("n_tickers")),
        n_eff=_i(row.get("n_eff")),
        ci_low=_f(row.get("ci_low")),
        ci_high=_f(row.get("ci_high")),
        q_value=q,
        p_hit=_f(row.get("p_hit")),
        # `baseline_empirical`, not `baseline_parametric`. ADR 013 makes the
        # empirical one the comparison the edge is measured against; the
        # parametric one is a diagnostic and surfacing it under the same
        # name would make two different numbers look interchangeable.
        baseline=_f(row.get("baseline_empirical")),
        edge=_f(row.get("edge")),
        earnings_frac=_f(row.get("earnings_frac")),
        p_value_randomization=_f(row.get("p_value_randomization")),
        mean_ret=_f(row.get("mean_ret")),
        median_ret=_f(row.get("median_ret")),
        mean_mfe=_f(row.get("mean_mfe")),
        mean_mae=_f(row.get("mean_mae")),
        capture_ratio=_f(row.get("capture_ratio")),
        mean_cofire=_f(row.get("mean_cofire")),
        exit_mix=row.get("exit_mix"),
        # Computed once, here, rather than by each of three consumers. ADR
        # 103: an era row carries no q-value because it entered no test
        # family, and "not tested" is not "survived".
        survives_fdr=q is not None and q <= sp.fdr_alpha,
        meta=cell_meta,
    )


def _fetch_cell(engine: Engine, config_hash: str, cell_id: str, arm: str) -> dict[str, Any] | None:
    found = _db.rows(
        engine,
        _SELECT_CELL,
        {"config_hash": config_hash, "cell_id": cell_id, "arm": arm},
    )
    return found[0] if found else None


def get_stats(
    signal_type: str,
    target_pct: float,
    dd_bucket: str,
    split: str,
    entry_kind: str = "next_open",
    signal_strength: int | None = None,
    horizon_days: int | None = None,
    era: str | None = None,
    arm: str = "signal",
    engine: Engine | None = None,
    sp: StatsParams | None = None,
    ep: ExitParams | None = None,
) -> CellStats | Suppressed:
    """One cell of the grid, selected by its components rather than its id.

    `cell_id` is derived, never stored (invariant 5b), so this builds the
    key with `core.cells.cell_key` - the same function `research/cell_stats.
    py` used to write the row. Accepting a `cell_id` string from a caller
    instead would let one arrive that no writer ever produced, and the
    handler would report "not found" for a cell that exists under a
    different spelling.

    `signal_strength` defaults to None, which `cell_key` renders `all`. ADR
    107: the serving cell is the one pooled over strength, because
    `signal_strength` is `len(signal_types_all)` and takes exactly two
    values, so splitting on it halves `n_eff` to distinguish "confluence"
    from "not confluence" - a distinction `signal_type` already carries.

    `era` defaults to None (pooled). Passing one returns a descriptive row
    with no q-value, per ADR 103, and `holdout_era` is unreachable because
    the corresponding rows are never written.
    """
    sp = sp or StatsParams()
    ep = ep or ExitParams()

    signal_type = enums.parse_signal_type(signal_type)
    side = enums.side_for_signal_type(signal_type)
    dd_bucket = enums.parse_dd_bucket(dd_bucket, sp)
    entry_kind = enums.parse_entry_kind(entry_kind)
    split = enums.parse_split(split)
    target_pct = enums.parse_target_pct(target_pct, sp)
    horizon = ep.max_hold_days if horizon_days is None else int(horizon_days)

    cell_id = cell_key(
        signal_type,
        side,
        dd_bucket,
        signal_strength,
        entry_kind,
        split,
        era,
        horizon,
        target_pct,
    )

    engine = _db.engine_or_default(engine)
    config_hash = _db.resolve_config_hash(engine)
    meta = _db.build_meta(engine, config_hash=config_hash, split=split)

    row = _fetch_cell(engine, config_hash, cell_id, arm)
    if row is None:
        # Not an error and not a widening. A cell absent from `cell_stats`
        # was never computed - most often because it is outside the headline
        # grid (ADR 101 permanently suppresses `20-35` and `35+`) - and
        # saying so is more useful than either raising or quietly answering
        # with the nearest cell that does exist.
        return validated(
            Suppressed(
                cell_id=cell_id,
                reason="cell not computed for this config",
                n_events=None,
                n_eff=None,
                min_n_eff=sp.min_n_eff,
                meta=meta,
            ),
            sp,
        )

    return validated(to_cell_stats(row, meta, sp), sp)
