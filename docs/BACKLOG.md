# Backlog

Work that is understood but deliberately not done, with the reason. An item
leaves this file by being built or by being rejected in an ADR — not by
being forgotten.

Ordered by when it blocks something, not by size.

## Open

### The deployed site is open — `SITE_AUTH_DISABLED=1` (ADR 138)

**Turned off deliberately 2026-08-20**, at the user's request and with
their reasoning recorded: a Vercel deployment URL is hard to discover, and
the content is public market data plus one operator's own analysis. No
PII, no credentials, no user accounts — there is no user model at all.

**Reaffirmed the same day when the item was reviewed.** The user's argument
is that there is no edge to protect: Bollinger Bands and the stochastic
oscillator have been studied exhaustively by people paid to do it, and
ADR 112 is the house result that no cell here survives correction. The thing
an observer would learn from this site is already public, and the part that
is not public — that the measured edge is absent — is published deliberately.

Implemented as an explicit variable rather than by weakening the default.
`SITE_PASSWORD` unset still returns 503; only `SITE_AUTH_DISABLED=1`
opens the site, and only that exact string — `0`, `true` and `yes` are all
refused, because a loose truthiness check would make `=0` mean "open",
which is the opposite of what someone typing it intends.

**What to check before deciding this is permanent.** `/api/chat` spends
Anthropic tokens per request. It is currently harmless because the route
connects to MCP on `127.0.0.1` and fails *before* any model call — an open
page, not an open wallet. That stops being true the moment MCP is
reachable remotely. If ADR 118's boundary ever moves, restore auth or
carve `/api/chat` out of the opt-out **in the same change**.

**To restore**: delete `SITE_AUTH_DISABLED` in Vercel and redeploy. The
middleware and its 38 tests are unchanged and still enforce everything.

---

### The serving store grows without bound and is at 80% of the free tier

Measured 2026-08-20: **410MB against Neon's 512MB free tier.** A "5 GB"
reading was misread earlier and briefly recorded in ADR 137 as a
correction; that has been withdrawn. 512MB is the limit.

**The real problem is not the current number, it is the direction.**
`run_sync` deliberately never deletes — the docstring says why: a bug in
the cutoff arithmetic would otherwise silently empty the served history.
But that means `ServingParams.history_years` bounds what is *sent*, not
what is *stored*. Rows that age past three years stay on serving forever,
and every nightly adds a day.

So the store only grows, from 80%, on a 512MB ceiling.

**Rough rate**: a session adds roughly 620 bars and 620 indicator rows
across the trade universe plus its events — small daily, and monotonic.
The question is months rather than days, but it has no natural stopping
point.

**What would settle it**, in increasing order of what they claim:

- **Prune on serving**: delete rows older than the cutoff after each sync.
  Straightforward, and it makes the deleted-by-mistake failure mode real
  again — which is exactly what `run_sync` avoided. Would need the delete
  scoped by the same cutoff expression the insert uses, and a test that
  the two cannot drift.
- **Narrow the window**: two years is 266MB, one year 139MB. Buys time and
  costs screener history.
- **Pay**: Neon Launch is 10GB, and full history is 2,149MB.

Not acted on: all three change what the deployed site is or how the sync
behaves, and the store is not full today.

**Reviewed 2026-08-20 and deliberately left.** The user's read is that 100MB
of headroom against a daily increment measured in kilobytes is a long
runway, and that all three fixes cost something real now to solve a problem
that is months away. Revisit when the number moves, not on a schedule.

**It moved on 2026-08-21, and the store filled.** ADR 145's rebuild took
`events` from 783,762 to 865,984 rows locally, and that night's `cscan sync`
died on the real limit rather than an estimate:

```
psycopg.errors.DiskFull: could not extend file because
project size limit (512 MB) has been exceeded
```

*The increment is not "measured in kilobytes" when a config is rebuilt.* The
entry above sized the daily nightly correctly and missed that a rebuild
resyncs the whole window at once. Any future ADR that moves `config_hash`
or widens the universe does the same.

**What was done that night**, in order, with measurements:

```
490 MB  before
308 MB  after deleting the superseded 86e91448a65aa40b (298,389 events,
        1,636 benchmarks, 448 cell_stats) + VACUUM FULL events
425 MB  after re-running sync with the current data
362 MB  after VACUUM FULL on indicators (46,489 dead) and universe (4,521)
```

**`DELETE` alone frees nothing.** It leaves dead tuples, and the size is
unchanged until `VACUUM FULL` rewrites the table. Plain `VACUUM` makes the
space reusable — enough to stop "could not extend file" — but only
`VACUUM FULL` returns it. `events` went 203 MB -> 21 MB that way.

**Neon's dashboard and `pg_database_size` disagree, and Neon's is the one
that enforces.** Postgres reported 425 MB while the dashboard showed
0.37 GB; the gap was dead tuples. Size this against the dashboard.

**Pruning superseded config hashes is the cheap recurring lever** and is not
in the three options above. The serving store had two hashes and 90% of its
events belonged to a hash nothing reads. That will be true again after every
rebuild, and unlike the three options it costs nothing anyone can see.

**Narrowing by `split_key` does not work, though it looks like it should.**
The store is 91% `holdout` (122,098 of 133,542) against 11,444 `validate`,
which suggests dropping holdout as an easy 90% cut. It is not: the serving
window opens 2023-08-22 and `holdout` opens 2024-01-02, so *most of the
window is holdout by construction*, and those are exactly the rows the
ticker page renders — `v_chart` and `v_events` deliberately carry no split
predicate (ADR 122). Dropping them would empty the charts. Statistics are
unaffected either way, since every statistical consumer hardcodes
`split_key = 'validate'`.

**Still open**: `run_sync` never deletes, so rows that age past
`ServingParams.history_years` remain forever. Pruning superseded hashes
buys time; it does not change the direction.

---

### `mcap_usd` has two bad inputs, and neither is ADR 145

Found 2026-08-22 while listing the Nasdaq additions. **Both predate ADR 145
and are unrelated to it** — verified against `universe_pre_adr145`, where
every affected row has a before/after factor of exactly 1.0.

**1. Share counts 1000x too large, from `sec_xbrl`.**

```
PKG    89,213,394,000 shares   real ~89.2 million    -> $18,933B
GRMN  198,077,418,000          real ~190 million     -> $13,875B
AAP    72,924,659,000          real ~73 million      ->  $6,459B
ALK    35,828,450,000          real ~35.8 million    ->  $2,453B
```

Exactly three orders of magnitude on every one. The raw cached facts show
why: GRMN's clean range is 190,687,357..208,077,418 and its bad values are
**208,077,418,000** — the same digits with `000` appended. A filer tagging
error in SEC's companyfacts feed, on 3 of PKG's 64 rows and 5 of GRMN's 70.
`jobs/fetch/sec.py` stores `val` verbatim, which is correct.

**This is a documented, deliberate gap — not a new finding.** The first
version of this entry claimed otherwise and was wrong.
`core.config.SharesPlausibility` predicts it precisely: *"a x1,000 error on
a company with real shares in the tens of millions (tens of billions after
corruption) now lands inside `[min_shares, max_shares]` and is accepted
undetected"*. It even enumerates the tickers — "AAP (4), GRMN (5), PKG (3),
ALK (3), FTNT (2), SWKS (2), MAA (2), and one each for AIZ, CNX, EOG, PNR,
REG" — 26 filings across 12 tickers.

The ceiling was widened 32B -> 320B knowingly, and the reasoning holds:
**rejecting good data is worse than admitting bad data here.** A rejected
genuine filing is invisible and freezes that ticker's share count forever;
a bad one surfaces as an absurd market cap. That mechanism worked — this
was found by looking at the largest market caps in the universe.

**What is new**: `BNTX` (241,521,065,000 shares, real ~240M) and `WWD`
(62,383,699,000, real ~60M) are the same class and are not in the note's
list, presumably added with the universe expansion after it was written.
BNTX produces the single worst value in the database, **$65.9T over 7
in-trade quarters**. Also confirmed: **zero** rows exceed 320B, so the
ceiling itself is doing its job.

**Impact is small but not zero**: 22 of 51,828 `universe` rows carry a
market cap above $5T, which is impossible (the largest company on earth is
about $4.8T). Six of those are `in_trade`, so they passed `crit_mcap` on a
number that is wrong by 1000x. Across 8 tickers: BNTX (7 quarters, peak
$65.9T), GRMN (5), PKG (3), AAP (3), MAA, SWKS, WWD, CNX.

**$5T is a conservative floor, not the real count.** ALK at $2.4T and NTES
at $1.7T are equally wrong and sit under it. A proper sweep needs a
plausibility bound per era rather than one constant.

**2. The ADR ratio map has one entry.**

`UniverseParams.adr_ordinary_per_adr` contains `TSM: 5.0` and nothing else.
ADR 014 records why it exists: a Form 20-F reports the issuer's **ordinary**
shares while the bar price is per **ADR**, and TSM priced at $10.5T against
an actual ~$2.1T before the correction.

The universe expansion added many depositary listings that are not in the
map and are therefore treated 1:1. NTES peaks at **$1,666.9B** against a real
NetEase peak near $100B — roughly the 1:16 ordinary-per-ADS ratio. Others
ingested and unmapped include BILI, JD, PDD, SNY, VOD, ERIC, TCOM, GRAB, LI,
FUTU, RYAAY, HTHT, ARGX, CCEP.

**Why this one is worse than it looks.** TSM cleared its threshold at either
figure, so ADR 014 could record the defect as harmless to `crit_mcap`. That
is no longer true: with the floor at $20B, an ADR genuinely worth $5B and
overstated 5x lands at $25B and enters the trade universe on a number that
was never real.

**What would settle it**

- **Guard `mcap_usd`, not `shares`** — the layer `SharesPlausibility`'s
  reasoning does not cover. Tightening the share ceiling is rejected for
  good reason (it freezes real tickers silently). But a market cap above
  any plausible bound can be set NULL at `universe` write time and logged,
  which rejects nothing at ingest, cannot freeze a ticker, and keeps the
  absurd value out of `crit_mcap` and out of `events.mcap_usd`. A NULL
  mcap already fails `crit_mcap`, so the effect is to exclude a name whose
  size cannot be measured rather than to admit it at a fabricated size.
  **This changes `in_trade` for 6 rows and needs a universe rebuild to
  take effect.**
- A relative check *inside* the band is the other option and is not as
  naive as `SharesPlausibility` rejects: the PSKY counterexample fails
  because that ticker has three filings, two of them bad. Requiring a
  minimum count of clean filings before trusting a ticker's own median
  would fire on GRMN (70 rows, 5 bad) and stay silent on PSKY.
- Populate the ADR map, or better, derive the ratio rather than hard-code
  it — `dei:EntityCommonStockSharesOutstanding` against the ADS count is
  available for most filers.

**RESOLVED 2026-08-22 by ADR 146** — inside the rebuild, as decided below.

`core.universe.scale_error_indices` catches the class by *local* shape: a
filing is rejected when it exceeds the median of its four nearest neighbours
per side by >50x **and** dividing by exactly 1,000 puts it back within 5x of
them. Tickers with fewer than 8 filings are not judged, which is what makes
it survive the PSKY counterexample that sinks a global-median test. WULF is
the other way round: 16 genuine filings up to 247x its *global* median, and
1.0-1.3x locally, so a global rule would have rejected all 16.

Swept over all 142,278 rows: **33 filings across 17 tickers**, every one
ending in `000`, zero false positives. Reproduces the hand-curated list of 26
exactly and adds 7 — including BNTX and WWD, which this file had already
identified by hand.

**Still owed: the 33 stored rows.** Ingest is fixed going forward, but
`run_shares` never re-offers a rejected accession, so they must be deleted
before the `universe` rebuild that propagates the fix.

The original decision, kept because its reasoning is what scheduled the work:

**Decided 2026-08-22, on the x1000 class: fix it when the universe is next
rebuilt anyway, not before.**

The user's challenge was the right one — if the note argues that keeping bad
data beats removing good data, why add a guard at all? The answer separates
two layers, and concedes most of the point:

*The documented asymmetry is about ingest.* Rejecting a filing is permanent
and silent, because `run_shares` never retries a rejected accession, so the
ticker freezes at its last accepted count forever. Nulling one quarter's
`mcap_usd` has neither property: the next quarter recomputes from scratch.

*But the absurd market cap is the detection mechanism.* The note says so, and
it is how both this and the original 32B-ceiling defect were found. A guard
that silently nulls $65.9T trades a loud wrong number for a quiet missing
one — the same error the note warns against, moved one layer up. **Any such
guard must log to `bar_rejects`, not merely null**, which makes it strictly
more detectable than today rather than less.

*And the economics do not justify it standing alone.* 22 rows in 51,828 is
0.04%, six of them `in_trade`. Propagating a fix means rebuilding `universe`
and re-running the backtest to push corrected membership into `events` —
hours of compute to move six rows. Do it inside the next rebuild, where it
costs nothing extra.

**The ADR ratio is the one worth fixing on its own schedule.** Unlike the
x1000 class it is not a rare filer error but a systematic units mismatch on
~14 currently-live tickers, and it produces a wrong number on every future
nightly rather than on 26 historical filings.

**Before Phase 6 either way**: a model taking `mcap_usd` as a feature would
train on a column containing 1000x outliers, which gradient boosting will
happily learn. That is also solvable at feature-engineering time with a
plausibility filter on the training query, so it does not force the rebuild.

---

### Expanding the universe beyond the S&P 500 seed

**Largely done 2026-08-21 — see ADR 143.** Nasdaq listings at or above $5B
are ingested, `min_mcap_usd` is $20B, and 541 tickers are ever `in_trade`
against 378 before. What remains open is below.

Raised 2026-08-21: the user intends to add other markets and more ETFs,
prompted by NBIS not resolving. NBIS is not excluded by a criterion -- it is
Netherlands-domiciled, so it is not an S&P 500 constituent, so it never
enters `tickers` and is never evaluated at all.

**Most of the machinery already works.** QQQ was added by hand and
participates fully: 5,280 daily bars, 66 universe evaluations, `in_trade`
true at $289B, 29,343 events. Market cap resolved without a CIK because
`shares_outstanding` already has a Yahoo fallback -- 68 of 653 tickers use
it today. The four criteria in `required_criteria` read price, SMA200,
slope and relative return, none of which mention an index.

So the work is not "support non-index names". It is deciding what the
universe *is*, and paying for the change.

**Four things bite, in increasing order of awkwardness.**

**The `config_hash` and the rebuild.** Universe definition is config (ADR
060). Broadening it moves the hash and invalidates every measured row --
another full `cscan backtest`, ~3h31m at the current population.

**ADR 035's survivorship argument does not survive hand-picking.** The S&P
union is *complete*: every historical member is present, including the ones
that failed. A ticker added because it looks interesting today is selected
on an outcome the study is trying to measure. Any expansion needs a rule
that could have been written in 2010 and applied mechanically -- "the
Nasdaq-100 union" is such a rule; "NBIS and a few others" is not.

**Relative return needs a benchmark that means something.** `crit_rel_return`
compares against the S&P series in `market_days`. For a Nasdaq name that is
defensible; for a foreign listing or a sector ETF it quietly changes what
the criterion tests.

**ETFs are not companies.** `sector` and `industry` are NULL on QQQ, and
`cell_id` is built from component columns -- so an ETF either lands in a
NULL-sector cell or needs its own dimension. `days_to_earnings` has no
meaning for one either, and ADR 041's earnings-window exclusion silently
does nothing.

**Still open, and the sharper half.** ETFs are not companies: `sector` and
`industry` are NULL on QQQ, and `cell_id` is built from exactly those
columns, so an ETF lands in a NULL-sector cell rather than being excluded.
`days_to_earnings` is meaningless for one too, so ADR 041's earnings-window
exclusion silently does nothing. **IBIT makes this concrete rather than
hypothetical** -- a spot Bitcoin trust with no sector, no industry and no
earnings date, now on the non-filer list and one `cscan bars` away from
being tradeable and uncellable at once.

**Also still open**: NYSE. The current seeds are the S&P union and Nasdaq,
so a $50B NYSE-listed name outside the index is still unreachable, exactly
as NBIS was.

**What would settle it**: decide whether ETFs get their own cell dimension
or are excluded from cells while remaining tradeable, and pick a mechanical
NYSE rule that could have been written in 2010.

---

---

### The validation harness is single-threaded and takes ~6 hours

**Measured 2026-08-21**: **3h58m35s** for 865,984 events / 543 tickers, at a
sustained 99.5% of *one* core.

**The cost is driven by tickers, not events.** A prediction of 5h58m was made
that afternoon by scaling CLAUDE.md's 4h19m linearly on event count
(627,380 -> 865,984, 1.38x). That was wrong by 47%. `_check_no_lookahead`
dominates, and it walks **bars**, not events: six passes over
`tickers x bars_per_ticker`. The universe went from 590 tickers to 543, so
`4h19m x 543/590 = 3h58m` — which is what it took, to the minute. Estimate
future runs from ticker count; event count is close to irrelevant. CLAUDE.md's "more workers do not shorten it"
is accurate — there is no pool, no chunking and no `max_workers` anywhere in
`research/harness.py`. It is structurally serial, not configurably serial.

**Where the time goes.** `_check_no_lookahead` does not inspect `events`; it
re-runs detection from scratch **six times** — a base pass, one per
`_SHIFT_LEVELS` entry `(1, 2, 5, 20)`, and a shuffled control. Each pass calls
`scan_candidates` over every ticker's bars concatenated, ~2.85M rows. The
other four checks walk the events once and are not the driver.

**The `iterrows` hypothesis above was wrong.** Profiled 2026-08-22 on AAPL,
5,248 bars, one pass, 11.3s:

```
.loc -> xs -> fast_xs      6.0s / 53%   two per-row Series extractions
comp_method_OBJECT_ARRAY   1.5s / 13%   object-dtype index scan, per row
datetimelike.__getitem__   2.5s         31,429 datetime index operations
```

`iterrows` itself barely registers. The cost was an **O(n²) scan**:
`scan_candidates` indexed the indicator frame by `datetime.date` *objects*
and evaluated `ind_group.index[ind_group.index < bar_date]` for every bar —
27.5M object comparisons per ticker per pass. **Fixed 2026-08-22** with
`searchsorted` (125x on that step); detection went ~7.5s -> 2.77s per
ticker-pass, and the projected harness **3h58m -> ~2.5h with no parallelism**.

Event sets before and after are byte-identical across AAPL, KLAC, CHRW and
NVDA — 11,686 events, zero difference either way.

**The remaining 53% is row extraction**, and it is not an indexing problem:
`.iloc[pos]` measured only 1.1x faster than `.loc[label]`. Pulling a row out
of a 39-column mixed-dtype frame as a Series costs what it costs. The lever
is issuing *one* extraction per bar instead of two — `own_ind` is looked up
only for `CLOSE_CONFIRMED_FIELDS` and could be precomputed into arrays
before the loop — not changing how either is indexed.

**Why it re-walks instead of computing all six variants in one pass.** Fusing
would remove five-sixths of the iteration overhead while keeping all 17.1M
`detect` calls, so the win depends entirely on how that 1.27 ms splits — which
has never been profiled. More importantly it would cost invariant 2: the
harness routes through `scan_candidates` precisely because that is the
production detection path, and the shift ladder's guarantee is "run the
*production* detector on shifted data and watch the event set change." A
hand-rolled fused walker validates the walker. Fusing is the last optimisation
to reach for, not the first.

**Plan, agreed 2026-08-21.** Implement only after a `cscan nightly` run has
completed; nothing edits `harness.py` while it is producing a verdict.

```
0  profile one ticker x 6 passes (cProfile) -- decides whether step 3 matters
1  split _check_no_lookahead into counts + verdict (tests first)
2  add max_workers, ticker chunking, merge; --workers on `backtest --phase harness`
3  only if the profile justifies it: itertuples / hoisted null checks in scan_candidates
```

**Two correctness details that would fail silently if missed.**

*Jaccard must aggregate counts, not average ratios.* Tickers are disjoint, so
workers return `(|A∩B|, |A∪B|)` per shift level and the parent computes
`J = Σ|∩| / Σ|∪|` once. Averaging per-chunk Jaccards yields a different number
that still looks plausible.

*The control shuffle stays global.* `_shuffled_control` mixes indicator values
**across** tickers on purpose. The parent must shuffle the whole frame and then
cut chunks from the shuffled result; shuffling inside a worker only mixes that
worker's tickers, which is a weaker control and would push `jc` toward the 0.15
floor for reasons unrelated to the engine.

**Invariant 2 is untouched** — workers call `scan_candidates` unmodified.

**Attempted 2026-08-22 and reverted — it deadlocks at production scale.**

The implementation chunked tickers and passed the sliced frames through
`ProcessPoolExecutor.map`. Correct on the fixture (four tickers, 400 bars)
and all nine equivalence tests passed. Against the real population it hung:
858 tickers, 46 minutes, **CPU frozen at 197.2s across repeated samples**,
no worker processes, no active database queries. Not slow — stopped.

**Cause: the frames are the payload.** `pool.map` pickles every argument
through a pipe. Bars for 858 tickers plus the equally large shuffled control
is roughly **2.7 GB** of DataFrames pushed through Windows pipes, which
deadlocks rather than erroring. The unit fixture is ~1 MB, so nothing about
the test suite could have surfaced this — the bug is purely a function of
size.

**The fix is to move the orchestration, not to tune the chunking.** Workers
must load their own tickers from Postgres rather than receive frames, the
way `compute._compute_one_ticker` already takes a `database_url` and opens
its own connection. `harness.py` is pure by design and must stay that way,
so the chunking belongs in `jobs/cli.py`, which owns IO: chunk tickers,
have each worker call `_load_bars_by_ticker` for its slice and then
`run_harness` serially on it, and merge with the counts machinery below.

**What was kept.** `_lookahead_counts` / `_lookahead_verdict` remain split,
with `LookaheadCounts` and its tests. That refactor is what makes any
parallel merge correct and is worth having on its own. `max_workers` was
removed from `run_harness` rather than left accepting-and-ignoring, so the
equivalence tests skip again on their signature guard until a working
implementation lands.

**Expected once fixed**: ~50 min at 8 workers, ~1h40m at 4. Note this makes the machine
*hotter*, not cooler: eight cores at full load for 50 minutes rather than one
for six hours. A `--workers` flag lets the operator trade speed for a usable
machine.

**Not an option: pruning the input.** The harness validates the population that
was actually produced. Shrinking its input to save time means the Phase 3 gate
passes on a subset while `events` ships whole — a weaker gate, not a speedup.

