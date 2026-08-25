# Phase 8 — Execution (preliminary)

> **NOTHING IN THIS DOCUMENT IS DECIDED.**
>
> No ADR exists. No session plan exists. No implementation begins from this
> file. This is a scoping sketch written 2026-08-24, while Phase 6 has not
> started and Phase 7 has not been planned, so every number, boundary and
> mechanism below is provisional and several will be wrong.
>
> Its only job is to record what Phase 8 would need to answer, so those
> questions are visible now rather than discovered during implementation.
> Real planning happens after Phase 7 closes.

---

## Position in the roadmap

Phase 6 Model, twenty heads, kill criterion at Session 24
Phase 7 Options, synthetic then measured against one month of Polygon
Phase 8 Execution, live broker, one share <- this file
Holdout Once, at the end, published whatever it says

Phase 8 follows Phase 7 deliberately, and the ordering is load-bearing rather
than convenient. Phase 7's pricing-error measurement is what decides whether
an execution path is allowed to touch options at all, or shorts, or whether
it stays long-only with puts reserved for a bear reversal. Building execution
first would mean making that decision without the measurement that answers it.

---

## 1. The decision Phase 8 cannot avoid

ADR 043 is Pinned and its rationale is unusually strong:

> Advisory only, no execution path in v1.
>
> The absence of an execution path is the safety property, not a disabled
> flag. It also frames the project accurately: decision support with
> calibrated uncertainty rather than an automated trading system.

Phase 8 removes that property. Not narrows, not gates, removes. "No execution
path" is not partially true.

**So Phase 8 opens with an ADR superseding 043, and that ADR has to name the
safety property replacing it.** A phase that quietly extends 043 rather than
superseding it leaves the strongest safety statement in `DECISIONS.md`
describing a system no longer being built. `CLAUDE.md` requires stopping on a
task appearing to contradict a Pinned ADR, so this is also the gate that lets
any Phase 8 work start.

Candidate replacement properties, none decided:

- A share cap enforced in code, not in `config.toml`. A config value is a
  flag; a constant with a test asserting it is a property.
- A daily order-count ceiling, same enforcement.
- A kill switch the process checks every cycle, so stopping it does not
  require the process to be healthy enough to receive a signal.
- No order without a matching `predictions` row and a matching event.

---

## 2. What Phase 8 measures, and what it must not

**It measures execution correctness. It does not measure strategy
performance.**

This distinction is the phase's whole framing, and getting it wrong makes the
phase worse than skipping it.

ADR 112 fired. No cell survived FDR correction across three configurations and
630,592 events. ADR 113 authorizes Phase 6 explicitly as "authorized, not
expected to succeed". A one-share live test on that foundation produces a P&L
number, that number is noise at any sample size Phase 8 will reach, and it
will read as evidence.

**The Phase 8 gate should refuse to record P&L as a result.** Not defer it,
not caveat it. Refuse. A number in `RESULTS.md` gets cited later by someone
who did not read the caveat, and the someone is likely the author in six
months.

### The four measurements worth having

Each is valuable whether or not the signal has an edge, and none requires the
signal to work.

**Fill price against assumed entry price.** Entry conventions are `next_open`
and close-confirmed. A real fill measures the slippage those conventions carry
instead of assuming it. This feeds the cost model directly and it is the one
number in the whole project no amount of historical data produces.

**Observation-to-fill latency.** The poller runs on a five-minute grid and ADR
140 already establishes its observation is not an entry price. Phase 8
measures how far apart observation and fill land, in time and in price.

**Reconciliation.** Does the position the system believes it holds match what
the broker reports, every morning, with no human intervention. A silent
divergence here is the failure costing real money.

**The discretionary gap.** New, and the most interesting of the four. Every
manual trade taken on a surfaced signal has a mechanical counterfactual: what
the strategy would have done, unmodified, on the same event. Recording both
makes the overlay measurable.

The motivating case, recorded 2026-08-24 and explicitly anecdote rather than
data: a set of put positions held roughly two weeks on a discretionary wait,
finishing flat to slightly positive, where mechanical execution would have
finished slightly down. One episode in a volatile stretch proves nothing. But
the *gap itself* is a quantity, and the system already stores everything
needed to compute it. Nothing measures it today.

---

## 3. Open questions, in rough order of how much they change the design

### Broker integration

- Schwab's Trader API individual-developer path: registration, approval time,
  and whether it is currently open. Verify at build time; the TD Ameritrade
  migration changed this and may change again.
- **OAuth refresh token lifetime.** The expiry window has moved since the
  migration, and an unattended Pi will hit it silently. A failed refresh must
  notify rather than log and continue. Discovering it a week later means a
  week of no execution and no alert.
- Sandbox or paper environment availability. If one exists, every mechanism
  below is tested there before a real share is bought.
- Rate limits, and whether they interact with the five-minute poller.

### Idempotency, which is the expensive failure

A Pi rebooting between "order submitted" and "response persisted" must not
submit twice on restart. Sketch, not a decision:

- A client-generated order ID written to the database **before** the request
  leaves the process.
- Startup reconciliation querying open and filled orders before placing
  anything at all.
- A test asserting a simulated crash at every point in the submit path
  produces at most one order.

More likely on a box with no UPS, and the one failure with an unbounded cost.

### Order mechanics

- Order type. Market orders make the slippage measurement clean and give up
  control. Limit orders control price and introduce unfilled orders, a second
  state the system has to model.
- What happens to an unfilled order at close.
- Whether an exit is automated at all. **Automating entry only is a
  defensible scope** and roughly halves the surface area. See §5 on the
  bear-reversal exit, which is the one exit with a stated conviction behind
  it and no measurement.

### State

- `positions` exists in the schema and has never carried a real row. Its shape
  needs review against what a broker actually returns.
- `positions` joins `predictions` and `outcomes` as data with no recovery
  path, so it joins the `pg_dump` off the Pi.
- Whether a broker fill writes back into `events`. Leaning strongly no: ADR
  140 makes the nightly authoritative for every grain, and a fill is an
  observation about an order rather than about an event.

---

## 4. The agent, scoped

The idea is an agent carrying trade history so execution reflects factors
influencing real decisions without being explicitly implemented. That is a
reasonable description of a real problem: several inputs demonstrably affect
the trades taken and none of them exist in `DESIGN.md` §7.3's twenty-two
features.

**But an agent is the wrong shape for that problem, and the reason is
structural rather than about trust.**

Every other layer here is deterministic, versioned and reproducible.
`features_json` round-trips and reproduces a prediction exactly. ADR 029
forbids regenerating a prediction. Session 25 requires live and backtest
features to agree byte for byte. An agent inferring behaviour from a small
example set has none of those properties, so a disagreement between what it
did and what it should have done is not reproducible, and an unreproducible
disagreement in an order path cannot be debugged.

There is also a sample-size problem the project already knows how to state.
ADR 001 widened the training universe to 500 tickers because clustering cut
an effective sample to about 38 and the detectable effect was 22 points
against a target of 5 to 15. A handful of personal trades is far below that,
and fitting behaviour to them is the overfitting ADR 033's second kill
criterion exists to catch, applied to a population too small to run the check
on.

**Provisional boundary, open for argument: the agent reads, explains and
flags. It never submits.**

The version worth building summarizes why a signal fired, what the model said,
what the cell statistics show, and how the setup compares to past trades under
similar conditions. Useful, and holding no authority.

**And the better use of the trade history is as a source of hypotheses rather
than a source of behaviour.** Each unimplemented factor influencing a decision
becomes a candidate feature or a candidate rule: written down, added to §7.3
or to the exit policy, and measured. A factor surviving that becomes part of
the system and gets the same lookahead handling and the same statistics as
everything else. A factor failing it was worth knowing about. Both outcomes
beat an agent quietly reproducing it.

---

## 5. The bear-reversal question, and why it is not answered

Stated conviction, recorded 2026-08-24: a bear reversal is close to a
guaranteed exit for a long position or an entry for a short, and it should be
acted on within the next day.

`bear_close_above_upper` exists as a signal type (`core/types.py`, ADR
108/109). Phase 4 measured it and found nothing surviving FDR correction. So
the conviction looks contradicted.

**It is not, because the horizons do not overlap.** Every horizon in the
project is $h \in \{5, 10\}$, and ADR 113 keeps that for Phase 6's twenty
heads. "Within the next day" is $h = 1$. Nothing in `RESULTS.md` speaks to it.
A null result at 5 and 10 days is silent about day 1, and for a reversal
signal those are plausibly different questions.

**This is answerable now and cheaply.** `path.favorable` and the forward price
path carry day-1 outcomes for every event already. No rebuild, no new
ingestion, no config move. It is a query and a statistics run against
machinery Phase 4 already built.

Provisional: run it before Phase 8 plans any exit automation, because it
decides whether the exit rule with the strongest conviction behind it has any
support. If it holds, an $h = 1$ head is a candidate for the model surface and
that is an ADR amending 113. If it does not, that is worth knowing before it
is wired to an order.

Either way it belongs in `RESULTS.md` with `n_eff` and confidence intervals
like everything else, and it should carry the same FDR treatment, since asking
a new horizon after seeing a null at two others is exactly the multiple
comparison the correction exists for.

---

## 6. Interaction with Phase 7

Phase 7 produces the synthetic pricing error against one month of real Polygon
chains. **That measurement gates what instruments Phase 8 is allowed to
touch.**

If synthetic pricing is materially wrong, an options execution path built on
it is executing against a model known to be inaccurate. ADR 018 already sizes
why this is not a small correction: theta alone costs a 30-day ATM option
roughly 8.7% of value over a five-day hold before any move, IV crush after
earnings runs 30 to 50%, and spreads run 1 to 3% of premium on liquid
weeklies.

Three provisional tiers, decided after Phase 7 closes and not now:

- **Long shares only.** The floor. Available regardless of what Phase 7 says.
- **Plus shorts.** Requires ADR 108/109's short side measured on the current
  population and a stop policy Phase 8 does not have.
- **Plus puts on a bear reversal.** Requires Phase 7's error to be small, and
  requires §5's $h = 1$ question answered.

---

## 7. What would make Phase 8 not worth doing

Recorded now, while no sunk cost exists, in the spirit of ADR 033.

- **Schwab individual API access is unavailable or requires terms not worth
  accepting.** The phase has no fallback broker identified.
- **Phase 6's kill criterion fires and Session 25 closes out.** The "consult
  the trained model" step then has no model. Entry on signals plus cell
  statistics remains possible and the phase shrinks.
- **Idempotency cannot be made convincing.** If a crash test produces
  duplicate orders and the cause is not fixable, the phase stops. Losing money
  to a bug in the order path is a different category from losing it to a signal
  with no edge.

---

## 8. What Phase 8 does not change

- ADR 112's result. Execution automation says nothing about whether the
  signals work.
- The advisory framing on every user-facing surface. The Phase 5 gate's item 8
  requires ADR 112's result visible everywhere, and `/forward` and any
  execution surface are included.
- Holdout. It stays sealed, evaluated once at the end, published whatever it
  says. No Phase 8 result touches it.