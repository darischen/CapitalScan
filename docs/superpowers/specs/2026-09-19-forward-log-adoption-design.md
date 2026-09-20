# Research adopts the Pi's predictions — design

**Date:** 2026-09-19. **Status:** approved in chat, not built.
**Decides:** option 3 of the 2026-09-17 open item, and the sync failure that
has blocked serving since 2026-09-17.

## The problem, measured

`cscan sync` has failed every night since 2026-09-17:

```
UniqueViolation: duplicate key value violates unique constraint
"predictions_event_id"  DETAIL: Key (event_id)=(83652402) already exists.
```

Serving has therefore received no research data since 2026-09-16. The Pi's
own live path is unaffected, so the site looks healthy.

**Cause.** ADR 191 keys the `predictions` sync on `id`, on the assumption
that serving only ever receives research's ids. Since ADR 158 the Pi writes
serving directly, and `cscan predict --serving` mints ids from serving's own
`predictions_id_seq` — the same numeric range research uses. Two stores now
allocate from one range.

Measured across both stores on 2026-09-19:

| | value |
|---|---:|
| research predictions / max id | 36,203 / 191,860 |
| serving predictions / max id | 35,407 / **224,862** |
| rows sharing an id, same signal | 35,292 |
| rows sharing an id, **different signal** | **3** |
| serving-only predictions (never in research) | **100** |

The three are the live hazard: research's JPM, MPC and ZS rows carry the ids
serving gave HPE, ECHO and VLO on 2026-09-17. A sync keyed on `id` would
have overwritten serving's row with a different signal's numbers. The unique
`event_id` index is the only reason this surfaced as a failure rather than
as silently wrong rows on the site.

The 100 serving-only rows are predictions **a reader actually saw** — scored
intraday by the Pi at fire time, and never recorded in research.

## What this fixes, and what it does not

Fixes: the sync failure, the id collision that caused it, and the gap
between what the forward log scores and what the site displayed.

Not in scope: the 2,078 rows contaminated before ADR 195 (they stay as they
are, and are not rewritten), and the missing lock between `nightly` and
`weekly`.

## Decisions

**Identity: split the id ranges** (chosen over natural-key identity and over
an id-mapping table). Serving mints at or above a floor; research stays
below it. ADR 191's property — an id names the same row in both stores —
holds again, and `outcomes.prediction_id` keeps meaning one thing.

**Adoption scope: all 100 serving-only rows**, including the 79 from
2026-09-10.

### The floor constant

`ServingParams.serving_id_floor = 1_000_000_000`, in `core/config.py`.

`ServingParams` is standalone and **not** part of `Config`, so nothing here
moves `config_hash` — the same reasoning its docstring already records for
`history_years` and `breadth_rank_floor`. Invariant 9 is satisfied: the
number lives in `core/config.py` and `jobs/sync.py` holds no literal.

**Enforced in code, not in a migration.** `_reset_sequences` raises
serving's `predictions_id_seq` to the floor when it is below it, so every
sync and every pull repairs it, including after a Pi reflash. This follows
the project's existing split: schema in migrations, server state (WAL,
autovacuum, sequences) applied by the code that depends on it.

## Components

### 1. `_reset_sequences` gains a floor, per store

- **Serving:** `predictions_id_seq` is set to `max(floor, max(id))`.
- **Research:** the sequence for `predictions` is set from
  `max(id) WHERE id < floor`. Without this clause an adopted billion-range
  id would push research's own allocation into the Pi's range and recreate
  the bug one day later.
- Every other table keeps today's behaviour.

### 2. `pull_live_records` gains a `predictions` step

Runs serving → research, alongside the three durable tables it already
pulls.

- **Selects** serving predictions with `id >= serving_id_floor`. After the
  repair below, that is exactly the set of Pi-born rows.
- **Remaps `event_id`** from serving's id space into research's, through the
  events natural key `(config_hash, ticker, signal_date, signal_type,
  entry_kind)`. This is ADR 191's `Remap` run in the opposite direction, and
  `_apply_remap` already implements it generically.
- **A row whose event research does not hold** (a provisional poller event
  the ADR 150 sweep removed) is adopted with `event_id = NULL` and counted
  in the report. It stays a record of what a reader saw; the forward log
  simply leaves it unresolved.
- **Writes with `copy_upsert` keyed on `("id",)`**, so a repeated pull is a
  no-op. Keying on `event_id` would be wrong here: NULLs are distinct in a
  unique index, so unmapped rows would duplicate on every pull.

### 3. Nightly ordering

`pull_live_records` moves **ahead of `predict`** in `cli.nightly`. Research's
write is insert-only since ADR 195, so once the Pi's row is present, research
keeps it and does not write its own for that event. That is the whole point:
the forward log then scores the number the reader saw.

The pull has no dependency on any earlier nightly step, so moving it is safe.
Its current position (just before the outbound sync) was chosen so a night's
records reach research whatever the sync does; that property is preserved,
because the pull still runs before the sync.

### 4. One-time repair, both stores

A script under `scripts/`, run by hand once, inside a transaction per store,
with a dry-run mode that prints what it would do:

1. On serving, assign each of the 100 serving-born rows a new id at or above
   the floor, updating any `outcomes.prediction_id` that references it.
2. Raise serving's `predictions_id_seq` above the highest assigned id.
3. Pull those rows into research through the path in §2.
4. Re-check: no id appears in both stores describing different signals.

Step 1 is what clears the three mis-matched ids, because the ids in question
belong to Pi rows that move out of research's range.

## Data flow, after

```
Pi intraday:   predict --serving  -> serving.predictions   id >= 1e9
nightly:       pull_live_records  -> research.predictions  (same id, event_id remapped)
               predict            -> insert-only; skips events already adopted
               outcomes           -> resolves what the reader saw
               sync               -> research -> serving by id; adopted rows no-op
```

## Error handling

- The pull is reported and non-fatal, like the rest of nightly's live-record
  pull: research already holds everything the night computed.
- An unmappable `event_id` is a NULL and a count, never a guessed id. ADR 191
  established that a kept foreign id is confidently wrong, which is worse.
- The floor is enforced on every run rather than assumed, so a restored or
  reflashed serving store cannot silently start minting into research's
  range.

## Testing

Unit, no real database:

1. `_reset_sequences` on serving raises a below-floor sequence to the floor,
   and leaves an above-floor one alone.
2. The research reset ignores ids at or above the floor.
3. The predictions pull selects only `id >= floor`.
4. `event_id` is remapped through the events natural key; an unmatched key
   yields NULL, not the source id.
5. The pull writes keyed on `id` (so a second pull inserts nothing).
6. `nightly` calls `pull_live_records` **before** `run_predict`.
7. An adopted row survives a following `run_predict` unchanged (ADR 195's
   insert-only, exercised against the adopted id).

Against real Postgres, on scratch tables — the fakes already missed the
`rowcount` defect once:

8. Two stores, a floor, an adoption, then a sync: no unique violation, and
   the adopted row keeps its Pi values.

Then, on the live pair: the repair script's dry run, the repair, and a
`cscan sync` that completes.

## Rollout

1. Merge the code with tests green.
2. Pull `wivie` and the Pi.
3. Run the repair script's dry run, read it, then run it.
4. Run `cscan sync` by hand and confirm it completes.
5. Confirm the site still renders rows, and that `serving_config` still names
   `f183b0f5209a4677`.
6. Watch the next nightly: the pull reports adopted rows, `predict` reports
   `rows_kept`, `outcomes` resolves, `sync` succeeds.

## Follow-ups, not this change

- An ADR recording the split id range, once this is built.
- The `nightly` / `weekly` lock.
- Whether serving's other Pi-written tables share this hazard. `events`
  syncs on its natural tuple rather than on `id`, so it does not; the pull
  of `signal_reports` keys on `id` and is worth the same check, and it is
  the table whose sequence drift already failed a poll on 2026-08-31.
