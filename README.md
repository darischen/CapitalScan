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
> 0.60 on train, 0.72 on validate, against α = 0.05.

That finding (ADR 112) has now held four times: under the original
measurement, under a widened stochastic-agreement rule, on a 43% larger
universe, and on a universe corrected for a market-cap defect.

The benchmark arms agree independently. Over the training window the signal
arm returns 2.71 against 4.47 for simply buying and holding the same universe,
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
| Serving | < 200 ms | Vercel + Postgres | screener, stats, positions |
| Interaction | seconds, streaming | Vercel Edge + Anthropic | chat, tool orchestration |

```
capitalscan/
  core/        pure computation — no IO, no clock, no database
  jobs/        ingest, indicators, universe, events, poller, CLI
  research/    backtest, validation harness, statistics, benchmarks
  handlers/    the query layer the chat and MCP tools share
  mcp/         read-only MCP server
web/           Next.js screener, ticker pages, research page
docs/          DECISIONS.md (145 ADRs), DESIGN, BUILD, TESTS, RESULTS, BACKLOG
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

## Status

Phases 1–5 are complete: ingest, detection, the live poller, the backtest and
statistics stack, and the serving layer. Phase 6 (a quantile model) is gated on
citing ADR 112's measurement rather than routing around it.

Open work, with reasons, is in `docs/BACKLOG.md`. Nothing leaves that file by
being forgotten.
