"""One-time repair: re-identify serving-born `predictions.id`s (component 4,
forward-log adoption, 2026-09-19).

**Why this exists.** Before `ServingParams.serving_id_floor` existed, serving
and research each minted `predictions.id` from their own sequence over the
same numeric range. Measured 2026-09-19: serving held 35,407 rows, max id
224,862; research held 36,203, max id 191,860; 35,292 ids agreed on both
sides; **3 ids named a different signal on each side** (research's JPM, MPC,
ZS against serving's HPE, ECHO, VLO, all 2026-09-17); **100 rows existed only
on serving**, scored live by the Pi and never seen by research at all.

`jobs/sync.py::_reset_sequences` and `_pull_predictions` (earlier tasks in
this plan) make the split real *going forward*: serving mints at or above
the floor, research stays below it, and the nightly pull adopts anything
serving mints there. Neither one touches a row that already exists below
the floor. This script is the one-time move of the 100 serving-born rows
out of research's range, which is also what clears the 3 collisions --
they belong to serving-born rows, so moving those rows off the shared ids
removes the collision along with them.

**What this script does NOT do.** It does not write research. Pulling the
repaired rows into research is `jobs/sync.py::pull_live_records`'s job (the
next nightly run does it, keyed on `id >= floor`, which is exactly the set
this script produces). It also does not touch any row already at or above
the floor -- if a Pi write already happened after the floor was deployed,
that row is left alone; only sub-floor, no-research-counterpart rows move.

**Identifying a serving-born row.** Below the floor *and* its natural key
-- `(config_hash, ticker, as_of, signal_type, entry_kind)`, the same tuple
`jobs/sync.py::_PREDICTIONS_EVENT_REMAP` already resolves `event_id`
through -- matches no research row. A row that IS in both stores under that
key, even at a colliding id, is not touched by this script; the collision
in that case is the 35,292 identical rows agreeing on the number, and remap
already handles reading `event_id` correctly across it.

**The FK ordering problem, and why the update is one combined statement.**
`outcomes.prediction_id bigint PRIMARY KEY REFERENCES predictions(id)`
(migration `7b31a50af774`) carries no `ON UPDATE CASCADE`. A plain
`UPDATE predictions SET id = ...` while an `outcomes` row still points at
the old id violates that constraint immediately -- Postgres checks a
NOT DEFERRABLE foreign key at the end of the SQL *command*, and two
separate `UPDATE` statements are two separate commands. A single
data-modifying `WITH` query that updates `outcomes` and `predictions`
together is one command: both tables reach their new, consistent state
before the constraint is ever checked. `_REASSIGN_SQL` below is that query,
not two statements run back to back.

**`signal_reports.prediction_id` also references `predictions(id)`**
(migration `f7d3a02e5c18`, nullable, no `ON UPDATE CASCADE` -- same FK
shape as `outcomes.prediction_id`). Every value is NULL today (verified
2026-09-19: `cscan poll` writes it as `None` -- see `jobs/poll.py`), so an
old id this script reassigns is never actually referenced there, and
`--apply` would abort cleanly on the same immediate-FK-check reasoning as
`outcomes` if that ever changed rather than silently corrupting anything.
`apply_reassignment` still checks it explicitly before writing, rather
than relying on that accident of current usage: a future write path that
starts populating the column would otherwise fail with a bare Postgres FK
error days or weeks later, on a row this script had no way to know about
when it was written.

**Default is `--dry-run`.** It prints the plan and writes nothing.
`--apply` performs it, in one transaction on the serving store, then raises
`predictions_id_seq` past the highest assigned id
(`jobs/sync.py::_reset_sequences`, reused rather than re-implemented) and
re-checks that no id names two different signals any more.

**The sequence reset is owed even when there is nothing left to reassign.**
`apply_reassignment` commits its own transaction; `_reset_sequences` used to
run afterwards only when `moves` was non-empty, which left a gap: if the
process died *between* those two calls, the reassignment survived (it was
already committed) but the sequence did not get raised. A rerun after
exactly that interruption recomputes `moves` as empty -- those rows now
carry ids at or above the floor, so `plan_reassignment` no longer selects
them -- so the old code printed "nothing to reassign" and exited 0 without
ever calling `_reset_sequences`. The Pi's next insert could then collide
with the very ids this script just assigned. `main()` now calls
`_reset_sequences` unconditionally on `--apply`, whether or not this run
found rows to move, so a rerun always closes that gap rather than trusting
that `moves.empty` means "nothing left to do."

**The dry run also prints `predictions_id_seq`'s current value and what
`--apply` would set it to** (`predicted_sequence_value`), computed the same
way `_reset_sequences` computes it -- `greatest(floor, max(id))`, taking the
reassignment's own new ids into account -- so a human reading the dry run
can predict that part of `--apply` without running it.

    uv run python scripts/repair_prediction_ids.py               # dry run
    uv run python scripts/repair_prediction_ids.py --apply
"""

from __future__ import annotations

import argparse

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.config import ServingParams
from capitalscan.jobs import db_io
from capitalscan.jobs.sync import _PREDICTIONS_EVENT_REMAP, _reset_sequences, serving_engine

#: The natural key a prediction is identified by, independent of which store
#: minted its surrogate id. Reused from `_PREDICTIONS_EVENT_REMAP` rather
#: than re-typed, so this script and the nightly pull can never disagree
#: about what "the same prediction" means.
NATURAL_KEY: tuple[str, ...] = _PREDICTIONS_EVENT_REMAP.source_key

# See "The FK ordering problem" above. `unnest` over two parallel arrays
# binds the whole mapping as two parameters regardless of row count, rather
# than one bind pair per row -- irrelevant at 100 rows, but it is also the
# form `jobs/sync.py` already prefers for a bounded, dynamic-length list.
_REASSIGN_SQL = """
WITH mapping AS (
    SELECT * FROM unnest(
        CAST(:old_ids AS bigint[]), CAST(:new_ids AS bigint[])
    ) AS m(old_id, new_id)
),
repointed AS (
    UPDATE outcomes o
       SET prediction_id = m.new_id
      FROM mapping m
     WHERE o.prediction_id = m.old_id
)
UPDATE predictions p
   SET id = m.new_id
  FROM mapping m
 WHERE p.id = m.old_id
"""


def _read_predictions_keys(engine: Engine) -> pd.DataFrame:
    """`id` plus the natural key, for every row. Small: ~35k rows, 6 columns."""
    cols = ", ".join(("id", *NATURAL_KEY))
    return pd.read_sql(text(f"SELECT {cols} FROM predictions"), engine)  # noqa: S608 - fixed names


def _read_sequence_value(engine: Engine) -> int:
    """`predictions_id_seq`'s current value, read directly off the sequence
    relation. A sequence is itself a one-row object in Postgres, so this
    needs no `pg_sequences` catalog lookup and no privilege beyond what
    owning `predictions` already implies."""
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT last_value FROM predictions_id_seq")).scalar_one())


def predicted_sequence_value(
    serving_predictions: pd.DataFrame, moves: pd.DataFrame, floor: int
) -> int:
    """What `_reset_sequences` will set `predictions_id_seq` to, computed
    without touching the database -- this is what the dry run prints
    alongside the current value.

    Mirrors `_reset_sequences`'s own arithmetic for the serving branch,
    `greatest(max(id), floor)`, except the ids in question do not exist in
    `serving_predictions` yet: `moves["new_id"]` is what the reassignment
    would write, so its max has to be folded in by hand rather than read
    back from a table.
    """
    values = [floor]
    if not serving_predictions.empty:
        values.append(int(serving_predictions["id"].max()))
    if not moves.empty:
        values.append(int(moves["new_id"].max()))
    return max(values)


def plan_reassignment(
    serving_predictions: pd.DataFrame, research_keys: pd.DataFrame, floor: int
) -> pd.DataFrame:
    """The serving-born rows to move, each carrying the new id it would get.

    Two classes move, and the second was missed until the repair ran for
    real on 2026-09-20.

    **Serving-born:** `id < floor` and the row's natural key resolves to no
    research row. That was the original rule, and it moved 100 rows.

    **Reused id:** `id < floor` and research holds a DIFFERENT natural key
    at that same id. These are the rows that actually block the sync.
    `_clear_remap_collisions` protects any serving row whose id appears in
    the incoming set, so serving's ECHO row at 174759 was protected by
    research's MPC row at 174759, and then raised `predictions_event_id`
    when research's own ECHO row claimed its `event_id` -- the
    `UniqueViolation` behind every failed nightly sync since 2026-09-17.
    The first run reported exactly this as its own postcondition: "ids
    naming a different signal on each side, after repair: 3".

    A below-floor row whose key AND id research agrees with is one of the
    35,292 rows the stores already share, and stays put.

    New ids start just past the higher of the floor and every id serving
    already holds (not just the ones below the floor), so a row already
    minted at or above the floor -- possible if the Pi has been writing
    there since the floor was deployed, ahead of this repair running --
    can never collide with a freshly assigned one.

    Returns an empty frame with a `new_id` column when there is nothing to
    move, so a caller never has to special-case "no rows serving-born".
    """
    below = serving_predictions[serving_predictions["id"] < floor]
    key_cols = list(NATURAL_KEY)
    merged = below.merge(
        research_keys[key_cols].drop_duplicates(),
        on=key_cols,
        how="left",
        indicator=True,
    )
    orphaned = below.loc[merged["_merge"].to_numpy() == "left_only"]

    # The reused-id class: same id on both sides, different natural key.
    # `mismatched_ids` is the same comparison, and it is this function's
    # postcondition -- so computing it here is what makes that
    # postcondition reachable rather than merely asserted.
    reused = below[below["id"].isin(mismatched_ids(serving_predictions, research_keys))]

    moving = (
        pd.concat([orphaned, reused])
        .drop_duplicates(subset=["id"])
        .sort_values("id")
        .reset_index(drop=True)
    )
    if moving.empty:
        return moving.assign(new_id=pd.Series(dtype="int64"))
    start = max(floor, int(serving_predictions["id"].max()) + 1)
    return moving.assign(new_id=list(range(start, start + len(moving))))


def mismatched_ids(serving_keys: pd.DataFrame, research_keys: pd.DataFrame) -> list[int]:
    """Ids present in both stores whose natural key disagrees.

    This is the direct measurement of the 3-row collision the design
    document describes, and it is also the repair's own postcondition:
    after `apply_reassignment` moves the serving-born rows off the shared
    ids, an id common to both stores can only be one of the 35,292 rows
    that already agreed, so this must return empty.
    """
    if serving_keys.empty or research_keys.empty:
        return []
    merged = serving_keys.merge(research_keys, on="id", suffixes=("_serving", "_research"))
    if merged.empty:
        return []
    differs = pd.Series(False, index=merged.index)
    for col in NATURAL_KEY:
        differs = differs | (
            merged[f"{col}_serving"].astype(str) != merged[f"{col}_research"].astype(str)
        )
    return sorted(int(i) for i in merged.loc[differs, "id"].tolist())


def apply_reassignment(engine: Engine, moves: pd.DataFrame) -> tuple[int, int]:
    """Write the reassignment: `predictions.id` and any referencing
    `outcomes.prediction_id`, in one transaction. Returns
    `(rows re-identified, outcomes repointed)`.

    The outcomes count is read before the write rather than via `RETURNING`
    from the combined statement below -- both tables are touched in one
    `WITH` command (see the module docstring), and a plain `rowcount` on
    that command reports only the top-level `UPDATE predictions`, not the
    nested one. Reading it first, inside the same transaction, is exact:
    nothing else writes `outcomes` for these ids between the two statements.

    **`signal_reports.prediction_id` is checked, not repointed.** It is a
    second FK onto `predictions(id)` (see the module docstring) that this
    repair's combined statement does not touch. Every value is NULL today,
    so the check below is expected to find nothing and `_REASSIGN_SQL`'s
    `UPDATE predictions` would in fact raise on Postgres's own immediate FK
    check if it ever did -- the same protection `outcomes` gets for free.
    The assertion exists so that failure reads as "a referencing row was
    found, this script does not move it" rather than a bare
    `ForeignKeyViolation` days after someone starts populating the column.
    """
    if moves.empty:
        return 0, 0
    old_ids = [int(i) for i in moves["id"].tolist()]
    new_ids = [int(i) for i in moves["new_id"].tolist()]
    with engine.begin() as conn:
        signal_reports_referencing = conn.execute(
            text("SELECT count(*) FROM signal_reports WHERE prediction_id = ANY(:old_ids)"),
            {"old_ids": old_ids},
        ).scalar_one()
        if signal_reports_referencing:
            raise RuntimeError(
                f"{signal_reports_referencing} signal_reports row(s) reference a "
                "prediction id this repair would reassign; this script does not "
                "repoint signal_reports.prediction_id, so it refuses to run rather "
                "than let the FK check fail mid-transaction. Investigate before "
                "re-running."
            )
        outcomes_repointed = conn.execute(
            text("SELECT count(*) FROM outcomes WHERE prediction_id = ANY(:old_ids)"),
            {"old_ids": old_ids},
        ).scalar_one()
        conn.execute(text(_REASSIGN_SQL), {"old_ids": old_ids, "new_ids": new_ids})
    return len(moves), int(outcomes_repointed)


def _print_plan(
    moves: pd.DataFrame, pre_mismatches: list[int], current_seq: int, predicted_seq: int
) -> None:
    print(f"serving-born rows to re-identify: {len(moves)}")
    if not moves.empty:
        lo, hi = int(moves["new_id"].min()), int(moves["new_id"].max())
        print(f"  new ids: {lo:,} .. {hi:,}")
        preview = moves[["id", "new_id", *NATURAL_KEY]].head(10)
        print(preview.to_string(index=False))
        if len(moves) > 10:
            print(f"  ... and {len(moves) - 10} more")
    print(f"\nids currently naming a different signal on each side: {len(pre_mismatches)}")
    if pre_mismatches:
        print(f"  {pre_mismatches}")
    print(f"\npredictions_id_seq: {current_seq:,} now -> {predicted_seq:,} after --apply")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the repair. Default is a dry run: print the plan, write nothing.",
    )
    args = parser.parse_args(argv)

    floor = ServingParams().serving_id_floor
    serving = serving_engine()
    research = db_io.get_engine()

    serving_keys = _read_predictions_keys(serving)
    research_keys = _read_predictions_keys(research)
    moves = plan_reassignment(serving_keys, research_keys, floor)
    pre_mismatches = mismatched_ids(serving_keys, research_keys)
    current_seq = _read_sequence_value(serving)
    predicted_seq = predicted_sequence_value(serving_keys, moves, floor)

    _print_plan(moves, pre_mismatches, current_seq, predicted_seq)

    if not args.apply:
        print("\ndry run, nothing written. Re-run with --apply to perform it.")
        return 0

    if moves.empty:
        print("\nnothing to reassign")
        reidentified, outcomes_repointed = 0, 0
    else:
        reidentified, outcomes_repointed = apply_reassignment(serving, moves)

    # Owed on every --apply, not only when this run found rows to move --
    # see the module docstring's "sequence reset is owed even when there is
    # nothing left to reassign" note. A rerun after an interruption between
    # `apply_reassignment`'s commit and this call must still repair the
    # sequence even though `moves` recomputes empty.
    _reset_sequences(serving, serving=True)

    post_serving_keys = _read_predictions_keys(serving)
    post_mismatches = mismatched_ids(post_serving_keys, research_keys)

    print(f"\nrows re-identified: {reidentified}")
    print(f"outcomes repointed: {outcomes_repointed}")
    print(f"ids naming a different signal on each side, after repair: {len(post_mismatches)}")
    if post_mismatches:
        print(f"  !! not empty, investigate before running pull_live_records: {post_mismatches}")
        return 1
    print("\nRun `cscan nightly` (or `pull_live_records`) to adopt these rows into research.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
