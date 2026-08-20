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

## 2. The benchmark record and the database disagree

`RESULTS.md` records the signal arm below its randomization null's 97.5th
percentile on both splits. The live config's run puts validate **above** it.

```
RESULTS.md   signal −10.10%, null 97.5th +3.02%   (08-13, 1835688bf7d760ba)
database     signal +12.63%, null 97.5th +6.36%   (08-16, 86e91448a65aa40b)
```

`/research` states the contradiction rather than picking a number.

**What would settle it**: re-run `cscan benchmarks` under the live config
and compare against the 08-16 rows. If they reproduce, `RESULTS.md`'s table
is stale and should be re-measured with the config hash recorded beside it.
If they do not, something in the arms is non-deterministic, which is a much
larger finding.

**Not to be settled by editing `RESULTS.md` to match.** The record is the
account of what was measured; changing it erases that the result moved.

Worth noting the cells did *not* move — the `cell_stats` digest is
byte-identical across ADR 122's rebuild. Stable cells and unstable arms is
itself worth understanding.

---

## 3. A delisted ticker passes the health filter forever

**Found 2026-08-20 while checking the two "small" ticker items, which turned
out to share this cause.**

`_latest_indicator_row` filters `ts <= as_of` with **no lower bound**, so a
ticker that stops trading keeps returning its last real row for every later
quarter. AET (Aetna, acquired by CVS 2018-11-29) passes all four criteria
in 2026-06-30 on data frozen in November 2018:

```
as_of        in_trade  mcap  above_sma  slope  rel_return
2026-06-30      t        t       t        t        t      <- 2018 data
2018-12-31      t        t       t        t        t
```

31 consecutive quarters `in_trade` with no bars behind any of them. It is
the only ticker in this state, measured.

**Why it matters beyond one row.** `core/arms.py` says a position in a
delisted name "must resolve rather than silently persist", and resolves it
by dropping out of `universe`. AET never drops out, so the buy-and-hold and
DCA arms carry a frozen position — neither gaining nor losing — for seven
years of an equal-weight book. That is a candidate contributor to item 2's
discrepancy and should be checked before that item is called
non-deterministic.

**Not fixed unilaterally.** A recency floor on the criteria changes the
universe definition, which changes `config_hash`, which invalidates every
measured number. Same class of decision as ADR 129 and needs the same
deliberate call.

**The obvious floor** is "no indicator row within one quarter of `as_of`
means not evaluable", which fails closed the way ADR 129 now does.

---

## 4. Nothing ever writes `tickers.delisted_on`

All 712 rows are NULL, and no code path in the tree sets the column —
verified by grep across `capitalscan/` and `scripts/`.

**It has a live reader.** `research/benchmarks.py:194` selects
`WHERE delisted_on IS NOT NULL` to resolve delisted positions in the
benchmark arms, and gets zero rows every time. The delisting mechanism the
arms document is present in code and inert in practice.

Same investigation as item 3 and probably the same fix: whatever sets a
recency floor can also stamp the date it noticed.

---

## 5. `tickers.is_active` is written True and never False

`run_tickers_refresh` upserts `is_active: True` for current constituents;
`ensure_tickers` does the same for ad-hoc backfills. **No code path writes
False**, and the column defaults True — yet 96 rows carry False, so they
were set from outside the current tree (manual SQL, or a script since
removed).

Two of the three named inactive rows look wrongly flagged: **HUBB**
(Hubbell Incorporated, a current S&P 500 member trading ~$504) and **Q**
(Qnity Electronics, a 2025 spin-off trading ~$142). Both are excluded from
every job, because every ticker list is `SELECT ticker FROM tickers WHERE
is_active`.

**Contained, verified**: the nine inactive tickers carrying 2026-08 bars
produced 0 indicator rows, 0 events, 0 `in_trade` universe rows, 0
`bars_live` rows and 0 screener rows. No measured number is affected. The
bars exist because these tickers were active when `bars_daily` ran on
2026-08-18 and were deactivated afterwards — the reverse of the backlog's
earlier guess that this was symbol reuse landing on an old row.

The cost is a false negative: a real S&P 500 name silently absent from the
universe. Worth an explicit reconciliation between `tickers` and the
Wikipedia constituent list, which would also give item 4 its date.

---

## 6. Documented limitations, unlikely to be built as stated

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
