# The three rebuild arms

**Decided 2026-08-25.** Three separate rebuilds rather than one, so each
change is attributable rather than confounded with the other two.

Run them in the order below and **run the production arm last**, for the
reason in "The overwrite problem".

---

## Before any arm

These are prerequisites, not part of an arm. They run once.

```powershell
# 1. indicators — 546 tickers, the long pole
$ind = Get-Content "<scratchpad>\ind_list.txt"
uv run cscan indicators --tickers $ind --lookback 8000 --workers 8

# 2. shares — no --workers; SEC rate-limits, serial is correct
uv run cscan shares
```

**`cscan indicators` writes nothing until every ticker finishes.** Results
are collected across all tickers and upserted once, so querying
`indicators` mid-run returns the pre-run count and looks exactly like a
hang. Two working runs were killed for that reason on 2026-08-25.

**Order is load-bearing: shares before universe.** ADR 143 records what
happens otherwise — `cscan universe` succeeded across 66 quarters with 296
rows carrying a NULL market cap, every new ticker failing `crit_mcap` for
want of `shares_outstanding`, no error and no warning.

---

## The overwrite problem

`universe` is `PRIMARY KEY (ticker, as_of)` with **no `config_hash`
column**, so two configs' membership cannot coexist. Evaluating a second
config overwrites the first, row for row.

`events` does carry `config_hash`, and ADR 122 stamps `in_trade` and
`in_watch` onto each event at creation, so **an arm's membership survives
inside its events**. That is what makes the comparison possible at all.

But the poller builds its ticker list from `universe.in_trade`, and
`v_universe` feeds the site. So after any arm runs, live membership is that
arm's. **Whichever arm should serve must run last**, or be restored with
another full universe pass (~20 min).

**Never run `universe_backfill.ps1` while a backtest is running.** Not
locking — MVCC handles that — but determinism: workers resolving
eligibility against a `universe` that changes mid-run produce different
output for one config, violating ADR 060.

---

## Each arm, same shape

```powershell
# a. universe, all 66 quarters (~18s/quarter, ~20 min)
.\scripts\universe_backfill.ps1          # -StartFrom 2017Q3 to resume

# b. backtest, split into phases so it is restartable
uv run cscan backtest --workers 8 --chunk-size 24 --phase compute
uv run cscan backtest --workers 8 --phase finalize
uv run cscan backtest --workers 8 --phase harness

# c. statistics. Every command needs --config-hash; cells and benchmarks
#    need both splits, and NEVER holdout.
uv run cscan stats rho        --config-hash <hash>
uv run cscan stats cells      --config-hash <hash> --split-key train
uv run cscan stats cells      --config-hash <hash> --split-key validate
uv run cscan stats benchmarks --config-hash <hash> --split-key train    --workers 8
uv run cscan stats benchmarks --config-hash <hash> --split-key validate --workers 8
```

**Pass `--workers 8 --chunk-size 24`. The CLI defaults are `1` and `25`
and are deliberately left alone**, so a bare `cscan backtest` runs serially.
Changing the `--chunk-size` default would silently invalidate every
in-progress rebuild: `_chunk_already_done` keys on `(config_hash, chunk,
of)`, so a different value re-runs every chunk rather than resuming.

**8 workers, not 16.** This machine is an **AMD Ryzen 7 3700X: 8 physical
cores, 16 logical**. The second logical processor on each core shares that
core's execution units and L1/L2 cache, so SMT pays off when threads stall
on memory *latency* and pays off least when they are already saturating
memory *bandwidth* -- which is what pandas frame work does. Eight is one
worker per physical core.

This line said 14 on 2026-08-27, from reading "16 logical" and treating
them as sixteen independent cores. It was never measured. **Whether 10-12
beats 8 here is an open empirical question**, and the way to settle it is
one `--phase compute` run per setting against the same config, comparing
the `backtest_compute` rows in `runs`. Until someone does that, 8 is the
defensible number and the 81.9-minute baseline was measured at it.

**`--chunk-size` should be a multiple of `--workers`.** The pool submits one
ticker per worker, so 25 across 8 runs waves of 8, 8, 8 and then **1** --
the last wave leaves seven workers idle, once per chunk for 59 chunks. 24
is three clean waves. The gain is the ragged tail only, so expect a few
percent, not the 20% an unaligned-to-aligned comparison might suggest.

**Per-chunk timings are too noisy to compare in small numbers.** Measured
across the 59 chunks of the 2026-08-26 run: **min 36.1 s, mean 83.2 s, max
148.0 s, standard deviation 29.9 s**. Chunks differ in how much history
their tickers carry, so a handful of them says nothing about a run.

On 2026-08-27 four chunks averaging 93.2 s were read as a 16% regression
against the 83.2 s mean. With sd = 29.9 the standard error at n=4 is ~15 s,
so a 10 s gap is inside one standard error -- and the comparison was
structurally invalid anyway, because `--chunk-size 24` versus 25 puts
different tickers in "chunk 1". Normalised per ticker the first chunk was
*faster* and the next three slower, which is what noise looks like.

**Measured again 2026-08-27, whole phase this time: 90.3 min over 57
chunks / 1,294,680 rows**, against 81.9 min over 59 / 1,365,000. Per row
that is 4.19 ms against 3.60 -- **~16% slower, and real**: at n=57 the
standard error is 4.0 s and the gap is ~12 s per chunk, about three of
them. (57 not 61 because four chunks from an aborted attempt shared
`of=61` and were correctly skipped.)

**Most of that is a cost added on purpose.**
`db_io.fill_event_sector_and_mcap` runs two `UPDATE`s per chunk, and the
same work as a one-shot migration took 15m57s across both databases -- so
roughly 8 minutes for research alone, spread over 61 chunks. The rest is
wider rows: `sector` and `mcap_usd` now carry values where they were NULL,
and `bb_mid`/`close`/`vix_pct_252d` will add three more.

Budget ~96 min for compute, not 81.9. It buys a training frame that no
longer has to join four tables to see what an event looked like.

**Compare whole phases, never chunk samples.** The `backtest_compute` rows
in `runs` sum to the phase; that sum is the only figure worth quoting.

**`stats benchmarks --workers 8` is the largest statistics win.** The 200
random-entry replications are independent by construction, each seeded on
`(config_hash, replication)`, and were running one at a time. Measured
2026-08-27 on validate: **15.6 min serial, 3.99 at 8 workers, 4.39 at 4**,
output verified byte-identical to serial on every arm and replication.

It does **not** help below ~40 replications -- 24 measured 109.7 s serial
against 109.5 s on eight workers, because the 35 s per-worker setup is the
whole job there. Leave ADR 061's `--replications 50` sweeps serial.

**Keep `--chunk-size` identical across restarts.** `_chunk_already_done`
keys on `(config_hash, chunk, of)`, so changing it re-runs every chunk.

**`--phase harness` writes no *event* rows.** It validates an
already-written config, so it is safe to re-run and safe to defer. It does
write a `backtest_harness` row in `runs`, with the verdict in `notes`
(`harness passed`, or the failing check) -- which is the cheapest way to
watch a long run without polling processes.

**Measured 2026-08-26**, on 1,470 tickers / 1,365,000 events under
`a38d3ca6b58295e8`, after the IPv6 fix made `localhost` connect in 40ms:

| step | wall clock |
|---|---|
| universe, 66 quarters | ~20 min |
| `--phase compute` (59 chunks x 25, 8 workers) | 81.9 min |
| `--phase finalize` | 3.6 min |
| `--phase harness` (8 workers) | 35.7 min, `harness passed` |
| **per arm, before statistics** | **~2h20m** |

So the three arms are roughly **7 hours**, not the 18-24 this file first
said. That estimate came from CLAUDE.md's 4h19m harness figure, measured
single-threaded on 590 tickers before `0b2cc00`. Statistics (`rho`,
`cells`, `benchmarks`) remain unmeasured and are additional.

**The harness is parallel** as of `0b2cc00`, which spools ticker slices to
parquet rather than pickling frames through a pipe (the deadlock `78d1e38`
reverted). That is what makes 35.7 min possible against a figure of 4h19m
recorded when it ran single-threaded on 590 tickers.

---

## Arm 1 — NYSE at the current definition

**`config_hash a38d3ca6b58295e8`.** No config edit needed; this is what is
checked in.

The population change is the 531 NYSE tickers, taking the universe from
1,032 to 1,563 names. Nothing about the criteria moves.

This is the baseline the other two are measured against.

---

## Arm 2 — a flat base counts

```python
# core/config.py, UniverseParams
sma200_slope_min: float = -0.01
```

Admits a name whose 200-day average has fallen less than 1% over 60
sessions. Measured at 2026-06-30 on the pre-NYSE universe:

| floor | extra tickers |
|---|---|
| −0.01 | +37 |
| −0.02 | +74 |
| −0.05 | +167 |

**`>` versus `>=` is not this change.** Not one of 909 tickers had a slope
of exactly 0.0 — it is a float ratio — so relaxing the comparison admits
nobody. Only a negative floor does anything.

---

## Arm 3 — drop the sector-median test, keep the history gate

**Corrected 2026-08-28 (user's decision). The previous version of this
section specified dropping `crit_rel_return` entirely; that was wrong, and
the reasoning below is why.**

```python
# core/universe.py, health_criteria
"crit_rel_return": None if bars < 757 else True,
```

`required_criteria` is **unchanged** — all four stay. What changes is what
the fourth one tests.

**ADR 014 defines the criterion as two independent things**: "trailing
3-year total return above the sector median". That is a **history
requirement** (757 daily bars, roughly three years of sessions) *and* a
**relative-performance test**. Dropping the whole criterion discards both.
This arm drops only the second.

**Why the median test is the part worth removing.** It compares against
the sector median, so **roughly half of every sector fails it by
construction** — measured 440 pass, 448 fail. And it is a *momentum filter
inside a mean-reversion study*: requiring three-year outperformance selects
recent winners while the signal hunts dips. Those pull against each other,
and that tension has never been measured. That is the experiment.

**Why the history gate is worth keeping.** It is not a judgement about a
company, it is a statement that there is not enough data to judge one. GE
Vernova was spun out of GE in April 2024 with 603 bars: its three-year
return is **undefined, not bad**. Relaxing that is a different change with
a different justification, and mixing the two into one arm makes the result
uninterpretable.

**`None`, never `False`, below 757 bars.** This is the whole implementation
risk. `core.universe._cmp` already returns `None` when either side is NaN,
which is exactly why `crit_rel_return` is `None` for a new ticker today,
and `watch_reason` keys on that at `universe.py:369`:

```python
if above is True and rel is None and bars < min_bars_for_rel_return:
    return WATCH_HISTORY
```

Write the criterion as a plain `bars >= 757` boolean and a new ticker
returns `False` instead of `None` — ADR 149's `history` watch route stops
firing and the watch universe silently loses that half of its purpose.
Returning `None` keeps the route intact. **A test must pin this**, because
both spellings look correct and only one preserves the route.

**What the superseded "drop everything" variant would have done**, measured
at 2026-06-30 and kept because it is the comparison that makes the case:

| | effect |
|---|---|
| +64 names failing on `crit_rel_return` alone | AAPL $4,250B, UNH $377B, MRK $317B, QCOM $195B, NEE $183B, UNP $161B |
| trade universe | 184 → 248, +35% |
| **watch universe** | **45 → 36** |

That last row is the problem. All **9** `history`-route names graduate to
`in_trade` at once, because nothing is left to hold them back — the watch
universe loses a fifth of its population and the route loses its reason to
exist. The 36 `pullback` names are unaffected either way; they fail
`crit_above_sma200`, which no variant touches.

---

## What to record per arm

For each, into `RESULTS.md`:

- `config_hash`, and the wall clock for each phase
- trade and watch universe size at 2026-06-30
- event count, and `n_eff` on the cell grid
- cells surviving FDR, and the minimum q-value

ADR 112 found **zero cells surviving FDR** on the pre-NYSE population with
a minimum q of 0.706. Each arm is a different population, so each is a
genuine re-test rather than a cosmetic re-run. Publish whatever they say.
