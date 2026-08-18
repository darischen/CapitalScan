# Sessions 16 through 18 — completing Phase 5

Companion to `session15-handlers-and-tools.md`. Read that first: every session here calls the layer it builds.

Split these into three files under `docs/sessions/` when you start each one, or keep as one. The session boundaries are real; the file boundary is convenience.

**Sessions 17 and 18 are written to be adjusted.** They render what Session 15 returns, and the handler contract will move once it meets real data. Their scope, gates, and constraints hold. Their task-level detail is a starting position, and a planner opening Session 17 should re-read the shipped `handlers/types.py` before trusting the field lists here.

---

# Session 16 — MCP server

## 0. Scope

### In scope

1. An MCP server over streamable HTTP wrapping the seven handlers from Session 15.
2. Bearer token auth, read-only scope, per-token rate limiting.
3. Tool schemas generated from the same closed enums the handlers validate against.
4. Deployment configuration and a documented local-client setup path.

### Out of scope

- Any new query logic. See the rule below.
- Any frontend. Sessions 17 and 18.
- Write operations of any kind.
- The chat layer. Session 18 owns the system prompt.

### The rule this session is built around

ADR 027: the MCP server wraps **the same tools**. Session 16 adds no query logic, no aggregation, no filtering the handlers do not already do. If it needs to, the handler contract was wrong and the fix belongs in Session 15.

A test should enforce this rather than a convention: no module under `mcp/` may import `sqlalchemy`, `db_io`, or construct SQL. The server calls handlers and serializes.

## 1. Prerequisites

| Item | Check |
|---|---|
| Session 15 gate passed | Seven handlers, validator, typed results |
| `MCP_BEARER_TOKEN` in `.env.example` | Already present |
| `capitalscan/mcp/` exists and is empty | It has since Session 0 |

## 2. Model assignment

| Task | Model | Reason |
|---|---|---|
| 16.1 Server scaffold and tool registration | Sonnet | Protocol surface, and the schema-generation contract |
| 16.2 Auth and rate limiting | Sonnet | An unauthenticated endpoint is an open database proxy |
| 16.3 Serialization and error mapping | Haiku | Typed results to JSON, exceptions to protocol errors |
| 16.4 Deployment and client setup | Haiku | Configuration and documentation |
| 16.5 Tests and documentation | Haiku | Inventory against a settled design |

## 3. Task breakdown

### 16.1 Server scaffold and tool registration

Rules:

- Tool schemas are **generated** from the handler signatures and the closed enums, not hand-written. A hand-written schema drifts from the handler it describes, and the drift surfaces as a caller passing a value the schema accepts and the handler rejects.
- Seven tools, matching Session 15 exactly. No eighth convenience tool.
- `limit` caps server-side at 200 per ADR 074, enforced in the handler and again here, because the MCP layer is reachable by clients that did not read the schema.
- The `Suppressed` union member serializes as a distinct shape, not as a `CellStats` with nulls. A client must be able to distinguish "suppressed" from "zero."

Acceptance:

- Seven tools registered, schemas generated, and a test asserting every schema enum matches its `SignalType` or `StatsParams` source.
- A test asserting no `mcp/` module imports `sqlalchemy` or `db_io`.
- Adding a value to `SignalType` changes the generated schema without any edit under `mcp/`.
- `Suppressed` round-trips as its own shape.

### 16.2 Auth and rate limiting

ADR 027: "Bearer token minimum, scoped read-only, rate limited per token. An unauthenticated MCP endpoint on the public internet is an open database proxy."

Rules:

- Bearer token required on every request including tool discovery. An unauthenticated client learns nothing, not even the tool list.
- Rate limit per token, not per IP.
- Read-only enforced at the connection level, not by trusting the handlers. Use a database role without write grants.
- Token comparison is constant-time.
- A missing or malformed token returns the same error as a wrong one, with no timing or message difference.

Acceptance:

- No token, malformed token, and wrong token all rejected identically.
- Rate limit triggers at the configured threshold and resets, tested against a fake clock rather than by sleeping.
- The read-only role cannot write, verified by attempting an insert through the same connection and asserting it fails.
- A test asserting the token is never logged, never in an error message, and never in a traceback.

### 16.3 Serialization and error mapping

Rules:

- Handler exceptions map to protocol errors with useful messages. An invalid enum says which values are valid; an out-of-window date names the window.
- No internal detail leaks: no SQL, no stack traces, no table names, no connection strings.
- `meta` survives serialization intact, including `staleness_days`. A client cannot render a staleness banner it never receives.
- Decimal precision is explicit. `numeric(12,6)` through JSON floats loses digits silently, and a q-value of 0.849 versus 0.8492 is not a distinction anyone should lose to serialization.

Acceptance:

- Each handler exception type maps to a distinct protocol error, tested individually.
- A test asserting no serialized error contains `SELECT`, a table name, or a file path.
- Round-trip precision on `numeric` fields verified to the stored decimal places.

### 16.4 Deployment and client setup

Acceptance:

- Local Claude Desktop and Claude Code setup documented and verified working end to end.
- Token generation and rotation documented.
- The server refuses to start without `MCP_BEARER_TOKEN` set, rather than starting unauthenticated.
- A documented health endpoint that requires auth and returns no data.

### 16.5 Tests and documentation

Acceptance:

- `TESTS.md` gains the Session 16 inventory.
- `DESIGN.md` §10 updated where the built server differs from the spec.
- `BUILD.md` lists Session 16 and its gate outcome.

## 4. Session gate

1. Seven tools registered with generated schemas matching the handler enums.
2. No `mcp/` module imports `sqlalchemy` or `db_io`.
3. Unauthenticated requests rejected, including discovery, with identical responses across all failure modes.
4. Rate limiting triggers and resets on a fake clock.
5. The connection role cannot write, proven by a failed insert.
6. No serialized response or error contains SQL, a table name, a file path, or the token.
7. `Suppressed` distinguishable from `CellStats` on the wire.
8. `meta.staleness_days` survives serialization.
9. Local client setup verified end to end.
10. Determinism: identical requests return identical responses against an unchanged database.

Items 3 and 5 are the ones that matter. The rest is plumbing; those two are the difference between a server and an open database.

## 5. What will be tempting and should not be done

**Adding a tool that combines two handlers.** It saves a round trip and it puts query logic in the wrong layer. If two handlers are always called together, that is a Session 15 change.

**Hand-writing schemas because generation is fiddly.** The generated schema is the only thing keeping the wire contract and the handler contract in agreement.

**Allowing unauthenticated tool discovery.** The tool list describes the database's shape. That is information.

**Skipping the read-only role because the handlers only read.** Defense in depth is the whole point. A future handler bug should not be able to write.

---

# Session 17 — Screener and ticker page

**Adjust before building.** Re-read `handlers/types.py` as shipped. The field lists below follow Session 15's plan, and the plan will have moved.

## 0. Scope

### In scope

1. `/` — today's screener, the default landing route.
2. `/ticker/[sym]` — chart, current state, event history.
3. The `web/` package, which does not yet exist.
4. Empty and stale states, which on this data are the common case rather than the exception.

### Out of scope

- `/research` and `/chat`. Session 18.
- `/positions`. Phase 2 built it; this session does not touch it.
- `/forward`. Phase 6.
- Any query logic. Routes select from the views (ADR 118).

## 1. The constraint that shapes both routes

ADR 112: zero cells survive FDR correction. 100 of 224 train cells suppress.

Per the screener-column ADR, the default view is the **event feed**: what fired, on what ticker, in what state. The statistical fields sit behind a deliberate action.

This is not pessimism in the UI. It is that four columns empty on every row, every day, teaches a reader to skip the row, and the row is the part that carries information.

Where statistics do render, they render whole: the hit rate, the baseline it is measured against, `n_eff`, the interval, and the q-value. DESIGN §11.2's rule that edge renders as a bar with CI width rather than a number holds, and on this data most bars will span zero. That is the correct rendering of the measurement.

## 2. Model assignment

| Task | Model | Reason |
|---|---|---|
| 17.1 `web/` scaffold and handler wiring | Sonnet | The boundary between route and handler, set once |
| 17.2 Screener route | Haiku | Table rendering against a settled contract |
| 17.3 Ticker page and chart | Sonnet | DESIGN §11.3 replicates a SharpCharts layout, built not integrated |
| 17.4 Empty, stale, and suppressed states | Haiku | Three states, each specified |
| 17.5 Tests and documentation | Haiku | Inventory |

## 3. Task breakdown

### 17.1 `web/` scaffold and handler wiring

Rules:

- ~~Routes call handlers. No route constructs SQL or imports `db_io`, enforced by the same import test Session 16 uses.~~ **Superseded by ADR 118, 2026-08-18.** This contradicted ADR 070 ("no Python functions on Vercel") and ADR 076 ("TypeScript API routes select from those views"), all three Pinned. Routes select from the views; MCP is for LLM callers. See `session17-screener-and-ticker.md` §0.
- The staleness banner reads `meta.staleness_days` and renders above 2 days per DESIGN §11.2.
- Server-side rendering where it is simpler. This is a research tool for two people, not a public product.

Acceptance:

- A test asserting no `web/` module imports `sqlalchemy` or `db_io`.
- Staleness banner renders at 3 days and not at 1.

### 17.2 Screener route

Default columns, per the screener-column ADR:

```
Ticker | Signal | Str | %B | %K | DD | Cofire | Fired
```

Behind an expander, per the ADR's option C:

```
Cell hit rate | Baseline | n_eff | Interval | q-value | Suppressed reason
```

Rules:

- Default shows all signal types with a toggle and count badge, per DESIGN §11.2.
- Row click expands inline before navigating: `signal_types_all`, crossover flags, days-to-earnings, VIX, cofire count.
- A suppressed cell renders its reason, never a number and never a blank.
- A cell whose q-value exceeds `fdr_alpha` renders the q-value alongside the hit rate. The reader learns it did not survive correction from the row.

Acceptance:

- A day with no events renders the empty state, not an empty table.
- A suppressed cell renders its reason string.
- The expander shows every statistical field or none, never a partial set.
- Signal-type toggle filters correctly and the count badge matches.

### 17.3 Ticker page and chart

DESIGN §11.3 replicates the StockCharts SharpCharts layout. There is no public charting API, so this is built.

Rules:

- Price with Bollinger bands, stochastic panel, volume. Event markers on signal dates.
- Current state from `v_ticker_state`.
- Event history from `get_events`, cluster heads by default with a toggle.
- ADR 110 moved the trigger to `k_fast` with agreement gating. The chart shows both `k_fast` and `k_full`, since the agreement between them is now part of the signal definition and a chart showing one is showing half the rule.

Acceptance:

- A ticker with no events renders state without an empty event table.
- Event markers align to `signal_date`, verified against the database on three tickers.
- Both `%K` series render and are distinguishable.
- A delisted ticker renders its history and says it is delisted.

### 17.4 Empty, stale, and suppressed states

DESIGN §11.2: "Empty state matters more than usual, because most days nothing fires."

Rules:

- Empty: `No signals today. Last fire: TSM, 3 days ago` with a link. Requires a handler call for the last fire, which Session 15's `get_events` covers.
- Stale: banner above 2 days, naming the last bar date.
- Suppressed: the stored reason, greyed, never a number.

Acceptance:

- Each state rendered from a fixture and asserted.
- The empty state's "last fire" is correct against a seeded database.

### 17.5 Tests and documentation

Acceptance:

- `TESTS.md`, `DESIGN.md` §11, and `BUILD.md` updated.

## 4. Session gate

1. `/` and `/ticker/[sym]` render against the live database.
2. No `web/` module imports `sqlalchemy` or `db_io`.
3. The default screener shows the event feed; statistics require a deliberate action.
4. Suppressed cells render their reason and never a number.
5. Where a hit rate renders, `n_eff`, the interval, and the q-value render with it.
6. Empty state renders with a correct last-fire reference.
7. Staleness banner triggers above 2 days.
8. Both `%K` series render on the ticker chart.
9. No route reads holdout, inherited from the handler contract.
10. Determinism: identical database state renders identically.

## 5. What will be tempting and should not be done

**Making the statistics the default because the empty columns look unfinished.** They are not unfinished. They are the measurement.

**Rounding a q-value of 0.849 to "not significant" and hiding it.** The number is more informative than the label.

**Substituting a broader cell when the requested one suppresses.** Session 15's handler makes this impossible. Do not work around it in the view.

**Adding a "signal strength" visual weight for `signal_strength = 2`**, per DESIGN §11.2. That value does not exist. `signal_strength` is `len(signal_types_all)`, so confluence is 3 and everything else is 1. ADR 102 removed it as a grid dimension for this reason, and §11.2's line predates that finding.

---

# Session 18 — Research page and chat

**Adjust before building.** Same caveat as Session 17.

## 0. Scope

### In scope

1. `/research` — cell grid, era breakdown, three arms, drawdown slice.
2. `/chat` — tool-backed conversation over the seven handlers.
3. The system prompt per DESIGN §10.2.
4. The Phase 5 close: gate, documentation, and the phase record.

### Out of scope

- `/forward`. Phase 6.
- Any new tool. Seven, from Session 15.
- Any model output. `predict` returns `NotFound`.

## 1. What `/research` is for

Phase 4 produced eight artifacts under `reports/phase4/` and a negative result. `/research` is where that result becomes legible.

This is the route ADR 033 was describing:

> The strongest version of this project is a rigorous event-study engine with a natural language interface and honest confidence intervals. Built for that outcome, a null result stops being a failure.

The page's job is to show the measurement clearly enough that someone can check it, including the parts that say nothing was found.

## 2. Model assignment

| Task | Model | Reason |
|---|---|---|
| 18.1 Research route | Haiku | Rendering settled artifacts |
| 18.2 Chat scaffold and tool wiring | Sonnet | Tool-call loop, and the boundary the system prompt cannot enforce |
| 18.3 System prompt and response constraints | Sonnet | Guardrails live in code; the prompt handles framing |
| 18.4 Phase 5 close | Haiku | Gate, documentation, phase record |

## 3. Task breakdown

### 18.1 Research route

Rules:

- Cell grid: all cells including suppressed, with `n_eff`, interval, q-value, and reason.
- Era breakdown as descriptive rows only, no q-values, per ADR 103. Era 2024+ absent, since it is the holdout split.
- Three arms with the null distribution rendered as a distribution, not a summary. The `replication` column exists for this.
- Drawdown slice per ADR 015, with every interval spanning zero, which is what Session 14 measured.
- The kill criteria status from `RESULTS.md`, rendered rather than restated, so the page cannot drift from the record.

Acceptance:

- Every cell renders, suppressed and not.
- The null renders as 200 points or a density, not a mean.
- Era 2024+ absent, asserted by test.
- The kill criteria table matches `RESULTS.md`.

### 18.2 Chat scaffold and tool wiring

Rules:

- Every statistical claim comes from a tool result. The chat layer performs no arithmetic.
- Tool results pass through Session 15's validator before reaching the model, unchanged.
- The model cannot query the database directly, only through the seven tools.
- Tool call limits per turn, and a documented behaviour when hit.

Acceptance:

- A test asserting the chat layer has no database access outside handlers.
- Tool results reach the model with `n_eff`, interval, and q-value intact.
- A question needing a calculation no tool provides produces a refusal, not an invented number.

### 18.3 System prompt and response constraints

DESIGN §10.2 gives the prompt. Its rules matter more after ADR 112 than they did when written:

- Every statistical claim from a tool result; no arithmetic.
- Report `n_eff` and the interval alongside any probability.
- Tools return historical frequencies conditional on past states, not forecasts.
- A suppressed cell means insufficient data; do not substitute a broader cell without saying so.

Two amendments, both from ADR 112:

- The prompt should state that no cell survived FDR correction, so the model does not present a hit rate as an edge.
- "Not a forecast" is now load-bearing rather than cautionary. The measurement says these events do not predict returns better than the ticker's base rate, and a chat layer that implies otherwise is making a claim the data contradicts.

Rules:

- Guardrails live in code. The prompt handles framing, not enforcement. Anything the prompt asks the model not to do, the handler should already make impossible.
- The prompt is version-controlled and its changes are reviewable.

Acceptance:

- A prompt-injection attempt asking for a bare probability without an interval fails, because the tool cannot return one.
- A question about holdout returns a refusal, because the handler raises.
- The prompt names ADR 112's result.

### 18.4 Phase 5 close

Acceptance:

- All Phase 5 gate criteria checked and recorded.
- `RESULTS.md` gains a Phase 5 section.
- `BUILD.md` marks Phase 5 complete.
- `DESIGN.md` §10 and §11 reconciled with what was built.
- A `docs/sessions/README.md` index, if Session 15 did not add one.

## 4. Session gate

1. `/research` renders every cell, the full null distribution, the drawdown slice, and the kill criteria.
2. Era 2024+ absent everywhere.
3. `/chat` answers only from tool results, with no arithmetic.
4. A probability without `n_eff` and an interval cannot reach the model.
5. Holdout questions refuse at the handler, not at the prompt.
6. The system prompt names ADR 112's result.
7. No chat or research module imports `sqlalchemy` or `db_io`.
8. Phase 5 gate criteria recorded in `RESULTS.md`.
9. Determinism where applicable.

## 5. What will be tempting and should not be done

**Letting the chat layer round or combine tool results.** It is arithmetic, and DESIGN §10.2 forbids it for a reason: a number the model computed is a number no test covers.

**Softening the research page because the result is negative.** The page exists to show the measurement. ADR 033 says a null result reads better than a suspiciously profitable backtest, and that only holds if the null result is legible.

**Enforcing anything in the prompt that could be enforced in code.** A prompt rule is a request. A handler that raises is a guarantee.

---

# Phase 5 gate

Checked at the end of Session 18.

1. Seven handlers, one contract, three consumers agreeing by construction.
2. No probability leaves the handler layer without `n_eff`, an interval, and a q-value.
3. MCP server authenticated, rate limited, read-only, adding no query logic.
4. `/`, `/ticker/[sym]`, `/research`, and `/chat` all render against the live database.
5. No route, tool, or chat surface reads holdout.
6. `v_positions` reads its thresholds from config.
7. The chat layer performs no arithmetic and cannot query outside the seven tools.
8. ADR 112's result is visible on every surface that reports a statistic.
9. `test_holdout_firewall.py` and `test_schema_drift.py` both pass, the latter having run.

Item 8 is what makes the phase honest rather than merely complete.
