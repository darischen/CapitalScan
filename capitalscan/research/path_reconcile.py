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


def diff_labels(derived: pd.DataFrame, actual: pd.DataFrame, columns: list[str]) -> dict[str, pd.DataFrame]:
    """Per-column mismatch frames, keyed by column name. A column with no
    mismatches is absent from the result (never an empty-but-present key,
    so `bool(mismatches)` reads as "any differences at all").

    Both-null is not a mismatch (invariant 4's null convention is shared
    by both sides). Floats compare within `_FLOAT_TOL` absolute tolerance
    — comparing two independently computed float pipelines for bit-exact
    equality would fail on ordinary floating-point round-off, which is not
    what this check exists to catch.
    """
    merged = derived.merge(actual, on="event_id", suffixes=("_derived", "_actual"))
    mismatches: dict[str, pd.DataFrame] = {}
    for col in columns:
        d_col, a_col = f"{col}_derived", f"{col}_actual"
        d_vals, a_vals = merged[d_col], merged[a_col]

        both_null = d_vals.isna() & a_vals.isna()
        if pd.api.types.is_numeric_dtype(d_vals) or pd.api.types.is_numeric_dtype(a_vals):
            diff = (d_vals.astype(float) - a_vals.astype(float)).abs()
            mismatch_mask = ~both_null & (diff.isna() | (diff > _FLOAT_TOL))
        else:
            mismatch_mask = ~both_null & (d_vals != a_vals)

        if mismatch_mask.any():
            mismatches[col] = merged.loc[mismatch_mask, ["event_id", d_col, a_col]]
    return mismatches


@dataclass
class ReconciliationReport:
    run_id: str
    total_events: int
    mismatches: dict[str, pd.DataFrame] = field(default_factory=dict)
    explained: dict[str, str] = field(default_factory=dict)

    @property
    def unexplained_mismatch_columns(self) -> list[str]:
        return [c for c in self.mismatches if c not in self.explained]

    @property
    def passes(self) -> bool:
        return len(self.unexplained_mismatch_columns) == 0


def reconcile(engine: Engine, config: Config, run_id: str) -> ReconciliationReport:
    derived = derive_session9_labels(engine, config, run_id)
    with engine.connect() as conn:
        actual = pd.read_sql(
            text(
                f"SELECT id AS event_id, {', '.join(LABEL_COLUMNS)} FROM events "
                "WHERE run_id = :run_id"
            ),
            conn,
            params={"run_id": run_id},
        )

    mismatches = diff_labels(derived, actual, LABEL_COLUMNS) if not derived.empty else {}
    explained = {col: reason for col, reason in EXPLAINED_COLUMNS.items() if col in mismatches}
    return ReconciliationReport(
        run_id=run_id,
        total_events=len(actual),
        mismatches=mismatches,
        explained=explained,
    )
