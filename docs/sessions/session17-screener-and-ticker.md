# Session 17 — Screener and ticker page

Split out of `session16-18-phase5.md` when Session 16 closed, per that
document's own instruction. Sessions 15 and 16 are done; read
`RESULTS.md` for what they measured and `handlers/types.py` for the shapes
they shipped.

---

## 0. Decided: TypeScript routes select from the views (ADR 118)

**This session was stopped on 2026-08-18 and unblocked the same day.** The
plan below said routes call the Python handlers; three Pinned ADRs said
otherwise. ADR 118 settles it in favour of the ADRs.

### The rule

**MCP is for LLM callers, not for deterministic retrieval.**

```
/  /ticker  /research   ──SELECT──►  v_screen, v_chart, v_events, v_ticker_state
                                            ▲
/chat, Claude Code, Claude Desktop          │
        └── MCP ──► handlers/ ──────────────┘
```

A screener query has one right answer and no model in the path. Sending it
through a protocol built for LLM tool calls buys an `initialize` handshake,
a session id, an SSE frame, and a tool-call envelope in order to run a
`SELECT` Postgres answers in 27 ms. The protocol earns its overhead when a
model is choosing which tool to call. It earns nothing when a route already
knows.

So: **`capitalscan/web/` stays empty.** The Next application lives at the
repo root and reads the views. Sessions 15 and 16 are unaffected and serve
`/chat` and Claude Code.

### What this session must carry that the handlers otherwise would

Two things move from Python to TypeScript, and both are named here so
neither is discovered as a bug:

- **ADR 114's event-feed default.** `with_stats=False` is a handler
  decision and does not help a route that never calls one. The TypeScript
  screener defaults to the feed on its own.
- **Invariant 8.** In Python `handlers/validate.py` raises. Here the
  guarantee is that `n_eff`, `ci_low`, `ci_high`, and `q_value` are
  *columns* on `v_screen` and `v_stats`, so returning a bare probability
  means deliberately dropping columns (ADR 076). Structural but weaker
  than a raise, and that is the price of this option. **A test asserts the
  serving views carry all four** (`test_serving_view_contract.py`, 9 tests,
  written first). It found one gap: `v_forward` exposes `p_touch_*` with no
  interval and no q-value. Phase 6's, not this session's, and recorded in
  `KNOWN_GAPS` with what would close it.

### Where the design constraints actually live

**Nothing blocks this session.** A second blocker was claimed on 2026-08-18
and was not real: the `frontend-design` skill is installed and enabled. It is
a *plugin* skill under `~/.claude/plugins/`, and the claim came from checking
`~/.claude/skills/` alone and generalising.

Reading it surfaced a different error. CLAUDE.md and `DESIGN.md` §11.7 both
said it "carries this environment's design tokens". **It does not.** It is
Anthropic's general design-guidance skill: avoid templated output, pair a
display and body face deliberately, pick one signature element, spend
boldness in one place. No palette, no type scale, no spacing system. Both
documents are corrected.

The constraints were always here:

| Source | Carries |
|---|---|
| CLAUDE.md, Frontend | Dense instrument panel, monospace numerals, dark by default, colour as meaning |
| DESIGN §11.6 | The five states, with exact renderings. "Suppressed is the one people get wrong" |
| DESIGN §11.7 | Small type, tight rows, no hero sections, colorblind-safe. Charts for shape, tables for lookup |
| DESIGN §11.8 | `lightweight-charts` for price and stochastic, `recharts` for statistics |
| DESIGN §11.9 | Screener-as-cards on mobile; `/research` is desktop-only and says so |

The skill's own rule settles how they combine: *"where the brief pins down a
visual direction, follow it exactly."* The brief is pinned, so the skill
governs **process**, not palette.

Its one warning that bites here: a near-black background with a single bright
accent is among the three looks it names as reading generated rather than
chosen. §11.7's direction sits close to that. The accent and the type pairing
are therefore the two places this session has to make a real decision rather
than reach for the default.

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

---

## 3. Result, 2026-08-19

**Ten of twelve passed. The two that did not are findings, not debt.**

| # | Item | |
|---|---|---|
| 1 | `/` and `/ticker/[sym]` render against the live database | pass |
| 2 | No `web/` module imports `sqlalchemy` or `db_io` | pass, 63 tests |
| 3 | Default screener shows the event feed | pass (ADR 114) |
| 4 | Suppressed renders its reason, never a number | pass |
| 5 | A hit rate renders with `n_eff`, interval, q-value | pass |
| 6 | Empty state with a correct last-fire reference | pass, after a fix |
| 7 | Staleness banner above 2 days | pass, and not at exactly 2 |
| 8 | Both `%K` series render on the ticker chart | pass, with a legend |
| 9 | No route reads holdout | pass, asserted on the source |
| 10 | Determinism | pass, six components |
| 11 | Ticker page load time in `RESULTS.md` | pass, **43 ms median** |
| 12 | The stack decision as an ADR | pass (ADR 118) |

**17.3's "a delisted ticker renders its history and says it is delisted"
cannot be satisfied against this database.** All 96 inactive tickers either
carry no bars or carry three to four with no indicators, so there is no
delisted ticker with history to render. The not-found state handles them and
deliberately does not claim the symbol is invalid, because it cannot tell an
unknown symbol from a known one with no indicators. Full numbers in
`RESULTS.md`.

**Statistics on the ticker page are absent, not hidden.** `cell_stats` has no
ticker dimension (ADR 102, ADR 104), so a panel here would repeat the
screener's numbers for the same signal type and bucket.

**One migration, `c4a7e91b53d8` (ADR 120).** `v_chart` was returning 963 rows
for 275 trading sessions. Four more defects found by running it, including
every chart date landing one session early. `RESULTS.md` has all of them.
