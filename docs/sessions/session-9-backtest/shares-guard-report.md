# Shares-outstanding plausibility guard — report

Session 9, shares-guard task. HEAD before this work: `5613eef`.

## What I implemented

An absolute plausibility guard on every candidate `shares_outstanding` row —
SEC XBRL and the Yahoo fallback alike — inside `run_shares`
(`capitalscan/jobs/ingest.py`). A row whose `shares` falls outside
`[min_shares, max_shares]` is **dropped, never corrected**, and logged to
`bar_rejects` with `rule` = `shares_below_plausible_floor` or
`shares_above_plausible_ceiling`, `severity = "reject"`, and a `payload`
carrying the rejected value, `filed_on`, `period_end`, `source`, and (for SEC
rows) `accn`. The bounds live in a new standalone dataclass,
`core.config.SharesPlausibility` (not a `Config` field — see below).

Rejection happens per-fact, before the intra-filing `period_end`/`accn`
tie-break, so a bad value can never win that tie-break either. The
`_latest_shares` query-time selection ("latest filing with `filed_on <
as_of`") is untouched — a rejected filing simply isn't a candidate, so the
next real filing on file supplies the value, per the "reject, don't correct"
design constraint.

`run_shares` gained one new parameter: `shares_bounds: SharesPlausibility =
SharesPlausibility()`, defaulted so every existing call site is unchanged.

## TDD evidence

**RED.** New file `capitalscan/tests/unit/test_shares_plausibility.py`
written first, run before any implementation change:

```
uv run pytest capitalscan/tests/unit/test_shares_plausibility.py -v
```

Result: 5 of 8 failed for the right reason — every guard-dependent test
failed because the bad/small row was still present in `upserted` and
`rejected` (`bar_rejects`) was empty, i.e. no guard existed yet:

```
FAILED ...test_a_1e6_scaled_filing_is_rejected_and_logged - AssertionError: assert [{'ticker': 'ORCL', ..., 'shares': 4819056000000000, ...}] == []
FAILED ...test_a_1e3_scaled_filing_is_rejected_and_logged - AssertionError: assert [{'ticker': 'ALK', ..., 'shares': 35831543000, ...}] == []
FAILED ...test_an_implausibly_small_filing_is_rejected - AssertionError: assert [{'ticker': 'PSKY', ..., 'shares': 1000, ...}] == []
FAILED ...test_psky_good_filing_is_accepted_even_though_its_own_median_is_bad - assert 3 == 1
FAILED ...test_a_bad_yahoo_fallback_row_is_rejected_and_logged - AssertionError: assert [{'ticker': 'ZZZ', ..., 'shares': 500, 'source': 'yahoo_shares_full'}] == []
5 failed, 3 passed in 0.55s
```

(The 3 that passed immediately were the bounds-sanity test and the two
"should stay accepted" cases — split and normal filing — which needed no new
code to pass, as expected.)

**GREEN.** After adding `SharesPlausibility` to `core/config.py` and wiring
`_implausible_shares_reason` into both the SEC and Yahoo branches of
`run_shares`:

```
uv run pytest capitalscan/tests/unit/test_shares_plausibility.py -v
8 passed in 0.06s
```

**Full safe suite**, run once before committing, per the task's step 2:

```
uv run pytest capitalscan/tests/unit capitalscan/tests/property
733 passed in 26.18s
```

(4 pre-existing tests in `test_shares_dedup.py` and
`test_shares_ingest.py::test_one_bad_ticker_does_not_block_a_good_one` used
`shares` fixture values like `100`, `300`, `400` to exercise the dedup
tie-break — values that are themselves below the new floor and so were
correctly rejected by the new guard before the dedup logic they were meant
to test ever ran. Bumped those fixtures to plausible magnitudes
(`100_000_000` etc.) without touching the assertions' logic — the tests
still check the same tie-break behavior, just with in-band inputs.)

## Files changed

- `capitalscan/core/config.py` — added `SharesPlausibility` (standalone
  dataclass) and `DEFAULT_SHARES_PLAUSIBILITY`.
- `capitalscan/jobs/ingest.py` — added `_implausible_shares_reason`; added
  `shares_bounds` parameter to `run_shares`; guard applied in the SEC-facts
  loop and the Yahoo-fallback loop; `share_rejects` collected and written to
  `bar_rejects` via `db_io.append`, `report.rows_rejected` set, and a note
  added to `report.notes` when any row is rejected.
- `capitalscan/tests/unit/test_shares_plausibility.py` — new, 8 tests.
- `capitalscan/tests/unit/test_shares_dedup.py`,
  `capitalscan/tests/unit/test_shares_ingest.py` — fixture `value`s bumped
  above the new floor (see GREEN note above).

Not touched: `compute.py`, `backtest.py`, `UniverseParams.min_mcap_usd`, the
existing bad rows in the live database.

## The affected set, re-derived, vs. the diagnosis's 43/24

The diagnosis counted 43 `universe` rows / 24 tickers by filtering
`universe.mcap_usd > 5e12` — a downstream, price-multiplied, single-threshold
view. I re-derived directly against `shares_outstanding.shares` with the
guard's own bounds:

```sql
SELECT count(*) AS total, count(*) FILTER (WHERE shares < 1000000 OR shares > 32000000000) AS would_reject
FROM shares_outstanding;
--  73232 | 135

SELECT count(DISTINCT ticker) FROM shares_outstanding
WHERE shares < 1000000 OR shares > 32000000000;
--  82
```

**135 rows across 82 tickers** — roughly 3x the diagnosis's row count and
3.4x its ticker count. Differences and why:

1. **The diagnosis's 43 is a strict subset of the ceiling side.** Every
   diagnosis row is a x1e3/x1e6-scaled filing that happened to make
   `universe.mcap_usd` exceed $5T once multiplied by price. That is a
   *downstream, price-dependent* filter — a bad filing whose ticker traded
   at a lower price, or whose bad filing was never selected as
   "latest-before-`as_of`" for any `universe` row's `as_of` date, never shows
   up in the diagnosis's list even though the underlying `shares` value is
   just as wrong. Filtering `shares_outstanding` directly, independent of
   price and of whether that filing ever got selected downstream, surfaces
   every bad filing, not just the ones that happened to blow past $5T.
2. **A floor side the diagnosis never looked for.** The diagnosis's method
   (`mcap_usd > 5e12`) can only ever catch *inflated* shares, never
   implausibly *small* ones — a tiny share count times a real price never
   reaches $5T, it produces an absurdly *small* market cap instead, which
   the diagnosis's own threshold is blind to. My scan found 39 rows with
   `0 < shares < 1,000,000` (placeholder-shaped values like `1`, `100`,
   `1000`, `12345`) plus 37 rows with `shares = 0`, all `sec_xbrl`, none of
   which the diagnosis's query could have surfaced. (The 37 zero-share rows
   sit outside my guard's own floor test trivially — `0 < min_shares` — but
   are worth flagging to the controller for the cleanup step; the guard
   rejects them the same way it rejects `1` or `100`.)
3. **A specific miss on the ceiling side too**: PKG, AAP, MAA, REG, PNR all
   have x1,000-scaled filings in the 7.2e10-9.9e10 range (e.g. PKG
   2023-05-03: `89,932,185,000` against a real ~90M) and ALK has three
   x1,000-scaled filings at ~3.58-3.60e10 (real ~35.8M) — all below the
   diagnosis's $5T *mcap* cutoff for whatever price ALK/PKG/etc. traded at
   historically, but well above my `max_shares` ceiling on `shares` itself.
   PKG appears in the diagnosis's list (at a different `filed_on`/quarter
   than the ones I found — the diagnosis's PKG rows are ones that did clear
   $5T mcap; mine are additional PKG filings that didn't clear $5T but are
   still corrupted shares). AAP and MAA are in both lists, partially, for
   the same reason. ALK, REG, PNR, AMCR, ANF, CMG, CPWR, CRM, CSX, CTVA,
   DXC, ETN, FOX, FOXA, FTI, ICE, LH, LIN, PCG, PSA, PSKY, QRVO, RMD, SW,
   TRIP, VTRS, and the BRK-B 2010-08 through 2011-05 rows are **not** in the
   diagnosis's 24 tickers at all.

The full 135-row list is reproducible on demand with the query under
"SELECT for the controller" below.

## The plausibility test: bounds and reasoning

`core.config.SharesPlausibility`:

```python
min_shares: int = 1_000_000
max_shares: int = 32_000_000_000
```

**Why absolute, not relative to the ticker's own history.** The obvious
first design — reject a filing that deviates from this ticker's own median
or its nearest-neighbor filing by more than some multiple — fails on PSKY.
PSKY has three known filings: two bad (`1,000` shares each) and one genuine
(`1,071,666,977`, its most recent). Both the median (`1,000`, dominated by
the two bad rows) and the nearest-neighbor comparison (the genuine row's
only neighbors are the two bad ones) flag the *genuine* row as the outlier
and would reject it — exactly backwards. Any criterion built from a
ticker's own measured data inherits that data's own corruption; this is the
project's stated lesson ("never set a criterion to the system's own
measured output") and PSKY is the concrete case that breaks a
relative-only design. So the bounds here are set from external facts about
the US equity market, not from anything in `shares_outstanding` itself.

**`min_shares = 1,000,000`.** Every confirmed-genuine row in the live
73k-row table sits above 1,000,000; every row I inspected below that line
was placeholder-shaped (`1`, `100`, `1000`, `12345`, `13001`, `25000`,
`27962`, `87846`, ...) — a filer cover-page defect, not a real
coincidentally-tiny issuer. Berkshire Hathaway is the standing "smallest
real share count a mega-cap can have" edge case (Class A runs in the
hundreds of thousands), which is why the floor sits at 1,000,000 rather
than, say, 10,000,000 — it keeps headroom for a legitimately low-float name
without weakening the guard against any bad value actually observed.

**`max_shares = 32,000,000,000`.** Set from the highest genuine share count
on file — Citigroup pre-2011-reverse-split, `29,206,440,560` shares
(2011-05-05 filing) — with roughly 10% headroom. The next value up on file
is confirmed-bad: Alaska Air's three 2011 filings at ~35.8-36.0 billion
(real ~35.8 million, x1,000). 32 billion sits ~12% below the lowest
confirmed-bad value found and ~10% above the highest confirmed-genuine one.

**PSKY specifically.** `1,071,666,977` clears `min_shares` by three orders
of magnitude and sits far below `max_shares`, so the guard accepts it
without needing any per-ticker context; its two `1,000`-share filings are
rejected by the same floor as any other placeholder-shaped value, using
nothing but the absolute bound. This is the direct locked-in regression
test (`test_psky_good_filing_is_accepted_even_though_its_own_median_is_bad`
in `test_shares_plausibility.py`).

**Legitimate splits.** NVDA's 2024 10:1 split (~2.5B -> ~24.5B) and AAPL's
2020 4:1 split (~4.3B -> ~17.1B) both stay inside `[min_shares, max_shares]`
before and after — the guard evaluates each filing's absolute plausibility
independently, never a ticker's own delta between filings, so a real 10x (or
even a hypothetical 20x) jump is never mistaken for a x1,000/x1,000,000
scale defect. Locked in by
`test_a_legitimate_10_for_1_split_jump_is_accepted`.

**Known limit, stated plainly.** `min_shares` and `max_shares` are close
together on a log scale (32B sits between 29.2B genuine and 35.8B
corrupted) because a x1,000 filer error on a company whose real share count
is in the tens of millions lands in the same absolute neighborhood as a
genuine mega-cap's real count in the billions — the corruption factor and
the market's own genuine range overlap right at that boundary. Concretely:
a hypothetical x1,000 error on a company with ~15-20 million real shares
(15-20 billion after corruption) would **not** be caught by `max_shares`
alone — nothing in the 73k-row scan found an instance of exactly that
shape, but the guard cannot rule one out structurally, and this is an
honest gap, not an oversight. `min_shares` has no equivalent ceiling-side
gap (every real value found is orders of magnitude above it, every bad
value found is far below it) with one exception: two BRK-B rows
(`filed_on` 2009-11-06: `1,056,884`; 2010-03-01: `1,103,764`) sit just
*above* `min_shares` yet are themselves implausible — BRK-B traded around
$66/share in that window, and `1.1M shares x $66 ≈ $73M` is nowhere near
Berkshire's real ~$150B+ market cap at the time. This looks like a
different, real defect (the DEI fact likely reports Class A's share count
under the BRK-B ticker/CIK for those two filings) rather than the
x1,000/x1,000,000 shape this guard targets, and it is not caught by an
absolute band alone. It's called out here for the controller's cleanup
step, not silently left out of this report.

## Where the thresholds live, and the observed `config_hash`

`SharesPlausibility` is a **standalone dataclass in `core/config.py`, not a
field of `Config`** — same pattern as `SweepParams`. Nothing it gates
affects a backtest's signal, exit, or cost behavior, so it has no reason to
be part of the hashed config, and folding it into `Config` would have
changed `config_hash` for every existing config for a value with zero
behavioral effect.

Verified: `config_hash(Config())` is unchanged.

```
uv run python -c "from capitalscan.jobs.config import config_hash; from capitalscan.core.config import Config; print(config_hash(Config()))"
22df3117b890793b
```

Matches the hash noted in the task brief exactly — the Postgres GUC the
user set from it does not need to move.

## SELECT for the controller (separate cleanup step)

Every row the guard would now reject, with which bound it trips:

```sql
SELECT
    ticker,
    filed_on,
    shares,
    source,
    CASE WHEN shares < 1000000 THEN 'shares_below_plausible_floor'
         ELSE 'shares_above_plausible_ceiling' END AS would_be_rule
FROM shares_outstanding
WHERE shares < 1000000 OR shares > 32000000000
ORDER BY ticker, filed_on;
```

135 rows, 82 distinct tickers. (`shares = 0` rows are included under
`shares_below_plausible_floor`, `0 < 1_000_000`.) This is read-only research
for this task — no rows were deleted, corrected, or otherwise modified in
the live database, per the task's explicit "not in scope."

## Self-review

- **Reject, not correct** — confirmed: `_implausible_shares_reason` only
  ever returns a rule string or `None`; nothing in the diff computes or
  applies a scale factor.
- **Logged, not silent** — confirmed: every rejected row (SEC or Yahoo)
  lands in `share_rejects`, gets `run_id` stamped, and is written via
  `db_io.append(engine, "bar_rejects", share_rejects)`, matching the
  existing `reject()`/`bar_rejects` pattern in `validate_bars`.
- **No magic numbers outside `core/config.py`** — confirmed: `1_000_000`
  and `32_000_000_000` exist in exactly one place
  (`SharesPlausibility`); `ingest.py` only ever reads
  `bounds.min_shares`/`bounds.max_shares`.
- **`core/config.py` stays dataclasses-only** — confirmed: no new import
  added to that file; `SharesPlausibility` uses the same
  `@dataclass(frozen=True)` the rest of the module uses.
- **`config_hash` unaffected** — confirmed by direct measurement above,
  not just by construction.
- **Windows spawn / no side effects on import** — confirmed: the new
  function and dataclass are pure definitions, no module-level IO, no
  change to `run_shares`'s existing `if __name__ == "__main__":`-guarded
  call sites (there are none inside `ingest.py` itself; entry points live
  in `jobs/cli.py`, untouched).
- **One check I verified by hand and initially got wrong**: my first
  candidate ceiling (5e10 / 50 billion) looked generous but actually missed
  ALK's ~35.8B bad rows entirely (35.8B < 50B) — caught only by directly
  querying the live table for rows between 1e10 and 5e10 and finding ALK's
  three bad filings sitting comfortably under that ceiling next to AAPL,
  AMZN, BAC, GE, GOOG/GOOGL, NVDA, and TSM's genuine large counts. Tightened
  to 32B specifically because that's where the real/corrupted bands split
  in the actual data, while keeping the *rule itself* justified from the
  external Citigroup/Alaska-Air facts, not from an automated best-fit
  against the table.
- **Not verified / left to the controller**: I did not check whether any
  ticker outside this scan's 629-name universe, or any future filer, could
  plausibly report between 20M and 32B shares in a way that lands inside
  the gap described above (a x1,000 error landing undetected). This is the
  stated structural limit, not a new finding, but I have not attempted to
  quantify how likely it is to occur again.

## Issues or concerns

- The two-tests-touched files (`test_shares_dedup.py`,
  `test_shares_ingest.py`) had their fixture `shares` magnitudes bumped
  from arbitrary small integers to plausible ones. This is a mechanical,
  non-behavioral change to those tests' inputs — the assertions and the
  behavior under test (dedup tie-break, one-bad-ticker-does-not-block)
  are unchanged. Flagging it explicitly since it touches files outside my
  primary scope (`ingest.py` / `core/config.py`), though both are unit
  tests, not `compute.py`/`backtest.py`.
- `docker-compose.yml` and `capitalscan/jobs/cli.py` show as modified in
  `git status` from before this session started (per the task's initial
  git status) and were not touched by this work; `scripts/universe_backfill.ps1`
  and `universe_backfill.log` are untracked files from something else
  entirely. None of these are part of this commit.
- The known gap (a x1,000 error on a ~15-32M-real-share company landing
  undetected) is real and inherent to an absolute-bounds-only design, not a
  bug in this implementation. If the controller wants tighter coverage
  there, the next lever is a second, defense-in-depth check at
  `run_universe` in `compute.py` comparing the computed `mcap_usd` against
  the ticker's own trailing history — explicitly out of scope for this
  task per the brief ("Do not touch ... `compute.py`").
