"""Delete every config generation except the ones named, in bounded batches.

**Why batches.** `path.event_id -> events.id` is `ON DELETE CASCADE`, so one
`DELETE FROM events WHERE config_hash = ...` drags roughly four path rows per
event into the same transaction. Measured 2026-09-10: 11,954,433 superseded
events carry 46,228,766 path rows. A single statement is a ~58M-row
transaction, which is the shape that got three full syncs killed by the
Windows low-memory reaper earlier the same night.

Batches of `BATCH` events commit independently. An interrupted run leaves
fewer superseded rows than it started with and re-running converges, which
is the right failure mode for a cleanup.

**What is kept, and why the list is explicit rather than "the live one".**
After a `config_hash` change the *previous* generation still owns the
`outcomes` forward log -- the only estimate in this project that nothing has
iterated against (ADR 179). Deleting "everything but current" the day after
a rebuild would take it. So the caller names every generation to keep and
the script refuses an empty list.

**This frees no disk on its own.** Postgres marks the tuples dead and reuses
the space only within the same table. `VACUUM FULL events` and
`VACUUM FULL path` return it to the filesystem, need roughly the table size
again in temp space, and hold ACCESS EXCLUSIVE for the duration -- so they
must not run while anything reads those tables.

    uv run python scripts/drop_superseded_generations.py --keep <hash> [--keep <hash>] --dry-run
    uv run python scripts/drop_superseded_generations.py --keep <hash> [--keep <hash>]
"""

from __future__ import annotations

import argparse
import time

from sqlalchemy import text

from capitalscan.jobs import db_io

#: Events per transaction. Each drags ~4 path rows behind it, so this is
#: roughly a 1M-row transaction -- large enough that the round trips are
#: noise, small enough to stay far under the commit ceiling.
BATCH = 200_000

#: Config-keyed tables that are NOT reached by the events cascade. `path` is
#: absent deliberately: it cascades, and deleting it separately would double
#: the work.
SIDE_TABLES = ("cell_stats", "benchmarks", "universe", "rho_era")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="append", default=[], metavar="HASH")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.keep:
        parser.error("--keep is required; refusing to guess which generations survive")

    engine = db_io.get_engine()
    keep = list(args.keep)

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT config_hash, count(*) FROM events "
                "WHERE config_hash <> ALL(:keep) GROUP BY 1 ORDER BY 2 DESC"
            ),
            {"keep": keep},
        ).all()
        kept = conn.execute(
            text(
                "SELECT config_hash, count(*) FROM events WHERE config_hash = ANY(:keep) GROUP BY 1"
            ),
            {"keep": keep},
        ).all()

    print("keeping:")
    for h, n in kept:
        print(f"  {h}  {n:,} events")
    if not kept:
        print("  !! none of the --keep hashes exist in events -- refusing to run")
        return 1

    total = sum(n for _, n in rows)
    print(f"\ndeleting {len(rows)} generation(s), {total:,} events (path cascades):")
    for h, n in rows:
        print(f"  {h}  {n:,}")
    if args.dry_run:
        print("\ndry run, nothing written")
        return 0
    if not rows:
        print("\nnothing to do")
        return 0

    started = time.time()
    done = 0
    for h, n in rows:
        while True:
            with engine.begin() as conn:
                deleted = conn.execute(
                    text(
                        "DELETE FROM events WHERE id IN ("
                        "  SELECT id FROM events WHERE config_hash = :h LIMIT :b)"
                    ),
                    {"h": h, "b": BATCH},
                ).rowcount
            if not deleted:
                break
            done += deleted
            rate = done / max(time.time() - started, 1e-9)
            print(
                f"  {h[:12]}  {done:,}/{total:,}  {rate:,.0f} events/s  "
                f"eta {(total - done) / max(rate, 1e-9) / 60:.0f}m",
                flush=True,
            )

    for tbl in SIDE_TABLES:
        with engine.begin() as conn:
            n = conn.execute(
                text(f"DELETE FROM {tbl} WHERE config_hash <> ALL(:keep)"),  # noqa: S608
                {"keep": keep},
            ).rowcount
        print(f"  {tbl}: {n:,} deleted", flush=True)

    with engine.begin() as conn:
        left = conn.execute(
            text("SELECT count(*) FROM events WHERE config_hash <> ALL(:keep)"), {"keep": keep}
        ).scalar_one()
        orphans = conn.execute(
            text(
                "SELECT count(*) FROM path p "
                "LEFT JOIN events e ON e.id = p.event_id WHERE e.id IS NULL"
            )
        ).scalar_one()
    print(f"\nsuperseded events remaining: {left:,}")
    print(f"orphaned path rows: {orphans:,}  (cascade should make this 0)")
    print(f"elapsed {(time.time() - started) / 60:.1f}m")
    print("\nDisk is NOT yet returned. Run, with nothing else touching these tables:")
    print("  VACUUM (FULL, ANALYZE) events;")
    print("  VACUUM (FULL, ANALYZE) path;")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
