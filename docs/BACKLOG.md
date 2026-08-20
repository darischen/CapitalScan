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

## 4. `tickers.is_active` and `delisted_on` are not derived from the delisting signal that exists

**Investigated 2026-08-20. Items 4 and 5 were the same defect and are merged.**

`jobs/ingest.py::_update_ticker_coverage` maintains `tickers.last_bar` and
its docstring names it exactly right: *"the delisting signal DESIGN 4.3
calls out under silent truncation."* It is populated for **638 of 712**
tickers.

Neither column that should follow from it does:

```
tickers                 712
  have last_bar         638
  have delisted_on        0     <- no writer anywhere in the tree
```

**`is_active` disagrees with `last_bar` on 14 tickers.**

Nine are marked inactive and still trading, so every job skips them —
every ticker list is `SELECT ticker FROM tickers WHERE is_active`:

```
HUBB  Hubbell Incorporated  $504.30  last bar 2026-08-17
Q     Qnity Electronics     $140.76  last bar 2026-08-17
UA                            $5.19  last bar 2026-08-17
FISV  Fiserv                 $52.21  last bar 2026-08-17
FB                           $45.05  last bar 2026-08-17
NKTR / PCLN / PCS / CPWR
```

HUBB is a current S&P 500 member. FB is not Meta — Meta trades as META,
and $45 is a different company holding a reused symbol. So the nine are a
mix of *wrongly excluded* and *genuinely reused*, and `last_bar` alone
cannot tell them apart.

Five are marked active and stopped trading years ago:

```
TLAB 2013-12-03   MOLX 2013-12-09   AET 2018-11-29
SCG  2018-12-31   BMS  2019-06-10
```

**Nothing in the tree writes `is_active = False`.** `run_tickers_refresh`
upserts `True` for current constituents, `ensure_tickers` upserts `True`
for ad-hoc backfills, and the column defaults `True`. The 96 `False` rows
were set from outside the current code. The last refresh was 2026-08-01
and wrote 503 rows.

**Why this is one decision, not two.** Populating `delisted_on` while
`is_active` still disagrees would leave the table telling two stories: a
ticker could carry a delisting date and an active flag, or trade daily and
carry neither. The reconciliation has to cover both columns or neither.

**Blast radius is the reason it is not done here.** `delisted_on` is inert
— `ArmWindow.delisted_on` is built and never read, so writing it changes
no measurement today, though `handlers/universe.py` projects it to MCP
callers and would start telling the truth. `is_active` is not inert: it
selects the ticker list for every job, so admitting HUBB and Q would give
them indicators, events, and universe evaluations, changing the measured
population. That is the same class of call as ADR 129 and ADR 135.

**The reconciliation, when someone takes it**: compare `tickers` against
the Wikipedia constituent list *and* `last_bar`, set `delisted_on =
last_bar` for names that stopped trading, and treat a name that is trading
but absent from the index as a membership question rather than an
activity one. `last_bar` is already the evidence; nothing needs fetching.

---

## 5. Documented limitations, unlikely to be built as stated

**No edge interval exists in the schema.** `cell_stats` stores a Wilson
interval on `p_hit`; `edge` is `p_hit - baseline` with no interval of its
own. `/research` shows the rate interval with the baseline marked inside
it, which answers the same question — verified 2026-08-20: every reported
cell's interval contains its baseline, 48/48 train and 28/28 validate.
DESIGN 11.2's edge bar cannot be drawn without either storing an edge
interval or differencing two bounds, and the latter assumes independence
the data does not have.

**Half-day sessions are not modelled.** `market_is_open()` is 09:30-16:00
ET every trading day. The market closes at 13:00 ET on roughly nine
afternoons a year, and on those the live price stays visible for three
hours after trading ends. Needs a holiday calendar carrying session
lengths, which nothing here has.

**Self-hosting the three Google fonts.** The only outbound request the app
still makes. Cosmetic.
