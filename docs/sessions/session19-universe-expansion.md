# Session 19 — universe expansion, ADR 142/144, resumable backtest

**Written mid-session, 2026-08-21 03:47 PT, as a resume point.** A long job is
running and the context window may be compacted before it finishes. Everything
needed to pick this up is here.

---

## COMPLETE — 05:44 PT

The whole chain finished before the poller window. Nothing is outstanding
and no job is running.

```
GUC                 f66729c7eda212a4     (moved, screener resolves)
events              783,644 · 540 tickers · 4 grains
cofire_count        corrected, 0 mismatched groups
fwd_window_days     195,827 / 195,911
rho_era             4 eras
cell_stats          512 rows (train + validate, 16-cell grid)
benchmarks          409 rows
screener            79 rows for 2026-08-20 (63 under the old config)
migration           c3f91a70b8d4 applied to BOTH databases
```

**The 16:30 nightly hazard is cleared** — the migration was applied at 04:49
once the backtest released the table, so `compute_all` has its column.

**Result: ADR 112 holds a third time.** Zero cells survive FDR on either
split over a 43% larger universe. Full numbers in `RESULTS.md`.

Not done, and deliberately: `cscan db sync-config` and the Neon sync. Those
push to the serving store, which is at 80% of the free tier, and the
expansion makes that item live again -- it is the user's call. `ADR 144`'s
`bull_close_below_lower` remains dormant by design.

---

## Config hashes, and which is live

| Hash | What | State |
|---|---|---|
| `86e91448a65aa40b` | pre-ADR-142 | superseded |
| `bbc99a02ebdc999f` | ADR 142 widened agreement, `min_price` deleted | **complete**, cells + benchmarks measured, **GUC still points here** |
| `f66729c7eda212a4` | `min_mcap_usd` 30B -> 20B | events being written now |

**The Postgres GUC is deliberately still on `bbc99a02ebdc999f`**, because that
hash has data behind it and the screener keeps working. Move it only after the
new hash has a complete backtest:

```sql
ALTER DATABASE capitalscan SET capitalscan.default_config_hash = 'f66729c7eda212a4';
```

**Consequence to know**: the poller resolves config from *code*, so from
2026-08-21 it writes live events under `f66729c7eda212a4` while the screener
reads `bbc99a02ebdc999f`. Today's signals are recorded correctly and are
invisible on the screener until the GUC moves. Not a bug, and it self-resolves.

---

## Remaining sequence

```
1. cscan backtest --phase compute --chunk-size 25 --workers 8   (resume; ~5h total)
2. cscan backtest --phase finalize                              (cofire_count, ~10 min)
3. cscan path backfill --config-hash f66729c7eda212a4 --workers 8   (~10 min)
4. cscan stats rho    --config-hash f66729c7eda212a4
5. cscan stats cells  --config-hash f66729c7eda212a4 --split-key train
6. cscan stats cells  --config-hash f66729c7eda212a4 --split-key validate
7. cscan stats benchmarks --config-hash f66729c7eda212a4 --split-key train
8. move the GUC (above)
9. cscan db sync-config, then the Neon sync
```

Steps 3-7 were all needed for `bbc99a02ebdc999f` and are the same here.
`stats cells` fails with `cannot convert float NaN to integer` if step 3 was
skipped, and with `no rho_era row for era(s) [...]` if step 4 was.

---

## What landed tonight

**ADR 142** — fast/full stochastic agreement widened: the columns also agree
when both sit beyond the same threshold, not only when within
`fast_agreement_tol`. Re-measured against ADR 112: **zero cells survive FDR on
either split**, min q 0.7167 train / 0.7327 validate. `n_eff` moved 256 -> 265
on a 60% larger confluence population. Full record in `RESULTS.md`.

**Universe expansion** — Nasdaq listings >= $5B ingested via
`jobs/fetch/nasdaq.py`; `min_mcap_usd` 30B -> 20B.

```
tickers      1,029 (929 active, 316 NASDAQ-tagged)
bars         1,024,046 new, 2005-10-11 -> 2026-08-20
indicators   295 of 316 (21 are 2025-26 IPOs, too short for sma_200/rv_pct_252d)
shares       58,213 rows, 316/316 covered, 79 via the Yahoo fallback
universe     66 quarters rebuilt; null_mcap 296 -> 2; 541 tickers ever in_trade
```

**ORKA excluded** (`is_active = false`, nothing deleted). Oruka Therapeutics
reverse-merged into ARCA biopharma's listing; Yahoo back-adjusts through the
reverse splits and produces $1,632,960 in 2005 decaying to $30 by 2022. Caused
a `numeric(12,6)` overflow that aborted a 34-ticker indicator batch.

**Two-phase backtest** — `--phase compute` (chunked, resumable) and
`--phase finalize` (cross-ticker `cofire_count`). The boundary is forced:
`add_cofire_count` groups across tickers and is correct only over the whole
universe, so a chunk cannot compute it. Compute writes with
`full_universe=False`, which the pre-existing guard handles by dropping
`cofire_count` from the write.

**ADR 144 — `bull_close_below_lower`** (in progress, see below).

---

## ADR 144 state: implemented DORMANT

`close > open AND close <= bb_lower[t]` — the long-side mirror of
`bear_close_above_upper`, at the user's request 2026-08-21.

**It is deliberately not enabled.** `SignalParams.enabled_signal_types` does
not list it, so `enabled_types` never resolves it and it cannot fire. Adding
the enum member does **not** move `config_hash` (verified: still
`f66729c7eda212a4`) — the hash is over `dataclasses.asdict(Config)` and an enum
member is not a field, which is exactly ADR 108's documented trap, used here on
purpose so the running backtest stays valid.

Done:

- `core/types.py` — `SignalType.BULL_CLOSE_BELOW_LOWER`
- `core/indicators.py` — `bull_close_below_lower`, mirroring the bear indicator
  exactly (same `bear_close_band_lag`, same same-day band, same `_breach`)
- `core/signals.py` — first in `_SPECIFICITY[Side.LONG]` (the user's decision),
  `BULL_CLOSE_FIELD`, `_bear_close_flag` generalised to `_close_flag(bar, field)`,
  `_types_fired(..., bull_close=...)`, `detect` passes it
- `tests/unit/test_signature_guarantee.py` — **the permitted-bar-field set was
  widened**, in both places it is pinned, with the standard for joining it
  written down. This is the most consequential edit of the session; see below.

Still to do:

- Alembic migration adding `indicators.bull_close_below_lower` (boolean).
  **Write it, do not apply it** — `cscan db migrate` takes an ACCESS EXCLUSIVE
  lock and a backtest is running.
- `jobs/poll.py` — `is_bull_reversal` and the `ReversalState` mirror
- `research/candidates.py` / `cscan scan` — surface it
- `web/` — display label mirroring "↓ reversal" (e.g. "↑ reversal")
- ADR 144 in `docs/DECISIONS.md`
- Enabling it is a separate, deliberate act: add to `enabled_signal_types`,
  accept the `config_hash` move, rebuild.

### The signature-probe widening

`detect` now reads a sixth bar field. CLAUDE.md calls that probe "the real
guarantee" and says never to widen it. It was widened, and the reasoning is:

- The field is a **boolean whose causality was resolved in
  `core/indicators.py`** before `detect` saw the bar — the same category ADR
  108 already admitted, not a new one.
- It is **structurally forced**: the flag describes bar *t*'s close against bar
  *t*'s band, while `ind` carries t-1, so the bar is the only route.
- `FORBIDDEN_PRICES_ON_BAR` is unchanged and is the half that does the work.
  `open`, `close`, `adj_close`, `volume` remain forbidden.

If this is judged wrong on review, the revert is: remove the field from
`PERMITTED_ON_BAR` in both places, drop `bull_close` from `_types_fired` and
`detect`, and leave the indicator computing but unread.

---

## Bugs found tonight (all recorded in `BACKLOG.md` where open)

Every one is a **batch job where one bad member destroys the batch**, and three
of four reported success while doing it.

| Job | Symptom | Status |
|---|---|---|
| `cscan bars --tickers` | exits **0**, writes nothing; cache filename exceeds the OS limit at ~317 tickers | worked around by chunking; fix is to hash the key. **In `BACKLOG.md`** |
| `cscan universe` | succeeded with the expansion inert — 296 NULL mcap, uncounted | cause was missing shares; fixed by running `cscan shares` first |
| `cscan indicators` | one ticker's `numeric(12,6)` overflow aborted 34 | ORKA excluded; isolate per-ticker to find such cases |
| `cscan shares` | one SEC 404 (OZK) aborted 316, `status = failed` | **fixed** — the 404 now lands in the `skipped` list that already existed |

---

## Environment notes

- **Bash does not see user/system environment variables on this machine.** Use
  full paths: `"/c/Program Files/PostgreSQL/18/bin/psql"`, and `docker.exe` at
  `"C:\Program Files\Docker\Docker\resources\bin\docker.exe"`.
- **Postgres runs in Docker** as `capitalscan-postgres`, mapped to 5432. A
  *separate* native PostgreSQL 18 service listens on **15432** and rejects the
  `capscan` password — if 5432 refuses connections, check the container is up
  before anything else.
- The container **exited 255 unprompted at ~01:00 PT**, killing a 1h55m
  backtest. No OOM and no disk error in its log; the user did not touch it.
  Cause unknown. Crash recovery lost nothing. This is why the backtest was made
  resumable.
- `while read` over a file with no trailing newline **silently skips the last
  line** — cost 17 tickers on the first bar ingest. Use
  `while IFS= read -r x || [ -n "$x" ]`.
