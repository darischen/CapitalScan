"""Repair JSONB values stored as Python reprs.

**The damage.** `db_io.json_safe` handled scalars and ended in
`return str(value)`, with no branch for `dict` or `list`. `append` routes
every dict-valued field through `json_safe_payload`, which walks the *top*
level only -- so from 2026-08-26 the column itself was a valid JSONB object
and every nested container inside it was stringified into a Python repr:

    "{'above_band': False, 'confirmed': False, 'band_gap': -13.597121}"

Nothing raised. A string is a legal JSONB value, and `->> 'confirmed'`
against one returns NULL rather than erroring, so `v_screen_live` reported
"no reversal" for two weeks and it looked like an absence of signals.

`db_io.json_safe` recurses as of 2026-09-09. This repairs what it wrote.

**Why parse rather than re-derive.** The reversal blocks are pure functions
of `(price, day_open, bands)` and all three are in the same row, so
recomputing was possible. Parsing is better: it restores what the poller
actually decided at the time, and a recompute would quietly paper over any
case where today's rule disagrees with the rule that ran that morning. This
is a serialisation bug, so the fix belongs at the serialisation layer.

**`ast.literal_eval`, never `eval`.** The input is our own repr, but the
rule stands anyway -- `literal_eval` cannot call anything. Measured before
writing this: zero rows contain `nan` or `inf`, which `literal_eval` would
reject, and the script counts any such failure rather than skipping it
silently.

Idempotent: a value that is already an object or that does not parse into a
container is left exactly as it is.

    uv run python scripts/backfill_json_reprs.py --target research --dry-run
    uv run python scripts/backfill_json_reprs.py --target research
    uv run python scripts/backfill_json_reprs.py --target serving
"""

from __future__ import annotations

import argparse
import ast
import json
from typing import Any

from sqlalchemy import text

from capitalscan.jobs import db_io
from capitalscan.jobs.sync import serving_engine

#: (table, primary key, json column). Every table `db_io.append` writes a
#: dict into. `bar_rejects.payload` is listed even though it measured clean
#: -- it goes through the same path, so leaving it out would make the sweep
#: depend on a measurement rather than on the code.
TARGETS: list[tuple[str, str, str]] = [
    ("signal_reports", "id", "state_json"),
    ("signal_reports", "id", "call_overlay_json"),
    ("runs", "run_id", "params"),
    ("bar_rejects", "id", "payload"),
]


class Stats:
    def __init__(self) -> None:
        self.rows_seen = 0
        self.rows_changed = 0
        self.values_repaired = 0
        self.parse_failures: list[str] = []


def _repair(value: Any, stats: Stats) -> Any:
    """Walk a decoded JSON value, turning repr strings back into containers.

    Recurses into what it repairs: `call_overlay_json['strikes']` is a list
    of dicts each holding a `payoff_at_reach` dict, and the whole subtree
    was flattened into one string by a single `str()` call.
    """
    if isinstance(value, dict):
        return {k: _repair(v, stats) for k, v in value.items()}
    if isinstance(value, list):
        return [_repair(v, stats) for v in value]
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if not (stripped.startswith(("{", "[")) and stripped.endswith(("}", "]"))):
        return value
    try:
        parsed = ast.literal_eval(stripped)
    except (ValueError, SyntaxError, MemoryError, RecursionError) as exc:
        # Recorded, not swallowed. A value that looks like a container and
        # will not parse is the one case a human has to look at.
        stats.parse_failures.append(f"{exc}: {stripped[:120]}")
        return value
    if not isinstance(parsed, (dict, list)):
        return value
    stats.values_repaired += 1
    return _repair(parsed, stats)


def _sweep(engine: Any, table: str, pk: str, column: str, *, dry_run: bool) -> Stats:
    stats = Stats()
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
        if not exists:
            print(f"  {table}.{column}: absent on this database, skipped")
            return stats

        rows = conn.execute(
            text(f'SELECT "{pk}", "{column}"::text FROM "{table}" WHERE "{column}" IS NOT NULL')
        ).fetchall()

        updates: list[dict[str, Any]] = []
        for key, raw in rows:
            stats.rows_seen += 1
            before = stats.values_repaired
            fixed = _repair(json.loads(raw), stats)
            if stats.values_repaired > before:
                stats.rows_changed += 1
                updates.append({"k": key, "v": json.dumps(fixed)})

        if updates and not dry_run:
            # One statement per batch rather than per row: 1,871 rows is
            # small, but this also runs against the Pi over the network.
            conn.execute(
                text(f'UPDATE "{table}" SET "{column}" = CAST(:v AS jsonb) WHERE "{pk}" = :k'),
                updates,
            )

    verb = "would repair" if dry_run else "repaired"
    print(
        f"  {table}.{column}: {stats.rows_seen} rows, {verb} {stats.values_repaired} "
        f"values across {stats.rows_changed} rows"
    )
    for failure in stats.parse_failures[:5]:
        print(f"    PARSE FAILURE {failure}")
    if len(stats.parse_failures) > 5:
        print(f"    ... and {len(stats.parse_failures) - 5} more")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["research", "serving", "url"], required=True)
    parser.add_argument(
        "--url",
        help=(
            "With `--target url`, the database to repair. Exists for a store "
            "that is neither of the two the config knows about -- a `wivie` "
            "stage restored from a dump taken before the fix, say, which "
            "carries the damage forward into a copy nothing else will ever "
            "sweep."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.target == "url":
        if not args.url:
            parser.error("--target url requires --url")
        engine = db_io.get_engine(args.url)
    else:
        engine = serving_engine() if args.target == "serving" else db_io.get_engine()
    print(f"{args.target}{' (dry run)' if args.dry_run else ''}")

    total_values = 0
    total_failures = 0
    for table, pk, column in TARGETS:
        stats = _sweep(engine, table, pk, column, dry_run=args.dry_run)
        total_values += stats.values_repaired
        total_failures += len(stats.parse_failures)

    print(f"total: {total_values} values, {total_failures} parse failures")
    return 1 if total_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
