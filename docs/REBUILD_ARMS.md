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
another full universe pass (~3 hours).

**Never run `universe_backfill.ps1` while a backtest is running.** Not
locking — MVCC handles that — but determinism: workers resolving
eligibility against a `universe` that changes mid-run produce different
output for one config, violating ADR 060.

---

## Each arm, same shape

```powershell
# a. universe, all 66 quarters (~2.6 min/quarter, ~3 hours)
.\scripts\universe_backfill.ps1          # -StartFrom 2017Q3 to resume

# b. backtest, split into phases so it is restartable
uv run cscan backtest --workers 8 --phase compute
uv run cscan backtest --workers 8 --phase finalize
uv run cscan backtest --workers 8 --phase harness

# c. statistics, so the arms are comparable
uv run cscan stats rho
uv run cscan stats cells
uv run cscan stats benchmarks
```

**Keep `--chunk-size` identical across restarts.** `_chunk_already_done`
keys on `(config_hash, chunk, of)`, so changing it re-runs every chunk.

**`--phase harness` writes nothing.** It validates an already-written
config, so it is safe to re-run and safe to defer.

**The harness is parallel** as of `0b2cc00`, which spools ticker slices to
parquet rather than pickling frames through a pipe (the deadlock `78d1e38`
reverted). No wall-clock figure exists for the parallel version at this
universe size — **measure it, do not quote the old 4h19m**.

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

## Arm 3 — drop the relative-return criterion

```python
# core/config.py, UniverseParams
required_criteria: tuple[str, ...] = (
    "crit_mcap",
    "crit_above_sma200",
    "crit_sma200_slope",
)
```

`crit_rel_return` stays **computed** and honest in the audit log; it simply
stops deciding membership. That is what `required_criteria` is for
(DESIGN §3.10), and it is how `crit_rev_growth` is already handled.

Measured at 2026-06-30: **64 names pass everything else and fail on this
alone**, including AAPL $4,250B, UNH $377B, MRK $317B, QCOM $195B, NEE
$183B, UNP $161B. Trade universe **184 → 248, +35%**.

**Two things make this arm the most interesting of the three.**

It compares against the **sector median**, so roughly half of every sector
fails it by construction — measured 440 pass, 448 fail. That is a much
heavier filter than its description suggests.

And it is a **momentum filter inside a mean-reversion study**. Requiring
three-year outperformance selects recent winners while the signal hunts
dips. Those pull against each other and the tension has never been
measured.

**One consequence to decide with it.** The `history` watch route requires
`crit_rel_return` to be `None`. Dropping the criterion from
`required_criteria` leaves that route intact, but *replacing* it with a
plain history check would make a new ticker return `False` instead, and the
route would stop firing. The config change above does the first, not the
second.

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
