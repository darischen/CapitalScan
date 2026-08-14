# Session 14 — Closing Phase 4

Read `DECISIONS.md` (especially ADRs 012, 015, 020, 061, 062, 092, 095, 099, 102, 108), `DESIGN.md` §6.4, §6.7, §6.8, §6.12 and §11.4-§11.5, and `TESTS.md` §10 first. This document says what to build and in what order. Those say why.

Sessions 11 through 13 built the statistics. Session 14 makes them **readable** and closes the Phase 4 gate.

---

## 0. Scope

### In scope

1. Equity-curve export for the three arms, plus the 200-replication null band.
2. The three-arm chart.
3. The drawdown slice — ADR 015 calls this the project's central claim.
4. Volatility-scaled reachability targets per DESIGN §6.12.
5. ADR 092's matcher replacement, which ADR 095 left as a known gap.
6. The Phase 4 gate, run and recorded.

### Out of scope

- The `/research` page itself. DESIGN §11.4 is Phase 5, and Sessions 15-16 own it. Session 14 produces **static artifacts a researcher opens**, not a served application.
- Any change to detection, indicators, exits, the backtest engine, `cell_stats`, or `benchmarks`' schema.
- Anything touching the serving store.
- Holdout. It is evaluated exactly once, at the end.

### The one-sentence version

Four artifacts a person can look at, one matcher that actually matches, and the Phase 4 gate closed.

### Why this session is different from 11 through 13

Those sessions produced numbers whose correctness was checkable against a formula. This one produces **pictures**, and a chart that is subtly wrong looks exactly like a chart that is right. The defense is that every chart is generated from a query whose numbers are also written to a CSV beside it, so the artifact is checkable against the data rather than trusted because it rendered.

---

## 1. Prerequisites

| Item | Check |
|---|---|
| Session 13 gate passed | Eight arms in `benchmarks`, null 1-200, all recorded |
| ADR 108 merged | `config_hash = 697f3ae71428d392`, fourteen cells |
| `cell_stats` populated for train **and** validate | 448 rows; validate is what `v_screen` reads |
| `benchmarks` populated for train and validate | 409 rows each |
| CI green on `main` | The 42-error `rho_era` fixture defect is fixed |

**The equity curves do not exist yet.** `benchmarks` stores scalars only — no curve columns. 14.1 exists because of that, and it is the dependency for 14.2 and 14.3.

---

## 2. Decisions already made, and why

These were settled while planning, from the ADRs rather than by asking. Each is recorded so a reader can disagree with the reasoning rather than guess at it.

**Charts are static SVG written by `research/`, with no new dependency.**
Three reasons. `uv add` locks `.venv` files against any running job (CLAUDE.md), and this project always has one running. The repo has no plotting dependency today, so adding matplotlib is a real supply-chain decision rather than a detail. And an equity curve, a shaded band, and a bar chart with error bars are a few hundred lines of `<path>` and `<rect>` — well inside what stdlib string formatting does cleanly. If a later session wants interactive charts, Phase 5's `recharts` (ADR: DESIGN §11.4) is the place, not here.

**Every chart writes a CSV beside it.**
A chart cannot be unit-tested for "looks right". The numbers behind it can be. The CSV is the artifact the tests assert against and the thing a reader checks the picture against.

**Equity curves are regenerated, not stored.**
Storing them needs a new table and a migration for data that `run_benchmarks` reproduces in about six minutes. Session 13's gate item 9 proved the arms are deterministic — two runs, identical output ignoring run identifiers — so regeneration is sound. A schema change for derivable data is the more expensive and less reversible option.

**Volatility-scaled reachability is computed at report time, not stored.**
DESIGN §6.12 wants 0.5σ, 1.0σ, 1.5σ of σ_5d reported alongside the fixed 2/3/5/10% ladder. `events` already carries `rv_20d` and `rv_pct_252d`, so σ_5d is derivable per event with no new column and no migration. Storing it would add four columns to a 627k-row table to hold a value that is a scalar multiple of one already there.

**The scaled ladder is a diagnostic, and does not enter the FDR family.**
DESIGN §6.12 is explicit: fixed is the headline because it matches how a limit order is placed; scaled reveals whether an apparent edge is a volatility effect. Adding four scaled targets per cell would take the Benjamini-Hochberg family from 56 tests to 112 for a quantity ADR 020's correction was never scoped over. Same treatment as era and breadth in DESIGN §6.11: reported, never tested.

---

## 3. Model assignment

| Task | Model | Reason |
|---|---|---|
| 14.1 Equity-curve export | Sonnet | Reuses `run_benchmarks`; the risk is silently re-simulating something different |
| 14.2 Three-arm chart | Haiku | Specified layout over a settled CSV |
| 14.3 Drawdown slice | Haiku | Same, plus Wilson intervals already in `core/stats.py` |
| 14.4 Volatility-scaled ladder | Sonnet | σ_5d derivation is where a wrong scale factor hides |
| 14.5 ADR 092 matcher | Sonnet | A matcher that passes on a file it cannot parse is worse than none |
| 14.6 Gate, tests, docs | Haiku | Inventory against a settled design |

Order strictly. 14.2 and 14.3 both depend on 14.1.

---

## 4. Task breakdown

### 14.1 Equity-curve export

`core/arms.py` already returns an `equity` series per arm; nothing persists it.

Rules:

- **`run_benchmarks` does not currently expose the curves.** It returns `(DataFrame, BenchmarkReport)`, and the `equity` series for each arm are local variables inside it. This is the trap in this task: the obvious workaround is to rebuild the arms inside `curves.py`, which is a second simulation of the same thing and precisely the invariant 2 violation that makes a chart disagree with the table beside it.

  Do it by **adding an optional `collect_curves: bool = False` to `run_benchmarks`** that, when set, also returns the already-computed series. One parameter, no duplicated simulation, and the default path is byte-identical to today's. Do **not** re-derive curves from `load_window` / `build_positions` / `simulate_*` in a new module even though those are public — that reproduces the orchestration, and the orchestration is where the arms' comparability lives (identical universe, identical dates, ADR 012).
- A new `research/curves.py` then only *shapes* what it is handed: percentile band, tidy frame, CSV. No simulation of any kind.
- The null band is the per-day 2.5th and 97.5th percentile across the 200 replications, plus the median.
- Output: `reports/phase4/equity_curves_<config_hash>_<split>.csv`, one row per (date, arm, value), with the band as three named series.

Acceptance:

- Curve endpoints reproduce `benchmarks.total_ret` for each arm to 1e-9. This is the check that the export and the stored table describe the same simulation.
- Two runs produce byte-identical CSVs ignoring the header's timestamp.
- The band contains the median at every date, and is monotone in the sense that `p2.5 <= p50 <= p97.5` on every row.
- A test asserting the curve has one row per trading day in the window, no gaps.

### 14.2 Three-arm chart

DESIGN §11.4 item 3: equity curves with the 200-replication band shaded, plus the summary table.

Rules:

- SVG, written to `reports/phase4/three_arms_<config_hash>_<split>.svg`.
- Three lines (buy-and-hold, signal, null median), one shaded band (2.5th to 97.5th).
- Log scale on the value axis. A 383% and a 108% arm on a linear axis makes the loser invisible, which is the arm the reader most needs to see.
- Axis labels, a legend, and the summary table rendered as text beneath: total return, annualized, Sharpe, max drawdown, deployment fraction, capital efficiency.
- **State the verdict on the chart itself**, in words: whether the signal arm cleared the 97.5th percentile. A reader should not have to infer the session's conclusion from line positions.

Acceptance:

- The SVG parses as XML and contains exactly three `<polyline>` elements and one band `<path>`.
- Every number in the rendered table matches the corresponding `benchmarks` row.
- A fixture with a deliberately flat arm renders without dividing by zero on the log scale.
- Rendering is deterministic: two runs produce identical bytes.

### 14.3 Drawdown slice

ADR 015's central claim gets its own chart: edge versus drawdown bucket with confidence bands.

Rules:

- Edge and Wilson interval per bucket, reusing `core.stats.wilson_ci` and the stored `cell_stats` rows. No recomputation.
- Buckets 0-10 and 10-20 render; 20-35 and 35+ are **shown as suppressed rather than omitted** (ADR 101 measured them and suppressed them permanently, and a chart that silently drops them hides the reason the cut exists).
- Grouped by signal type, both sides.
- A zero line, because the entire question is whether the interval crosses it.

Acceptance:

- Every rendered interval matches its `cell_stats` `ci_low`/`ci_high` exactly.
- A suppressed bucket renders with its `n_eff` and suppression reason visible, never as an empty gap.
- ADR 015's hypothesis is stated on the chart and the measured answer beside it, whichever way it falls.

### 14.4 Volatility-scaled reachability

DESIGN §6.12's diagnostic half.

Rules:

- σ_5d per event derived from `events.rv_20d`: `sigma_5d = rv_20d * sqrt(5/252)`. `rv_20d` is annualized (see `core/indicators.py::realized_vol`), so the scale factor is the same one `core/baselines.py::horizon_drift_vol` uses. **Derive it, never write `0.1409`.**
- Targets at 0.5σ, 1.0σ, 1.5σ per event, then the fraction of events reaching each.
- Reported alongside the fixed ladder in the same table, clearly labelled as the diagnostic.
- **No q-value, no entry into the FDR family** (see §2 above).

Acceptance:

- A ticker-year with double the volatility gets double the absolute target, asserted on a fixture.
- A null `rv_20d` yields a null scaled target, never a substituted one (invariant 4).
- The scaled and fixed ladders agree exactly when σ_5d happens to equal the fixed target.
- A test asserting the scaled ladder does not appear in the Benjamini-Hochberg input.

### 14.5 ADR 092's matcher replacement

ADR 092's enforcement is a substring search for `"80.0"` and `"20.0"` in one module. ADR 095 recorded that this is insufficient and its own proposed fix — widen the file list — does not work either, because `db/schema.sql` spells the same threshold `(s.k_full >= (80)::numeric)`.

Rules:

- Replace the substring search with a **pattern for a numeric literal adjacent to a comparison operator on a threshold-bearing column**, over both Python and checked-in SQL.
- Threshold-bearing columns are named in one place, not scattered: `k_full`, `k_fast`, `d_full`, `bb_upper`, `bb_lower`, `bb_mid`, and the `%K` spellings SQL uses.
- The matcher reports file, line, and the offending text. A boolean pass/fail is not actionable at this size.
- **Allowlist by explicit annotation**, not by silence. A legitimate literal carries a comment naming the ADR that permits it.

Acceptance:

- It catches `db/schema.sql`'s `(s.k_full >= (80)::numeric)`, which the current matcher misses. This is the regression test.
- It catches `80`, `80.0`, `80.00`, and `int(80)` in a Python comparison.
- It does **not** flag `ExitParams.exit_stoch_threshold = 80.0`, which is the definition rather than a use.
- Pointed at the repository as it stands, it either passes or reports findings that are then fixed. **A matcher whose first run is green on a codebase with a known defect is not finished** — verify it fails on the pre-ADR-095 `v_positions` definition before trusting a green run.

### 14.6 Phase 4 gate, tests, documentation

The gate, from `TESTS.md` §10:

| Criterion | State entering Session 14 |
|---|---|
| Three-arm comparison produces a chart | **open** — 14.2 |
| Random-entry null spans 200 replications | closed, Session 13 |
| Every headline cell reports `n_eff`, CI, baseline, q-value | closed, Session 12 |
| Drawdown slice renders | **open** — 14.3 |
| Random-walk null test passes on the full pipeline | closed, Session 11.4 |

Acceptance:

- `TESTS.md` §10's Phase 4 block marked closed with the evidence for each item.
- `TESTS.md` gains the Session 14 inventory.
- `RESULTS.md` gains a Session 14 section: the artifacts, where they live, and what they show.
- `BUILD.md` lists Session 14 and its gate outcome.
- `DESIGN.md` §6.12 and §11.5 updated if any rule resolved differently from the spec.

---

## 5. Session gate

1. All four artifacts exist under `reports/phase4/` and are regenerable by one command.
2. Every chart's numbers match the `benchmarks` or `cell_stats` rows they came from, asserted by test rather than by eye.
3. Chart generation is deterministic: two runs, identical bytes.
4. The volatility-scaled ladder is reported and is provably absent from the FDR family.
5. ADR 092's matcher catches the `db/schema.sql` spelling the old one missed.
6. The Phase 4 gate's five criteria are each marked closed with evidence.
7. CI fast and slow both green.
8. Determinism: two runs, identical output ignoring run identifiers.

Item 2 is the one that matters. A chart is the easiest place in this project to publish a confident wrong number, because nothing about a rendered picture fails loudly.

---

## 6. What will be tempting and should not be done

**Adding matplotlib because SVG-by-hand feels primitive.** It is a dependency decision that locks `.venv` against every running job, on a project that always has one running. The charts here are three line series and a bar chart.

**Letting the chart recompute what `benchmarks` already stores.** Then the chart and the table can disagree, and the chart is the one people look at. Read the stored rows.

**Dropping the suppressed drawdown buckets from the slice.** ADR 101 suppressed them *after measuring them*, and the measurement is the argument. A chart showing only the two rendering buckets makes the cut look arbitrary.

**Letting the scaled reachability ladder into the FDR family.** It doubles the family for a diagnostic, and DESIGN §6.12 already says which of the two ladders is the headline.

**Declaring the matcher done on a green first run.** The whole reason ADR 092's enforcement needed replacing is that it was green while a real defect sat in `db/schema.sql`. Prove the new one fails on that input before trusting it.

---

## 7. What Phase 5 needs from this one

| Phase 5 needs | From |
|---|---|
| Equity-curve series for `/research` | 14.1 |
| The drawdown slice's shape and interval logic | 14.3 |
| The scaled ladder as a served column | 14.4 |
| A Phase 4 gate that actually passed | 14.6 |

---

## 8. Rollback

- Every artifact is a file under `reports/phase4/`. Deleting the directory reverses 14.1 through 14.3 entirely.
- 14.4 adds no column and no migration; it is a reporting function.
- 14.5 touches a test and a helper module.
- **No migration in this session.** If one becomes necessary, write it and stop — do not apply it while the poller is live (CLAUDE.md).
