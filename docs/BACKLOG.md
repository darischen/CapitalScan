# Backlog

Work that is understood but deliberately not done, with the reason. An item
leaves this file by being built or by being rejected in an ADR — not by
being forgotten.

Ordered by when it blocks something, not by size.

---

## 1. Blocked on the poller quiet window — do at 13:00 PT, 2026-08-19

**The poller's stored timestamps are four hours early** (ADR 127). The
clock is fixed, so rows written from the next poller restart are true
instants. Every row before that is ET wall-clock wearing a `+00` label.

Four steps, and they are coupled — **do all four or none**:

1. Stop the poller.
2. `uv run python scripts/backfill_poller_timestamps.py --before '<the moment the poller last started on the old code>' --apply`
   Dry run today: **876 `signal_reports` rows, 876 `quotes_live` rows.**
3. Drop the three-step conversion in `scripts/wait_and_poll.ps1` down to a
   single `AT TIME ZONE 'America/Los_Angeles'`.
4. Invert `test_poller_clock.py::test_the_csv_script_still_compensates_for_uncorrected_rows`
   to assert one conversion instead of three.

**Why coupled.** The script's chain compensates for the bad data, which is
why its CSV reads correctly while the screener does not. Fixing either side
alone moves the CSV four hours. Measured on a real row: stored
`09:30:39+00`, three-step gives `06:30` PT (right), single gives `02:30` PT.

**The cutoff must be a timestamp you can name.** The script shifts rows at
or before it and cannot tell a corrected row from an uncorrected one, so
running it twice with the same cutoff double-shifts. Use the poller's own
`runs.started_at`.

---

## 2. Today's bar on the ticker chart

**Asked for 2026-08-19. Not built, because the obvious implementation
breaks invariant 3.**

The chart stops at yesterday's close. `bars` gains today's row only after
the nightly ingest.

**The poller cannot supply it.** It writes a `quotes_live` row only when a
signal fires, not a continuous tape — measured today, 128 quotes across 86
tickers, at most 2 per ticker. No open, no true high or low, no volume.

**yfinance can**: it returns a partial daily bar for the current session
with real OHLCV.

**The trap is where it lands.** A partial row in `bars` would get an
indicator row from `cscan indicators`, and the poller's t−1 lookup would
then read *today's* partial indicators instead of yesterday's closed ones.
That is exactly the look-ahead failure the t−1 discipline exists to
prevent, and it would be silent — every band would tighten around a price
that had not finished happening.

So it needs one of:

- a separate table, or a distinct `interval` value the indicator job
  ignores;
- an `is_partial` column on `bars`, with every indicator and signal read
  filtering it out.

The second is smaller and more dangerous: it adds a predicate that
*eighteen* consumers must carry, which is the shape of ADR 122's problem.
The first keeps the invariant structural.

Either way: a migration, an ADR, and a test asserting that no indicator or
signal path can see a partial bar.

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
