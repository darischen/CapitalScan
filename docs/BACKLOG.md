# Backlog

Work that is understood but deliberately not done, with the reason. An item
leaves this file by being built or by being rejected in an ADR — not by
being forgotten.

Ordered by when it blocks something, not by size.

**Closed 2026-08-19**: the poller's four-hour timestamp offset (ADR 127,
1,752 rows corrected), today's live candle (ADR 128, `bars_live`), the
live price reading a stale `quotes_live` row (ADR 131), the `/chat` and `/`
date disagreement (ADR 132, `grain` argument), the ticker history's
blank outcomes (ADR 133, `v_ticker_events`), and the live price outliving
the session (ADR 134, `market_is_open()`). All are recorded in
`DECISIONS.md`; they are gone from here rather than marked done, because a
backlog of finished work is a changelog wearing the wrong name.

**Removed 2026-08-19**: the Neon sync item. It asserted that `events` at
14.6M rows does not fit the free tier, which read the local row count as
the sync payload. ADR 053 specifies a subset and the measured subset is
157,915 rows, inside that ADR's own estimate. The sync job is unbuilt, but
nothing about it is undecided, so it is a deployment task rather than a
backlog item. ADR 053 carries the re-verification.

---

## 1. ~~Make `in_trade` fail closed (ADR 129)~~ — done 2026-08-20

Kept for one release as the record of what it cost, then deleted.

Flipped, rebuilt, and verified. `cscan events` 2h54m / 1,313,204 rows;
`cscan backtest --workers 8` 3h31m / 557,012 rows, harness passing all five
checks. 55,986 stale rows from the superseded 2026-08-16 run deleted after
a decision (`run_backtest` upserts and filters, so it never emits an
ineligible row and cannot remove one).

```
entry_kind   in_trade   fail_open
next_open     139,253       0
touch         139,280       0
touch_5m      139,253       0
touch_30m     139,253       0
```

`cell_stats` digest moved `d4df6c3d...` -> `7ad6eb4c...`, as expected.

**ADR 112 re-established rather than assumed:**

| split | cells | suppressed | survives FDR | min q |
|---|---|---|---|---|
| train | 56 | 8 | **0** | 0.7604 |
| validate | 56 | 28 | **0** | 0.7061 |

448 cells, 248 suppressed, 0 surviving. Every reported cell's interval
still contains its baseline. Train's minimum q moved from 0.849 toward
significance and landed nowhere near it.

## 2. ~~The benchmark record and the database disagree~~ — resolved 2026-08-20

**The arms are deterministic. `RESULTS.md`'s table is stale.**

Re-run under the live config on corrected membership. The database's
numbers reproduce **exactly**:

```
                       08-16 run    08-20 run    RESULTS.md
validate signal pooled   +12.63%      +12.63%      -10.10%
validate null 97.5th      +6.36%       +6.36%       +3.02%
```

The backlog's own test settles it: "If they reproduce, `RESULTS.md`'s table
is stale... If they do not, something in the arms is non-deterministic,
which is a much larger finding." They reproduce to the cent, twice, across
a universe rebuild. `RESULTS.md`'s figures were measured on 2026-08-13
under config `1835688bf7d760ba`; the live config is `86e91448a65aa40b`.

**Do not read the validate line as an edge.** The pooled signal arm sits
above its randomization null on validate and **below** it on train
(+81.68% against a null 97.5th of +216.70%), and the breadth_high split
disagrees in sign on validate (-6.45% against +23.61%). One uncorrected arm
comparison on one split is not the cell grid, and the cell grid is where
ADR 112 measured zero cells surviving FDR correction.

**What remains**: `RESULTS.md` still prints the 08-13 table without its
config hash beside it. Re-measuring it is Session 18.4's job, not a
backlog item — the record should state which config produced each number
rather than being edited to match the newest run.

---

## 3. ~~A delisted ticker passes the health filter forever~~ — fixed 2026-08-20 (ADR 135)

`core.universe.evaluation_max_age_days` derives a floor from
`rebalance_freq`, which had no consumer until this change.
`_latest_indicator_row` gained `AND i.ts > :floor`.

**The rebuild did not fix it on its own**, and that is the part worth
keeping. `db_io.upsert` never deletes, so the re-run declining to evaluate
AET left its old rows in place with their old claims — the same shape as
the 55,986 stale backtest rows earlier the same night. 30 rows were flipped
to `false` explicitly; AET's last `in_trade` quarter is now 2018-12-31,
which is the one-final-quarter boundary `core/arms.py` expects.

**Flipped, not deleted, and the direction matters.** `v_universe` is
`DISTINCT ON (ticker) ORDER BY as_of DESC`, so deleting AET's later rows
would have promoted its 2018 row to newest and had the view claim AET is
in the trade universe *today*.

**Measured effect on the arms**, which was the whole reason to care:

```
train buy_hold   +383.66%  ->  +392.97%
train signal     +85.75%   ->  +81.68%
validate signal  +12.63%   ->  +12.63%   (unchanged)
```

Removing a seven-year frozen position raised buy-and-hold on train by 9.31
points. Validate's signal arm did not move, because the signal arm trades
events and no event changed — the structural claim in ADR 135, confirmed
by measurement rather than assumed.

**Also surfaced, unrelated to the floor**: 11 BLK rows flipped `f` -> `t`
on the re-run. BLK's indicator rows are 0 days stale in every affected
quarter, so the floor is a no-op for it. The cause is `tickers.sector`,
now populated for 502 of 712 rows where `compute.py`'s own docstring
records it as NULL for all 711 when that code was written; sector changes
the peer set, which changes the median, which flips borderline
`crit_rel_return`. The stored rows were stale relative to data that
arrived later, and the re-run corrected them.

---

## 4. ~~`is_active` and `delisted_on` not derived from the delisting signal~~ — done 2026-08-20

`jobs/ingest.py::_update_ticker_coverage` maintains `tickers.last_bar` and
its docstring names it "the delisting signal DESIGN 4.3 calls out". It was
populated for 638 of 712 tickers and nothing read it.

**Stamped**: `delisted_on = last_bar` for the 18 names that stopped
trading, and `is_active = false` with them. `handlers/universe.py` projects
the column to MCP callers, so that field now tells the truth.

**Re-admitted**: HUBB (Hubbell, a current S&P 500 member, $504) and Q
(Qnity Electronics, $141) were marked inactive and invisible to every job,
since each ticker list is `SELECT ticker FROM tickers WHERE is_active`.
Both have 4 bars and no history, so nothing measurable changed — the
`cell_stats` digest is byte-identical at `7ad6eb4cda94d7e5cad85a54b49c9dc2`
and 0 events exist dated after any delisting. A future
`cscan bars --backfill` would pull their history and *then* change the
population; that is a separate deliberate act.

**Junk bars deleted and the fetch guarded.** FB, PCLN, PCS, NKTR and CPWR
are dead symbols; Yahoo answers for whoever holds them now, so `FB` came
back at $45 against a Meta that trades as `META`. 19 rows removed, and
`ingest._drop_delisted_before` now skips a ticker whose `delisted_on`
predates the fetch window. ADR 035's survivorship-bias policy is untouched
— historical bars for delisted names are exactly the point; what cannot be
real is a bar dated after the ticker stopped existing.

**How the bars got there, corrected.** An earlier note guessed symbol reuse
landing on an active row. The `runs` params show the truth: that backfill
passed **712 tickers**, the whole table, while every other job resolves
`WHERE is_active` (616 at the time). `run_bars_daily` was the one entry
point with no activity filter.

---

## 5. ~~Documented limitations~~ — all three closed 2026-08-20

**"Won't build as stated" was too broad a label.** Examined individually,
two of the three were buildable and one was a genuine rejection.

**Half-day sessions — fixed** (migration `d2f6b48e1a07`). The note claimed
this "needs a holiday calendar carrying session lengths, which nothing here
has." Wrong twice: `trading_days.is_early_close` has existed since the
reference-tables migration, and `jobs/ingest.py:177` populates it from
measured session length. 38 days marked across 2009-2026, agreeing exactly
with the hourly bars (a half-day has 3 bars ending 11:30 ET against 7
ending 15:30). `market_is_open()` now closes at 13:00 ET on those days. The
data was present, correct, and unread.

**Self-hosting the fonts — done.** `next/font/google` fetches at build time
and emits the files into the app's own static output; 21 woff2 files, and
no reference to `fonts.googleapis.com` survives in the build. This also
removed the preconnect pair and the first-paint round trip.

**The edge interval — rejected, ADR 136.** The only one that genuinely
could not be built as specified. Differencing the rate bounds assumes an
independence the data does not have; storing a bootstrapped interval is
possible and answers a question already answered. Verified: the baseline
falls inside the rate interval on 48 of 48 train cells and 28 of 28
validate, so the edge bar would have crossed zero on every cell.

---

## 6. Open

Nothing. Every item above is closed, and this file is kept so the next
finding has a home.
