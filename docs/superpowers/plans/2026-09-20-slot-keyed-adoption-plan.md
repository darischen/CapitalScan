# Plan — slot-keyed adoption

**Spec:** `docs/superpowers/specs/2026-09-20-slot-keyed-adoption-design.md`
(binding authority). **Branch:** `slot-keyed-adoption`.

## Global Constraints

- `core/` performs no IO (invariant 1); `core/config.py` holds dataclasses
  only (invariant 10). No magic numbers outside `core/config.py` (invariant 9).
- **One source for side.** `core/cells.py`'s `LONG_SIGNALS` / `SHORT_SIGNALS`
  already define which side a `signal_type` belongs to, and
  `handlers/enums.py::side_for_signal_type` reads them. Do not write a second
  mapping anywhere.
- **Adoption relabels nothing.** The adopted row keeps the live `signal_type`
  it was written with.
- **Never guess a link.** No event, or more than one, means `event_id` stays
  NULL and the row is counted.
- Adoption stays insert-only (ADR 195). Nothing in this work may rewrite an
  existing `predictions` row except the backfill, which may set `event_id`
  and only where it is currently NULL.
- The outbound `run_sync` remap is unchanged.
- **Never run bare `pytest`.** Only
  `uv run pytest capitalscan/tests/unit capitalscan/tests/property`. Nothing
  under `capitalscan/tests/integration/` may run.
- All four gates before each commit, whole-repo scope: `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy`, then the unit+property run
  with `-p no:randomly --hypothesis-profile=ci_fast --cov=capitalscan/core
  --cov-report=term-missing --cov-fail-under=90`.
- **No writes to any live database.** Tests use fakes; the real-Postgres task
  uses `zz_`-prefixed scratch tables in the local research database only, and
  never the serving store, the Pi, or `wivie`.
- Match the house voice: comments explain *why*, with measured numbers.

Measured facts you may quote: 338 poller-written events on serving since
2026-09-08, 0 matching research's natural key, 114 sharing a ticker-date;
100 of 100 adopted rows carrying NULL `event_id`; 15 ambiguous slots out of
182,921 events since 2026-08-01.

## Task 1 — resolve a prediction's side from its signal type

Files: `capitalscan/jobs/sync.py` (or a small helper it imports),
`capitalscan/tests/unit/test_slot_side.py` (new).

Add the derivation the remap needs: given a frame carrying `signal_type`,
produce a `side` column using `core/cells.py`'s tuples. An unrecognised type
raises rather than defaulting — a silently wrong side would link a long
prediction to a short event.

Prefer reusing `handlers/enums.py::side_for_signal_type` if `jobs/` may
import `handlers/`; check the existing import layering first and say in your
report which way you went and why.

Tests: every `SignalType` member resolves; the two close-confirmed types land
on the right sides; an unknown string raises; the helper does not mutate its
input (project convention).

## Task 2 — the slot remap, used by the inbound pull only

Files: `capitalscan/jobs/sync.py`,
`capitalscan/tests/unit/test_slot_remap.py` (new).

Resolve `event_id` for adopted rows on
`(config_hash, ticker, signal_date, side, entry_kind)` against `events`:

- exactly one match → that id
- no match → NULL, counted as `no_slot`
- more than one match → NULL, counted as `ambiguous`, never a pick

Keep `_apply_remap`'s existing outbound behaviour untouched; add a sibling
path or extend it without changing the natural-key case. `_pull_predictions`
uses the slot resolution; `run_sync` does not.

Tests: the label-mismatch case links (a `bb_lower_touch` prediction onto a
`bull_close_below_lower` event in the same slot); no match gives NULL; two
matches give NULL and never an id; the adopted frame's `signal_type` is
unchanged by resolution.

## Task 3 — counts that name the failure

Files: `capitalscan/jobs/sync.py`, `capitalscan/jobs/cli.py`,
`capitalscan/tests/unit/test_pull_predictions.py` (extend).

`pull_live_records` returns `predictions`, `predictions_unmapped_no_slot` and
`predictions_unmapped_ambiguous`. The nightly line prints them. Replace the
existing single `predictions_unmapped` key rather than adding a fourth.

Tests: the three counts are returned and add up; the nightly print includes
them; a pull with everything resolving reports zeros.

## Task 4 — the NULL-only backfill

Files: `scripts/backfill_prediction_event_ids.py` (new),
`capitalscan/tests/unit/test_backfill_prediction_event_ids.py` (new).

Adoption is insert-only, so the 100 rows already adopted with NULL links will
never be repaired by a later pull. This script repairs them once:

- `--dry-run` is the default and writes nothing; `--apply` performs it.
- It resolves with the same slot rule as Task 2 — import it, do not restate
  it.
- It sets `event_id` **only where it is currently NULL**, and touches no
  other column of any row.
- It reports rows linked, `no_slot`, and `ambiguous`, and is idempotent.
- No absolute path to the repo, the venv or `psql` may appear in it.

Tests, against fakes: only NULL rows are updated; a second run changes
nothing; an ambiguous slot is left NULL; the generated SQL updates exactly
one column.

## Task 5 — real-Postgres check and docs

Files: `scripts/verify_slot_adoption.py` (new), `docs/DECISIONS.md`,
`docs/RESULTS.md`, `docs/BACKLOG.md`, `CLAUDE.md`.

Script, on `zz_` scratch tables in the local research database, dropped in a
`finally`: seed an events table holding a slot labelled
`bull_close_below_lower` and a predictions row labelled `bb_lower_touch` in
the same slot; show the natural-key resolution finding nothing and the slot
resolution linking them; seed a second event in one slot and show that one
staying NULL. Exit non-zero if any step misbehaves.

Docs: a new ADR recording slot-keyed adoption, the 0-of-338 measurement, and
why the two labels cannot be made to agree (the live path has no close).
`RESULTS.md` gets the measurement table. `BACKLOG.md`'s parked "adopted row
keeps a NULL link forever" item closes, pointing at Task 4. Bump the ADR
count line in `CLAUDE.md`.
