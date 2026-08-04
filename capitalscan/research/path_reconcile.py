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
from datetime import date, timedelta

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

# Real reconciliation run (2026-08-03, config_hash=3e598c59e7d71eae,
# 246,134 events) showed `mfe`/`mae` mismatching on ~14% of events at the
# original `1e-9` tolerance, with a median absolute diff of *exactly*
# `1e-6` — one unit in the last decimal place of `numeric(12,6)`. Both
# sides derive `favorable`/`adverse`/`mfe`/`mae` from the same underlying
# price via the same formula (`core.returns._favorable_adverse_series`),
# but each independently rounds to `numeric(12,6)` before/after storage —
# two values that agree to the true float can still differ by up to one
# quantum (`1e-6`) after independent rounding. `1e-9` was miscalibrated
# for `numeric(12,6)`-sourced data; it's the right tolerance for two
# float64 computations, not two independently-quantized DECIMAL(12,6)
# values. Verified by hand against `bars`/`path` for several sample
# mismatches (event 2896328/ORCL: derived mfe=0.027566 vs stored
# mfe=0.027565, exactly the rounding-boundary case) — not a computation
# bug.
#
# `3e-5`, not `2e-6`: after excluding the live-bars-revision population
# (see `RECENT_BARS_REVISION_DAYS` below) and fixing the `touched_*pct`
# rounding bug, the remaining historical-events-only residual had a much
# longer tail than a single independent-rounding quantum predicts —
# median `3e-6`, 90th percentile `8e-6`, 99th percentile `2.8e-5`. `mfe`
# is a `max()` over several already-noisy per-day `favorable` values, so
# when two candidate days are nearly tied, the two computation paths can
# pick different "winning" days, compounding beyond one quantum. `3e-5`
# covers ~99% of that measured historical noise while staying ~30x
# tighter than the live-bars-revision population's tight cluster around
# `3e-4` (see `RECENT_BARS_REVISION_DAYS`) and orders of magnitude
# tighter than any real window/off-by-one bug would produce (those show
# up as cents-scale price differences on typical mega-cap prices, not
# rounding-scale noise).
_FLOAT_TOL = 3e-5

# Real reconciliation run: 42 events (all `signal_date` in July 2026, the
# month before this run), clustered tightly around a `mfe` absolute diff
# of ~`3e-4` — two orders of magnitude above the historical noise floor
# above, but uniform in magnitude across a month-wide date spread (not a
# gradient that gets worse the closer to "today", which a genuinely
# still-settling single day would show). Root-caused directly for one
# sample (event 2862277/LRCX, signal_date=2026-07-29): `bars` for that
# ticker carried `run_id=bars_daily_20260803T211515_...` — ingested
# *after* the path backfill ran, revising the OHLC the backfill had
# already used. The uniform magnitude across a month of dates is
# consistent with one bars-refresh job re-ingesting/revising a rolling
# window of recent daily data in one pass, not a per-event data-quality
# issue. `path`/`events.mfe` were computed against two different
# snapshots of the same ticker's bars — a data-freshness artifact of a
# system that keeps ingesting, not a Session 10 computation defect.
# `reconcile()` excludes events whose `signal_date` falls in this
# trailing window from the `mfe`/`mae`/`capture_ratio` comparison — the
# comparison is only meaningful once the underlying bars have settled.
RECENT_BARS_REVISION_DAYS = 45

# `mfe`/`mae` are compared at `_FLOAT_TOL` because both sides derive from
# already-quantized (`numeric(12,6)`) `path.favorable`/`path.adverse`
# values, so they agree to within one quantum (see `_FLOAT_TOL`'s comment).
# `capture_ratio = r_exit / mfe` divides by `mfe`, which amplifies that
# same quantization error: a `numeric(12,6)` value carries up to `1e-6` of
# rounding noise, and dividing a fixed numerator by an `mfe` that differs
# by that much produces a *relative* error of `~1e-6/mfe` in the ratio —
# for realistic MFE values (a few percent), that is orders of magnitude
# above a tight absolute tolerance. Using the absolute tolerance here would
# flag mass false-positive mismatches on a real reconciliation run for a
# reason that has nothing to do with an actual defect (finding #3 of the
# final review). Columns listed here compare with *relative* tolerance
# instead; everything else keeps the absolute `_FLOAT_TOL` above.
#
# `5e-4` is a *fallback* only, used when `mfe` isn't available alongside
# `capture_ratio` in the frame being diffed (e.g. an isolated unit test
# constructing only a `capture_ratio` column). The real comparison uses
# `_capture_ratio_tolerance` below, which scales with `1/|mfe|` — a real
# reconciliation run showed a *fixed* relative tolerance cannot work here
# at all: mismatches clustered at `mfe` just above `CAPTURE_RATIO_MFE_FLOOR`
# (median mfe=0.016 among 2,090 residual mismatches after mfe/mae's own
# tolerance was fixed), where `_FLOAT_TOL/mfe` noise is `~0.2%` — several
# times a `5e-4` (`0.05%`) tolerance — while at `mfe` an order of magnitude
# larger the same absolute noise is negligible. A single fixed relative
# tolerance is either too tight near the floor or too loose far from it;
# nothing in between is correct for every `mfe` scale.
RELATIVE_TOLERANCE_COLUMNS = {"capture_ratio": 5e-4}

# Safety margin over the theoretical `_FLOAT_TOL/|mfe|` noise floor for
# `capture_ratio`'s adaptive tolerance — see `_capture_ratio_tolerance`.
# `3` was chosen against real data: at the real run's median residual
# `mfe=0.016`, `3 * _FLOAT_TOL / 0.016 ≈ 0.56%`, comfortably above the
# observed median relative diff there (`0.097%`) with room for the
# distribution's own spread, while at `mfe=CAPTURE_RATIO_MFE_FLOOR`
# (`0.005`) it caps at `~1.8%` — wide but still 50x+ tighter than the
# near-zero-`mfe` outliers `CAPTURE_RATIO_MFE_FLOOR` excludes entirely.
CAPTURE_RATIO_TOLERANCE_MARGIN = 3

# Below this |mfe|, `capture_ratio = r_exit / mfe` is dividing by a value
# close enough to zero that ordinary `numeric(12,6)` rounding noise on
# `mfe` (up to `1e-6`) produces enormous, meaningless swings in the ratio
# — the real run's worst `capture_ratio` mismatches were absolute
# differences in the *thousands* (e.g. derived -6880.28 vs actual
# -11758.23 on one event), not a computation error: both sides agree the
# position barely moved favorably and the ratio is dominated by rounding
# noise in a near-zero denominator. No relative tolerance can distinguish
# that from a real bug, because the ratio itself is not a stable quantity
# at this scale. `reconcile()` drops `capture_ratio` mismatches below this
# floor from the report entirely (not "explained" — genuinely not
# comparable), the same way `diff_labels` already drops both-null pairs.
CAPTURE_RATIO_MFE_FLOOR = 0.005


def _capture_ratio_tolerance(merged: pd.DataFrame) -> float | pd.Series:
    """Relative tolerance for `capture_ratio`, adaptive to `mfe`'s own
    magnitude where possible. `capture_ratio = r_exit / mfe`'s noise is
    driven entirely by `mfe`'s absolute tolerance (`_FLOAT_TOL`)
    propagated through division: `~_FLOAT_TOL/|mfe|`, times
    `CAPTURE_RATIO_TOLERANCE_MARGIN` for safety margin — see that
    constant's comment for the real-data calibration. Falls back to the
    fixed `RELATIVE_TOLERANCE_COLUMNS["capture_ratio"]` when `mfe_actual`
    isn't present in `merged` (e.g. an isolated unit test diffing only
    `capture_ratio`, with no `mfe` column to scale against).
    """
    if "mfe_actual" not in merged.columns:
        return RELATIVE_TOLERANCE_COLUMNS["capture_ratio"]
    mfe = merged["mfe_actual"].astype(float).abs()
    return CAPTURE_RATIO_TOLERANCE_MARGIN * _FLOAT_TOL / mfe.where(mfe > 0, np.nan)


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
                rel_tol = _capture_ratio_tolerance(merged) if col == "capture_ratio" else RELATIVE_TOLERANCE_COLUMNS[col]
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


def _drop_unstable_capture_ratio_rows(
    mismatches: dict[str, pd.DataFrame], actual: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Removes `capture_ratio` mismatch rows whose reference `mfe` (Session
    9's stored value — the denominator the *actual* `capture_ratio` was
    computed against) is below `CAPTURE_RATIO_MFE_FLOOR`. See that
    constant's comment for why: near a zero denominator, ordinary
    `numeric(12,6)` rounding on `mfe` swings the ratio by orders of
    magnitude, so a flag there says nothing about whether the underlying
    computation is correct. Drops the `capture_ratio` key entirely if
    every remaining mismatch was below the floor (never leaves an
    empty-but-present key — same convention `diff_labels` already uses).
    """
    if "capture_ratio" not in mismatches:
        return mismatches
    frame = mismatches["capture_ratio"]
    mfe_by_event = actual.set_index("event_id")["mfe"]
    stable = frame["event_id"].map(mfe_by_event).abs() >= CAPTURE_RATIO_MFE_FLOOR
    out = dict(mismatches)
    if stable.any():
        out["capture_ratio"] = frame.loc[stable]
    else:
        del out["capture_ratio"]
    return out


def _drop_recent_events(
    mismatches: dict[str, pd.DataFrame],
    columns: list[str],
    signal_dates: pd.Series,
    today: date,
    window_days: int = RECENT_BARS_REVISION_DAYS,
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    """Removes mismatch rows for `columns` whose `event_id`'s `signal_date`
    (from `signal_dates`, indexed by `event_id`) falls within
    `window_days` of `today`. See `RECENT_BARS_REVISION_DAYS`'s comment:
    bars for recent dates can still be revised after the path backfill
    ran, and comparing against a Session-9 value computed from an earlier
    bars snapshot is not a meaningful check until the data settles.

    Drops a column key entirely if every one of its mismatches was within
    the window (same "never leave an empty-but-present key" convention
    `diff_labels`/`_drop_unstable_capture_ratio_rows` already use).

    Returns `(filtered_mismatches, dropped_counts)` — the counts are
    surfaced on `ReconciliationReport.recent_events_excluded` rather than
    silently discarded, so a human reading the report can see exactly how
    many rows were excluded and why, not just take it on faith.
    """
    cutoff = today - timedelta(days=window_days)
    out = dict(mismatches)
    dropped: dict[str, int] = {}
    for col in columns:
        if col not in out:
            continue
        frame = out[col]
        dates = frame["event_id"].map(signal_dates)
        old_enough = dates < pd.Timestamp(cutoff)
        n_dropped = int((~old_enough).sum())
        if n_dropped:
            dropped[col] = n_dropped
        if old_enough.any():
            out[col] = frame.loc[old_enough]
        else:
            del out[col]
    return out, dropped


@dataclass
class ReconciliationReport:
    config_hash: str
    total_events: int
    mismatches: dict[str, pd.DataFrame] = field(default_factory=dict)
    explained: dict[str, str] = field(default_factory=dict)
    # Per-column count of mismatches excluded because signal_date fell
    # within RECENT_BARS_REVISION_DAYS of today — see _drop_recent_events.
    recent_events_excluded: dict[str, int] = field(default_factory=dict)

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


def reconcile(engine: Engine, config: Config, config_hash: str, today: date | None = None) -> ReconciliationReport:
    """Filters by `config_hash`, not `run_id` — see `derive_session9_labels`'s
    docstring for why `run_id` cannot reliably select a durable row set
    once any later run reuses the same config.

    `today` (defaults to the real current date) is used only to exclude
    the live-bars-revision population from `mfe`/`mae`/`capture_ratio`
    (see `RECENT_BARS_REVISION_DAYS`) — this is a diagnostic/reporting
    function, not part of the deterministic backtest computation, so a
    wall-clock read here doesn't touch ADR 060's "no wall-clock reads in
    the backtest" rule. Exposed as a parameter for tests, not for
    production callers to override.
    """
    if today is None:
        today = date.today()
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
    mismatches = _drop_unstable_capture_ratio_rows(mismatches, actual)

    with engine.connect() as conn:
        signal_dates_df = pd.read_sql(
            text("SELECT id AS event_id, signal_date FROM events WHERE config_hash = :config_hash"),
            conn,
            params={"config_hash": config_hash},
        )
    signal_dates = pd.to_datetime(signal_dates_df.set_index("event_id")["signal_date"])
    mismatches, recent_excluded = _drop_recent_events(
        mismatches, ["mfe", "mae", "capture_ratio"], signal_dates, today
    )

    explained = {col: reason for col, reason in EXPLAINED_COLUMNS.items() if col in mismatches}
    return ReconciliationReport(
        config_hash=config_hash,
        total_events=len(actual),
        mismatches=mismatches,
        explained=explained,
        recent_events_excluded=recent_excluded,
    )
