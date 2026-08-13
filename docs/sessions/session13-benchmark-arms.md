# Session 13 — Benchmark arms

Read `DECISIONS.md` (especially ADRs 012, 017, 032, 061, 099, 105, 106), `DESIGN.md` §6.4 through §6.6 and §6.10, and `BUILD.md` first. This document says what to build and in what order. Those say why.

Session 12 answered "does this signal predict anything." Session 13 answers "is it worth running."

---

## 0. Scope

### In scope

1. Buy-and-hold and signal arms on identical universe and dates.
2. Random-entry null, 200 replications, seeded from `config_hash`, one `benchmarks` row per replication.
3. Trim-and-redeploy variant per ADR 017.
4. Four DCA variants per DESIGN §6.6.
5. Pre-tax and post-tax reporting with wash-sale flagging per ADR 032.
6. The three-arm comparison run additionally on the high-breadth subset per ADR 099.

### Out of scope

- The three-arm chart and the drawdown slice. Session 14.
- Volatility-scaled reachability targets. Session 14.
- The ADR 092 matcher replacement. Session 14.
- Any change to detection, indicators, exits, the backtest engine, or `cell_stats`.
- Anything touching the serving store.
- Any execution path. `benchmarks` is a table.

### The one-sentence version

Eight arms written to `benchmarks`, one of which is a 200-row distribution rather than a summary, all on one universe and one date range, with the signal arm's significance read off the 97.5th percentile of that distribution.

### Why this session is the hard one

Session 12 aggregated events that already existed. Session 13 simulates capital: positions open, close, compound, sit in cash, and interact with each other through a shared capital pool. Every arm has to answer "what would this have returned," and a subtle error in one arm produces a plausible number that makes another arm look better or worse than it is.

DESIGN §6.4 states the difficulty plainly. Buy-and-hold is hard to beat by construction: a signal firing on 4% of days with a 5-day hold sits in the market roughly 20% of the time, needing about 5x the annualized rate while deployed to match a name compounding at 30%. If the signal arm comes out ahead, the first question is whether the comparison was fair, not whether the signal is good.

---

## 1. Prerequisites

| Item | Check |
|---|---|
| Session 12 gate passed | Twelve cells in `cell_stats`, ten rendered, two suppressed |
| Breadth terciles re-measured under ADR 104 | Recorded in `RESULTS.md`, superseding the trade-universe values |
| Per-ticker concentration reported | Needed to interpret any arm dominated by one name |
| `benchmarks` table present | Already in `db/schema.sql`, never written to |
| `test_cell_grid_measured.py` fixture guard fixed | See below |

**The fixture guard.** `test_cell_grid_measured.py` crashes with 42 errors rather than skipping when `rho_era` is empty, because its module fixture calls `cell_n_eff` before `test_rho_era_prerequisite_is_populated` can run. The file's own docstring says that test exists so a missing prerequisite "fails first" with a clear signal. It cannot, since it is parametrized off the fixture that crashes. Move the emptiness check into the fixture and `pytest.skip` with an actionable message. Session 13 adds more database-dependent integration tests and will inherit the same pattern if it is not fixed first.

---

## 2. Model assignment

| Task | Model | Reason |
|---|---|---|
| 13.1 Buy-and-hold and signal arms | Haiku | Two arms, specified rules, verifiable against hand-computed cases |
| 13.2 Random-entry null, 200 replications | Sonnet | Seeding, firing-rate matching, and a distribution that must be reproducible |
| 13.3 Trim-and-redeploy | Sonnet | Stateful position tracking with cash accounting |
| 13.4 DCA, four variants | Haiku | Four rules, no exits, positions accumulate |
| 13.5 Pre-tax, post-tax, wash-sale | Sonnet | Date-window edge cases that produce wrong answers rather than errors |
| 13.6 High-breadth subset comparison | Haiku | Re-runs 13.1 and 13.2 on a filtered population |
| 13.7 Tests and documentation | Haiku | Inventory against a settled design |

Order strictly. 13.2 depends on 13.1's exit machinery. 13.5 reads every arm's trade list. 13.6 depends on 13.1 and 13.2.

---

## 3. Task breakdown

### 13.1 Buy-and-hold and signal arms

Two arms, identical universe and identical dates per ADR 012.

**Buy and hold.** Equal-weight the trade universe, rebalance quarterly on universe changes, hold throughout.

**Signal entry.** The actual strategy, using the same exit rules the backtest applies.

Rules:

- Both arms run on the same start date, end date, and universe membership. A signal arm that starts on its first signal and a buy-and-hold arm that starts on day one are not comparable.
- Universe changes come from `universe.as_of`, which is quarterly. A ticker leaving the trade universe is sold at the next rebalance, not immediately.
- Delisted tickers are handled explicitly. `tickers.delisted_on` exists and a position in a delisted name must resolve rather than silently persist. Whatever the rule, state it.
- Report per arm: total return, annualized return, Sharpe, max drawdown, fraction of time deployed, capital efficiency, win rate, and `n_trades`.
- `capital_efficiency = total_ret / frac_deployed`.
- Both arms write to `benchmarks` with `replication = NULL`.

Acceptance:

- A hand-computed three-ticker, twenty-day case reproduces both arms' total return exactly.
- Start and end dates identical across arms, asserted rather than assumed.
- `frac_deployed` for buy-and-hold is 1.0 by construction, and a test asserts it.
- Sharpe uses a documented risk-free rate from config, not a literal.
- A test where the signal arm holds cash for the entire window returns 0.0 rather than dividing by zero in `capital_efficiency`.
- Quarterly rebalance verified against a fixture where a ticker enters and leaves the universe.

### 13.2 Random-entry null, 200 replications

The primary significance test per ADR 061.

Rules:

- Entry dates sample the signal's **empirical firing rate per ticker-year**, not a uniform rate. A ticker that fired 12 times in 2018 gets 12 random entries in 2018.
- Identical exit rules to the signal arm. The null isolates entry timing, so any difference in exit handling contaminates it.
- **Seeded from `config_hash`.** This makes the null reproducible within a config and different across configs, which is what comparing sweep configs requires. Seeding from a wall clock breaks reproducibility; seeding from a fixed constant makes every config share one null. Both wrong versions run without complaint.
- 200 replications on the default and validation configs. 50 during sweeps, escalating to 200 on any anomaly.
- One `benchmarks` row per replication, with `replication` populated 1 through 200. This is what makes the null a queryable distribution rather than a summary.
- The significance criterion is signal performance above the **97.5th percentile** of the null distribution.

Development note: run 50 replications while building, switch to 200 for the gate. DESIGN §6.4 sanctions the split.

Acceptance:

- Two runs against the same `config_hash` produce identical replications. Verified on all 200, not a sample.
- Two different `config_hash` values produce different nulls, asserted.
- Firing-rate matching verified per ticker-year against a fixture where a uniform rate would give a visibly different count.
- Exit rules verified identical to the signal arm, by asserting the same function is called rather than by comparing outputs.
- 200 rows written with `replication` 1 through 200, no gaps, no duplicates.
- The 97.5th percentile is computed from the stored rows, not from an in-memory list that could diverge from what was written.
- A test asserting the null's median is near buy-and-hold scaled by `frac_deployed`. A null wildly off that is a construction bug, and this is the cheapest way to see it.

### 13.3 Trim-and-redeploy

ADR 017's variant, measured against buy-and-hold on the same names.

| Rule | Action |
|---|---|
| Baseline | Buy and hold the trade universe |
| Trim | Sell 20% of the position on `CONFLUENCE_HIGH` or `%K_full >= 80` |
| Redeploy | Buy back on the next `CONFLUENCE_LOW` in the same name |
| Cash idle | Held at the risk-free rate between trim and redeploy |

Rules:

- The 20% fraction and the `%K` threshold come from config, never literals. ADR 092's pattern, and it has already escaped once into `v_positions`.
- A second trim before a redeploy takes 20% of the **remaining** position, not 20% of the original. State which, and test it.
- A position trimmed and never redeployed by the window's end stays in cash. Report it rather than force-closing.
- Report `terminal_value`, Sharpe, `max_drawdown`, `n_round_trips`, `avg_days_in_cash`, and terminal value versus never trimming.

Acceptance:

- Hand-computed case with two trims and one redeploy in one name.
- A test where no `CONFLUENCE_LOW` follows a trim, asserting the cash position persists and is reported.
- Cash accrues at the risk-free rate, verified against a fixture spanning a known number of days.
- `n_round_trips` counts completed trim-redeploy pairs, not trims, and a test distinguishes them.

### 13.4 DCA comparison

Four variants, same total capital, same window, no exits. Positions accumulate and hold.

| Variant | Rule |
|---|---|
| `dca_fixed` | Buy C/12 on the first trading day of each month |
| `dca_signal` | Hold cash, deploy C/N on each signal |
| `dca_hybrid` | Monthly base, double the tranche on a signal day |
| `dca_lump` | Deploy C on day one |

Rules:

- `N` comes from the historical firing rate.
- Leftover capital deploys on the final day. Underfiring is reported explicitly in `capital_undeployed`, since idle capital has a real cost.
- Report `terminal_value`, `irr`, `avg_cost_basis`, `cash_drag`, `capital_undeployed`.

Acceptance:

- All four variants deploy exactly C by the window's end, asserted to the cent.
- A fixture where the signal underfires, verifying `capital_undeployed` is non-zero and the final-day deployment closes the gap.
- IRR verified against a hand-computed case with three cash flows.
- `dca_lump` and buy-and-hold agree on terminal value when the universe is a single ticker, which is a useful cross-arm consistency check.

### 13.5 Pre-tax, post-tax, and wash-sale flagging

ADR 032, marked Provisional. It stays provisional.

Rules:

- Sleeve gains taxed at short-term ordinary rates. The rate comes from config.
- Wash-sale disallowance triggers when a loss-closing position's ticker was bought within 30 days **either side**, including the core position and dividend reinvestment.
- The 30-day window is calendar days, not trading days. This is the most likely place to be quietly wrong.
- Both `pre_tax_ret` and `post_tax_ret` are stored. `wash_sale_flagged` is a boolean per arm.
- This is a modeling assumption for evaluation, not tax advice, and the output says so.

Acceptance:

- A fixture with a loss closed 29 days after a purchase flags; 31 days does not. Both directions tested, since the window is symmetric and only testing one side would miss a one-sided implementation.
- A core-position purchase within the window triggers the flag even when the sleeve alone would not.
- `post_tax_ret <= pre_tax_ret` for any arm with net gains, asserted.
- The disallowance affects the reported number, not just the flag. A flag with no numeric consequence would pass a careless test.

### 13.6 High-breadth subset comparison

ADR 099 requires the three-arm comparison run additionally on the high-breadth subset alone.

This is the direct test of the market-timing hypothesis. If the signal's edge lives on broad-selloff days, it fires exactly when buy-and-hold is also buying cheaply, and the pooled comparison flatters it for the wrong reason.

Rules:

- The high-breadth subset is the top tercile under ADR 104's train-universe denominator.
- All three arms re-run on that subset: buy-and-hold, random entry, signal. Buy-and-hold restricted to the same dates.
- Reported alongside the pooled comparison, never instead of it.

Acceptance:

- Three arms written with a distinguishable `split_key` or era marker so the subset rows are queryable separately.
- The subset's event count matches Session 12's top-tercile count, asserted.
- A test asserting the subset comparison uses the same arm code as the pooled one, not a parallel implementation.

### 13.7 Tests and documentation

Acceptance:

- `TESTS.md` gains the Session 13 inventory.
- `RESULTS.md` gains a Session 13 section: all eight arms with their metrics, the null distribution's percentiles, where the signal arm falls against the 97.5th, the trim comparison, the four DCA variants, and the high-breadth subset comparison.
- `DESIGN.md` §6.4 through §6.6 updated if any rule was resolved differently from the spec.
- `BUILD.md` lists Session 13 and its gate outcome.

---

## 4. Session gate

1. All eight arms written to `benchmarks` on identical universe and dates.
2. The random-entry null holds exactly 200 rows with `replication` 1 through 200.
3. Two runs against one `config_hash` reproduce the null identically; two configs produce different nulls.
4. The signal arm's position against the 97.5th percentile of the null is computed from stored rows and recorded, whichever way it falls.
5. Buy-and-hold's `frac_deployed` is 1.0 and `capital_efficiency` is finite for every arm.
6. All four DCA variants deploy exactly C, with `capital_undeployed` reported.
7. Wash-sale flagging correct at 29 and 31 days on both sides of a purchase.
8. The high-breadth subset comparison runs on all three arms and is recorded alongside the pooled one.
9. Determinism: two runs, identical output ignoring run identifiers.
10. `test_holdout_firewall.py` and `test_schema_drift.py` both pass, the latter having run rather than skipped.

Item 4 is worded deliberately. The gate is that the number is computed correctly and recorded, not that the signal wins. A session gate that requires a favorable result is not a gate.

---

## 5. What will be tempting and should not be done

**Starting the signal arm on its first signal.** Buy-and-hold starts on day one. If the signal arm starts later, it skips whatever the market did in between, and the comparison silently measures a different window. ADR 012's "identical universe and dates" is the whole basis of the comparison.

**Reporting the null's mean instead of its distribution.** The `replication` column exists so the null is queryable per DESIGN §6.10. A summary statistic cannot answer "where does the signal fall," which is the actual test.

**Running 50 replications and calling it done.** 50 is for sweeps where ranking is the goal. The gate needs 200, and the difference matters most in the tail, which is exactly where the 97.5th percentile lives.

**Treating a favorable signal-versus-buy-and-hold result as the answer.** DESIGN §6.4 says buy-and-hold is hard to beat by construction. If the signal arm wins, check the comparison before believing it: identical dates, identical universe, identical exit rules, and `frac_deployed` accounted for. An arm that beats buy-and-hold while deployed 20% of the time is doing something worth understanding, and the first candidate explanation is a bug.

**Skipping 13.6 because the pooled result looks good.** A strong pooled result is precisely when nobody asks whether it is market timing. That is why ADR 099 made the subset comparison mandatory rather than conditional.

---

## 6. What Session 14 needs from this one

| Session 14 needs | From |
|---|---|
| Three arms' equity curves for the chart | 13.1, 13.2 |
| The 200-row null distribution for the percentile band | 13.2 |
| Max drawdown per arm for the drawdown slice | 13.1, 13.3 |
| Trim comparison for ADR 017's two-way ranking | 13.3 |
| High-breadth subset results | 13.6 |

Session 14 also carries the ADR 092 matcher replacement, which is independent of everything here and can proceed regardless of what the arms return.

---

## 7. Rollback

- `benchmarks` is written for the first time. Deleting by `run_id` reverses the session entirely.
- No existing table is modified.
- No migration.
- No consumer reads `benchmarks` until Session 14.
