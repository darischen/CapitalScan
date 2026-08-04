"""Session 10 Task 10.4: the session gate. Compares Task 10.3's
path-derived labels against the labels Session 9 actually wrote to
`events` for one run, and reports every mismatch rather than silently
accepting or silently "fixing" one — `docs/session10.md`: "Do not adjust
the new layer to match the old one without understanding the cause."

Committed, re-runnable check (not a one-off script): call `reconcile(...)`
from `cscan path reconcile` or from a test/notebook at any time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import Config
from capitalscan.research.path_labels import derive_session9_labels

# Every column Task 10.3 derives, in the order they appear on `events`.
LABEL_COLUMNS = [
    "mfe",
    "mae",
    "time_to_mfe",
    "capture_ratio",
    "touched_2pct",
    "day_touched_2pct",
    "touched_3pct",
    "day_touched_3pct",
    "touched_5pct",
    "day_touched_5pct",
    "touched_10pct",
    "day_touched_10pct",
    "fwd_ret_1d",
    "fwd_ret_2d",
    "fwd_ret_3d",
    "fwd_ret_5d",
    "fwd_ret_10d",
]

# fwd_ret_*d is a known, explained difference (see plan header "Price
# series discipline"): path.terminal is entry-price-anchored,
# split-adjusted-close return; Session 9's fwd_ret_*d is total-return
# (adj_close), self-referential to the entry bar's own close. The two
# measure genuinely different quantities by design, not by defect.
EXPLAINED_COLUMNS = {
    col: (
        "path.terminal uses entry-price-anchored split-adjusted close "
        "(matching mfe_mae's convention); Session 9's fwd_ret_*d uses "
        "total-return adj_close self-referential to the entry bar's own "
        "close (DESIGN §2.2, core/returns.py module docstring). Both are "
        "intentional, different price-series conventions — not a bug."
    )
    for col in ["fwd_ret_1d", "fwd_ret_2d", "fwd_ret_3d", "fwd_ret_5d", "fwd_ret_10d"]
}

_FLOAT_TOL = 1e-9

# `mfe`/`mae` are compared at `_FLOAT_TOL` because both sides derive from
# already-quantized (`numeric(12,6)`) `path.favorable`/`path.adverse`
# values, so they agree to near bit-exactness. `capture_ratio = r_exit /
# mfe` divides by `mfe`, which amplifies that same quantization error: a
# `numeric(12,6)` value carries up to ~5e-7 of rounding noise, and dividing
# a fixed numerator by an `mfe` that differs by that much produces a
# *relative* error of `~5e-7/mfe` in the ratio — for realistic MFE values
# (a few percent), that is several orders of magnitude above `1e-9`. Using
# the absolute tolerance here would flag mass false-positive mismatches on
# a real reconciliation run for a reason that has nothing to do with an
# actual defect (finding #3 of the final review). Columns listed here
# compare with *relative* tolerance instead; everything else keeps the
# absolute `_FLOAT_TOL` above.
RELATIVE_TOLERANCE_COLUMNS = {"capture_ratio": 1e-4}


def diff_labels(derived: pd.DataFrame, actual: pd.DataFrame, columns: list[str]) -> dict[str, pd.DataFrame]:
    """Per-column mismatch frames, keyed by column name. A column with no
    mismatches is absent from the result (never an empty-but-present key,
    so `bool(mismatches)` reads as "any differences at all").

    Both-null is not a mismatch (invariant 4's null convention is shared
    by both sides). Floats compare within `_FLOAT_TOL` absolute tolerance
    — comparing two independently computed float pipelines for bit-exact
    equality would fail on ordinary floating-point round-off, which is not
    what this check exists to catch. Columns in `RELATIVE_TOLERANCE_COLUMNS`
    (currently just `capture_ratio` — see its comment above) compare with
    relative tolerance instead, since an absolute tolerance sized for
    `mfe`/`mae` is too tight for a ratio that divides by `mfe`.
    """
    merged = derived.merge(actual, on="event_id", suffixes=("_derived", "_actual"))
    mismatches: dict[str, pd.DataFrame] = {}
    for col in columns:
        d_col, a_col = f"{col}_derived", f"{col}_actual"
        d_vals, a_vals = merged[d_col], merged[a_col]

        both_null = d_vals.isna() & a_vals.isna()
        if pd.api.types.is_numeric_dtype(d_vals) or pd.api.types.is_numeric_dtype(a_vals):
            d_float, a_float = d_vals.astype(float), a_vals.astype(float)
            diff = (d_float - a_float).abs()
            if col in RELATIVE_TOLERANCE_COLUMNS:
                rel_tol = RELATIVE_TOLERANCE_COLUMNS[col]
                # Relative to the actual (Session 9's stored) value — the
                # reference the derived value is being checked against.
                # Denominator zeros are guarded explicitly (rather than
                # dividing and letting inf/NaN propagate) so a ratio
                # Session 9 stored as exactly 0 still flags on any nonzero
                # `diff`, without a division-by-zero warning along the way.
                zero_denom = a_float == 0
                safe_denom = a_float.abs().where(~zero_denom, 1.0)
                rel_diff = diff / safe_denom
                exceeds = (zero_denom & (diff > 0)) | (~zero_denom & (rel_diff > rel_tol))
                mismatch_mask = ~both_null & (diff.isna() | exceeds)
            else:
                mismatch_mask = ~both_null & (diff.isna() | (diff > _FLOAT_TOL))
        else:
            mismatch_mask = ~both_null & (d_vals != a_vals)

        if mismatch_mask.any():
            mismatches[col] = merged.loc[mismatch_mask, ["event_id", d_col, a_col]]
    return mismatches


@dataclass
class ReconciliationReport:
    config_hash: str
    total_events: int
    mismatches: dict[str, pd.DataFrame] = field(default_factory=dict)
    explained: dict[str, str] = field(default_factory=dict)

    @property
    def unexplained_mismatch_columns(self) -> list[str]:
        return [c for c in self.mismatches if c not in self.explained]

    @property
    def passes(self) -> bool:
        return len(self.unexplained_mismatch_columns) == 0


def assert_event_ids_match(derived: pd.DataFrame, actual: pd.DataFrame) -> None:
    """Raises if `derived` and `actual` don't cover the exact same
    `event_id` set (finding #5 of the final review).

    `diff_labels`'s inner `merge` on `event_id` silently drops any
    `event_id` present on only one side — a real coverage gap (a
    half-written `derive_session9_labels` result, or a Session 9 write
    that never landed) would otherwise vanish from the diff instead of
    being reported, making the reconciliation itself untrustworthy.
    """
    derived_ids = set(derived["event_id"]) if "event_id" in derived else set()
    actual_ids = set(actual["event_id"]) if "event_id" in actual else set()
    only_derived = derived_ids - actual_ids
    only_actual = actual_ids - derived_ids
    if only_derived or only_actual:
        raise ValueError(
            "reconcile: derived and actual event_id sets differ — "
            f"{len(only_derived)} event_id(s) only in derived, "
            f"{len(only_actual)} only in actual. The reconciliation diff "
            "cannot be trusted while this gap exists (an inner join would "
            "silently drop them)."
        )


def _unbackfilled_resolved_event_ids(rows: pd.DataFrame) -> pd.Series:
    """Pure helper (unit-testable with in-memory frames, no engine): the
    `event_id`s in `rows` (columns `event_id`, `holding_days`,
    `fwd_window_days`) with a resolved position but no path backfill.

    A resolved position (`holding_days IS NOT NULL`) with zero `path` rows
    (`fwd_window_days IS NULL`) is a real coverage gap, not a legitimate
    "never filled" case (which is `entry_price IS NULL` and never gets a
    `holding_days` value in the first place — see `path_backfill`'s module
    docstring). `derive_labels_from_path` returns `touched_*pct=False` /
    `day_touched_*pct=None` for every threshold on an empty `path` frame,
    which for the majority of real events (which never touch high
    thresholds anyway) matches Session 9's stored value by coincidence,
    hiding the gap instead of surfacing it as a mismatch (finding #5 of
    the final review).
    """
    return rows.loc[rows["holding_days"].notna() & rows["fwd_window_days"].isna(), "event_id"]


def assert_backfill_covers_resolved_events(engine: Engine, config_hash: str) -> None:
    """Raises if any event under `config_hash` with a resolved position
    (`holding_days IS NOT NULL`) has no path backfill
    (`fwd_window_days IS NULL`). See `_unbackfilled_resolved_event_ids`.
    """
    with engine.connect() as conn:
        rows = pd.read_sql(
            text(
                "SELECT id AS event_id, holding_days, fwd_window_days FROM events "
                "WHERE config_hash = :config_hash"
            ),
            conn,
            params={"config_hash": config_hash},
        )
    gap = _unbackfilled_resolved_event_ids(rows)
    if len(gap) > 0:
        sample = list(gap.head(10))
        raise ValueError(
            f"reconcile: {len(gap)} event(s) under config_hash={config_hash} have a "
            "resolved position (holding_days IS NOT NULL) but no path backfill "
            f"(fwd_window_days IS NULL). Run `cscan path backfill` before "
            f"reconciling. Sample event_ids: {sample}"
        )


def reconcile(engine: Engine, config: Config, config_hash: str) -> ReconciliationReport:
    """Filters by `config_hash`, not `run_id` — see `derive_session9_labels`'s
    docstring for why `run_id` cannot reliably select a durable row set
    once any later run reuses the same config.
    """
    derived = derive_session9_labels(engine, config, config_hash)
    with engine.connect() as conn:
        actual = pd.read_sql(
            text(
                f"SELECT id AS event_id, {', '.join(LABEL_COLUMNS)} FROM events "
                "WHERE config_hash = :config_hash"
            ),
            conn,
            params={"config_hash": config_hash},
        )

    if actual.empty:
        raise ValueError(
            f"reconcile: config_hash={config_hash} matches zero events — nothing to "
            "reconcile against. A PASS on an empty comparison is not a real "
            "verification; check the config_hash is correct and that events for it "
            "still exist (see derive_session9_labels' docstring: a later run reusing "
            "the same config_hash does not delete rows, but run_id-based lookups can "
            "miss them)."
        )

    assert_event_ids_match(derived, actual)
    assert_backfill_covers_resolved_events(engine, config_hash)

    mismatches = diff_labels(derived, actual, LABEL_COLUMNS) if not derived.empty else {}
    explained = {col: reason for col, reason in EXPLAINED_COLUMNS.items() if col in mismatches}
    return ReconciliationReport(
        config_hash=config_hash,
        total_events=len(actual),
        mismatches=mismatches,
        explained=explained,
    )
