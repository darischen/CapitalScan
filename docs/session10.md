# Session 10 — Forward path store and derived label layer

Read `DECISIONS.md`, `DESIGN.md`, and `BUILD.md` first. This document says what to build and in what order. Those say why.

Session 10 sits between the Phase 3 gate and Phase 4. It is a data-layer session. No modeling work happens here.

---

## 0. Scope

### In scope

A new table holding the forward price path of every event, and a derived label layer computed from it.

The path store keeps, for each event, the per-day forward outcome across the full evaluation window: the extreme favorable move, the extreme adverse move, and the terminal mark. Direction-neutral, so both tails exist for every event regardless of signal type.

The derived layer recomputes the labels session 9 already produces, then adds three new families:

1. Terminal return distribution inputs at multiple horizons inside the window.
2. First-touch day for every threshold, favorable and adverse.
3. Giveback, meaning the gap between the peak reached and the terminal mark.

### Out of scope

- Any change to indicator computation, signal definitions, exit rules, or the backtest engine.
- Any change to how events are detected or how entry prices are set.
- Model training of any kind. The eleven heads remain Phase 6.
- Anything touching the serving store. This session is research-store only.

### Non-goal worth stating explicitly

This session does not improve any statistic. It changes where labels come from so future label changes cost a query instead of a full re-derivation from price history. Success looks like identical numbers from a different source.

---

## 1. Prerequisite

The Phase 3 gate must pass before this session opens. Session 9 still owns that gate. Session 10 does not replace it.

Confirm before starting:

- Exit invariants hold across the property-generated case set.
- Ambiguity rate sits under the threshold, or escalation is implemented.
- Event rate passes BUILD §9a's three checks (structural invariants,
  component rates against independent predictions, the 10-25% headline
  band). §9a replaced the old "within tolerance of the analytical estimate"
  wording — that estimate named neither side nor price field, and its ~4%
  figure matched a close-based reading ADR 005 rejects.
- Two runs with identical config produce identical output ignoring the run identifier.
- All validation harness checks pass.

If any check fails, stop. Building a label layer on top of an unstable event set wastes the reconciliation work in task 10.4.

---

## 2. Model assignment

| Task | Model | Reason |
|---|---|---|
| 10.1 Schema and migration | Haiku | Mechanical DDL against a specified shape |
| 10.2 Path extraction and backfill | Sonnet | Window alignment and boundary handling. Errors here propagate silently |
| 10.3 Derived layer, existing labels only | Sonnet | Must reproduce session 9 semantics exactly |
| 10.4 Reconciliation | Sonnet | The correctness gate for the whole session |
| 10.5 New label families | Haiku | Additive work on a proven layer |
| 10.6 Live path capture | Sonnet | Touches the write path of an existing pipeline |
| 10.7 Tests and documentation | Haiku | Inventory work against a settled design |

No Opus needed. The session is deliberately structured so the one high-risk step, reconciliation, is isolated and independently verifiable rather than tangled through every task.

---

## 3. Task breakdown

### 10.1 Schema and migration

Add the path table to the research schema. One row per event per forward day. Foreign key to the event table, composite primary key on event plus day offset, cascade delete.

Add a nullable column to the event table recording whether the forward window completed, and how many days of it exist. Events near the end of available price history have short windows and must be identifiable.

Acceptance:

- Migration applies cleanly to a fresh database and to a copy of the current research database.
- Migration reverses cleanly with no orphaned objects.
- Deleting an event removes its path rows.
- Schema documentation updated in `DESIGN.md`.

### 10.2 Path extraction and backfill

Populate the path table for every existing event from the price history already in the research store.

Rules the implementation must follow:

- Day offsets count trading days, never calendar days. The price history defines the trading calendar. Do not compute it independently.
- The entry price definition must match whatever session 9 uses. Read the existing code and reuse it rather than reimplementing.
- Use the same price adjustment convention as the rest of the pipeline. A mismatch here produces plausible but wrong returns.
- Favorable and adverse extremes come from intraday extremes. The terminal mark comes from the close.
- Events without a complete forward window get partial rows plus the completeness flag. Never pad, never silently truncate the event.

Acceptance:

- Row count equals the sum of available forward days across all events, verified independently of the insert logic.
- Every event has either a full window or a correctly set completeness flag and matching row count.
- No event has a gap in its day offsets.
- Spot check five events by hand against raw price data, including one near the end of history and one spanning a holiday week.
- Backfill is idempotent. Running twice produces the same table state.
- Backfill completes inside the time budget and reports progress.

### 10.3 Derived layer, existing labels only

Compute the labels session 9 already produces, this time from the path table, and write them to the event table.

Do not add new labels in this task. The point is a controlled comparison.

Acceptance:

- The layer is a single documented entry point, re-runnable at will.
- Re-running produces byte-identical results.
- The layer reads only the path table and event metadata, never price history.
- Runtime stays under a small number of seconds for the full event set.

### 10.4 Reconciliation — session gate

Compare the labels from 10.3 against the labels session 9 produced. This is the task the session exists to pass.

Acceptance:

- Every existing label matches on every event, with zero exceptions.
- Any mismatch is investigated and explained before proceeding. Do not adjust the new layer to match the old one without understanding the cause. The old one could be the wrong one.
- The comparison itself is a committed, re-runnable check, not a one-off script.
- Investigation outcomes recorded in `RESULTS.md`, including the case where the old labels were wrong.

Known mismatch causes worth checking first:

- Trading day versus calendar day offsets.
- Inclusive versus exclusive threshold comparison at the boundary.
- Intraday extreme versus close used for peak detection.
- Different entry price convention.
- **Different windows for different labels.** MFE/MAE covers
  `[t+1, exit_idx]`; reachability covers the full `[t+1, t+5]` regardless of
  when the exit fired (DESIGN §5.6). Reconciliation using one window for
  both will mismatch on every early-exit event.
- Look-ahead in the old path, where a label used information unavailable at detection time.

Do not start 10.5 until this task passes clean.

### 10.5 New label families

The requirement driving this task grew since it was first scoped: a return
*distribution* at each of 1, 2, 3, 5, and 10 days, not just "P(reach X%
within 5 days)." Modeling the peak's timing, not only its occurrence, turns
the label space from a vector into a grid:

```
thresholds x horizons x directions  =  4 x 5 x 2  =  40 touched flags
                                                  +  40 first-touch days
                                                  =  80 columns
```

One new threshold adds ~10 columns to that grid; one new horizon adds ~16.
Materializing the full grid as event-table columns is not something anyone
can evolve — every future config change becomes a migration.

**The path table (10.1/10.2) is the source of truth. The label families
below are queries against it, not materialized columns.** Every cell in the
grid is derivable from the per-day favorable/adverse/terminal rows the path
table already stores:

```
reached r by day d  ->  max(favorable over days 1..d) >= r
first touch of r    ->  min(d where favorable_d >= r), null if never
return at day d     ->  terminal mark at day d
```

Materialize only what serving needs hot. Session 9's existing event-table
columns (`touched_2pct`/`day_touched_2pct` and the `2pct`/`3pct`/`5pct`/
`10pct` family, `fwd_ret_1d`/`2d`/`3d`/`5d`/`10d`) stay in place as a cache
and for the serving views — they do not move to query-only. New thresholds
and new horizons beyond those already materialized are computed from the
path table on demand.

**`events` already has `fwd_ret_1d`, `fwd_ret_2d`, `fwd_ret_3d`,
`fwd_ret_5d`, `fwd_ret_10d`** — the terminal return at each of those exact
horizons, per event, populated by Session 9. The "terminal return
distribution inputs at multiple horizons" family in §0 is therefore already
half-built; this task's job for that family is reconciling those five
columns against the path table's terminal mark (10.4's job, not a new
computation) and scoping any additional horizon to the path-table query
form above rather than a sixth materialized column.

**A probability distribution is an aggregate across events, not a per-event
label.** What this task stores, per event, is a value: a terminal return, a
touched flag, a first-touch day. The distribution — the shape of those
values across the population of events in a cell — is what the Phase 4
statistics layer computes. Nothing in this task produces a distribution
directly; it produces the point values Phase 4 aggregates.

**Column naming.** Reconciliation and any newly materialized column must
reuse `capitalscan/research/enrich.py`'s `_pct_suffix`, not re-derive
threshold-to-column-name logic — it exists specifically to handle the
`0.10 * 100 = 10.000000000000002` float trap that a naive string-format
would reintroduce.

**Giveback**, the gap between the peak reached and the terminal mark, is a
third view of the same path shape Session 9's exit metrics already capture,
not an independent computation:

- `capture_ratio = R_exit / MFE`, null when `MFE <= 0` — already computed
  and stored on `events` by Session 9.
- `time_to_mfe` — already stored.
- Giveback itself (peak minus terminal) is derivable from `mfe` and the
  terminal mark the same way `capture_ratio` is; define it against these
  existing columns rather than recomputing the peak independently.

**MFE is deliberately unclamped (ADR 089).** A position that never traded
above entry has negative MFE, and that is load-bearing — giveback and
capture-ratio computations must not clamp it to zero along the way.

Thresholds and horizons come from configuration, not from hardcoded values. Adding a threshold later should require a config edit and a re-run of the query layer, nothing else.

First-touch day is null when the threshold goes untouched inside the window. Null means untouched, never zero.

Acceptance:

- Every new label is derivable by hand from the path rows of a sampled event, verified on at least ten events covering touched, untouched, and partial-window cases.
- Monotonicity holds where required. A tighter threshold is touched no later than a looser one in the same direction.
- Giveback is non-negative for favorable peaks by construction, and any violation raises rather than writes.
- Adding one new threshold via config and re-running produces the expected additional labels with no code change.
- Null handling verified end to end, including in whatever consumes these labels downstream.
- Column names for anything newly materialized match `_pct_suffix`'s output exactly, not a re-derivation.

**Flag, do not build here:** expanding terminal quantiles from `R_5` only to
all five horizons changes DESIGN §7.4's eleven model heads to roughly three
times that count. That is a modeling-surface change, not a quiet label-layer
edit, and it warrants its own ADR before Phase 6 touches it. This document
does not write that ADR.

### 10.6 Live path capture

Extend the event write path so newly detected events accumulate their forward path as trading days pass, rather than requiring a periodic full backfill.

Acceptance:

- A newly created event gets path rows appended on the correct schedule.
- An event created today and observed over the full window produces a path identical to what the backfill would produce for the same event.
- Restart safety holds. Interrupting mid-update leaves no partial or duplicate rows.
- The derived layer picks up newly completed windows without manual intervention.

### 10.7 Tests and documentation

Acceptance:

- Test inventory added to `TESTS.md` covering path extraction boundaries, label derivation, reconciliation, and live capture.
- Property-based tests for the invariants: monotonicity across thresholds, non-negative giveback, no day-offset gaps, null semantics.
- A new ADR recording the source-of-truth split, superseding the label-definition portion of the session 9 decision rather than editing it in place.
- `BUILD.md` updated to list session 10 and restate where the Phase 4 boundary now falls.

---

## 4. Session gate

Session 10 passes when all of the following hold:

1. Every event has a complete and correct forward path, or a correctly flagged partial one.
2. Reconciliation against session 9 labels passes with zero unexplained differences.
3. All new label families verified by hand on a sample covering the edge cases.
4. Adding a threshold requires only a config change and a re-run.
5. Backfill and derived layer are both idempotent and deterministic.
6. Live capture verified on at least one real event through a full window.
7. Documentation and ADR committed.

Failing item 2 blocks the session regardless of everything else.

---

## 5. Keeping this under the session 3 and session 9 time cost

Both prior sessions ran long because implementation and bug discovery interleaved. Three structural choices here prevent a repeat.

**Reconciliation is a hard gate, not a final check.** Task 10.4 sits before the new labels. Bugs surface against a known-correct reference instead of against numbers nobody has seen. Debugging a mismatch on labels you already trust is bounded work. Debugging a wrong number on a brand new label is not.

**Nothing in this session changes behavior.** Tasks 10.1 through 10.4 must produce identical outputs to today. Any difference is a bug by definition, which removes the hardest question from the debugging loop.

**Reversibility is cheap.** The old label columns stay in place until the session gate passes. Rolling back means pointing consumers at the old columns and dropping the new table.

Order the work strictly. Do not begin 10.5 before 10.4 passes, and do not begin 10.6 before 10.5 passes. Parallelizing these is where the time goes.

---

## 6. Deferred cleanup — not part of this session

The old label columns stay in place through session 10. Two sets of the same labels coexist during reconciliation, which is the point.

Removing the superseded set is a separate task, run after Phase 4 has executed once against the new layer and produced expected results.

The cleanup task, when it happens:

- Point every consumer at the new columns and confirm nothing still reads the old ones. Grep the codebase and check the view definitions.
- Drop the superseded columns in a reversible migration.
- Rename the new columns to the original names if the shadow naming convention was used, in the same migration.
- Re-run the full statistics layer and confirm output is unchanged.

Do not fold this into session 10. Dropping columns while the reconciliation evidence still matters removes the ability to answer questions about a mismatch found later.

Note the redundancy is not the thing being removed. Label columns on the event table are deliberately redundant with the path table, and that redundancy is the precompute. What gets removed is the second copy written by the old code path.

---

## 7. Rollback

- Old label columns remain untouched through the entire session.
- Nothing downstream reads the new labels until the gate passes.
- Reversal is a migration down plus a consumer config change.
- Drop the old columns only after Phase 4 has run once against the new layer and produced expected results.
