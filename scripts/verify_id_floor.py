"""Real-Postgres check that `ServingParams.serving_id_floor` removes the
`predictions` unique violation an id-keyed upsert used to raise (task 5,
forward-log adoption plan).

**Why this exists.** Every test for the floor split so far (`_reset_
sequences`, `_pull_predictions`, `pull_live_records`) runs against a fake
engine. On 2026-09-19 a fake missed a real defect -- `db_io.insert_new`'s
`rowcount` came back **-1** for a multi-row insert against real Postgres,
only visible on a scratch table against the actual server (see
`db_io.insert_new`'s docstring). This script is the different-instrument
check the design doc's "Testing" item 8 calls for: it runs the same shape
of collision, and the same shape of fix, against the real research
database, on tables nothing else reads.

**Scope.** Two throwaway tables, `zz_`-prefixed so they cannot be mistaken
for `predictions`, created and dropped by this script alone. It never reads
or writes `predictions`, `events`, `outcomes`, or any other real table, and
it never opens a connection to the serving store, the Pi, or `wivie` --
`db_io.get_engine()` resolves `DATABASE_URL_RESEARCH` only. The 2,078
pre-floor contaminated rows and the one-time repair script are out of
scope; this proves the *ongoing* mechanism, not the historical cleanup.

**Why two tables in one database, not two databases.** The defect is about
two independently-minted id sequences colliding, not about network
topology -- `_reset_sequences` and `_pull_predictions` both operate on
whichever engine they are given. Modelling "research" and "serving" as two
tables on the one local engine exercises the identical SQL path
(`db_io.copy_upsert`'s `ON CONFLICT (id) DO UPDATE`) without touching the
real serving store this task must not reach.

**Why the shared logic in `jobs/sync.py` is not called directly, except
where it now can be.** `_reset_sequences` scans the whole database for any
table named `%predictions` and would reset the *real* `predictions`
sequence if run against this engine; `_pull_predictions` and
`pull_live_records` are hardcoded to the table name `predictions` and to
`serving_engine()`. All three are the right shape to reuse in production
and the wrong shape to run against a shared research database from a
scratch script, so this script reimplements their few lines of SQL, scoped
to the `zz_` tables, and reuses the two functions that *are*
table-name-generic and side-effect-scoped to what they are given:
`db_io.copy_upsert` for the write, and (Task 5c) `sync_job.
predictions_max_id_sql` for the sequence comparison itself -- the one
`_reset_sequences` cannot expose directly because its DO block only knows
the table name at runtime, from the catalog. `predictions_max_id_sql`
takes a table name as a Python argument instead, so step 5 below runs the
REAL research-branch comparison (`WHERE "id" < floor`) rather than a
hand-copied mirror of it that could silently reverse.

**What this does and does not cover, stated plainly.** This script proves
the floor MECHANISM -- that a `setval` past a floor plus an `id >= floor`
selection plus an id-keyed `copy_upsert` removes the collision, against a
real server -- not that `jobs/sync.py::_reset_sequences` and
`_pull_predictions` are themselves wired correctly. Their own SQL is
reimplemented here rather than called (see above), so a regression
*inside either function's own SQL* -- a wrong comparison operator, a typo'd
column name, the floor read from the wrong config field -- would leave
this script passing while that function was broken. That gap is covered
elsewhere, not here: `test_sync_id_floor.py` and
`test_sync_resets_sequences.py` unit-test `_reset_sequences` directly, and
`test_pull_predictions.py` unit-tests `_pull_predictions` directly (all
against fakes, so they catch a logic error but not a real-Postgres-only
failure mode), and the nightly run's own output
(`pulled["predictions"]`, `pulled["predictions_unmapped"]`, the `runs` row)
is the real-server evidence that the wired-up path actually ran. A reader
who reaches for this script after a production failure in either function
should read those instead -- this script is the different-instrument check
for the *shape* of the defect, not a rerun of `_reset_sequences` or
`_pull_predictions` themselves.

    uv run python scripts/verify_id_floor.py
"""

from __future__ import annotations

import sys

import pandas as pd
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from capitalscan.core.config import ServingParams
from capitalscan.jobs import db_io
from capitalscan.jobs.sync import predictions_max_id_sql

RESEARCH_TABLE = "zz_verify_research_predictions"
SERVING_TABLE = "zz_verify_serving_predictions"

# A minimal stand-in for `predictions`: a surrogate `id` (its own sequence,
# mirroring `_reset_sequences`'s per-store split) plus a second column that
# is independently unique. That second constraint is what makes the
# collision raise rather than silently merge -- the real table's failure is
# `predictions_event_id`, not the primary key itself; see
# `jobs/sync.py::_clear_remap_collisions`.
_DDL = """
CREATE TABLE {table} (
    id bigint PRIMARY KEY,
    event_id bigint NOT NULL UNIQUE,
    signal_type text NOT NULL
);
CREATE SEQUENCE {table}_id_seq OWNED BY {table}.id;
ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('{table}_id_seq');
"""


def _create(engine: Engine, table: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(_DDL.format(table=table)))


def _drop(engine: Engine, table: str) -> None:
    # `IF EXISTS` and `CASCADE` so a script that failed partway through
    # setup still cleans up whatever did get created.
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        conn.execute(text(f"DROP SEQUENCE IF EXISTS {table}_id_seq"))


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608


def _row(engine: Engine, table: str, row_id: int) -> tuple[int, str] | None:
    with engine.connect() as conn:
        result = conn.execute(
            text(f"SELECT event_id, signal_type FROM {table} WHERE id = :id"),  # noqa: S608
            {"id": row_id},
        ).one_or_none()
    return (int(result.event_id), str(result.signal_type)) if result else None


def _read_all(engine: Engine, table: str, where: str = "") -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT * FROM {table} {where}")).mappings().all()  # noqa: S608
    return [dict(r) for r in rows]


def main() -> int:
    engine = db_io.get_engine()
    floor = ServingParams().serving_id_floor
    ok = True

    try:
        # --- Step 1: two scratch tables, each with its own id sequence ---
        _create(engine, RESEARCH_TABLE)
        _create(engine, SERVING_TABLE)
        print(f"[1] created {RESEARCH_TABLE} and {SERVING_TABLE}, each with its own id sequence")

        with engine.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {RESEARCH_TABLE} (event_id, signal_type) VALUES "
                    "(101, 'bull_close_below_lower'), (102, 'bear_close_above_upper'), "
                    "(103, 'bull_close_below_lower'), (104, 'bear_close_above_upper'), "
                    "(105, 'bull_close_below_lower')"
                )
            )
            conn.execute(
                text(
                    f"INSERT INTO {SERVING_TABLE} (event_id, signal_type) VALUES "
                    "(201, 'bull_close_below_lower'), (202, 'bear_close_above_upper'), "
                    # This third insert's sequence-assigned id is 3, which
                    # already names a DIFFERENT signal in the research
                    # table (event_id 103). Its own event_id, 105, is a
                    # third signal again -- one research already holds
                    # under its own id=5. Same id, different signal, on
                    # both ends: the exact pre-floor shape measured
                    # 2026-09-19 (three ids shared between rows describing
                    # different signals).
                    "(105, 'bear_close_above_upper')"
                )
            )
        r_count, s_count = _count(engine, RESEARCH_TABLE), _count(engine, SERVING_TABLE)
        print(f"    research rows: {r_count}, serving rows: {s_count}")
        if r_count != 5 or s_count != 3:
            raise RuntimeError(f"unexpected seed counts: research={r_count}, serving={s_count}")

        # --- Step 2: the failure, id-keyed, the way `_pull_predictions`
        # upserts today. All three serving rows go in one call, exactly as
        # `copy_upsert` would ship a chunk -- Postgres runs the ON CONFLICT
        # statement as one command, so row 3's violation aborts the whole
        # batch and rows 1-2 are rolled back with it. ---
        serving_rows = _read_all(engine, SERVING_TABLE)
        frame = pd.DataFrame(serving_rows)
        raised = None
        try:
            db_io.copy_upsert(engine, RESEARCH_TABLE, frame, conflict_cols=["id"])
        except IntegrityError as exc:
            raised = exc
        if raised is None:
            ok = False
            print(
                "[2] FAIL: id-keyed upsert of the colliding rows did not raise -- expected a "
                "unique violation"
            )
        else:
            print(f"[2] id-keyed upsert raised as expected: {raised.orig}")
        # The failed statement must not have partially applied -- confirm
        # research's id=3 row is still its own, unrelated signal.
        after = _row(engine, RESEARCH_TABLE, 3)
        if after != (103, "bull_close_below_lower"):
            ok = False
            print(f"[2] FAIL: research id=3 changed despite the raised violation: {after}")
        else:
            print(f"    confirmed no partial write: research id=3 is still {after}")

        # --- Step 3: the fix. Apply the floor to serving's sequence, so
        # every id it mints from here on lands at or above it --
        # `predictions_max_id_sql(serving=True)` is the REAL comparison
        # `_reset_sequences`'s serving branch makes (`greatest(max, floor)`),
        # executed directly rather than hand-copied, scoped to this one
        # scratch table rather than the whole-database sweep that function
        # does. ---
        serving_max_sql = predictions_max_id_sql(SERVING_TABLE, floor, serving=True)
        with engine.begin() as conn:
            n = conn.execute(text(serving_max_sql)).scalar_one()
            conn.execute(text(f"SELECT setval('{SERVING_TABLE}_id_seq', :n)"), {"n": n})
            new_id_row = conn.execute(
                text(
                    f"INSERT INTO {SERVING_TABLE} (event_id, signal_type) "
                    "VALUES (301, 'bull_close_below_lower') RETURNING id"
                )
            ).one()
        new_id = int(new_id_row.id)
        print(f"[3] floor applied ({floor}); newly minted serving row got id={new_id}")
        if new_id < floor:
            ok = False
            print(f"[3] FAIL: new serving id {new_id} is below the floor {floor}")

        # Re-identify the serving-born rows: `id >= floor`, exactly what
        # `_pull_predictions` selects on the real table.
        serving_born = _read_all(engine, SERVING_TABLE, where=f"WHERE id >= {floor}")
        print(f"    re-identified {len(serving_born)} serving-born row(s) at or above the floor")
        if len(serving_born) != 1:
            ok = False
            print(f"[3] FAIL: expected exactly 1 serving-born row, got {len(serving_born)}")

        # Adopt into research: the same `copy_upsert` call as step 2, on
        # the re-identified rows. Research's own ids stay under the floor
        # (mirroring `_reset_sequences`'s research branch), so this id
        # cannot collide with anything research already holds.
        adopt_frame = pd.DataFrame(serving_born)
        try:
            adopted = db_io.copy_upsert(engine, RESEARCH_TABLE, adopt_frame, conflict_cols=["id"])
        except IntegrityError as exc:
            ok = False
            adopted = 0
            print(f"[3] FAIL: id-keyed upsert raised after the floor was applied: {exc.orig}")
        else:
            print(f"    id-keyed upsert completed, {adopted} row(s) adopted")
        if adopted != 1:
            ok = False
            print(f"[3] FAIL: expected 1 adopted row, got {adopted}")

        # --- Step 4: the adopted row kept its serving values. ---
        landed = _row(engine, RESEARCH_TABLE, new_id)
        print(f"[4] adopted row in research: id={new_id} -> {landed}")
        if landed != (301, "bull_close_below_lower"):
            ok = False
            print(f"[4] FAIL: adopted row does not match serving's values: {landed}")
        else:
            print("    confirmed: adopted row kept its serving event_id and signal_type")

        # --- Step 5: the REAL research-branch comparison (Task 5c). ---
        # `RESEARCH_TABLE` now holds both its original below-floor rows
        # (ids 1-5) and the just-adopted at-or-above-floor row from step 4
        # -- exactly the shape research's real `predictions` table is in
        # after a nightly pull. `predictions_max_id_sql(serving=False)` is
        # the same `WHERE "id" < floor` comparison `_reset_sequences`'s
        # research branch runs, executed here rather than mirrored by
        # hand: a reversed comparison (`>=` instead of `<`) would compute
        # its max FROM the adopted row and push research's own next id up
        # into serving's range, recreating the exact collision this
        # script exists to prove is closed.
        research_max_sql = predictions_max_id_sql(RESEARCH_TABLE, floor, serving=False)
        with engine.begin() as conn:
            n = conn.execute(text(research_max_sql)).scalar_one()
            if n and n > 0:
                conn.execute(text(f"SELECT setval('{RESEARCH_TABLE}_id_seq', :n)"), {"n": n})
            new_research_id_row = conn.execute(
                text(
                    f"INSERT INTO {RESEARCH_TABLE} (event_id, signal_type) "
                    "VALUES (401, 'bull_close_below_lower') RETURNING id"
                )
            ).one()
        new_research_id = int(new_research_id_row.id)
        print(
            f"[5] real research-branch comparison applied "
            f"(ignored id={new_id} adopted above the floor); "
            f"newly minted research row got id={new_research_id}"
        )
        if new_research_id >= floor:
            ok = False
            print(
                f"[5] FAIL: research's own allocation reached the floor: "
                f"{new_research_id} >= {floor} -- the adopted row was not excluded"
            )
        else:
            print(
                "    confirmed: research's own allocation stayed below the floor "
                "despite the adopted row already sitting at or above it"
            )

    finally:
        _drop(engine, RESEARCH_TABLE)
        _drop(engine, SERVING_TABLE)
        print(f"dropped {RESEARCH_TABLE} and {SERVING_TABLE}")

    if ok:
        print("PASS: floor split removes the collision without losing the adopted row's values")
        return 0
    print("FAIL: see above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
