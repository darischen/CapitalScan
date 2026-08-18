# Session 18 — Research page, chat, and the Phase 5 close

Split out of `session16-18-phase5.md` when Session 16 closed. Read
`session17-screener-and-ticker.md` §0 first: **the same stack decision
blocks this session**, and the chat route is where it bites hardest.

---

## 0. Decided: chat routes through MCP (ADR 118)

**Unblocked 2026-08-18.** See `session17-screener-and-ticker.md` §0 for the
shape. The rule is that MCP is for LLM callers, not for deterministic
retrieval, and `/chat` is the clearest LLM caller in the system.

### What that settles

§18.2 required tool results to pass through Session 15's validator before
reaching the model. Under ADR 070 the chat route is TypeScript and cannot
call `handlers/validate.py` directly. It does not need to: **the chat route
calls the MCP server, which calls the handlers, which validate.**

```
/chat ──MCP──► handlers/ ──► views
```

One validator on the path that carries a model, which is the path that
needs it. `/research` is deterministic and reads the views like `/` and
`/ticker`.

That makes Session 16 load-bearing rather than a side surface, which is
worth stating: the MCP server is now the only way a model reaches this
data.

### Not blocked

`/research` renders finished artifacts and needs no new query logic.
`docs/SYSTEM_PROMPT.md` is written and version-controlled (18.3's
requirement). Only the loader waits on the route work.

---

## 1. What is already done

| 18.x | Status |
|---|---|
| 18.3 the prompt itself | **Done.** `docs/SYSTEM_PROMPT.md`, with ADR 112's two amendments in the body rather than appended |
| 18.3 the loader | Blocked on the stack decision |
| 18.1 `/research` | Not started; unblocked by data, blocked by stack |
| 18.2 chat scaffold | Blocked, see §0 |
| 18.4 Phase 5 close | Pending 17 and 18 |

---

## 2. `/research`, with the artifacts it renders

ADR 033's line is the brief:

> The strongest version of this project is a rigorous event-study engine
> with a natural language interface and honest confidence intervals. Built
> for that outcome, a null result stops being a failure.

`/research` is where that result becomes legible. Everything it renders
already exists and is regenerable:

| Panel | Source |
|---|---|
| Cell grid, all cells including suppressed | `cell_stats` / `v_stats` |
| Era breakdown, descriptive only, no q-values (ADR 103) | `cell_stats` rows with `era` set |
| Three arms with the null as a distribution | `benchmarks`, `replication` 1-200 |
| Drawdown slice (ADR 015) | `cscan stats artifacts` |
| Kill criteria status | `RESULTS.md`, rendered rather than restated |

**Era 2024+ must be absent everywhere.** It is exactly the holdout split;
both begin 2024-01-01 (ADR 103, `core.cells.holdout_era`). The test should
read `core.cells.reported_eras` rather than hardcoding three labels.

**The null renders as 200 points or a density, never as a mean.** The
`replication` column exists so the distribution can be drawn. A mean with an
error bar is the same evidence compressed into a shape that invites a
t-test nobody ran.

**Artifacts are not committed.** Session 14's `reports/phase4/` was deleted
on 2026-08-17; `cscan stats artifacts --config-hash <hash>` regenerates all
eight deterministically. `/research` should call the same path rather than
reading files that may not exist.

---

## 3. What the chat layer must not do

Repeating `session16-18-phase5.md` §18.5, because these are the ones that
look helpful:

**Rounding or combining tool results.** It is arithmetic, and a number the
model computed is a number no test covers.

**Softening the negative result.** The page exists to show the measurement.
ADR 033 says a null result reads better than a suspiciously profitable
backtest, and that only holds if the null result is legible.

**Enforcing in the prompt what could be enforced in code.** A prompt rule is
a request. `docs/SYSTEM_PROMPT.md` opens with the table of which is which,
and the right response to a new rule is usually a handler change.

---

## 4. Session gate

Unchanged from `session16-18-phase5.md` §18.4, plus:

10. The stack decision from Session 17 §0 is recorded as an ADR, and the
    chat layer's validator path (§0 above) is recorded with it.

---

## 5. Phase 5 gate

`TESTS.md` §10 carries the live version with Sessions 15 and 16 checked off.
Item 8 — "ADR 112's result is visible on every surface that reports a
statistic" — is discharged for MCP by the server's `instructions` and stands
open for `/`, `/ticker`, `/research`, and `/chat`.

That item is what makes the phase honest rather than merely complete.
