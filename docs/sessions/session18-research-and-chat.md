# Session 18 — Research page, chat, and the Phase 5 close

Split out of `session16-18-phase5.md` when Session 16 closed. Read
`session17-screener-and-ticker.md` §0 first: **the same stack decision
blocks this session**, and the chat route is where it bites hardest.

---

## 0. Blocked on the same decision, plus one of its own

### Inherited

ADR 070 ("no Python functions on Vercel") and ADR 076 ("TypeScript API
routes select from views; Python MCP handlers select from the same views")
against `session16-18-phase5.md` §17.1's Python `web/` package. Fully stated
in Session 17's plan.

### Specific to chat

`session16-18-phase5.md` §18.2 requires:

> Tool results pass through Session 15's validator before reaching the
> model, unchanged.
> A test asserting the chat layer has no database access outside handlers.

`handlers/validate.py` is Python. Under ADR 070 the chat route runs on
Vercel in TypeScript, so it cannot call it. That leaves three readings, and
they are not equivalent:

1. **The chat route calls the MCP server**, which calls the handlers, which
   validate. One validator, one implementation, and a network hop ADR 072
   explicitly rejected — though ADR 072's rejection was about the *API*
   path, and a chat turn already pays several hundred milliseconds to a
   model. This is the option the existing code most nearly supports: the
   MCP server is built, authenticated, and passing.
2. **The chat route selects from views directly** and re-implements the
   invariant-8 check in TypeScript. Two implementations of the one rule the
   whole handler layer exists to hold. ADR 076's answer is that the views
   make it structural, which is true of the *columns* and not of the raise.
3. **A Python chat service.** Contradicts ADR 070 for the same reason
   Session 17 option B does.

Reading 1 looks right and is not mine to choose. It is worth noting that it
makes Session 16 load-bearing rather than a side surface, which is a change
in what that session was for.

### Not blocked

`/research` is a rendering of finished artifacts and needs no new query
logic under any option. `docs/SYSTEM_PROMPT.md` is written and
version-controlled (18.3's requirement); only the loader depends on the
stack.

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
