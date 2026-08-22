# Session 21 — ADR 146, and the rebuild's measurement

**Written 2026-08-22 as a resume point.** Stage 6 closed the rebuild that
sessions 19 and 20 set up; ADR 146 came out of chasing its residue.

---

## The rebuild finished, and ADR 112 held a fifth time

| | value |
|---|---|
| harness | **PASS** on all five checks, **48m21s**, 864,133 events / 858 tickers |
| path backfill | 14m45s |
| cells | 48 scored train, 28 validate — **zero survive FDR** on either |
| min q | 0.6400 train, 0.7317 validate |
| universe | 51,837 rows, **6,299 in_trade**, 543 tickers |

The scored denominator is 48/28 in this and all three prior measurements, so
the comparison is like-for-like rather than a shifting grid. Full write-up in
`RESULTS.md`.

**The harness ran in 48m21s against 3h58m35s last rebuild**, on a job of the
same size (864,133 events vs 865,984). That is the parquet-spooling parallel
path, not a smaller job.

**`entry_price` means one thing again.** 1,807 out-of-trade events, **every
one unpriced**, all from a single `cscan events` run. Before the
single-writer fix, 755 out-of-trade events carried a price and 1,324 `path`
rows were built from them. The four-module allowlist in
`test_events_in_trade_filter.py` is true for its stated reason again rather
than by accident.

---

## ADR 146 — the x1,000 share-scale class

**Found by chasing the residue.** After the rebuild, 14 universe rows still
carried impossible market caps between $5T and $6T — under the
`McapPlausibility` ceiling, which cannot be lowered without threatening AAPL
at $4.25T. Three were `in_trade`.

**The first two things I believed were wrong, and both were cheap to check.**

- *"The bad filings are the same digits with `000` appended, so an exact-twin
  test finds them."* True for GRMN, false for almost everything else. It was
  a coincidence of the four rows I happened to look at first.
- *"A cross-sectional bound on `max/p99` separates them."* The measured
  distribution said p90 = 16 and max = 24.87 — but **24.87 *is* SWKS**, one
  of the corrupt rows. The statistic was contaminated by the values it was
  being used to find. Excluding the known-bad tickers, the clean max is
  **5.50**, and the corrupt set starts at 10.4.

**The root cause is not splits and not market cap.** 33 SEC XBRL filings are
stored ×1000. `jobs/fetch/sec.py` stores `val` verbatim, which is correct —
this is a filer tagging error in SEC's own companyfacts feed.

**The fix that does not work, and is already written down.**
`SharesPlausibility`'s docstring rejects a test relative to a ticker's own
history and gives the counterexample: PSKY's median **is** the corruption
(two of three filings are a placeholder `1,000`), so a median test flags its
one genuine filing. That argument stands.

**What separates them is locality.** `core.universe.scale_error_indices`
compares each filing to the median of its four nearest neighbours per side,
and flags it only when it exceeds that by >50× **and** dividing by exactly
1,000 puts it back within 5× of them. Under 8 filings, it declines to rule.

Two counterexamples fall out structurally rather than by tuning:

- **PSKY** has 3 filings, so the gate excludes it rather than out-voting it.
- **WULF** is the one a *global* test gets backwards: TeraWulf diluted from a
  tiny base, so 16 consecutive **genuine** filings sit up to 247× its global
  median. Locally each is 1.0–1.3×, so no window looks at it twice.

**Verified over all 142,278 live rows: 33 filings, 17 tickers, every one
ending in `000`, zero false positives.** It reproduces the docstring's
hand-curated list of 26 exactly and adds 7 ingested later, two of which
(BNTX, WWD) `BACKLOG.md` had already confirmed by hand.

`config_hash` does not move — `SharesPlausibility` is not a `Config` field.

---

## What is deliberately NOT done

**The 33 stored rows are still in `shares_outstanding`.** Ingest rejects the
class from now on, but `run_shares` never re-offers a rejected accession, so
they need a one-off delete: `scripts/adr146_clear_scale_errors.sql`, whose
flagged set is verified identical to the Python detector.

**Do not run it on its own.** Deleting the rows without rebuilding leaves
`shares_outstanding` disagreeing with the `universe` computed from it, and
nothing downstream reports the mismatch. The script header carries the full
sequence and timings, including the `--chunk-size` warning and the mandatory
stale-event sweep.

**Why it was deferred.** `BACKLOG.md`'s own decision was *"hours of compute
to move six rows — do it inside the next rebuild, where it costs nothing
extra."* Stage 6 was already past its compute phase, so folding it in here
meant redoing exactly the expensive part that decision rules out. It also
runs against the user's stated objection to the machine being tied up and hot
for hours.

**Expected effect when it does run:** the five remaining universe rows above
$5T resolve, one of which (AAP 2011-12-31, $5.04T) is `in_trade`. Six
`in_trade` quarters total passed `crit_mcap` on a ×1000 number.

---

## Still open

- **The ADR ratio map has one entry** (`TSM: 5.0`). This is the more damaging
  of the two share defects — systematic on ~14 live tickers rather than a
  rare filer error — and it produces a wrong number on every future nightly.
  Unchanged in `BACKLOG.md`.
- **`cscan nightly` is manual and was not run today.** No poller session file
  for 2026-08-22 and no scheduled task registered.
- **Phase 6** is gated on citing ADR 112's measurement rather than routing
  around it. This rebuild is that measurement.

---

## The recurring lesson, now four times

Three times in session 20 and once here, a confident conclusion came from a
broken instrument: a PowerShell filter that could not see spawn workers, a
`cd` that persisted into a later glob, and now a percentile contaminated by
the outliers it was measuring. Each time the reading was surprising and each
time the tool was the problem.

**Verify the instrument before acting on a surprising reading.** In this
session that cost two queries and caught both errors before either reached
code.
