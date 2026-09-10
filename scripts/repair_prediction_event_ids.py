"""Repair `predictions.event_id` on serving, once.

**The sync fixes what it pushes from now on (ADR 191); this fixes what is
already there.** `nightly` syncs incrementally, so a historical row whose
`event_id` was copied out of research's id space would keep its wrong value
until someone ran a full sync -- 2h48m, to repair a column no query reads
yet.

Measured on serving 2026-09-09, before this ran:

    20,200 predictions
     7,403 event_id matching no event at all
     3,035 event_id matching the WRONG event

The second number is the one that matters. PRGO's 2026-08-05 prediction
pointed at an SMTC event from 2020-07-13 and NRG's at PKX from 2018 -- links
that resolve, join cleanly, and are wrong. A dangling id is findable with an
outer join; this is invisible to any check that asks "does it join".

**Two steps, in order.**

1. Collapse duplicate natural keys. Two writers reach serving and identify a
   prediction differently -- `jobs/predict.py` upserts on `event_id`, the
   sync on `id` -- so one event can hold a Pi-written row and a synced row.
   Before the repair they never collided, because their ids came from
   different stores. Making `event_id` correct makes the collision real, and
   `predictions_event_id` is UNIQUE.

   The synced row wins: research is the authority for a row it has scored,
   and its id is the one `outcomes.prediction_id` can reference. Verified
   before writing this -- the pairs carry identical probabilities, so
   nothing measured is lost, only the second copy.

   It is identified as the row whose current `event_id` does *not* resolve,
   which is a local test and was checked against the data: of today's rows,
   the synced 498 resolve 0 times and the Pi's 472 resolve every time.

2. Rewrite every `event_id` from the natural key.

Idempotent: a second run finds no duplicates and rewrites each id to the
value it already holds.

    uv run python scripts/repair_prediction_event_ids.py --dry-run
    uv run python scripts/repair_prediction_event_ids.py
"""

from __future__ import annotations

import argparse

from sqlalchemy import text

from capitalscan.jobs.sync import serving_engine

#: The join `v_screen_live` uses and `Remap` reproduces.
_ON = """
      e.config_hash = p.config_hash AND e.ticker = p.ticker
  AND e.signal_date = p.as_of AND e.signal_type = p.signal_type
  AND e.entry_kind = p.entry_kind
"""

_SURVEY = f"""
SELECT count(*) AS total,
       count(*) FILTER (WHERE cur.id IS NULL) AS dangling,
       count(*) FILTER (WHERE cur.id IS NOT NULL
                          AND (cur.ticker <> p.ticker OR cur.signal_date <> p.as_of)) AS wrong,
       count(*) FILTER (WHERE tgt.id IS NULL) AS unresolvable
  FROM predictions p
  LEFT JOIN events cur ON cur.id = p.event_id
  LEFT JOIN events e ON {_ON}
  LEFT JOIN events tgt ON tgt.id = e.id
"""

#: Rows sharing a natural key, keeping the one whose current `event_id`
#: does not resolve -- the synced row. See the module docstring.
_DUPES = """
WITH keyed AS (
  SELECT p.id, p.config_hash, p.ticker, p.as_of, p.signal_type, p.entry_kind,
         (cur.id IS NOT NULL) AS resolves_now
    FROM predictions p
    LEFT JOIN events cur ON cur.id = p.event_id
),
ranked AS (
  SELECT id,
         row_number() OVER (
           PARTITION BY config_hash, ticker, as_of, signal_type, entry_kind
           ORDER BY resolves_now ASC, id ASC
         ) AS rn,
         count(*) OVER (
           PARTITION BY config_hash, ticker, as_of, signal_type, entry_kind
         ) AS n
    FROM keyed
)
SELECT id FROM ranked WHERE n > 1 AND rn > 1
"""

_REWRITE = f"""
UPDATE predictions p
   SET event_id = e.id
  FROM events e
 WHERE {_ON}
   AND p.event_id IS DISTINCT FROM e.id
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    engine = serving_engine()
    with engine.begin() as conn:
        before = conn.execute(text(_SURVEY)).mappings().one()
        print(
            f"before: {before['total']} predictions, "
            f"{before['dangling']} dangling, {before['wrong']} pointing at the WRONG event, "
            f"{before['unresolvable']} with no event to point at"
        )

        dupes = [r[0] for r in conn.execute(text(_DUPES))]
        print(f"duplicate natural keys to collapse: {len(dupes)}")

        if args.dry_run:
            would = conn.execute(
                text(
                    f"SELECT count(*) FROM predictions p JOIN events e ON {_ON} "
                    "WHERE p.event_id IS DISTINCT FROM e.id"
                )
            ).scalar_one()
            print(f"would rewrite {would} event_id values (dry run, nothing written)")
            return 0

        if dupes:
            deleted = conn.execute(
                text("DELETE FROM predictions WHERE id = ANY(:ids)"), {"ids": dupes}
            ).rowcount
            print(f"deleted {deleted} duplicate row(s)")

        rewritten = conn.execute(text(_REWRITE)).rowcount
        print(f"rewrote {rewritten} event_id value(s)")

        after = conn.execute(text(_SURVEY)).mappings().one()
        print(
            f"after:  {after['total']} predictions, "
            f"{after['dangling']} dangling, {after['wrong']} pointing at the WRONG event"
        )
        if after["wrong"]:
            print("FAILED: rows still point at the wrong event")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
