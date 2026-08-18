# Session 17 — Screener and ticker page

Split out of `session16-18-phase5.md` when Session 16 closed, per that
document's own instruction. Sessions 15 and 16 are done; read
`RESULTS.md` for what they measured and `handlers/types.py` for the shapes
they shipped.

---

## 0. STOP — one decision is needed before any code

**This session cannot start until someone chooses between ADR 070/076 and
`session16-18-phase5.md` §17.1. They contradict each other, and the choice
changes almost everything about the work.**

CLAUDE.md: *"If a task appears to require contradicting one [ADR], stop and
ask. Do not work around it."* This is that case, so this session stops here
rather than picking.

### The contradiction

`session16-18-phase5.md` §17.1 says:

> Routes call handlers. No route constructs SQL or imports `db_io`,
> enforced by the same import test Session 16 uses.

That describes a **Python** `web/` package, and it is what ADR 072 said:

> Business logic lives in handler functions. The HTTP API and the MCP server
> both call those handlers directly.

**ADR 076 refines ADR 072 and withdraws exactly that arrangement:**

> ADR 072 named a Python handler layer as the shared contract, which would
> have required either duplicating queries in TypeScript **or hosting a
> Python service**. Views achieve the same guarantee at the database level.
> Query logic lives in Postgres views. TypeScript API routes select from
> those views. Python MCP handlers select from the same views.

And ADR 070 is unambiguous about where the routes run:

> Every endpoint is an indexed SELECT executed by Next.js against Neon
> through the Postgres driver. **No Python functions on Vercel.**

DESIGN §8.1 repeats it, and DESIGN §11.8 names `lightweight-charts` and
`recharts` — both JavaScript. DESIGN's own architecture diagram says
`CLIENT (Next.js on Vercel)`.

So the session plan describes a Python web layer that three Pinned ADRs and
DESIGN all replaced.

### What is *not* in conflict

Session 15's handler layer stands either way, and it already complies with
ADR 076: `screen_signals` reads `v_screen`, `get_events` reads `v_events`,
`get_universe` reads `v_universe`, and `get_indicators` reads `indicators`
joined to `bars`. The handlers hold no query logic the views do not already
carry. Session 16's MCP server is the "Python MCP handlers" half of ADR 076,
built and passing.

The conflict is confined to **who serves `/` and `/ticker/[sym]`**.

### The options

**Option A — Next.js on Vercel, TypeScript routes selecting from the views.**
Follows ADR 070, ADR 076, and DESIGN as written. `capitalscan/web/` stays
empty and a `web/` (or `app/`) directory at the repo root holds the Next
application. The handler layer serves MCP and chat only.

*Cost.* A second toolchain in the repo: `package.json`, `node_modules`, a
third CI job, and a second place where "no probability without `n_eff`"
has to hold. ADR 076's answer to that last point is that the views make it
structural — `v_screen` and `v_stats` carry `n_eff`, `ci_low`, `ci_high`,
and `q_value` as columns, so returning a bare probability requires
deliberately dropping columns. That is a weaker guarantee than
`handlers/validate.py`'s raise, and it is the guarantee ADR 076 chose.

*Also.* ADR 114's event-feed default is a *rendering* decision, so it has to
be re-implemented in TypeScript. The handler's `with_stats=False` does not
help a route that never calls it.

**Option B — a Python web layer calling the handlers.**
Follows the session plan. One implementation of the response contract, and
`handlers/validate.py` guards every surface rather than two of three.

*Cost.* Contradicts ADR 070 and ADR 076, both Pinned. It needs a Python host
somewhere that is not Vercel, which is the cost ADR 076 named and declined.
Choosing this means amending both ADRs and saying why the hosting cost is
now acceptable.

**Option C — Next.js frontend, Python API behind it.**
The frontend is TypeScript and calls a Python service that calls the
handlers. Keeps one validator.

*Cost.* Still hosts a Python service, so it carries ADR 076's cost plus a
network hop ADR 072 explicitly rejected ("no network hop"). It is the option
both ADRs already weighed.

### The second blocker

CLAUDE.md: *"**Read the frontend-design skill before writing any
component.** It carries this environment's design tokens and styling
constraints, which `DESIGN.md` does not cover."*

**That skill is not available in this environment.** Whatever the stack
decision, components written without it will use invented tokens and be
redone. Either make the skill available or accept that the first pass is
throwaway and say so.

### Recorded, not worked around

`DECISIONS.md` "Open items" carries this as an entry. Nothing in Session 17
is built until it is resolved, and the resolution belongs in an ADR that
either amends 070/076 or supersedes §17.1 of the session plan.

---

## 1. What Session 17 builds once the decision is made

Scope is unchanged from `session16-18-phase5.md` §17. What follows only
records what Sessions 15 and 16 changed about it.

### Routes

- `/` — today's screener, the default landing route.
- `/ticker/[sym]` — chart, current state, event history.

`/positions` is Phase 2's and is not touched, **except** that its view was
rebuilt in Session 15: `v_positions` now reads `serving_config` and carries
a new trailing `exit_stoch_k` column, `days_held` counts trading sessions
rather than calendar days, and `exit_signal_mid_band` is NULL when the
policy is off. Any existing consumer that read `days_held` positionally or
treated the mid-band flag as a boolean needs a look.

### Adjustments from what shipped

| The plan said | What shipped |
|---|---|
| Screener statistics behind an expander | ADR 114, and `ScreenRow.stats` is a `CellStats \| Suppressed \| None` union, not columns |
| "Re-read `handlers/types.py`" | Done: 14 frozen dataclasses, `RESULT_TYPES` enumerates them |
| `get_stats(..., ticker=...)` | **No `ticker` parameter.** `cell_stats` has no ticker dimension (ADR 102, ADR 104) |
| `explain_signal` returns SHAP top-5 | No SHAP field. It is a Phase 6 attribution and is absent, not empty |
| Empty state needs a "last fire" query | `handlers.last_fire()` exists and returns an `EventRow` or `None` |

### Two measurements to respect

**Fixed before this session started (ADR 116), and the claim that
prompted it was wrong.** The Session 15 note said nothing pushed a ticker
predicate down through the view's `DISTINCT ON`. Postgres pushes a
*constant* one down fine - a single-ticker read was 17 ms all along - and
only a *correlated* predicate, which is what `v_positions` uses, paid the
23.8 s. The ticker page was never the query at risk.

The view is now a loose index scan: **27 ms** for all 612 tickers, **1.4 ms**
for one, **23.5 ms** for a `v_positions` row. Nothing here needs a
mitigation. Measuring a page's real load time before trusting it is still
worth doing, which is why gate item 11 below stays.

**ADR 112 is the shape of the data, not a caveat.** Zero cells survive FDR
correction; 100 of 224 train cells suppress. On the ticker page and the
screener, "suppressed" and "did not survive correction" are the normal
renderings, not the error states.

### The union tag, already decided

Session 16 serializes `Suppressed` and `NotFound` with a `"kind"` field so a
client can tell "we cannot say" from "it never happened". A route rendering
the same union should make the same distinction rather than inventing a
second one — a greyed-out number and a blank cell both read as data.

---

## 2. Session gate

Unchanged from `session16-18-phase5.md` §17.4, plus:

11. `v_ticker_state`'s cost is measured and the ticker page's load time is
    recorded in `RESULTS.md`, whatever it is.
12. The stack decision above is recorded as an ADR before any component is
    written.
