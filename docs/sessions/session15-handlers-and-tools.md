# Session 15 — Handlers, tools, and the response validator

Read `DECISIONS.md` (especially ADRs 027, 074, 092, 095, 105, 107, 112), `DESIGN.md` §8 and §10, and `BUILD.md` first. This document says what to build and in what order. Those say why.

Session 15 opens Phase 5. It builds the layer every surface calls, and nothing it produces has a user interface.

---

## 0. Scope

### In scope

1. The `handlers/` layer: one function per tool, database in, typed result out, no HTTP and no formatting.
2. The seven tools per DESIGN §10.1, with closed enums and server-side limits.
3. The response validator: the guarantee that no probability leaves this layer without `n_eff` and an interval attached.
4. ADR 095's `v_positions` rebuild, deferred from Phase 4 and now in scope because Phase 5 is the first thing to read the view.
5. The screener column contract per the ADR drafted alongside this session.

### Out of scope

- MCP server. Session 16.
- Any frontend route. Sessions 17 and 18.
- Any chat layer or system prompt wiring. Session 18.
- `predict()` returning real predictions. Phase 6 owns the model; this session builds the contract and returns `NotFound`.
- Any change to detection, statistics, or the backtest.
- Opening holdout.

### The one-sentence version

Seven pure functions between the database and every future surface, each of which either returns a complete statistical claim or refuses to return one.

### Why handlers come before anything visible

Three consumers will call this layer: the web frontend, the MCP server, and the chat tools. ADR 027 says the MCP server wraps "the same tools." If the contract is settled once here, those three agree by construction. If it is settled three times, they drift, and the drift shows up as one surface reporting a number another suppresses.

---

## 1. The context this session is built in

Phase 4 closed with a negative result. ADR 112 records it: three configurations, 630,592 events, zero cells surviving FDR correction on either split, minimum q-value 0.706.

That is not a footnote to this session. It determines what the layer returns.

| Reality | Consequence for handlers |
|---|---|
| 100 of 224 train cells suppress at `n_eff < 30` | `get_stats` returns `Suppressed` more often than `CellStats` |
| 0 of 124 unsuppressed cells survive FDR | Every `CellStats` that does return carries a q-value near 1 |
| Every edge interval spans zero | An "edge" field returning a point estimate would be actively misleading |

A layer built as though these numbers were positive would produce a screener that looks confident and is not. The response validator in 15.3 exists for exactly this.

---

## 2. Prerequisites

| Item | Check |
|---|---|
| ADR 112 committed | `DECISIONS.md` index and body, `RESULTS.md` kill criteria table updated |
| The screener column ADR committed | Settles what `screen_signals` returns; 15.2's contract depends on it |
| `cell_stats` populated for the live config | `86e91448a65aa40b` |
| `capitalscan/handlers/` exists and is empty | It has since Session 0 |
| `cli.py::backtest` timing fix | `run_job` closed before `run_harness`, so `runs` understated a 4h50m job as 32m55s. Small, and Phase 5 adds no long jobs, but the fix is cheap and the record should mean what it claims |

---

## 3. Model assignment

| Task | Model | Reason |
|---|---|---|
| 15.1 Result types and the handler contract | Sonnet | Every later consumer inherits these shapes. Wrong once, wrong in three places |
| 15.2 The seven handlers | Haiku | Query, map to a type, return. The contract is settled by 15.1 |
| 15.3 Response validator | Sonnet | The guard that decides what may not leave the layer |
| 15.4 `v_positions` rebuild (ADR 095) | Sonnet | SQL literals replaced by config-derived values, and a test that fails against the old view |
| 15.5 Input validation and limits | Haiku | Closed enums, date-window checks, server-side caps |
| 15.6 Tests and documentation | Haiku | Inventory against a settled design |

Order strictly. 15.2 depends on 15.1. 15.3 wraps 15.2.

---

## 4. Task breakdown

### 15.1 Result types and the handler contract

Typed results, one per tool, defined before any handler is written.

Rules:

- Handlers take primitives and return typed objects. No HTTP, no JSON serialization, no display formatting, no `rich` console output. A handler that prints has the wrong shape.
- Every result carries a `meta` block: `config_hash`, `run_id` where applicable, `as_of`, and `staleness_days`. DESIGN §11.2 renders a staleness banner above 2 days, and it cannot do that if the handler does not say.
- `get_stats` returns `CellStats | Suppressed`, a union, not a `CellStats` with null fields. A caller that forgets to check gets a type error, not a silently empty number.
- Any field expressing a probability is accompanied in the same object by `n_eff`, `ci_low`, `ci_high`, and `q_value`. Not optional, not nullable when the probability is present. This is invariant 8 expressed as a type.
- No handler reads holdout. `split` is a closed enum of `train` and `validate`. `holdout` is not a value it accepts, and a test asserts passing it raises rather than returning empty.

The last rule deserves emphasis. `test_holdout_firewall.py` guards the database. Nothing yet guards a serving layer that could ask for it, and Phase 5 is the first layer that could.

Acceptance:

- Seven result types plus `Suppressed`, all frozen dataclasses or equivalent.
- A structural test asserting no result type has a probability field without the four companions. Enforced by reading annotations, not by convention.
- A test asserting `split="holdout"` raises for every handler that takes a split.
- `meta.staleness_days` computed from the most recent bar, not from the current date alone, so a stale database reports staleness rather than pretending.
- No handler module imports `rich`, `fastapi`, or anything HTTP. Asserted by an import test.

### 15.2 The seven handlers

Per DESIGN §10.1:

```
screen_signals(date, signal_types, universe, dd_bucket, min_strength, limit) -> ScreenResult
get_stats(signal_type, target_pct, dd_bucket, signal_strength, entry_kind, split, ticker) -> CellStats | Suppressed
get_indicators(ticker, start, end, fields) -> IndicatorSeries
get_events(ticker, start, end, signal_types, cluster_head_only, limit) -> EventList
predict(ticker, as_of) -> Prediction | NotFound
explain_signal(ticker, date) -> Explanation
get_universe(as_of) -> UniverseResult
```

Rules:

- `screen_signals` reads `v_screen`, which already carries ADR 100's `config_hash` predicate, ADR 105's `arm = 'signal'` predicate, and ADR 107's pooled-over-`signal_strength` selection. It does not rebuild that logic.
- The screener column contract from the ADR governs what `ScreenResult` rows contain. Default is the event feed; the statistical fields sit behind an explicit flag.
- `get_stats` returns `Suppressed` with the stored `suppress_reason` whenever `cell_stats.suppressed` is true. It never substitutes a broader cell. DESIGN §11.2's system prompt says the chat layer must not do this silently; the handler makes it impossible rather than discouraged.
- `predict` returns `NotFound` for every input. The model does not exist, ADR 093 is Provisional, and ADR 112 made Phase 6 conditional. Building the contract now is right; faking a return is not.
- `explain_signal` returns features and the cell. The SHAP top-5 in DESIGN §10.1 is a Phase 6 field and is absent, not empty.
- `get_events` defaults `cluster_head_only=True`, matching every statistics query in Phase 4.

Acceptance:

- Each handler tested against a seeded fixture database with a known answer.
- `get_stats` returns `Suppressed` for a suppressed cell, verified against a real suppressed cell from the live config rather than a synthetic one.
- `screen_signals` on a date with no events returns an empty result with populated `meta`, not an error. DESIGN §11.2 says the empty state matters more than usual because most days nothing fires.
- A test asserting `get_stats` never returns a broader cell when the requested one is suppressed.
- `predict` returns `NotFound` for every input, with a test that will fail when Phase 6 changes it, so the change is deliberate.

### 15.3 Response validator

The layer's guarantee, and the reason it is a layer.

Rules:

- Every response passes through the validator before returning. A probability without `n_eff` and an interval does not leave.
- A q-value above `StatsParams.fdr_alpha` is not suppressed, but it is flagged in the result. The reader learns the cell did not survive correction from the object, not from remembering to check.
- The validator refuses, it does not repair. A response failing validation raises. Silently filling a missing interval would be worse than the missing interval.
- Validation is on by default and cannot be disabled per-call. If a debugging escape hatch is needed, it is a module-level flag with a test asserting it is off.

Acceptance:

- A handcrafted response with a probability and no interval raises.
- A response with `q_value > fdr_alpha` returns and carries the flag.
- Every one of the seven handlers passes its output through the validator, asserted structurally rather than by inspection.
- A test constructing each failure mode and confirming the raise, one per rule.
- The validator's own coverage is complete. A guard with untested branches is not a guard.

### 15.4 `v_positions` rebuild — ADR 095

Deferred from Phase 4 by ADR 095's own reasoning, because Phase 4 never read the view. Phase 5 does.

The view currently hardcodes:

```sql
(s.k_full >= (80)::numeric) AS exit_signal_stoch,
((CURRENT_DATE - p.entry_date) >= 5) AS exit_signal_timeout
```

Those are `ExitParams.exit_stoch_threshold` and `ExitParams.max_hold_days` as literals, which ADR 092 bans. The view is a second exit implementation that can disagree with the first.

Also: `exit_signal_mid_band` is exposed unconditionally though `exit_on_mid_band` defaults `False` per ADR 046, so the view surfaces a signal the policy has switched off.

Rules:

- Thresholds come from config. Either generate the view DDL from `ExitParams` in the migration, or read them from a settings row. Pick one and say why.
- ADR 110 moved the trigger to `k_fast`. The view reads `k_full`. Whichever the exit policy uses, the view must use the same one, and the test must assert they agree rather than assuming.
- `exit_signal_mid_band` is gated on `exit_on_mid_band`.

Acceptance:

- A test that fails against the current view and passes against the rebuilt one. A test asserting the view's shape passes both ways and proves nothing.
- Changing `exit_stoch_threshold` in config and confirming the view's output moves.
- The view and `core/exits.py` agree on the same fixture, asserted by comparing outputs on a seeded position set.
- Migration applies and reverses cleanly. `db/schema.sql` regenerated and committed, with `test_schema_drift.py` confirmed to have run rather than skipped.

### 15.5 Input validation and limits

Per ADR 074.

Rules:

- Closed enums for `signal_types`, `universe`, `dd_bucket`, `entry_kind`, `split`. An unrecognized value raises; it does not fall through to an empty result.
- Dates validate against the ingested window. A date before the first bar or after the last raises with the window in the message.
- `limit` capped server-side at 200 regardless of what the caller passes. A caller passing 10,000 gets 200, not an error and not 10,000.
- Enum values derive from `SignalType` and `StatsParams`, never duplicated as string literals in the handler layer. A new signal type should not require editing two places.

Acceptance:

- Each enum tested with a valid value, an invalid value, and a near-miss such as wrong case.
- A date outside the window raises and the message names the window.
- `limit=10_000` returns 200 rows.
- A test asserting the enums match their source of truth, which fails if a signal type is added to `SignalType` and not to the handler layer.

### 15.6 Tests and documentation

Acceptance:

- `TESTS.md` gains the Session 15 inventory.
- `DESIGN.md` §10.1 updated where the built contract differs from the spec, particularly `predict` and `explain_signal`'s SHAP field.
- `BUILD.md` lists Session 15 and its gate outcome.
- `RESULTS.md` gains a Session 15 note. It has no measurements, and the note should say so rather than being omitted, because an absent session reads as an incomplete one.

---

## 5. Session gate

1. Seven handlers, each returning a typed result, none importing HTTP or display libraries.
2. No probability leaves the layer without `n_eff`, an interval, and a q-value. Enforced by the validator and by a structural test on the types.
3. `split="holdout"` raises on every handler that takes a split.
4. `get_stats` returns `Suppressed` for suppressed cells and never substitutes a broader cell.
5. `predict` returns `NotFound` for every input, with a test that fails when Phase 6 changes it.
6. `v_positions` reads its thresholds from config, and a test fails against the pre-rebuild view.
7. Closed enums derive from their source of truth, and `limit` caps at 200.
8. Empty results carry populated `meta` rather than raising.
9. `test_holdout_firewall.py` and `test_schema_drift.py` both pass, the latter having run rather than skipped.
10. Determinism: two calls with identical arguments against an unchanged database return identical results.

Item 2 is the one this session exists for. Everything else is plumbing.

---

## 6. What will be tempting and should not be done

**Filling a missing interval rather than raising.** The validator refuses, it does not repair. A response that silently acquires a plausible interval is worse than one that fails loudly, because the first ships.

**Making `predict` return something.** The model does not exist. A stub returning a plausible-looking distribution will be forgotten and then trusted.

**Substituting a broader cell when the requested one suppresses.** It feels helpful and it is the exact behaviour DESIGN §11.2's system prompt forbids. The handler should make it impossible rather than ask the chat layer not to.

**Presenting the statistical fields as the screener's default.** ADR 112 measured zero cells surviving correction. A default view with four always-empty columns trains the reader to ignore the row. The event feed is the default; the statistics sit behind a deliberate action.

**Adding a `split="holdout"` value because it seems symmetric.** It is not symmetric. Holdout is evaluated once, at the end, per ADR 019 and ADR 033.

---

## 7. File layout

The repo has grown to 218 formatted files and `docs/sessions/` now holds eleven entries at two nesting levels. Phase 5 adds three new packages. Settling the layout now costs nothing; settling it after four sessions costs a rename across every import.

```
capitalscan/
  handlers/          types.py validate.py screen.py stats.py
                     indicators.py events.py predict.py explain.py universe.py
  mcp/               (Session 16)
  web/               (Sessions 17-18, does not yet exist)
```

One module per tool, plus `types.py` for the result shapes and `validate.py` for the validator. Grouping all seven into one `handlers.py` would work today and be unpleasant by Session 18.

For `docs/sessions/`, the convention has drifted: `session10.md`, `session11-statistical-foundations.md`, and a `session-9-backtest/` directory. Renaming historical files breaks every reference in `BUILD.md` and `RESULTS.md`, so the cheap fix is a `docs/sessions/README.md` listing each session, its file, and its phase. One page, no renames, and it is the first thing a newcomer opens.

---

## 8. What Session 16 needs from this one

| Session 16 needs | From |
|---|---|
| Seven handlers with stable signatures | 15.2 |
| Typed results serializable to JSON | 15.1 |
| The validator, callable independently | 15.3 |
| Closed enums as the source for MCP tool schemas | 15.5 |

ADR 027 requires the MCP server to wrap "the same tools." Session 16 should add no query logic. If it needs to, the handler contract was wrong and the fix belongs here rather than there.

---

## 9. Rollback

- `handlers/` is new. Deleting the package reverses the session.
- One view rebuilt, reversible by migration.
- No table modified, no data written.
- Nothing outside `v_positions` is read by any existing consumer.
