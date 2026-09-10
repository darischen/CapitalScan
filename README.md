# CapitalScan

A Bollinger Band and Stochastic Oscillator **event-study engine** for US
large-cap equities and ETFs. It detects indicator events, measures what
historically followed them, and reports the result — including when the result
is that nothing works.

**Advisory only. No execution path exists or may be added.** The absence of a
broker client is a safety property, not a disabled feature.

## The headline result

The system's own measurement is a negative one, and it is published rather than
buried:

> **Zero cells survive FDR correction on either split.** Across a 16-cell grid
> of signal type × drawdown bucket, no conditional hit rate is distinguishable
> from its baseline once multiple testing is accounted for. Minimum q-value
> 0.67 on train, 0.73 on validate, against α = 0.05.

That finding (ADR 112) has now held six times: under the original
measurement, under a widened stochastic-agreement rule, on a 43% larger
universe, on a universe corrected for a market-cap defect, on the rebuild
that corrected the share basis and the ADR share counts, and finally on a
universe with every known market-cap defect fixed. The scored grid is 48
cells on train and 28 on validate in every one of them, so the comparison
is like-for-like.

The benchmark arms agree independently. Over the training window the signal
arm returns 2.49 against 4.38 for simply buying and holding the same universe,
and sits below the 97.5th percentile of a 200-replication random-entry null
with matched firing counts.

## Why the numbers are smaller than they look

Signals cluster. When one ticker touches its lower band, dozens do — the same
market move counted many times. Effective sample size corrects for this:

$$n_{\text{eff}} = \frac{n}{1 + \rho\,(\bar{c} - 1)}$$

With a pooled ρ between 0.23 and 0.46, a cell holding 1,213 raw events carries
the independent information of about 120. Every published probability is
reported with its `n_eff` and a confidence interval, never a bare percentage.

## Architecture

Four planes, with one rule holding them together: **nothing heavy runs in a
request path.** Every number the UI shows was computed offline and written to a
table; serving does indexed lookups only.

| Plane | Latency | Runs on | Owns |
|---|---|---|---|
| Ingest & compute | minutes, offline | workstation | bars, indicators, events |
| Research | hours, offline | workstation | backtests, statistics, models |
| Serving | < 200 ms | Raspberry Pi (Postgres + Next.js) | screener, stats, positions |
| Interaction | seconds, streaming | Edge + Anthropic | chat, tool orchestration |

Three machines, and the split is what makes the rest work. A **workstation**
does heavy research. A **laptop** runs the scheduled jobs. A **Raspberry Pi**
holds the serving database, the web app, and the live poller.

**The poller lives on the Pi and writes the serving store, never research**
(ADR 158). That one decision is why research has no live writer during market
hours: it can be rebuilt, resynced, or switched off between 06:30 and 13:00
without touching what the site serves. Research is copied to serving by an
explicit `sync`; nothing else crosses.

```
capitalscan/
  core/        pure computation — no IO, no clock, no database
  jobs/        ingest, indicators, universe, events, poller, CLI
  research/    backtest, validation harness, statistics, benchmarks
  handlers/    the query layer the chat and MCP tools share
  mcp/         read-only MCP server
web/           Next.js screener, ticker pages, research page
docs/          DECISIONS.md (194 ADRs), DESIGN, BUILD, TESTS, RESULTS, BACKLOG,
               OPERATIONS (what broke and the fix), TIMINGS (measured budgets)
```

## Invariants

These are enforced by tests, not by convention:

1. **`core/` performs no IO** — no database, no HTTP, no file reads, no clock.
2. **One signal implementation.** `jobs/` and `research/` both import
   `core/signals.py`. There is never a second band comparison.
3. **Indicators are read at t−1, never t.** The highest-risk silent failure in
   the system, guarded by a shift ladder and a signature probe that restricts
   which bar fields `detect` may even see.
4. **Nulls are dropped and logged, never filled or interpolated.**
5. **`split_key` is assigned at event creation**, never at query time, and no
   view may join statistics on an event's own split.
6. Every generated row carries `run_id` and `git_sha`.
7. **No broker client, no order placement, no credentials.**
8. Every response carrying a probability carries `n_eff` and a confidence
   interval.
9. **No magic numbers outside `core/config.py`** — including a threshold that
   happens to match a default elsewhere. A literal in an exit path while the
   same value is sweepable lets entry and exit disagree inside one backtest,
   and the output looks fine.
10. **`core/config.py` holds dataclasses only.** Its sole import is
    `dataclasses`; resolution lives in `jobs/`.

## Validation

A five-check harness gates every backtest: no look-ahead, entry sanity, exit
sanity, return identity, and non-overlap. The look-ahead check does not inspect
the output — it re-runs detection from scratch on shifted indicator columns and
on a shuffled control, then asserts the event sets decay monotonically toward
the control. A backtest whose harness fails does not ship.

Holdout data is evaluated exactly once, at the end, and published whatever it
says.

## Getting started

```bash
uv sync
cp .env.example .env.local      # database URLs, SEC_USER_AGENT
uv run cscan db migrate
uv run cscan nightly            # bars, indicators, events, path capture
uv run cscan backtest --phase compute --workers 8
uv run cscan backtest --phase harness --workers 8
uv run cscan stats cells --config-hash <hash> --split-key validate
```

The test suite deliberately excludes the integration tier, which truncates
live tables:

```bash
uv run pytest capitalscan/tests/unit capitalscan/tests/property
```

## The model, and what it is honest about

Phase 6 added a multi-task distributional model: a shared trunk over six
softmax heads, scored by summed CRPS, calibrated against observed frequency
rather than against its own confidence. It does **not** overturn ADR 112 —
the cell grid still has nothing surviving FDR correction. It answers a
different question: given this signal, how far does price tend to travel.

Three things it says about itself, all measured:

**Ranking is durable; the level is not.** The isotonic tables are anchored to
their calibration period's base rate. That rate ran 43.2% on the fitted split
and swings 36.5–65.0% year to year — a range wider than the model's entire
Brier skill of 0.079. So "A scores above B" is trustworthy and "A is 48%" is
worth about ±5 points.

**Split diagnostics by task family before believing them.** A coverage gate
reporting 26/30 concealed an inversion: the family backing every displayed
probability had degraded while the one displayed nowhere improved. Aggregates
over heads that reach different surfaces are not comparable.

**The forward log is never trained on.** `outcomes` records predictions made
before their results existed. It is the only estimate here that nothing has
iterated against, and training or recalibrating on it converts it into another
contaminated split — irreversibly, since no later run can un-teach a row.

## Status

Phases 1–6 are complete: ingest, detection, the live poller, the backtest and
statistics stack, the serving layer, and the model.

Open work, with reasons, is in `docs/BACKLOG.md`. Nothing leaves that file by
being forgotten — including the items that turned out to be wrong, which are
struck through and kept rather than deleted.

## What this repository is actually for

It is a measurement instrument that happens to have a UI, and most of the
engineering is spent on one problem: **a wrong answer that looks right costs
more than a crash.** A crash gets fixed in an hour. Nested JSON silently
stored as strings, a probability label naming the wrong direction, a
prediction row pointing at an unrelated event, a nightly job reporting
success having written nothing — each of those shipped clean output and
survived until someone measured the specific thing.

So the invariants above are tests, the ADRs record what was tried and
refuted alongside what was adopted, `OPERATIONS.md` keeps the failures with
their diagnoses, and every published probability drags its sample size and
interval along with it.
