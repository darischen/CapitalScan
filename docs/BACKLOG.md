# Backlog

Work that is understood but deliberately not done, with the reason. An item
leaves this file by being built or by being rejected in an ADR — not by
being forgotten.

Ordered by when it blocks something, not by size.

**Closed 2026-08-19**: the poller's four-hour timestamp offset (ADR 127,
1,752 rows corrected) and today's live candle (ADR 128, `bars_live`). Both
are recorded in `DECISIONS.md` and `RESULTS.md`; they are gone from here
rather than marked done, because a backlog of finished work is a changelog
wearing the wrong name.

---

## 1. Make `in_trade` fail closed (ADR 129) — decided, needs a rebuild

**Blocked on the database being free.** ~3 hours of `cscan events`, then
`cell-stats` and the benchmark arms. Must not overlap the poller.

`core.universe.in_trade` returns `True` when no universe evaluation exists
on or before a bar. The check is per *ticker*, so a name that entered the
universe late fails open across all its earlier history — not only the
pre-2010 window, which is how it was first mismeasured.

Live config, `touch` slice:

| Split | Events | Tickers | Range |
|---|---|---|---|
| train | **18,805** | 566 | 2010-01-04 → 2021-09-23 |
| validate | 45 | 3 | 2022-02-18 → 2023-12-29 |
| holdout | 35 | 3 | 2024-09-13 → 2025-03-21 |

11.9% of the training population. Across all configs and entry kinds the
same predicate covers 1,672,092 rows.

**Steps:**

1. Flip the fallback in `core/universe.py` to `False`, with the test that
   currently asserts fail-open inverted.
2. `cscan events` full window. ADR 122 means this re-*stamps* rather than
   deletes — the events stay visible on the ticker page and drop out of
   every statistical read, which already carries the predicate.
3. Record the `cell_stats` digest **before and after**. It is expected to
   move; the current baseline is `96af3a8dd09438c4c62cc162fdc0fdff`.
4. Re-run `cscan cell-stats` and the benchmark arms.
5. Re-establish ADR 112's result rather than assuming it. Zero cells
   surviving FDR is likely to hold on an 11.9%-smaller train set and is not
   entitled to.

**Holdout is touched** — 35 events, 3 tickers. Re-stamping is a change to
the population *definition*, not a look at the data, so it is legitimate;
but it must land before any holdout evaluation rather than after.

---

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

## 3. Held by the user, not to be acted on unilaterally

**Neon and the sync job — deferred, 2026-08-19.** Not needed for Session
18: every route reads the local views and `/chat` calls MCP on 127.0.0.1.

The user's reasoning for deferring: the constraint is cost rather than
hosting, and this workstation can serve. `events` at 14.6M rows does not
fit Neon's free tier, so "what gets synced" is a paid decision before it is
a technical one. Revisit at deployment.

---

## 4. Small and unblocked

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
