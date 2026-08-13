# TESTS.md

Test strategy and inventory for `CapitalScan`.

---

## 1. What actually needs testing

Coverage is allocated by **how silently a failure would propagate**, not uniformly (ADR 085).

| Area | Risk | Coverage |
|---|---|---|
| `core/signals.py`, `core/exits.py` | Silent bias — wrong results that look plausible | Exhaustive |
| Data validation and ingest | Corrupt input producing confident output | High |
| Statistics layer | Correct code answering the wrong question | High |
| API routes, UI, CLI | Visible breakage, cheap to catch manually | Smoke only |

The distinguishing property: a bug in `exits.py` produces a number that looks fine and is wrong, and could persist for months. A bug in the screener produces a blank screen noticed immediately.

**Coverage gate: 90% on `core/` only.** No repo-wide target — that pushes toward meaningless tests for CLI argument parsing.

---

## 2. Tiers

```
tests/
  unit/          pure functions, in-memory fixtures, <1 s total
  property/      hypothesis-generated invariants
  golden/        known-answer cases, hand-verified against charts
  integration/   real Postgres via testcontainers, small ticker set
  acceptance/    phase gates, run manually
```

Tooling: `pytest`, `pytest-xdist` for parallelism, `hypothesis` for property tests, `testcontainers[postgres]` for integration.

**Danger: a bare `pytest` collects `integration/` and truncates live tables against the real database, not a testcontainer.** See `CLAUDE.md` § Before running anything for the mechanism and the only safe invocation.

---

## 3. The five correctness tests

Each catches a failure that would otherwise ship silently. These carry the correctness load for the entire project.

### 3.1 Look-ahead detection

A single Jaccard threshold does not work here. Measured on real bars, a one-bar shift lands near 0.59, not below 0.5, and the cause is arithmetic rather than a bug: bands drift roughly 0.35% of price per day while a bar's own range is roughly 1.59%, so a bar touching yesterday's band usually touches the day-before's too.

Four bounds anchored to a shuffled control, jointly stronger than the original threshold:

```python
def test_shift_ladder(bars, indicators):
    base = detect_all(bars, indicators)
    shifted = {k: detect_all(bars, indicators.shift(k)) for k in (1, 2, 5, 20)}
    control = detect_all(bars, indicators.sample(frac=1.0))  # row-shuffled

    j = {k: jaccard(base, v) for k, v in shifted.items()}
    jc = jaccard(base, control)

    assert jc < 0.15  # shuffled control floor
    assert j[1] < 0.80  # one-bar shift changes materially
    assert j[1] > j[2] > j[5] > j[20]  # monotonic decay
    assert j[5] < 0.50  # original threshold, at shift-5
    assert j[20] < 2 * jc  # converges toward the control
```

Plus a blind-detector guard proving the suite can fail:

```python
def test_suite_catches_a_blind_detector():
    """A detector ignoring indicators entirely must fail the ladder."""
    with pytest.raises(AssertionError):
        run_shift_ladder(detector=lambda bar, ind, sp: always_fire(bar))
```

**What this test does and does not prove.** It catches a signal that is not reading the indicators at all, and it catches a detector whose response to indicator perturbation is structurally wrong.

It does **not** distinguish correct t-1 reading from incorrect t reading. If `detect` wrongly read bar t's indicators, shifting forward one bar would make it read t-1 — the same magnitude of change, roughly the same Jaccard. That limitation is inherent to the shift design.

### 3.1b Signature guarantee — the real t-1 enforcement

The actual guarantee is structural: `detect(bar, ind, sp)` receives **one** indicator row as an argument and reads only `low`, `high`, `ts`, and `ticker` from the bar. Bar t's indicators are not in scope, so there is no path to them.

This is fragile to a future refactor that passes the full frame "for convenience," so it is asserted:

```python
def test_detect_cannot_reach_bar_t_indicators():
    sig = inspect.signature(core.signals.detect)
    assert list(sig.parameters) == ["bar", "ind", "sp"]

    # ind must be a row, not a frame
    with pytest.raises((TypeError, ValueError, AttributeError)):
        detect(BAR, INDICATOR_FRAME, SP)

    # bar must expose only the four permitted fields
    probe = TrackingSeries(BAR)
    detect(probe, IND_ROW, SP)
    assert probe.accessed <= {"low", "high", "ts", "ticker"}
```

The `TrackingSeries` probe is the load-bearing part. If someone later adds `bar.close` to a band comparison, this fails immediately rather than silently introducing look-ahead.

### 3.2 Signal path parity — ADR 006 enforcement

```python
def test_backtest_and_live_agree(fixture_bar, fixture_ind, fixture_bands):
    bt = detect(fixture_bar, fixture_ind, SP)
    live = []
    for tick in simulate_intraday(fixture_bar):  # open → low → high → close
        live += breach_live(tick, fixture_bands, SP)
    assert {s.signal_type for s in bt} == set(dedupe(live))
```

The simulated path walks open → low → high → close, which is the ordering assumption the backtest makes.

This is the test that prevents the live system from firing on events the backtest never measured.

### 3.3 Determinism — ADR 060

```python
def test_identical_config_identical_output(bars, indicators, universe):
    a = backtest(CFG, bars, indicators, universe)
    b = backtest(CFG, bars, indicators, universe)
    assert_frame_equal(a.drop(columns=["run_id"]), b.drop(columns=["run_id"]))
```

Run with test-ordering randomization **enabled** so hidden global state surfaces.

### 3.4 Exit resolver invariants — property-based

```python
@given(entry=prices(), bars=ohlc_sequences(min_size=5, max_size=5), cfg=exit_configs())
def test_exit_invariants(entry, bars, ind, atr, cfg):
    r = resolve_exit(entry, 0, Side.LONG, bars, ind, atr, cfg)

    assert bars.low[r.exit_idx] <= r.exit_price <= bars.high[r.exit_idx]
    assert 1 <= r.holding_days <= cfg.max_hold_days
    assert r.mae <= r.mfe  # NOT mae <= 0 <= mfe, see below
    assert r.mfe >= realized_return(entry, r.exit_price, Side.LONG)

    if r.reason == ExitReason.STOP:
        assert r.exit_price <= entry * (1 - min_stop_distance(cfg))
```

**The MFE invariant is the sharp one.** Realized return can never exceed max favorable excursion. Any violation means the path metrics and the exit disagree about what happened.

**MFE is not clamped at zero.** An earlier draft asserted `mae <= 0 <= mfe`. That is wrong and contradicts DESIGN §5.6. A position that gaps down at t+1 and never trades back above entry has a genuinely negative MFE, since `MFE = max_i (high_i - P0)/P0` and every `high_i < P0`. Clamping would make DESIGN §5.6's "`capture_ratio` stored null when MFE <= 0" clause dead code and would overstate every capture ratio. Assert `mae <= mfe`, not `mae <= 0 <= mfe`.

Target: 10,000 generated cases in the slow tier, 1,000 in fast (§9).

### 3.5 Split leakage — structural

```python
def test_no_split_leakage(events):
    assert events[events.split_key == "train"].signal_date.max() <= TRAIN_END
    assert events[events.split_key == "holdout"].signal_date.min() > VALIDATE_END

    for fold in purged_folds:
        assert not overlaps(fold.train_windows, fold.validate_start)
```

`split_key` is assigned at event creation, never computed at query time (ADR 019), which makes leakage a schema violation rather than a discipline problem.

### 3.6 Holdout firewall — ADR 088

```python
def test_serving_views_never_read_holdout(db):
    for view in SERVING_VIEWS_WITH_STATS:
        ddl = get_view_definition(db, view)
        assert "'validate'" in ddl, f"{view} must pin split_key"
        assert "e.split_key" not in ddl, f"{view} must not inherit event split"


def test_screener_shows_no_holdout_stats(db):
    rows = query(db, "SELECT * FROM v_screen WHERE p_hit IS NOT NULL")
    ids = [r.cell_id for r in rows]
    splits = query(db, "SELECT DISTINCT split_key FROM cell_stats WHERE cell_id = ANY(%s)", (ids,))
    assert set(splits) <= {"validate"}
```

A live event carries `split_key = 'holdout'` by date assignment. Without this guard, a join inheriting it would surface holdout statistics continuously and the numbers would look entirely reasonable.

---

## 4. Golden fixtures

Hand-verified cases committed as CSV, each with a comment explaining what it tests.

| Fixture | Content | Verifies |
|---|---|---|
| `tsm_2026_07.csv` | TSM, 60 bars around the July 28-30 move | A real observed event, end to end |
| `nvda_split_2024.csv` | NVDA 10-for-1 split window | Split adjustment does not fabricate a breach |
| `meta_2022_crash.csv` | Meta's 2022 decline | Repeated lower-band touches in a deep drawdown |
| `intc_2021_2024.csv` | Intel's slow decline | Health filter drops it, and exactly when |
| `flat_series.csv` | Constant price | Stochastic division by zero returns NaN, not 50 |
| `gap_down.csv` | 12% overnight gap through a stop | Gap fill rules (DESIGN §5.5) |
| `ambiguous_bar.csv` | Low hits stop, high hits target, same bar | Stop wins, `ambiguous` set |
| `external_reference.csv` | 5 dates × 2 tickers | Indicator math matches external convention |

### 4.1 External reference — ADR 086

```bash
cscan verify-indicators --ticker TSM --ticker NVDA --dates 2026-07-29,...
```

Prints computed `bb_upper`, `bb_mid`, `bb_lower`, `k_full`, `d_full` and writes `tests/golden/external_reference.csv` with empty `external_*` columns. The user fills them once by hand from StockCharts or TradingView (~30 minutes). A test asserts agreement within 0.1%.

**Why this matters:** the formulas in ADR 004 are standard, but ddof choice and the middle-band SMA-vs-EMA question are exactly where implementations silently diverge, and every downstream number inherits the error. Thirty minutes against a class of error that would stay invisible for months.

---

## 5. Data validation tests

Every rule in DESIGN §2.3 gets a test with a crafted violating row. Additionally:

```python
def test_no_nulls_after_warmup(indicators):
    post = indicators[indicators.ts >= "2010-01-01"]
    for col in REQUIRED_INDICATOR_COLS:
        assert post[col].isna().sum() == 0  # ADR 040
```

Removed 2026-08-01: a `test_stooq_agreement` integration test used to compare Yahoo and Stooq closes here. Stooq began serving a JavaScript proof-of-work challenge to automated requests on every endpoint tried, so the cross-check it validated was removed along with `jobs/fetch/stooq.py`; the pipeline is single-source on Yahoo with no independent cross-check.

```python
def test_ingest_idempotent(db, ticker):
    run_ingest(ticker)
    n1 = row_count(db, "bars")
    run_ingest(ticker)
    n2 = row_count(db, "bars")
    assert n1 == n2
```

```python
def test_indicator_read_window_expands():
    """Writing 5 days must read ~400 calendar days (max_warmup × 1.6)."""
    read_start, write_start = plan_indicator_window(date(2026, 7, 25), date(2026, 7, 30))
    assert (write_start - read_start).days >= ceil(max_warmup() * 1.6)
```

### 5.a Session 10 path store and derived labels

Forward path extraction and label derivation tests (Session 10, tasks 10.2-10.6). Labels are deterministically recomputed from the path table rather than cached at event creation time (ADR 094). Path rows are append-mostly, not immutable: `run_path_capture` writes through `ON CONFLICT DO UPDATE`, so a re-ingested bar rewrites its day (corrected 2026-08-05, along with the ADR number — the design ADR is 094, not 093).

**Unit tests: Path extraction (10.2)** — 12 tests covering window boundaries, truncation at history end, entry offset padding, and idempotency.

```python
def test_fwd_window_for_signal_truncates_near_end_of_history_never_pads():
    """No padding. A signal 3 days before end of data yields 3-day window, not 10."""
    dates = [date(2024, 1, i) for i in range(1, 5)]
    bars = _ticker_bars(dates)
    window = fwd_window_for_signal(bars, date(2024, 1, 1), window_days=10)
    assert len(window) == 3
```

**Unit tests: Label derivation (10.3)** — 15 tests covering MFE/MAE window separation, reachability across exit timing, entry offset shifts, and null semantics.

```python
def test_mfe_mae_bounded_by_holding_days_not_full_window():
    """Exit on day 2; day 4's bigger move does not count toward MFE."""
    path = pd.DataFrame([
        (1, 0.01, -0.005, 0.01),
        (2, 0.02, -0.01, 0.02),
        (3, 0.03, -0.01, 0.03),
        (4, 0.09, -0.01, 0.09),  # bigger but past exit
    ], columns=["day_offset", "favorable", "adverse", "terminal"])
    # holding_days=2 means MFE is max over days 1-2 only
    out = derive_labels_from_path(path, ..., holding_days=2, ...)
    assert out["mfe"] == pytest.approx(0.02)
```

The critical traps this covers: (a) MFE/MAE window is `[entry_offset+1, entry_offset+holding_days]`, (b) reachability window is `[entry_offset+1, entry_offset+max_hold_days]` independent of exit timing (different windows, DESIGN §5.6), and (c) entry_offset shifts both windows (NEXT_OPEN entry at day_offset=1 means first reachable day is 2, not 1).

**Unit tests: Reconciliation (10.4)** — 17 tests covering tolerance calibration, float quantization noise, boolean and null handling, and capture_ratio edge cases.

```python
def test_diff_labels_mfe_tolerates_one_quantum_of_numeric_12_6_rounding():
    """Two independent numeric(12,6) columns rounded separately can differ by 1 ULP."""
    # mfe computed from favorable extremes, realized_return from terminal;
    # path table precision 6 decimals, independent rounding paths
    # => Reconciliation must tolerate sqrt(2) ULPs, ~1.4e-6
```

**Unit tests: Live capture (10.6)** — 3 tests covering query scoping to incomplete windows, incremental accumulation matching one-shot backfill, and idempotency after completion.

```python
def test_incremental_capture_matches_one_shot_backfill_once_window_is_complete():
    """Run path_capture daily as events age. Once fwd_window_days reaches 10,
    the accumulated rows must be byte-identical to what run_path_backfill
    would produce on the full history."""
```

**Unit tests: Label grid queries (10.5)** — 17 tests in `tests/unit/test_path_queries.py` covering the threshold x horizon x direction grid derived on demand from `path`, including the adverse tail (which Session 9 never materialized), three-valued `touched`, `_pct_suffix` column naming, and the config-widening gate criterion.

```python
def test_partial_window_returns_none_not_false():
    """Three observed days, asked about a 5-day horizon, no touch inside
    what exists: the honest answer is unknown, never False."""
    short = _path([(1, 0.005, -0.002, 0.004), (2, 0.006, -0.003, 0.005), (3, 0.007, -0.004, 0.006)])
    assert touched_by(short, 0.02, 5, Direction.FAVORABLE, entry_offset=0) is None


def test_adding_a_threshold_widens_the_grid_with_no_code_change():
    """Gate item 4: a config edit and a re-run, nothing else."""
    stats = dataclasses.replace(base.stats, reach_targets=base.stats.reach_targets + (0.07,))
    added = set(reach_grid(HAND_PATH, 0, dataclasses.replace(base, stats=stats))) - set(before)
    assert added == {...}  # 2 directions x 2 label kinds x 5 horizons
```

**Property-based tests (invariants)** — 9 property tests in `tests/property/test_path_invariants.py`.

Rewritten 2026-08-05. The previous six generated pre-shaped path frames and asserted the generator's own constraints back (contiguous offsets, `favorable >= 0`, `adverse <= terminal <= favorable`), so they passed against any implementation; one asserted nothing at all, its loop body being comments. The sign constraint also contradicted ADR 089, which requires negative MFE to stay representable. These generate raw OHLC bars and run them through `core.returns.path_for_event`, the code that actually builds a path:

```python
@given(ohlc_bars(), _PRICE, st.sampled_from(Side))
def test_extracted_path_has_contiguous_one_based_offsets(bars, entry_price, side):
    """A gap would silently shift every entry-anchored label reading
    day_offset = entry_offset + horizon."""
    path = path_for_event(entry_price, side, bars)
    assert list(path["day_offset"]) == list(range(1, len(bars) + 1))


def test_first_touch_is_monotonic_across_thresholds(bars, entry_price, side):
    """A tighter threshold is touched no later than a looser one, on both
    tails, and can never be untouched while a looser one is touched."""


def test_touched_is_monotonic_across_horizons(bars, entry_price, side):
    """Touched by day 3 implies touched by day 5. None may become True or
    False as the window grows; True never becomes False."""


def test_giveback_is_never_negative(bars, entry_price, side, data):
    """exit_price is drawn from the exit bar's own range, because that is
    what production does. Drawn independently it generates an impossible
    event (exit at a price the window never traded) which the code
    correctly raises on."""
```

Each hypothesis test generates 200 cases in `dev` profile, 250 in CI fast tier, and are not marked for the slow tier. Total inventory: 76 unit tests + 9 property tests.

---

## Session 11.1: Interval and Multiple-Testing Primitives

### Unit Tests (capitalscan/tests/unit/test_stats.py)

**Wilson Confidence Interval**
- Reference values: 6 published cases (small n, p near 0/1/0.5)
- Bounds in [0,1]: property test across full parameter space
- Fractional `n_eff` accepted and used as a float, property test. The ADR 098 correction is almost never integral, and Wilson is continuous in the sample size
- Interval widens as `n_eff` shrinks at a fixed hit rate, so the clustering correction reaches the published number
- Signature: sample-size parameter is named `n_eff`, with `trials` and `n` both absent (structural test, 11.1 acceptance 6). An interval sized on a raw event count is too narrow by `sqrt(1 + (k_bar - 1) * rho_bar)`
- Error handling: invalid inputs rejected

**Standard Error on n_eff**
- Formula: SE = sqrt(p(1-p)/n_eff) against known values
- Boundaries: SE=0 at p=0 and p=1
- Signature: parameter named n_eff (structural test)

**Benjamini-Hochberg**
- Hand-computed example: reproduces with monotonicity
- Monotonicity enforced: q-values never decrease in p-value order
- Property test: q >= p always
- Edge cases: all-ones rejects nothing, all-zeros rejects all
- Error handling: invalid p-values and alpha rejected

**Coverage:** `capitalscan/core/stats.py` at 97% under the fast tier's `--cov=capitalscan/core` gate. The two uncovered lines are the out-of-range `alpha` raise in `wilson_ci` and the list-to-array coercion in `benjamini_hochberg`, which every caller bypasses by passing an ndarray.

---

## Session 11.2: Baselines

### Unit Tests (capitalscan/tests/unit/test_baselines.py)

**Parametric Baseline (DESIGN §6.2 worked example)**
- Horizon scaling: mu_5d = mu_ann / 50.4, sigma_5d = sigma_ann * sqrt(5/252)
- Reachability: P(R_5d >= 2%) matches analytical at ~40.1% with drift, ~36.1% without
- Degenerate volatility: zero variance handled determinately, not as error

**Trailing Window Strictly Prior (lookahead guard)**
- Observation day excluded from its own baseline window
- Short history returns null, never shortened (< 252 days prior)
- Tested on synthetic jump data: jump day's own baseline does not see the jump

**Empirical Baseline Hand-Verified**
- Three ticker-years checked against independent arithmetic
- Split spanning year: baseline identical to same series with no split (reads adj_close)
- No complete forward window returns null
- Counts only complete windows

**Disagreement Flag**
- Fires on fat-tailed synthetic data, quiet on Gaussian
- Two-sided: fires on either empirical > parametric or parametric > empirical
- Null propagates: flag returns None when either baseline is None

**Null Propagation**
- Ticker-year without 252 prior days has null parametric baseline
- Empirical baseline still computed (no prior history needed)
- Cell counts its nulls separately from n_events

**Event-Weighted Aggregation**
- Event-weighted differs visibly from pooled rate (16 points on hand-computed case)
- Never pools; always reads per-event baseline from ticker-year join

**Coverage:** `capitalscan/core/baselines.py` at 96% under the fast tier's `--cov=capitalscan/core` gate. `capitalscan/research/baselines.py` is exercised by these tests but sits outside the coverage gate, which is `core/` only (CLAUDE.md).

---

## Session 11.3: Effective Sample Size and Rho-Bar

### Unit Tests (capitalscan/tests/unit/test_stats.py)

**Effective Sample Size Properties (ADR 098)**
- n_eff never exceeds n: property test across all parameter space
- Boundary equality: n_eff = n exactly when k_bar = 1 or rho_bar = 0
- Monotone decreasing in k_bar: property test with step increments
- Monotone decreasing in rho_bar: property test with step increments
- Clustering widens intervals: standard error never narrows with correlated events

### Unit Tests (capitalscan/tests/unit/test_rho.py)

**Co-Fire Counting**
- Distinct tickers per `(signal_date, signal_type)`, matching `add_cofire_count`
- Entry-kind fan-out collapses: one ticker firing four kinds is one co-firing name
- Different signal types on one date are not co-firing
- Solo days produce no pairs
- `n_cofire_days` counts **days**, not pair-days: five names co-firing is one day and ten pairs

**Empirical rho_bar Weighting (the part ADR 098 argues for)**
- Weighted mean matches hand arithmetic on three tickers with known correlations
- Weighting visibly changes the answer against the unweighted mean
- Pairs that never co-fired are excluded, constructed so their inclusion would move the result
- Pairs under `RhoParams.min_pair_overlap` drop out rather than counting as perfectly correlated
- No co-firing at all yields null, never zero

**Factor-Implied Diagnostic**
- Reproduces the analytical value at zero residual correlation, tolerance 0.03
- Recovers the known betas within 0.10
- Correlated residuals make `rho_gap` positive by more than 0.10 (ADR 098's stated bias direction, generated rather than assumed)
- Zero residual correlation leaves a gap under 0.05
- Every quantity comes from one sample: a ticker observed only on calm days gets its own `sigma_m`, and the implied correlation stays in [-1, 1]
- A ticker under the overlap floor drops out rather than poisoning the others
- Missing market series yields a null diagnostic and does not block `rho_empirical`

**Era Aggregation and the rho_era Row**
- One estimate per era; missing `era` column raises
- Two runs against identical data agree on every measured column
- A second config adds rows rather than replacing the first's
- Row shape matches `RHO_ERA_COLUMNS`
- An era with no measurable `rho_empirical` writes no row (the column is NOT NULL)

**Coverage:** `capitalscan/core/stats.py` at 97% under the fast tier gate. `capitalscan/research/rho.py` sits outside the gate, which is `core/` only.

---

## Session 11.4: Self-Validation

### Unit Tests (capitalscan/tests/unit/test_selfvalidation.py)

Run by the fast tier. `capitalscan/tests/acceptance/` is **not** collected by the fast-tier command in CLAUDE.md, so a self-validation test placed there would never run.

**Null Test (driftless correlated synthetic data)**
- 50 tickers, 2,500 days, zero drift, single-factor panel at 0.22 market and 0.22 residual annualized volatility, betas spread across [0.6, 1.4]
- The shared market factor is load-bearing: on independent tickers the `n_eff` correction has nothing to correct and the broken variant behaves identically to the correct one
- Full pipeline: synthetic panel → synthetic events → ticker-year baselines → empirical rho_bar → n_eff → Wilson CI → Benjamini-Hochberg
- Assertion: fraction of cells at `q < StatsParams.fdr_alpha` must not exceed that same alpha
- 3 replications in the fast tier, 10 in the recorded run; the rate is reported, never reduced to a boolean
- `z_sd <= 1.0` asserted separately: it distinguishes a calibrated correction from one that passes on the luck of a seed
- Seeded and reproducible: two runs produce identical frames; a different seed produces a different world

**The Null Is Actually Null**
- Event generation never reads a price: the panel's prices are scrambled and the event set is unchanged
- Events leave room for the forward window to close
- Cells are assigned per firing day, not per event (per-event assignment leaves no within-cell clustering and makes the null test unable to fail)
- Outcomes come from the same series the baseline is measured on

**Recovery Test (known drift)**
- 30 tickers at beta 0, 30% annualized log drift and 40% volatility
- The analytical target carries the Ito correction (`mu + sigma^2/2`), pinned by its own test. Asserting against DESIGN §6.2's uncorrected 40.1% would pass on roughly half a point of luck
- Computed parametric baseline matches the analytical value within 1 percentage point

**Deliberately Broken Variant (session gate item 3)**
- Standard errors computed on raw `n` instead of `n_eff`
- Asserted: the broken rate exceeds the threshold, its `z_sd` exceeds the correct run's, and its smallest p-value is smaller
- The load-bearing test in the file. Every other assertion would still pass on a null test that could never fail

---

## Session 12: Cell Grid, `cell_stats`, and Serving Predicates

Four of these files sit in `capitalscan/tests/integration/`, which the fast tier does **not** collect. Three of the four are `SELECT`-only and safe to run beside a live job; `test_v_screen_predicates.py` writes, but only rows it generates and deletes. None of them truncates anything.

### Unit Tests (capitalscan/tests/unit/test_cells.py)

**`cell_key` String Construction**
- Oracle strings read out of Postgres 18 by hand, never derived by reasoning about what `concat_ws` and `to_char` ought to do. The 2026-08-09 peak-label defect is the reason for that ordering
- All four coalescing paths: null `dd_bucket` to `'all'`, null `strength` to `'all'`, null `era` to `'pooled'`
- The Session 12 call shape pinned specifically: null strength *and* null era together, which is every headline cell and therefore the shape most likely to rot unnoticed
- Target formatting at each `StatsParams.reach_targets` value, plus a guard asserting those four values are what the parametrization covers
- `FM990.999` quirks: `1.0` renders `1.` (the pattern's `.` prints even when `FM` strips the zeros), and rounding is half **away from zero**, so `0.1235` gives `0.124` where Python's `round` gives `0.123`
- `concat_ws` **drop** paths, which are not coalescing paths: a null in any non-coalesced slot yields a *shorter* key rather than an empty field. A collision hazard, reproduced deliberately

**Grid Enumeration (ADR 102)**
- Exactly twelve cells, checked against the ADR 102 table as a set
- Signal type pairs with side rather than crossing it: twelve cells, not thirty-six
- `20-35` and `35+` never enter the grid (ADR 101)
- Buckets and floor both derive from `StatsParams`, asserted by editing the params and watching the grid follow
- Enumeration order stable; the twelve `cell_id` values distinct

**Suppression**
- Below the floor returns a reason string naming both numbers; at and above the floor returns `None`
- The floor's source is `StatsParams.min_n_eff`, pinned by varying it

### Unit Tests (capitalscan/tests/unit/test_stats_pvalue.py)

**`one_sided_p_value`**
- Hit rate equal to baseline gives 0.5; above gives a small value; below gives a large one. One-sided in the direction the system claims, so a cell underperforming its baseline is not handed a small p-value for failing
- `n_eff`, not `n`, is what moves it — the ADR 098 failure that makes every downstream q-value too small to believe
- Null propagates; a baseline of exactly 0 or 1 yields null rather than an infinite z-score; non-positive `n_eff` raises

### Unit Tests (capitalscan/tests/unit/test_baselines_direction.py)

**Direction-Aware Ticker-Year Baselines (ADR 106)**
- Six of the twelve headline cells are short, and a short hits when the price *falls*. Measuring them against `P(R_h >= target)` would subtract the probability of a long winning from the probability of a short winning and call the difference an edge
- A monotonically rising ticker has a long baseline of 1 and a short baseline of 0; a falling one reverses both
- Direction flips drift and leaves volatility alone, asserted separately. A `direction` that flipped sigma too would be a sign error visible only as a slightly wrong parametric baseline
- `direction=1` reproduces Session 11's frames exactly, asserted by frame equality against the default call
- `direction=0` and other values raise: a silent zero would report `P(0 >= target)`, which is 0 for every positive target and looks like a measurement

### Unit Tests (capitalscan/tests/unit/test_cell_stats.py)

**Pooled `rho_bar`**
- Weighted by each era's **event count**, not a plain mean over `rho_era` rows. A plain mean weights a 300-event era the same as a 140,000-event one
- A missing `rho_era` row raises rather than defaulting to zero, which would set the correction to nothing and hand back `n_eff == n`

**Effective Sample Size**
- Matches `n / (1 + (k_bar - 1) * rho_bar)`; no co-firing leaves `n` untouched; a negative measured rho is clamped at the point of use, never producing `n_eff > n`

**Side-Aware Hit Flags**
- A long hits on a rise, a short on a fall, matching how `path_labels` builds `touched_*` from `reach["favorable"]`
- A mixed-side frame raises: side is a grid dimension, so a cell is single-sided by construction
- A null forward return stays null rather than counting as a miss, which would deflate `p_hit` by the null rate silently

**Grid Event Selection**
- Null `dd_bucket` excluded and counted, with a fixture built so inclusion would move the cell's `n`. `cell_key` coalesces null to `'all'`, so an unfiltered null merges into an aggregate cell rather than dropping
- Deep buckets excluded and counted; open forward windows excluded (today's live events are exactly this case); null `fwd_window_days` excluded
- Exclusions appear in a summary string rather than being silent

**`exit_mix`**
- Fractions sum to 1 and match a hand count; absent reasons omitted rather than zeroed
- Tied counts break deterministically, ordered by descending share then name. `value_counts` does not order ties stably, and two runs over identical data produced `timeout` and `stop` in different orders where both landed on 0.3125 (`confluence_high|short|10-20`, 2015-2019). Nothing stored was affected, since `jsonb` normalizes key order, but a frame differing run to run makes the determinism check report a difference that is not one

**Holdout Era Refused Structurally (ADR 103)**
- `compute_grid` **raises** on the holdout era rather than relying on the caller's `eras` argument. 12.4's acceptance requires a test that fails if the exclusion is removed, and an exclusion living only at a call site has nothing to remove
- A reported era is accepted, which is the control: a `compute_grid` refusing every era would pass the test above
- `era_labels` and `reported_eras` derive from `StatsParams.era_bounds` and `SplitParams.event_start`, mirroring `research.enrich._era`, which stamps `events.era`. A label built differently would match nothing and return empty era rows, a failure that reads as "no events in that era" rather than as a bug
- A structural test asserts `SplitParams.validate_end` equals the last `era_bounds` entry. `reported_eras` drops the final era on the reasoning that it coincides with holdout; move one without the other and dropping it becomes wrong while keeping it becomes a firewall breach

**Benjamini-Hochberg Family Construction (DESIGN §6.8)**
- The family is 48 tests: twelve cells across four ladder targets, for one config
- `q >= p` throughout; q-values monotone in sorted p-value order
- Suppressed cells carry no q-value **and do not enlarge the family**, asserted by comparing against the same data with the suppressed rows removed. Including them would inflate `m` and weaken every q-value
- Era rows carry null `q_value` and enter no family (ADR 103, ADR 099)

**Suppressed Cell Rows**
- Nulls for `p_hit`, `edge`, `ci_low`, `ci_high`; counts retained, because `n_events` and `n_eff` are how a reader sees *why* it suppressed

### Unit Tests (capitalscan/tests/unit/test_cell_reporting.py)

**Breadth Denominator (ADR 104)**
- Denominator is distinct tickers with any event that quarter — the train universe, not the trade universe
- Ratio never exceeds 1, the boundary ADR 099's denominator crossed. A companion test pins the failure mode itself, so the bound test cannot quietly stop proving anything
- Null `cofire_count` propagates rather than reading as "fired alone"

**Terciles**
- Cut within era, not pooled: firing rates differ across regimes, and a pooled cut measures the era rather than the breadth. Asserted with disjoint per-era breadth ranges
- Bucket count comes from `ReportingParams`

**Per-Ticker Concentration**
- Largest contributor and its share; threshold read from `ReportingParams.max_ticker_share`, not a literal
- Empty cell reports no contributor rather than a zero share

### Integration Tests (capitalscan/tests/integration/test_cell_key_sql.py) — read-only

**Executed Parity**
- The Python `cell_key` diffed against the **executed** Postgres function across the full parameter cross-product, 96 combinations built from the headline grid rather than a hand-written list, so a grid change widens the check
- A separate test asserts all four coalescing paths are actually exercised. A parity test that never passes a null proves nothing about the coalescing branches and would still be green
- `provolatile = 'i'`: a `VOLATILE` function cannot back an index or generated column, and nothing warns at the point of use
- `proisstrict = false`: `RETURNS NULL ON NULL INPUT` would make every coalescing branch dead code
- `pronargs = 9`: a tenth parameter (`config_hash` is the recurring proposal) would change every existing `cell_id` silently

### Integration Tests (capitalscan/tests/integration/test_cell_grid_measured.py) — read-only

**The load-bearing test of Session 12.** The only thing connecting the code to the measurement the grid design rests on.

- All twelve cells' `n` matched against ADR 102 **exactly**, no tolerance. `n` is a count, and a mismatch means the population filter changed
- `k_bar` within 0.05; `n_eff` within ±2, a tolerance covering ADR 102's rounded intermediates and deliberately too tight to absorb a wrong filter, which would move `n` by a factor of four
- The population filter is `is_cluster_head AND entry_kind = 'next_open'` with a closed forward window. **Not stated in ADR 102**, recovered by measurement on 2026-08-11, and pinned here
- Exactly two cells suppress, and they are the two ADR 102 predicts. A third suppressing, or either of these rendering, means something changed
- The two suppressed cells asserted to be nowhere near the floor, which is what makes "lower the floor" visibly a bad trade rather than a near miss
- `rho_era` prerequisite checked first, so a missing row fails clearly instead of failing twelve cells at once

### Integration Tests (capitalscan/tests/integration/test_cell_stats_write.py) — scoped writes

**ADR 096's composite key, exercised for the first time.** Nothing previously wrote two configs and checked both survived, so a `cell_id`-only key would have passed the whole suite while silently keeping one snapshot at a time.

- A second `config_hash` adds rows rather than replacing the first's, asserted on the *values*: the first config still reads 0.41 after the second writes 0.62
- A guard test asserts the two configs share `cell_id` values. If the fixtures produced distinct ids, both writes would insert cleanly under a `cell_id`-only key and nothing would have been proven
- Re-writing one config updates in place rather than duplicating, which is the other half of `ON CONFLICT`
- Written rows carry `arm = 'signal'` from the column default, since the writer does not set it
- `exit_mix` round-trips as `jsonb`; an empty frame writes nothing

### Integration Tests (capitalscan/tests/integration/test_v_screen_predicates.py) — scoped writes

**`config_hash` Predicate (ADR 100)**
- A signal row under the default config reaches the screen, asserted first so every negative assertion below is non-vacuous
- A second config holding the same cell adds no duplicate row
- **The same data with the predicate removed returns 2.** This is what proves the test above is not passing for an unrelated reason; if it ever returns 1, the fixture has stopped reproducing the defect
- An unset GUC nulls the statistics but keeps the event. `current_setting(..., true)` returns NULL and `c.config_hash = NULL` is never true, so an unconfigured database serves events without numbers rather than an empty screener
- A non-default config does not leak its statistics

**`arm` Predicate (ADR 105)**
- `control` and `benchmark` rows never reach the screen, asserted as a negative. Asserting only that `signal` rows appear would pass on a view with no predicate at all
- The identical row with `arm` flipped to `signal` does reach it, which is the control for the two tests above
- The check constraint rejects an unknown value, so a typo fails at write time rather than vanishing from the screener
- `arm` defaults to `'signal'`, so every row written before the column existed reads correctly with no backfill

**Strength Pooling (ADR 107)**
- A pooled row (`signal_strength` NULL, production shape) matches. Before ADR 107 the view joined `c.signal_strength = e.signal_strength`, so this row could never match and every Session 12 statistic was invisible
- A strength-conditioned row does **not** match. `IS NULL` has to reject a populated row, or the view is not selecting the pooled cell, it is selecting whichever cell happens to exist
- A pooled row and a split row present together yield exactly one screener row, and it is the pooled one. `cell_id` embeds the strength slot, so both exist as distinct rows for one cell; a condition-free view would match both and duplicate every row, which is the ADR 100 fan-out through a different column

### Migration and Schema

- `test_schema_drift.py` **must run, not skip.** It is read-only (`pg_dump --schema-only`) and safe against the live instance. A stopped Docker container turns the guard off silently
- `test_holdout_firewall.py` still passes after the view rebuild
- Migration `e3c7f5a91d24` applies and reverses cleanly, verified by an actual `cscan db rollback --yes` followed by re-application: column dropped, constraint dropped, view restored, row count unchanged, no orphaned objects
- Migration `f1a8d3b62c07` (ADR 107) likewise. Its rollback removes only the strength predicate and leaves ADR 105's `arm` predicate intact, which is what proves the two revisions compose rather than overwrite each other

---

## Session 13: Benchmark Arms

Session 12 aggregated events that already existed. Session 13 simulates capital, and every failure mode it has produces a plausible number rather than an exception. The suite is split accordingly: `test_arms.py` proves the arithmetic on hand-computable cases, `test_benchmarks_arms.py` proves the windowing on in-memory frames, and `test_benchmarks_measured.py` proves the *stored* result against the live database.

### Unit Tests (capitalscan/tests/unit/test_arms.py)

Pure `core/arms.py`, no database, no fixtures on disk. 58 tests.

**Metrics**
- `max_drawdown` is a **positive** fraction, matching `core.indicators.drawdown_from_high` and `StatsParams.dd_buckets`. A sign flip here inverts every drawdown comparison in Session 14's slice
- `annualized_return` over exactly one year returns the total return, and 69% over two years annualizes to 30% (`1.30² = 1.69`)
- `sharpe` returns **None** on a constant series, never `inf` and never a substituted value. A flat stretch would otherwise make an arm look decisive
- Two risk-free rates on one return series give different Sharpes, which is what proves `risk_free_annual` is read rather than ignored. 13.1's "documented risk-free rate from config, not a literal"
- `capital_efficiency` is 0.0 when nothing was deployed (13.1 acceptance), not a divide-by-zero

**Position return paths**
- On a dividend-free series the path reproduces the price path exactly; with a dividend it picks up the `adj_close` stream. This is the only place the two price series meet
- A short is the exact negation
- The path compounds to the realized return, so the daily decomposition and the round-trip number cannot disagree

**Portfolio simulation**
- A single position drives the curve, and the days around it earn the risk-free rate
- Two opposing positions on one day net to flat, which is the equal-weight rule
- An empty arm earns `rf` and reports `frac_deployed = 0.0`
- **Trade `pnl` sums to the curve's total move.** Attribution has to close, and a position's weight changes every time another opens beside it, so `pnl` is accumulated daily rather than derived from the realized return
- `win_rate` is **None** with no trades. 0.0 reads as "never won," a different claim
- Identical inputs give an identical curve

**Buy and hold and the share book**
- **`frac_deployed` is 1.0 by construction** (13.1 acceptance, gate item 5). Structural, not measured
- Members are equal-weighted; a departing member is sold at the **next rebalance**, so its move on the day after it leaves is excluded
- The book **re-weights to equal on a membership change**: a name that doubles alone and then shares the book with a new member only moves half of it on the next doubling. DESIGN §6.4's "equal-weight, rebalance quarterly on universe changes"
- A name that stops printing bars is **held at its last observed price** until the next rebalance sells it. Dropping it from the valuation instead would book its whole value as a loss on the day the bars stopped
- A member with no price at all drops out of the weighting rather than poisoning the mean (invariant 4)
- **Stint `pnl` sums to the curve.** Without dollar P&L on a stint, the buy-and-hold arm reports `post_tax_ret == pre_tax_ret` for the wrong reason: not because it owed nothing, but because it had no numbers to owe on

**Trim and redeploy**
- **Never trimming reproduces buy-and-hold under *changing* membership.** The static-membership version of this test passes even when the two arms hold different books, which is exactly what happened: the first implementation gave trim a fixed slice of every ticker bought at first appearance while buy-and-hold rebalanced to current members, and it measured **+725% against +413%** on the train split. One simulator now runs both arms and the regression test varies membership
- A trim moves `trim_fraction` into cash; a **second** trim takes the fraction of what *remains*, leaving 0.64 invested rather than 0.60. The rule is stated and tested, per the 13.3 brief
- **Two trims plus one redeploy is one round trip, not two.** `n_trims` and `n_round_trips` are separate numbers and a test distinguishes them
- Days in cash run from the **first unredeployed** trim
- A trim with no following redeploy stays in cash and is reported in `n_open_cash_positions`, never force-closed
- Idle cash accrues at `rf` over a fixture spanning a known 10 days
- **Never trimming reproduces buy-and-hold.** ADR 017's comparison only means something if the no-trim case is the same curve

**DCA**
- All four variants deploy exactly `C`, asserted to the cent
- Underfiring reports non-zero `capital_undeployed` and the final-day sweep still closes the gap
- **Lump sum agrees with buy-and-hold on a single ticker** — the cross-arm consistency check. A disagreement means one of the two simulators is wrong
- `avg_cost_basis` is capital-weighted; `cash_drag` is zero for lump sum and positive for a laggard
- **`frac_deployed` and `max_drawdown` are measured off each variant's own curve, not assumed.** A signal-triggered variant holding cash until its first signal is not fully deployed, and cash does not fall with the basket, so its drawdown is shallower than the index's

**IRR**
- A hand-computed three-flow case. At annual spacing the discount factor solves `220x² − 100x − 100 = 0`, giving `r = 6.53%`; the test asserts NPV ≈ 0 at the measured rate and sanity-checks the rate itself
- A doubling over exactly 365 days is 100%
- **None** when the flows carry no sign change. Returning a bracket endpoint would put a number in `benchmarks.irr` that no cash flow implies

**Tax and wash sales (ADR 032)**
- **29 days flags and 31 days does not, on both sides of the disposal.** Testing only the earlier direction would pass a one-sided implementation
- The window is **calendar** days: 2020-03-31 is inside a 30-day window from 2020-03-01 and 2020-04-01 is not. The 13.5 brief names this as the most likely place to be quietly wrong
- A trade's **own entry never flags it**. A five-day hold would otherwise flag every losing trade, which is a statement about the holding period rather than a wash sale
- A core-position purchase alone triggers the flag where the sleeve alone would not
- A different ticker never flags; a winning trade is never a wash sale
- `post_tax_ret <= pre_tax_ret` for any arm with net gains
- **The disallowance moves the number, not only the flag.** A flag with no numeric consequence would pass a careless test
- **A loss cannot offset a gain from a different tax year.** Pooling the window into one net figure would let a 2021 loss cancel a 2011 gain, which no tax year permits
- **A wash-sale loss is deferred into the next year, not destroyed.** The rule adds it to the replacement lot's basis. Treating it as permanent produced `post_tax_ret = −354%` against `pre_tax_ret = +109%` on the train split — a tax bill several times the account, from twelve years of losses discarded one year at a time. The deferral still costs when it lands in a year with no gains to offset, which is what keeps the flag testable
- A net-loss arm owes nothing and is not refunded — no carry-forward is modeled

**The null percentile**
- The percentile matches `numpy` on 200 stored values
- A value inside the null's range but below its upper tail does **not** clear the criterion. Beating the median is not the test
- An empty null returns None from both `null_percentile` and `exceeds_null`. "No distribution" is not "did not clear it"

### Unit Tests (capitalscan/tests/unit/test_benchmarks_arms.py)

`research/benchmarks.py` on in-memory frames. 41 tests, no database.

**Window and universe**
- Train and validate bounds do not overlap; an unknown split raises
- An event outside the trade universe **that day** is dropped. ADR 012: the signal arm has to trade the names buy-and-hold holds, or the comparison is between two universes rather than two entry rules

**Entries**
- An entry fills at the **next open**, not the signal bar. Resolving off the signal bar shifts every exit in the arm by one bar
- A signal on the last bar never fills and is dropped, never fabricated
- A flat series times out at exactly `max_hold_days`
- **`build_positions` calls `resolve_exit_for_entry`**, asserted by spy rather than by comparing outputs. 13.2's acceptance is worded that way because comparing outputs would pass on two implementations that happen to agree on the fixture
- The exit cache does not change the answer
- A missing interior bar is padded with a **0.0** return so the position still spans the shared calendar. No bar means no observed price change; filling from a neighbour would invent one

**The null (ADR 061)**
- **Two draws at one `config_hash` are identical, verified on all 200 replications, not a sample**
- Two different `config_hash` values produce different nulls on all 20 tested replications. A fixed constant seed makes every config share one null and runs without complaint
- Two replications of one config differ. A null whose replications are identical has no distribution and its 97.5th percentile is its median
- **Firing-rate matching per ticker-year**, on a fixture where a uniform rate would give a visibly different count: AAA fires 12 times in 2018 and 3 in 2019, and a uniform rate over three ticker-years would give ~5 each
- A ticker-year that never fired draws nothing
- A pool smaller than the firing count draws the whole pool, never with replacement — that would enter the same day twice
- Eligible days exclude the window's last two bars, where no exit could resolve, and days outside the trade universe

**Trim signals**
- `CONFLUENCE_HIGH` trims regardless of `%K`; a high `%K` trims without confluence
- **The `%K` threshold comes from `BenchmarkParams`**, not a literal and not `ExitParams.exit_stoch_threshold`. Both default to 80, so a test moving one and asserting the other did not follow is the only thing that catches the coupling ADR 092 warns about
- Only `CONFLUENCE_LOW` is a redeploy; a plain oversold event is not
- A signal outside the calendar is dropped

**DCA schedules and purchases**
- Month starts are the first trading day of each month
- **Signal days are distinct calendar days, not events.** Six names firing on one day is one deployment day; counting events would make `N` the event count and shrink every tranche
- A core purchase is recorded each time a ticker joins the universe, including a rejoin — one purchase would understate a name that left and came back
- **The arm's own re-entry counts as a wash-sale purchase.** ADR 032 says "including the core position," not "only." The sleeve buying back a name it just took a loss in is the textbook case, and the self-exclusion is what stops a trade flagging itself

**Row shaping**
- Every row carries `run_id` and `git_sha` (invariant 6)
- `replication` is null on every arm but the random one. A populated value elsewhere pulls rows into the null's distribution query that are not part of it
- Subset rows carry a distinguishable `era` marker and **share** the pooled `split_key`. The subset narrows the entry population, not the dates, and overloading `split_key` would make the firewall query ambiguous

### Integration Tests (capitalscan/tests/integration/test_benchmarks_measured.py) — read-only

**The session gate, checked against what was actually written.** The gate is stated in terms of rows in `benchmarks`, and reading them back is the only version that catches a writer dropping a replication or a percentile computed off an in-memory list. `SELECT`-only plus one `write=False` re-run; nothing is inserted, deleted, or truncated.

- All eight arms present, one `config_hash`, one `split_key`, `run_id` and `git_sha` on every row
- **The null holds exactly 200 rows with `replication` 1 through 200, no gaps and no duplicates**
- The null has a real distribution: over 100 distinct values and non-zero standard deviation
- `load_null_distribution` returns exactly the stored values, so the percentile is provably computed from the table
- The signal arm's position against the 97.5th percentile resolves to a real boolean. **The gate is that the number is computed and recorded, not that the signal wins**
- The null's median lands near buy-and-hold scaled by `frac_deployed`. A construction check with a deliberately wide band, not a precision claim
- Buy-and-hold's `frac_deployed` is 1.0; `capital_efficiency` is finite on every row and equals `total_ret / frac_deployed`
- Every DCA variant reports `capital_undeployed` and an IRR; lump sum leaves nothing undeployed and carries zero cash drag
- **Lump sum and buy-and-hold agree on terminal value** on the real multi-ticker basket
- `post_tax_ret <= pre_tax_ret` everywhere; `wash_sale_flagged` populated on every row
- The high-breadth subset runs all three arms, is reported **alongside** the pooled result rather than instead of it, shares the pooled `split_key`, and its buy-and-hold matches the pooled one exactly — same dates, same universe, same number
- **Determinism:** two `run_benchmarks` calls on `validate` produce identical frames once `run_id`, `computed_at`, and `git_sha` are dropped

### Fixture Guard Fix (carried from the Session 13 prerequisites)

`test_cell_grid_measured.py` crashed with 42 collection errors rather than skipping when `rho_era` was empty: its module fixture calls `cell_n_eff` before `test_rho_era_prerequisite_is_populated` could run, so the test that existed to "fail first with a clear signal" could not. The emptiness check now lives **in the fixture**, which `pytest.skip`s with the exact `cscan stats rho` command to run. The named test stays as the assertion of the prerequisite, so a future refactor that drops the guard fails there.

---

## 6. Statistical verification

Two tests catching a category no unit test can (ADR 087).

### 6.1 Null strategy produces null edge — the highest-value test in the suite

```python
def test_random_walk_has_no_edge():
    synthetic = gbm(n_tickers=50, days=2500, mu=0, sigma=0.02, seed=42)
    events = backtest(CFG, synthetic, compute_all(synthetic), universe_all)
    stats = compute_cells(events)
    assert (stats.q_value < 0.05).mean() <= 0.05
```

If a random walk produces significant cells, the statistics layer has a bug and **every real result is suspect.** This validates the reasoning rather than the code.

### 6.2 Known drift is recovered

```python
def test_baseline_recovers_known_drift():
    synthetic = gbm(mu=0.20, sigma=0.30, seed=7)
    base = compute_baseline(synthetic, target=0.02, horizon=5)
    expected = 1 - norm.cdf((0.02 - 0.20 / 50.4) / (0.30 * sqrt(5 / 252)))
    assert abs(base.parametric - expected) < 0.01
```

---

## 7. Model tests (Phase 6)

```python
def test_quantiles_monotone(model, features):
    q = model.predict_quantiles(features)
    assert (np.diff(q, axis=1) >= 0).all()


def test_calibration_beats_cell_lookup(model, validation):
    assert brier(model.predict(validation)) < brier(cell_lookup(validation))


def test_no_future_features(feature_frame, event_dates):
    """Every feature value must be derivable from data at t−1."""
    for col in FEATURE_COLS:
        assert not depends_on_future(col, feature_frame, event_dates)


def test_promotion_gate_rejects_flattened_model():
    """A model predicting the base rate everywhere gains ECE but must fail."""
    flat = ConstantModel(base_rate)
    assert not passes_promotion_gate(flat, incumbent, validation)
```

The last one guards the degenerate win described in ADR 067.

---

## 8. Tool and chat tests (Phase 5)

```python
def test_every_tool_returns_valid_schema(tool_name, sample_args):
    result = call_tool(tool_name, sample_args)
    assert TOOL_SCHEMAS[tool_name].model_validate(result)


def test_validator_rejects_naked_probability():
    resp = "There's a 51% chance of a rebound."
    assert not validate_response(resp, tool_calls=[...]).ok


def test_validator_allows_sourced_advisory():
    resp = (
        "TSM fired confluence-low. That cell resolved up 3% within "
        "5 sessions in 51% of 340 effective cases, CI 46-56."
    )
    assert validate_response(resp, tool_calls=[...]).ok


def test_suppressed_cell_never_yields_a_number():
    result = get_stats(signal_type="confluence_low", ticker="RARE")
    assert result.suppressed and result.p_hit is None
```

---

## 9. CI configuration

```yaml
on: [push, pull_request]
jobs:
  fast:
    - ruff check
    - ruff format --check
    - mypy                                    # config-driven, see below
    - pytest tests/unit -p no:randomly
    - pytest tests/property --hypothesis-profile=ci_fast
  slow:
    services: [postgres]
    - pytest tests/property --hypothesis-profile=full
    - pytest tests/golden tests/integration
```

Hypothesis profiles, registered in `conftest.py`:

```python
settings.register_profile("ci_fast", max_examples=250, deadline=None)
settings.register_profile("full", max_examples=10000, deadline=None)
settings.register_profile("dev", max_examples=200)  # default
```

`CAPSCAN_HYPOTHESIS_PROFILE` also selects a profile, so gate scripts request `full` without threading a pytest flag through.

**`max_examples` is per test, not per suite.** With 9 property tests, a naive `full` run measures 509 s. Two consequences shape the tiering:

*`full` is scoped to the exit invariants only.* TESTS §3.4 and the Phase 3 gate both name the exit invariants specifically, not every property test. Four tests at 10,000 examples lands near 226 s, inside the slow-tier budget. Parity, ladder, and the remaining property tests run at `ci_fast` in both tiers.

```python
# tests/property/test_exits.py
pytestmark = pytest.mark.exit_invariant
```

```
slow tier:  pytest tests/property -m exit_invariant --hypothesis-profile=full
            pytest tests/property -m "not exit_invariant" --hypothesis-profile=ci_fast
```

*`ci_fast` is 250, not 1,000.* At 1,000 examples the property suite alone measures 56.9 s, exceeding the fast-tier budget before unit tests, ruff, and mypy run. 250 lands near 14 s.

Measured reference: `dev` 17.4 s, `ci_fast` at 250 roughly 14 s, `full` scoped to exit invariants roughly 226 s.

`mypy` takes no path argument. Invocation style is pinned in `pyproject.toml` via `packages = ["capitalscan"]`, since path invocation causes module-resolution ambiguity. `pandas-stubs` is a dev dependency.

Fast tier under 60 seconds (measured: 29 s). Slow tier under **10 minutes** (measured: 459 s).

The slow budget was raised from 5 minutes after measurement. `test_stop_exits_land_at_or_beyond_the_stop_level` alone costs 193 s because `assume(r.reason is ExitReason.STOP)` discards roughly two of every three draws. That cost is the test sampling the natural distribution of stop-hitting scenarios, which is the property worth keeping. Do not replace it with a targeted generator. **Acceptance tests never run in CI** — they need the real database and the full dataset.

---

## 10. Phase acceptance gates

Each gate is a command producing a pass or fail.

### Phase 1

```bash
cscan scan --ticker TSM --start 2026-07-01 --end 2026-07-30
```

- Returns the 2026-07-29 event with correct %B and %K
- All golden fixtures pass
- Zero nulls in indicators after 2010-01-01
- Random-walk null test passes

(This gate used to also require Stooq agreement within tolerance. Removed 2026-08-01 — Stooq began blocking automated requests; the pipeline is single-source on Yahoo.)

### Phase 2

- Poller detects a live breach within one polling interval
- Notification delivered on all three configured channels
- `poller_sessions` records the session with coverage percentage
- Restart mid-session does not re-fire an already-sent event

### Phase 3

- Exit invariants hold across 10,000 property-generated cases each (`--hypothesis-profile=full -m exit_invariant`)
- Ambiguity rate below 10%, or hourly escalation implemented
- Event rate passes all three checks below (see BUILD.md §9a for the derivation)
- Determinism test passes
- All five validation-harness checks pass (DESIGN §5.10)

**Event-rate checks.** The former wording — "within 20% of the analytical
estimate (~4% of ticker-days for confluence)" — named no side and no price
field, and the three defensible readings span four-fold: `confluence_low` on
closes 4.43%, on intraday extremes 7.16%, either side on extremes 18.34%
(measured 2026-08-01 over 2,371,529 ticker-days at t−1). The ~4% figure
matches the close-based reading, which ADR 005 explicitly rejects — "a daily
bar's extremes are the intraday touch".

Setting the criterion to the engine's own measured output would make it
circular and unable to fail. These three are checkable against something
other than the engine:

1. **Structural** — `P(low ≤ bb_lower) ≥ P(close ≤ bb_lower)`, likewise for
   the upper band, and `P(confluence) ≤ min(P(touch), P(oversold))`. These
   follow from `low ≤ close ≤ high` and from confluence being a conjunction,
   so a violation is a real detection bug.
2. **Component rates** — `P(k_full ≤ 20)` near 20% (bounded, roughly uniform
   oscillator; measured 15.92%), `P(close ≤ bb_lower)` near 2.5% under
   normality and higher with fat tails (measured 5.29%), `P(low ≤ bb_lower)`
   strictly above it (measured 11.18%).
3. **Headline band** — confluence either side on intraday extremes fires on
   **10-25%** of ticker-days. Deliberately wide: tight enough to catch a
   dropped t−1 shift, loose enough not to encode today's number.

### Phase 4

- Three-arm comparison produces a chart — **open, Session 14.** The three arms and their equity curves exist as of Session 13; the chart does not
- Random-entry null spans 200 replications — **closed, Session 13.** 200 rows in `benchmarks` with `replication` 1-200, reproducible within a `config_hash` and different across configs
- Every headline cell reports `n_eff`, CI, baseline, and q-value — **closed, Session 12**
- Drawdown slice renders — **open, Session 14.** `max_drawdown` is stored per arm as of Session 13
- Random-walk null test passes on the full pipeline — **closed, Session 11.4**

### Phase 5

- Every tool returns a schema-valid response
- Validator rejects a crafted naked-probability response
- Validator allows a sourced advisory response
- MCP server responds to `tools/list` and one live `tools/call`

### Phase 6

- Model beats cell-lookup Brier score on validation, or lookup ships alone
- Reliability diagram renders
- Forward log accumulates predictions and resolves them at T+6
- Promotion gate rejects a deliberately flattened model
