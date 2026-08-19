"""One-shot: correct the poller timestamps ADR 127 left four hours early.

`poll.py::_now_et` returned a naive ET datetime until 2026-08-19. It was
written to `signal_reports.fired_at` and `quotes_live.ts`, both
`timestamptz` on a database running `Etc/UTC`, so Postgres read the naive
value as UTC. Every row written by the poller before the fix is ET wall
clock wearing a `+00` label — four hours early in summer, five in winter.

The fix corrects new writes. This corrects the ones already stored.

**Run it with the poller stopped.** A running poller writes rows in the old
format, and this script cannot tell a not-yet-corrected old row from a
correct new one — they are both just timestamps. Stop the poller, run this,
then start it again on the fixed code.

**Idempotent by watermark, not by value.** There is no way to look at
`13:31:47+00` and know whether it is a corrected instant or an uncorrected
wall clock. So the script refuses to guess: it takes an explicit cutoff and
shifts only rows at or before it, and it records what it did in
`runs`. Running it twice with the same cutoff would double-shift, which is
why the cutoff must be a timestamp you can name — the moment the poller was
last restarted on the old code.

    uv run python scripts/backfill_poller_timestamps.py --before '2026-08-19 18:00:00+00'
    uv run python scripts/backfill_poller_timestamps.py --before '...' --apply

Without `--apply` it prints what it would change and touches nothing.
"""

from __future__ import annotations

import argparse
from datetime import datetime

from sqlalchemy import text

from capitalscan.jobs import db_io

# The zone the naive values were written in. `AT TIME ZONE` on a
# `timestamptz` first renders it as a naive local time, so
# `ts AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'` reads "strip the
# false UTC label, then attach the true Eastern one" -- which is exactly
# what the value always meant. Named zone, not a fixed offset, so a row
# written in January gets -5 and one in July gets -4 from Postgres tzdata.
_CORRECT = "({col} AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York'"

TABLES = (
    ("signal_reports", "fired_at"),
    ("quotes_live", "ts"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before",
        required=True,
        help="Shift rows with a timestamp at or before this. Use the moment "
        "the poller was last started on the pre-ADR-127 code.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write. Without it, prints the plan and exits.",
    )
    args = parser.parse_args()

    cutoff = datetime.fromisoformat(args.before)
    engine = db_io.get_engine()

    with engine.begin() as conn:
        # Refuse to run against a live poller. `runs` is written by SQL and
        # is therefore already correct, so it is a trustworthy witness.
        running = conn.execute(
            text("SELECT count(*) FROM runs WHERE job = 'poll' AND status = 'running'")
        ).scalar_one()
        # Only blocks the write. A dry run reads and prints, so refusing it
        # while the poller is up meant you could not see the plan until the
        # moment you were about to run it -- which is the wrong time to
        # first look at a data migration.
        if running and args.apply:
            print(
                f"refusing: {running} poll run(s) are still 'running'. Stop the "
                "poller first -- this script cannot tell an uncorrected old row "
                "from a correct new one."
            )
            return 1

        total = 0
        for table, col in TABLES:
            n = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE {col} <= :cutoff"),  # noqa: S608
                {"cutoff": cutoff},
            ).scalar_one()
            total += n
            print(f"  {table}.{col}: {n:,} rows at or before {cutoff}")

        if not args.apply:
            print(f"\ndry run: {total:,} rows would shift. Re-run with --apply.")
            return 0

        for table, col in TABLES:
            expr = _CORRECT.format(col=col)
            conn.execute(
                text(f"UPDATE {table} SET {col} = {expr} WHERE {col} <= :cutoff"),  # noqa: S608
                {"cutoff": cutoff},
            )
            print(f"  {table}.{col}: shifted")

        print(f"\napplied to {total:,} rows")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
