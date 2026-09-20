# Plan — research adopts the Pi's predictions

**Spec:** `docs/superpowers/specs/2026-09-19-forward-log-adoption-design.md`
(read it; it is the binding authority). **Branch:** `forward-log-adoption`.

## Global Constraints

- **`core/` performs no IO** (invariant 1). `core/config.py` holds
  dataclasses only, importing `dataclasses` alone (invariant 10).
- **No magic numbers outside `core/config.py`** (invariant 9). The floor is
  `ServingParams.serving_id_floor = 1_000_000_000`. `jobs/sync.py` carries no
  literal for it.
- **`ServingParams` is not part of `Config`** and must stay that way: adding
  it to `Config` moves `config_hash` and orphans every row keyed on
  `f183b0f5209a4677`. `test_the_default_config_hash_did_not_move` guards this.
- **Write the test before the implementation** for anything in `core/`.
- **Never run bare `pytest`.** Only
  `uv run pytest capitalscan/tests/unit capitalscan/tests/property`.
  Nothing under `capitalscan/tests/integration/` may run.
- **All four gates before each commit**, whole-repo scope:
  `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`,
  then the unit+property run with `-p no:randomly --hypothesis-profile=ci_fast
  --cov=capitalscan/core --cov-report=term-missing --cov-fail-under=90`.
- **No writes to the live serving store or to `wivie`** from any task here.
  Real-database checks use scratch tables in the local research database
  only (workstation, `DATABASE_URL_RESEARCH`).
- Match the surrounding comment density and voice: this codebase explains
  *why*, with measured numbers, in prose.

## Task 1 — the floor constant and the per-store sequence reset

Files: `capitalscan/core/config.py`, `capitalscan/jobs/sync.py`,
`capitalscan/tests/unit/test_sync_id_floor.py` (new).

1. Add `serving_id_floor: int = 1_000_000_000` to `ServingParams` with a
   docstring paragraph explaining: serving mints prediction ids at or above
   it, research stays below, and why it is here rather than in `Config` (the
   hash must not move — the same argument the class docstring already makes
   for `history_years` and `breadth_rank_floor`).
2. `_reset_sequences(engine, ...)` learns which store it is fixing. Give it a
   keyword argument (for example `serving: bool`) rather than inspecting the
   engine:
   - serving → `predictions_id_seq` becomes `max(floor, max(id))`
   - research → the `predictions` sequence is computed from
     `max(id) WHERE id < floor`; a research store holding only adopted rows
     (all ids above the floor) leaves the sequence untouched rather than
     setting it to 0, because `setval(seq, 0)` is an error.
   - every other table keeps today's behaviour on both sides.
3. Both call sites pass the flag: `run_sync` resets serving, and
   `pull_live_records` resets research.

Tests (no real database — assert on the SQL these build, using the existing
fake-engine style in `capitalscan/tests/unit/`):
- serving reset raises a below-floor sequence to the floor
- serving reset leaves an above-floor sequence alone
- research reset excludes ids at or above the floor
- the floor reaches the SQL from `ServingParams`, not from a literal
- `ServingParams` is still not reachable from `Config`

## Task 2 — `pull_live_records` adopts serving-born predictions

Files: `capitalscan/jobs/sync.py`,
`capitalscan/tests/unit/test_pull_predictions.py` (new).

Add a `predictions` step to `pull_live_records`, serving → research:

- Select serving predictions with `id >= serving_id_floor`. Carry the
  events natural key columns the remap needs.
- Remap `event_id` into research's id space with the existing `_apply_remap`
  and a `Remap` whose source key is
  `(config_hash, ticker, as_of, signal_type, entry_kind)` on `predictions`
  and whose target key is
  `(config_hash, ticker, signal_date, signal_type, entry_kind)` on `events`.
  An unmatched key yields NULL, never the source id.
- Write with `db_io.copy_upsert(target, "predictions", frame, ["id"])`, so a
  repeated pull inserts nothing. Do **not** key on `event_id`: NULLs are
  distinct in a unique index and unmapped rows would duplicate every night.
- Count adopted rows into the returned dict under `predictions`, and count
  the unmapped ones so the caller can report them.
- The existing three tables keep their current behaviour and order.

Tests: selection is floor-scoped; the remap resolves through the events
natural key; an unmatched key gives NULL; the write is keyed on `id`; a
second pull over the same rows adopts nothing.

## Task 3 — nightly pulls before it predicts

Files: `capitalscan/jobs/cli.py`,
`capitalscan/tests/unit/test_nightly_chain.py`.

Move the `pull_live_records` call so it runs **before** the `predict` step
and still before `sync`. Keep its failure non-fatal and its console line.
Say in the comment why the order is load-bearing: `predict` is insert-only
(ADR 195), so an adopted row must already be present for research to keep
the number a reader saw.

Tests, in the existing `TestOutcomesRunsNightly` style: nightly calls
`pull_live_records` before `run_predict`; the pull still precedes `sync`; a
failing pull does not stop the chain.

## Task 4 — the one-time repair script

Files: `scripts/repair_prediction_ids.py` (new),
`capitalscan/tests/unit/test_repair_prediction_ids.py` (new).

A script, not a job: it runs by hand once. `--dry-run` is the default; it
prints what it would change and writes nothing. `--apply` performs it.

For each serving prediction whose `id` is below the floor **and** whose
natural key is absent from research (the serving-born rows):

1. Assign a new id at or above the floor.
2. Update serving's row, and any `outcomes.prediction_id` referencing it,
   inside one transaction per store.
3. Raise serving's `predictions_id_seq` above the highest assigned id.
4. Report: rows re-identified, outcomes repointed, and any id that appears in
   both stores describing different signals (this set must be empty after).

Adoption into research is **not** this script's job — it is Task 2's pull.

`scripts/` may contain no absolute path to the repo, the venv or `psql`.
Tests cover id assignment, the dry run writing nothing, and the
different-signal detection — all against fakes.

## Task 5 — real-Postgres verification of the whole path

Files: `scripts/verify_id_floor.py` (new, throwaway-quality is fine but it
must be readable and self-cleaning).

Against the **local research database only**, on scratch tables that the
script creates and drops:

1. Build two tables standing in for research and serving predictions, with a
   unique `event_id` and a sequence each.
2. Show the failure first: same-id-different-signal rows make an id-keyed
   upsert raise the unique violation this work exists to remove.
3. Apply the floor, re-identify, adopt, then run the id-keyed upsert again
   and show it completes, with the adopted row keeping its serving values.
4. Print each step's counts, and drop the scratch tables in a `finally`.

The fakes missed the `rowcount` defect on 2026-09-19; this is the
different-instrument check that would have caught it.

## Out of scope

The 2,078 pre-ADR-195 contaminated rows, the `nightly`/`weekly` lock, and
any write to the live serving store or to `wivie`.
