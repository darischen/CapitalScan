# TIMINGS

Measured durations for long jobs, so nobody starts one blind. `CLAUDE.md`
carries the headline budgets; the per-step tables, the regime notes, and the
history of every figure that turned out wrong live here.

**Read before trusting a number here.** A step has regimes, and one
measurement is one regime. Check `runs` for the distribution before quoting
or recording a figure. Every timing taken between 2026-08-24 and 2026-08-26
was measured with the [IPv6 `localhost`
bug](OPERATIONS.md#binding-the-container-ipv4-only-makes-localhost-cost-2-seconds)
live and is inflated.

Index:

- [`cscan backtest`, full universe](#cscan-backtest-full-universe)
- [`cscan nightly`, per step](#cscan-nightly-per-step)
- [How this table has been wrong](#how-this-table-has-been-wrong)
- [`cscan bars --daily --lookback`](#cscan-bars---daily---lookback)
- [`cscan indicators`](#cscan-indicators)
- [`cscan bars --hourly --backfill`](#cscan-bars---hourly---backfill)
- [`cscan universe --quarter`](#cscan-universe---quarter)

---

## `cscan backtest`, full universe

`cscan backtest --workers 8`, full universe: **~2 hours**, measured end to
end on 2026-08-26 against **1,470 tickers / 1,365,000 events** under
`a38d3ca6b58295e8`, on a machine where `localhost` connects in 40ms.

| phase | wall clock | output |
|---|---|---|
| `compute` (59 chunks x 25 tickers, 8 workers) | **81.9 min** | 1,365,000 rows |
| `finalize` (cross-ticker cofire) | **3.6 min** | 1,365,000 rows |
| `harness` (8 workers, parallel) | **35.7 min** | `harness passed` |

Compute averaged 83s per chunk. **The harness is the number that was most
wrong**: this file said 4h19m, which was single-threaded on 590 tickers.
It is 35.7 min against 2.5x the tickers -- roughly the worker count, which is
what parallelising it was supposed to buy.

- **The harness is parallel now, and this line said otherwise until
  2026-08-25.** It read "the validation harness is single-threaded and takes
  the rest regardless of worker count". That was true between `78d1e38`
  (which reverted a parallel harness that deadlocked passing frames to
  workers) and `0b2cc00`, which re-parallelised it by spooling ticker slices
  to **parquet** instead of pickling frames through a pipe.
  `research/harness.py::_run_harness_parallel` runs whenever `max_workers > 1`.
- `--phase` splits the job into `compute` (resumable, checkpointed per
  `--chunk-size`, default 25), `finalize` (cross-ticker cofire) and
  `harness`. Use the phases for anything long: `_chunk_already_done` keys on
  `(config_hash, chunk, of)`, so **keep `--chunk-size` identical across
  restarts** or every chunk re-runs.
- **Each phase writes its own `runs` row** -- `backtest_compute` (one per
  chunk), `backtest_finalize`, `backtest_harness`. The harness writes no
  *event* rows, which is a different claim; it does record its run, and
  `notes` carries the result (`harness passed`, or the failing check). That
  is the cheapest way to watch a long run without polling processes.
- **`compute` warns on every chunk that `cofire_count` is only correct within
  the chunk**, and excludes it from that write's update columns. That is by
  design: cofire is cross-sectional and a 25-ticker slice cannot see the
  other 1,445. `finalize` is the whole-universe pass that corrects it, and it
  is only correct if `compute` finished for the config.
- **Superseded history.** 2026-08-13 measured 4h55m total (write 35m50s,
  harness ~4h19m) on 627,380 rows / 590 tickers under `697f3ae71428d392`,
  single-threaded harness. Kept only so an old `runs` row can be read
  correctly; do not quote it as an estimate.
- **`runs` measures this from 2026-08-18, and did not before.**
  `cli.py::backtest` used to close its `with ingest.run_job(...)` block
  before calling `run_harness`, so `finished_at - started_at` timed the write
  phase **only** -- 20-38 min against a 2h48m-to-4h55m job. A 2026-08-09
  session read those durations as the whole job and briefly "corrected" this
  line to ~36 min. It was wrong. Session 15 moved the harness inside the
  block, so new rows time the whole job and a failing harness now records
  `status='failed'`. **Rows written before 2026-08-18 are still
  write-phase-only** and must be read that way; check `started_at` before
  quoting one.
- `cscan weekly` genuinely is ~36 min: it calls `run_backtest` and
  deliberately skips the harness (`cli.py::weekly` docstring). Do not read a
  weekly duration as a `cscan backtest` duration.
- **Never run `cscan universe --quarter` while a backtest is running.** Not
  locking -- MVCC handles that -- but determinism: workers resolving
  eligibility against a `universe` that changes mid-run produce different
  output for one config, violating ADR 060.

## `cscan nightly`, per step

**Budget 35-40 minutes for a cold nightly**, not 21. The `~21 min` total
below was never a cold total: `shares` at 0.7 min in the 08-26 column is a
cache read, and 10 minutes is its real cost at the current universe size.

**Every figure below is one measured run, 2026-08-26 evening**, on 1,470
tickers. Where a step has two regimes the normal one is given and the other
is named, because quoting the wrong regime is how this table has been wrong
three times.

| step | wall clock | rows |
|---|---|---|
| `bars_daily` (batched) | 5.0 min | 5,740 |
| `bars_hourly` (batched; loop fixed `4f97d8b`) | 5.4 min | 40,615 |
| `actions` (batched + dated key `9b008c9`) | 4.0 min | 349 |
| `market` | 0.0 min | 5 |
| `shares` | 0.7 min | 236,008 |
| `earnings` (bulk calendar `647ee25`) | **0.6 min** | 1,203 |
| `indicators` (5-day, `max_workers=1`) | 1.4 min | 5,574 |
| `events` (5-day) | 1.2 min | 3,119 |
| `path_capture` (incremental) | 1.2 min | 17,117 |
| `peak_labels` | 1.0 min | 484,929 |
| `sync` (**incremental**, COPY) | **0.4 min** | 104,669 |
| **total** | **~21 min** | |

**Measured end to end 2026-08-26 19:00 PT: 20.5 minutes**, on 1,470 tickers,
against ~4h35m two days earlier. What changed, in order of size:

| | before | after |
|---|---|---|
| `earnings` | 43.5 min | **0.6** -- the Finnhub calendar is bulk and was being asked per symbol |
| `sync` | 114.2 min | **0.4** -- incremental bound, then `COPY` instead of a dict per row |
| `bars_hourly` | 54.5 min | **5.4** -- batched fetch, then three per-ticker scans removed |
| `actions` | 21.3 min | **4.0** -- batched, and the cache key now carries a date |

**A second measured run, 2026-08-28 13:36 PT, totalling 36.8 minutes** -- the
first one Task Scheduler ran rather than a hand-typed command, on the same
1,470 tickers. Recorded beside the 08-26 column rather than replacing it,
because the two disagree on exactly two steps and only one of them is
understood:

| step | 08-26 | 08-28 | rows 08-28 |
|---|---|---|---|
| `bars_daily` | 5.0 | 3.4 | 7,192 |
| `bars_hourly` | 5.4 | 3.8 | 50,743 |
| `actions` | 4.0 | **13.0** | 348 |
| `market` | 0.0 | 0.0 | 5 |
| `shares` | **0.7** | **10.0** | 235,839 |
| `earnings` | 0.6 | 0.6 | 1,179 |
| `indicators` | 1.4 | 1.7 | 6,978 |
| `events` | 1.2 | 1.7 | 3,643 |
| `path_capture` | 1.2 | 1.0 | 20,693 |
| `peak_labels` | 1.0 | 1.0 | 490,436 |
| `sync` | 0.4 | 0.5 | 107,845 |
| **total** | **~21** | **36.8** | |

**`shares` at 0.7 min is a cache read, not a measurement.** 236,008 rows at
`RATE_LIMIT_PER_SEC = 0.5` cannot complete in 42 seconds -- that is the [cache-hit
test](OPERATIONS.md#the-fetch-cache-lies-when-you-change-what-a-key-means)
applied to a row in this table. The 10.0 min figure agrees with the note that
`shares` went to ten minutes when the universe reached 1,470, so **10 minutes
is the real cost.**

`actions` at 4.0 against 13.0 is *not* explained. Its recent history is
0.3 / 4.0 / 14.7 / 13.0, and 0.3 is the known dateless-cache-key bug. Treat
it as unresolved rather than quoting either end.

Other per-step notes:

- **`cscan sync` and nightly's sync are different commands now.** The bare
  command still copies everything; nightly passes `incremental=True`, bounded
  by the **serving store's own watermark** so a Pi that missed a night is
  caught up rather than left with a hole. A full pass is still ~7.4M rows --
  run it after a rebuild or a reflash.
- **`sync` writes via `COPY` into a TEMP staging table** (`bd8cbc5`).
  Measured 78,589 rows/s against ~1,090 for the dict path. Profiling said the
  obvious suspects were wrong: the Pi was 76% idle (load 1.11 of four cores,
  SD card 15% utilised) while the workstation held 894 MB and 53.8% of *one*
  core. It was `to_dict("records")` and SQLAlchemy re-binding, not the
  network and not the database.
- **`indicators` runs `max_workers=1` in nightly** (`cli.py`). Harmless at a
  5-day window; do not read 1.4 min as what the step costs on history.
- **`bars_hourly` at 5.4 min is `_back_adjust_hourly`, not fetching.** The
  fetch is ~15 batched requests, about 30 seconds. Predicting ~2 min was
  wrong and the remainder is real per-ticker computation.
- **Indicator chunking does not appear here and should not.** It fixed the
  full-history rebuild, where 1,462 tickers held 11 GB resident. Nightly's
  `indicators` step is a 5-day window and never touched that path.
- **Three fetchers still ask one ticker at a time.** `bars_hourly` was
  batched on 2026-08-26 (54.5 min -> ~1 min); `actions` and `earnings` are in
  `BACKLOG.md`. `bars_daily` does the whole universe in 4.9 minutes by
  batching, which is the comparison that makes the point.
- **Every per-step average recorded before 2026-08-26 was measured on ~929
  tickers.** The universe is now 1,470, so scale by ~1.6 before using an old
  figure -- `shares` went from a 2.2 min average to 10 minutes on exactly
  that change, and a stale threshold caused two false "this is stuck" alarms
  in one night.

## How this table has been wrong

Three separate ways, all on 2026-08-26. Read the failure modes before
trusting a figure.

1. **The sum was never re-added.** `bars_hourly` was batched, the row was
   updated, the total stayed at ~4h35m. A session quoted it back as fact.
   **Re-add the column when you change a row.**
2. **`actions` 21.3 min was a cache miss, and 0.3 min was the bug.**
   `fetch_actions` keyed on the bare ticker with no date, so it always hit
   and fetched nothing -- 640 tickers had zero corporate actions after
   2026-07-31. Neither number described a working step. See `BACKLOG.md`.
3. **`path_capture` 41.2 min / 6.5M rows was a post-rebuild catch-up, not a
   nightly.** The `runs` history is unambiguous: normal nights are **1-3 min
   / 10-25k rows**, and the ~41 min runs follow a full backtest, which
   creates millions of events with incomplete forward windows. Throughput is
   *higher* on those (2,627/s against 285/s), so the short runs are fixed
   overhead rather than slow work. Nothing is wrong with the step.

The shared lesson: **a step has regimes, and one measurement is one regime.**
Check `runs` for the distribution before recording a number here.

## `cscan bars --daily --lookback`

`cscan bars --daily --lookback 8000`: **11 minutes for 521 tickers /
2,002,797 rows** (measured 2026-08-25). Do **not** extrapolate from a small
sample: 10 tickers took 2m37s, which predicts 2h20m and is wrong by an order
of magnitude, because `_batch_key` puts every ticker in one `yf.download`
rather than one request each. Per-ticker timing does not scale here.

## `cscan indicators`

The slow one, and **it writes nothing until it finishes** -- results are
collected across all tickers then upserted once. Querying `indicators`
mid-run returns the pre-run count, which looks exactly like a hang. Two
working runs were killed on 2026-08-25 for that reason. Pass `--workers 8`;
it defaults to 1.

## `cscan bars --hourly --backfill`

All tickers: **~4.5-5.5 hours**. Yahoo caps hourly at 60 days per request, so
backfill walks 13 sequential windows per ticker at 0.5 req/s. No incremental
path -- already-stored data does not reduce the cost.

## `cscan universe --quarter`

One quarter: **~18 seconds** on the 1,563-ticker universe, so a full
66-quarter backfill is **~20 minutes** (measured 2026-08-26, all 66 quarters
`ok`, zero failures).

- This line has been wrong twice. It said ~10s, true at an earlier universe
  size. It was then "corrected" to ~2.6 min on 2026-08-25 -- a figure taken
  while every `localhost` connect cost 130 seconds, so it measured the IPv6
  bug rather than the job.
- Use `scripts/universe_backfill.ps1` rather than a hand-rolled loop. It
  resumes (`-StartFrom 2017Q3`), times each quarter, and collects failures
  instead of dying on one bad quarter.
- **Never run it while a backtest is running** (ADR 060, see above).
