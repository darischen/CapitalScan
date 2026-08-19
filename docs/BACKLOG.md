# Backlog

Work that is understood but deliberately not done, with the reason. An item
leaves this file by being built or by being rejected in an ADR — not by
being forgotten.

Ordered by when it blocks something, not by size.

---

## 1. ~~Poller timestamps four hours early~~ — DONE 2026-08-19 11:20 PT

All four coupled steps landed together, which is the only way they could:

1. Poller stopped, its `runs` row closed as `interrupted`.
2. `backfill_poller_timestamps.py --apply` shifted **1,752 rows** — 876
   `signal_reports`, 876 `quotes_live`.
3. The three-step chain came out of `wait_and_poll.ps1`.
4. `test_poller_clock.py` inverted to assert one conversion.

Verified from both consumers afterwards: the CSV query reads `06:30:40 PT`
and the screener's Fired column reads `09:41` ET for the same session. The
first fire of the day is the 09:30 open, which is what it always was.

---

## 2. ~~Today's bar on the ticker chart~~ — DONE 2026-08-19 (ADR 128)

Live, verified against a real session. TSM at 11:23 PT: open 418.13, high
419.60, low 407.88, close 411.24 on 5.5M shares, drawn hollow beside the
closed bars with its bands stopping at yesterday.

No new data source and no new request — `fetch_quotes` was already
receiving the session aggregates and discarding them, then writing
`quotes_live` only for tickers that fired. 139 rows now, one per in-trade
ticker, all self-consistent (`low <= close <= high`).

`bars_live` is a separate table, not a flag on `bars`.
`test_bars_live_isolation.py` keeps it invisible to anything that computes
an indicator.

---

## 3. The benchmark record and the database disagree

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

## 4. Held by the user, not to be acted on unilaterally

**The 17,919 fail-open events.** 11.4% of the live config's `touch` rows,
across 512 tickers, entered `train` before the first universe snapshot
(2010-03-31) through `core.universe.in_trade`'s fail-open branch. Closing
it drops them and moves every measured cell. Recorded in ADR 122.

**Neon and the sync job.** Not needed for Session 18 — every route reads
the local views and `/chat` calls MCP on 127.0.0.1. It becomes necessary at
deployment. `events` at 14.6M rows will not fit Neon's free tier, so "what
gets synced" is a real design decision with an ADR attached.

---

## 5. Small and unblocked

**No edge interval exists in the schema.** `cell_stats` stores a Wilson
interval on `p_hit`; `edge` is `p_hit − baseline` with no interval of its
own. `/research` shows the rate interval with the baseline marked inside
it, which answers the same question. DESIGN §11.2 asks for an edge bar; one
cannot be drawn without either storing an edge interval or differencing two
bounds, and the latter assumes independence the data does not have.

**Self-hosting the three Google fonts.** The only outbound request the app
still makes. Cosmetic.

**`tickers.delisted_on` is NULL on all 96 inactive rows**, so the date a
listing ended is recorded nowhere.

**Nine delisted symbols carry 2026-08 bars** — UA, FB, FISV, HUBB, NKTR,
PCLN, PCS, Q, CPWR — three to four bars each on tickers delisted years ago.
Reads like symbol reuse landing on the old row.
