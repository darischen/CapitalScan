"""One-time backfill: link the 100 adopted predictions' `event_id` on the
slot, not the label (task 4, slot-keyed-adoption plan, 2026-09-20).

**Why this exists.** The 2026-09-20 nightly adopted 100 of the Pi's
predictions into research (`jobs/sync.py::_pull_predictions`) before the
slot fix in this plan landed -- each one resolved `event_id` through the
natural key `(config_hash, ticker, as_of, signal_type, entry_kind)`, and
that key assumes the Pi and the end-of-day pass label a debounce slot the
same way. Measured that night, `bb_lower_touch` (intraday, no close to
confirm against) and `bull_close_below_lower` (end of day, ADR 194) never
agreed once, so all 100 rows adopted with `event_id = NULL`.

Earlier tasks in this plan fix the key for every pull from here forward --
`_apply_slot_remap` resolves on `(config_hash, ticker, signal_date, side,
entry_kind)` instead. **They do not repair the 100 rows already written.**
ADR 195 makes `predictions` insert-only: `_pull_predictions` writes with
`insert_new` (`ON CONFLICT (id) DO NOTHING`), so a repeat pull over the
same ids adopts nothing and touches nothing. Those rows are permanently
stuck with the `event_id` the old key gave them -- NULL -- unless something
else fills it in. This script is that something, run once.

**What it does.** Reads every research `predictions` row that is
adopted (`id >= ServingParams.serving_id_floor`, the same test
`_pull_predictions` uses to mean "serving minted this id") and still
unresolved (`event_id IS NULL`), resolves `event_id` through the *same*
slot rule `_apply_slot_remap` applies nightly -- imported, not restated,
per this task's brief -- and writes back only the column that changed.

**Why the collision and intra-frame checks are needed here too, and are
the same two `_pull_predictions` runs.** This script writes `event_id`
onto rows that already exist in research, and `predictions_event_id` is
UNIQUE. Two ways that constraint can fire: (1) the slot a backfilled row
resolves to is already claimed by a different research row -- a row
adopted correctly on some other night, or written by `predict` --
(`_null_inbound_remap_collisions`, reused unchanged: `target` and `source`
are the same engine here, but that function only ever reads `target`,
so the equality is invisible to it); (2) two of the 100 rows resolve to the
*same* slot -- the mirror case `_null_duplicate_slot_targets` guards,
found in review of the nightly path and just as reachable here. Both are
reused directly rather than re-implemented, which is the whole point of
importing them: a second copy of either rule is the exact drift this
project has been burned by twice this week (see `jobs/sync.py`'s own
module docstring).

**Why collision-then-dedup, not the other order.** Same reasoning
`_pull_predictions` gives for its own ordering: a row this script is *not*
touching (already linked on a prior run, or linked some other way) is a
legitimate existing owner of its event and must be excluded before two
*backfilled* rows are ever compared against each other. Running dedup
first could null two rows against each other when only one of them
actually collides with anything real.

**The write touches exactly one column, and only where it is still
NULL.** `_BACKFILL_SQL` below is a single `UPDATE ... SET event_id = ...`
guarded by `WHERE p.id = m.id AND p.event_id IS NULL` -- the guard is not
redundant with scoping the read the same way: it is what makes a stale
read safe. If some other process links one of these rows between this
script's SELECT and its UPDATE, the guard makes that row's UPDATE a no-op
instead of an overwrite, and Postgres's own MVCC settles which write
happened first. No other column of any row -- linked, unlinked, above or
below the floor -- is ever named in the SET clause or touched by the
WHERE.

**Idempotent by construction, not by a check.** A second run's read
(`event_id IS NULL`) already excludes every row the first run linked, so
there is nothing left to resolve and nothing left to write. Rerunning
after a partial `--apply` (a crash mid-transaction, a `KeyboardInterrupt`)
is safe for the same reason: whatever got written is excluded from the
next read, and whatever did not survives to be tried again.

**Reports the same four reasons `pull_live_records` reports**, spelled the
same way (`no_slot`, `ambiguous`, `collision`, `duplicate_target`) --
Task 3 named exactly this drift risk: a caller reading last night's log
and this script's output must recognise the same words for the same
failure, not a second vocabulary for one rule.

    uv run python scripts/backfill_prediction_event_ids.py               # dry run
    uv run python scripts/backfill_prediction_event_ids.py --apply
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import ServingParams
from capitalscan.jobs import db_io
from capitalscan.jobs.sync import (
    _PREDICTIONS_EVENT_REMAP,
    _apply_slot_remap,
    _null_duplicate_slot_targets,
    _null_inbound_remap_collisions,
)

logger = logging.getLogger(__name__)

# See "The write touches exactly one column" above. `unnest` over two
# parallel arrays binds the whole (id -> event_id) mapping as two
# parameters regardless of row count, the same form `jobs/sync.py` and
# `scripts/repair_prediction_ids.py` already prefer for a bounded,
# dynamic-length list, rather than one bound pair of statement per row.
#
# The `event_id IS NULL` guard is load-aware, not decorative: it is the
# only thing standing between this script and overwriting a link some
# other writer set between the SELECT above and this UPDATE.
_BACKFILL_SQL = """
UPDATE predictions p
   SET event_id = m.event_id
  FROM unnest(
      CAST(:ids AS bigint[]), CAST(:event_ids AS bigint[])
  ) AS m(id, event_id)
 WHERE p.id = m.id AND p.event_id IS NULL
"""

#: The reason names, in the order `pull_live_records` reports them. Kept as
#: a tuple (not re-typed at each call site) so the report and the tests
#: agree on the four labels by construction.
REASONS: tuple[str, ...] = ("no_slot", "ambiguous", "collision", "duplicate_target")


def read_unresolved(engine: Engine, floor: int) -> pd.DataFrame:
    """Adopted rows (`id >= floor`) still carrying a NULL `event_id`.

    Scoped to `event_id IS NULL` on the read, not only on the write, so a
    row a prior `--apply` already linked never enters the resolution frame
    at all -- the write's own guard would no-op on it regardless, but
    filtering here means the report describes only the rows this run
    actually looked at, which is what makes "a second run changes nothing"
    visible in the printed counts rather than only in the database.
    """
    return pd.read_sql(
        text(
            "SELECT * FROM predictions "  # noqa: S608 - fixed table name
            "WHERE id >= :floor AND event_id IS NULL"
        ),
        engine,
        params={"floor": floor},
    )


def resolve(frame: pd.DataFrame, engine: Engine) -> tuple[pd.DataFrame, dict[str, int]]:
    """Resolve `event_id` for every row in `frame` against `engine`,
    reusing `_pull_predictions`'s own three-step sequence in its own order
    (slot lookup, then the collision check, then the intra-frame dedup --
    see the module docstring for why that order matters).

    `engine` plays both roles `_pull_predictions` splits into `source` and
    `target`: these rows already live in research, so the only "pull"
    happening is filling in one column already-resident rows arrived
    without.

    Returns the frame with `event_id` resolved where it could be, and the
    four reason counts, keyed by `REASONS`.
    """
    if frame.empty:
        return frame, dict.fromkeys(REASONS, 0)
    frame, no_slot, ambiguous = _apply_slot_remap(frame, engine)
    frame, collision = _null_inbound_remap_collisions(
        frame, engine, "predictions", ("id",), _PREDICTIONS_EVENT_REMAP
    )
    frame, duplicate_target = _null_duplicate_slot_targets(frame)
    return frame, {
        "no_slot": no_slot,
        "ambiguous": ambiguous,
        "collision": collision,
        "duplicate_target": duplicate_target,
    }


def plan_updates(frame: pd.DataFrame) -> pd.DataFrame:
    """The rows that resolved to a real event, as the `(id, event_id)`
    pairs the write needs. Rows still NULL after `resolve` are not writes
    at all -- there is nothing to set them to, and the row already reads
    NULL, so touching them again would violate "only where it changes"
    for no gain.
    """
    if frame.empty:
        return frame.assign(event_id=pd.Series(dtype="Int64"))[["id", "event_id"]]
    linked = frame.loc[frame["event_id"].notna(), ["id", "event_id"]]
    return linked.reset_index(drop=True)


def apply_updates(engine: Engine, updates: pd.DataFrame) -> int:
    """Write `updates` with `_BACKFILL_SQL`, in one transaction. Returns the
    number of rows Postgres actually updated -- which can be less than
    `len(updates)` if the `event_id IS NULL` guard caught a row linked by
    something else since the read (see the module docstring).
    """
    if updates.empty:
        return 0
    ids = [int(v) for v in updates["id"].tolist()]
    event_ids = [int(v) for v in updates["event_id"].tolist()]
    with engine.begin() as conn:
        result = conn.execute(text(_BACKFILL_SQL), {"ids": ids, "event_ids": event_ids})
        return int(result.rowcount or 0)


def _print_report(
    *, total: int, planned: int, reasons: dict[str, int], applied: int | None
) -> None:
    print(f"adopted rows examined (id >= floor, event_id IS NULL): {total}")
    print(f"resolved to exactly one event: {planned}")
    for name in REASONS:
        print(f"  {name}: {reasons[name]}")
    unresolved = sum(reasons[name] for name in REASONS)
    print(f"unresolved total: {unresolved}")
    if planned + unresolved != total:
        # Same ground-truth check `test_pull_predictions.py` runs on the
        # nightly path: the four reasons must sum to "not resolved", or a
        # fifth, uncounted nulling step crept in somewhere.
        raise RuntimeError(
            f"resolved ({planned}) + unresolved ({unresolved}) != examined ({total}); "
            "a nulling step is uncounted, investigate before trusting this report"
        )
    if applied is None:
        print("\ndry run, nothing written. Re-run with --apply to perform it.")
    else:
        print(f"\nrows updated: {applied}")
        if applied != planned:
            logger.warning(
                "planned %d update(s) but Postgres reported %d -- some row's "
                "event_id was set by something else between the read and the "
                "write; its link is left as whatever that write set, untouched "
                "by this run",
                planned,
                applied,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the backfill. Default is a dry run: print the plan, write nothing.",
    )
    args = parser.parse_args(argv)

    floor = ServingParams().serving_id_floor
    engine = db_io.get_engine()

    frame = read_unresolved(engine, floor)
    total = len(frame)
    frame, reasons = resolve(frame, engine)
    updates = plan_updates(frame)
    planned = len(updates)

    applied = apply_updates(engine, updates) if args.apply else None
    _print_report(total=total, planned=planned, reasons=reasons, applied=applied)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
