# Standing orders — overnight 2026-08-19

Written 02:00 PT while the `cscan events` rebuild runs. The user is asleep.
Autonomous, same rules as the last one: branch per unit of work, PR, CI
green, merge to main. Record blocking decisions rather than guessing, and
move to something non-blocking.

**Use PowerShell `Get-Date` for the time.** `TZ=America/Los_Angeles date` in
Git Bash treats the already-local clock as UTC and reads seven hours fast.
That mistake cost a wrong "6:16 AM" call earlier in this session.

---

## 0. The hard stop is armed

A background watcher kills the rebuild at **06:20 PT** if it is still
running, to clear the poller window (`scripts/wait_and_poll.ps1` fires at
06:30). Log: `scratchpad/hardstop.log`.

**A kill costs the whole run, not part of it.** `run_events` accumulates
every hit in memory and writes once at the end, so an interrupted run
leaves zero partial rows and a `runs` row marked `interrupted`.

**If it is killed, the fallback is written and ready:**

    uv run python scripts/rebuild_events_chunked.py

One `run_events` call per year, each its own transaction, so a stop costs
one year instead of everything and a re-run upserts completed years to
identical values. **Run it after 13:00 PT**, not before.

**Why the monolithic run may not make it.** 2,386,193 bars in the window
across 625 tickers. The old gated run took 140 min calling `detect()` on
roughly a tenth of them; this one calls it on all of them, and the loop
overhead was already paid on every bar either way. Depending on the split
between loop and `detect()`, the total is somewhere between 3 and 13 hours.
It passed the old run's total at 146 min with no end visible, which says
`detect()` is a real share of the cost.

**It holds no locks while it works.** Measured at 02:00 PT: the connection
was `idle`, last query `ROLLBACK`, 8,658 seconds earlier. All the time is
Python, one core at 100%. So it cannot conflict with the poller — the only
reason for the hard stop is that its final upsert would land inside the
quiet window.

**Nothing may touch the database or `.venv` between 06:30 and 13:00 PT.**
No migrations, no `uv sync`, no jobs.

---

## 1. First thing when the rebuild finishes

Verification, in this order. The baseline is in
`scratchpad/baseline.txt` and the digest in
`scratchpad/cellstats_digest_before.txt`.

| Check | Expected |
|---|---|
| `cell_stats` live rows | **448** |
| `cell_stats` digest | **`96af3a8dd09438c4c62cc162fdc0fdff`** |
| `v_screen_live` | **40,906** |
| `v_screen` | **40,819** |
| `cscan scan` ticker set | unchanged |
| SMCI ticker page | firings past 2010-03-24 |

**If the digest moves, stop and report.** It means the ADR 122 predicate
sweep missed a consumer, and that is the whole risk of the change. Do not
"fix it forward" — find which read widened.

Then: full fast tier exactly as CI runs it (all four steps, whole-repo
scope), PR, merge.

---

## 2. Session 17 leftovers

Everything in the §3 gate passed except the two recorded as findings. What
remains is bookkeeping and one queued change.

- **Docs for the work done after the gate.** `RESULTS.md` has the Session
  17 entry; it does not yet carry ADR 121 (chart/event paging), ADR 122,
  the crosshair readouts, the search, or the date navigation. `TESTS.md`
  needs the four new web test files. `BUILD.md`'s Session 17 row predates
  all of it.
- ~~The live-reversal gap.~~ **Done, ADR 123**, migration `a7c519d3e8b4`
  applied. It ran beside the rebuild on purpose: `DROP`/`CREATE VIEW` takes
  ACCESS EXCLUSIVE on the *view* and only ACCESS SHARE on the tables
  beneath, which does not conflict with an ingest's ROW EXCLUSIVE. Zero
  ungranted locks after, rebuild unaffected.

  **Still needs verification against real data.** ADR 117 merged 2026-08-18
  11:18 PT and the last poller run wrote at 08:34 PT, so every existing
  `signal_reports` row lacks the key and `v_screen_live.rev_ts` is NULL on
  all 40,906 rows. **The first real data lands with the 06:30 PT poller
  run.** After it: confirm `rev_ts` populates, that a near-miss renders its
  ATR distance rather than nothing, and that `confirmed` agrees with what
  the poller printed to the terminal for the same ticker and minute.

---

## 2b. BUG: the poller stores every timestamp four hours early

**Found 2026-08-19 02:15 PT. Not fixed — deliberately.** `_now_et` also
feeds session timing, and a naive/aware mismatch throws. Do not touch it
before 13:00 PT.

`poll.py::_now_et()` returns a **naive** datetime holding ET wall-clock.
It is written to `signal_reports.fired_at` and `quotes_live.ts`, both
`timestamptz`, on a database running `Etc/UTC` — so Postgres reads the
naive value as UTC and every poller timestamp lands 4 hours early (5 under
EST).

Decisive evidence, same moment, two tables:

    runs.started_at        2026-08-13 13:30:41+00   (SQL wrote it: correct)
    signal_reports.fired_at 2026-08-13 09:30:43+00  (poller wrote it: -4h)

Two seconds apart in reality, four hours apart in storage.

**What it breaks.** The screener's "Fired" column renders `clock()` in ET
over an already-ET value, so it shows 05:30 for a 09:30 fire. `liveQuote()`
compares `(q.ts AT TIME ZONE 'America/New_York')::date` to `market_date()`
and survives only because no session spans the shift.

**The CSV is right by accident.** `wait_and_poll.ps1` subtracts 3 hours,
which happens to undo ET→PT correctly. Fixing the poller without fixing the
script will make the CSV wrong.

**Fix.** Return an aware ET datetime (`ZoneInfo("America/New_York")`), check
every use of the return value for naive comparisons, drop the script's -3h,
and decide whether to backfill — roughly 800 `quotes_live` rows and a few
hundred `signal_reports`. Needs an ADR: it changes what a stored timestamp
means.

---

## 3. Session 18 — the main work

`docs/sessions/session16-18-phase5.md` §18. Read it before starting.

`/research`, `/chat`, and the Phase 5 close. `docs/SYSTEM_PROMPT.md` is
already written.

**Neon is not needed.** Both routes read the local views, and `/chat` calls
the MCP server on 127.0.0.1. The user is holding the Neon/sync decision
until they actually deploy, and Session 18's gate says "render against the
live database" — which is the local one.

Inherit Session 17's conventions rather than reinventing: the tokens in
`globals.css`, the five states, `lib/format.ts`, the boundary test's list of
client components (it asserts the list *whole*, so a new one is a
deliberate edit).

`/research` is desktop-only and says so (DESIGN §11.9).

---

## 4. Open questions for the user, not to be guessed

- **The 17,919 fail-open events.** 11.4% of the live config's `touch` rows,
  across 512 tickers, entered `train` before the first universe snapshot
  (2010-03-31) through `core.universe.in_trade`'s fail-open branch. Closing
  it drops them and moves every measured cell. Recorded in ADR 122's
  consequences; **not** to be acted on unilaterally.
- **Neon and the sync job.** Held by the user. `events` at ~700k rows for
  the live config will not fit Neon's free tier, so "what gets synced" is a
  real design decision with an ADR attached.
- **Self-hosting the three Google fonts.** The only outbound request the
  app still makes. Cosmetic, cheap, not urgent.
