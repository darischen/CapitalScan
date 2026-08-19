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
leaves zero partial rows and a `runs` row marked `interrupted`. If it is
killed, **do not restart it monolithically** — chunk it by year, which is
restartable, and run it after 13:00 PT.

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
- **The live-reversal gap** (queued, needs a migration so it waits for the
  rebuild). `v_screen_live` joins `signal_reports` for `min(fired_at)` and
  never reads `state_json`. The poller writes an intraday reversal
  judgement to `state_json->'bear_reversal'` (ADR 117) and the screener
  cannot see it, so during a session the terminal knows a confluence is
  sitting below its open and the home page does not. Extend the existing
  lateral join; add a live variant of the reversal badge, visibly distinct
  from the close-confirmed one.

  **First data lands 06:30 PT today.** ADR 117 merged 2026-08-18 11:18 PT;
  the last poller run wrote at 08:34 PT, so no existing `signal_reports`
  row carries the block. Verify against real data before trusting the view.

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
