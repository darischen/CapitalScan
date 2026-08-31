# DECISIONS.md

Architecture decision record for `CapitalScan`.

Format: each entry states the decision, why, and what it costs. Status is one of Pinned, Provisional, or Superseded. Never delete an entry. Mark it Superseded and add the replacement below it.

Last updated: 2026-08-17

Recent structural changes: ADR 097 added 2026-08-08 and reverted 2026-08-09
without ever taking effect (its window emptied the train and validate splits
— read the entry before proposing a lookback window again); ADR 094's
2026-08-05 corrections; ADRs 095 and 096 added 2026-08-06; ADR 002 marked
Superseded; the "Phase gates" block rewritten to the seven-phase numbering
(see the note in that section); body order restored to ADR sequence — 035-040
and 093-094 had drifted outside the ordered run; ADRs 098-100 added 2026-08-10
opening Phase 4 (098 defines rho-bar and therefore every confidence interval
in the statistics layer, 100 records a `v_screen` defect ADR 096 created and
left dormant until `cell_stats` holds two configs); ADRs 101-106 added
2026-08-11 from the first real `n_eff` measurement, which redesigned the
headline grid against measured clustering rather than an assumed sample size
(101-103 shrink and repurpose the grid's dimensions, 104 amends ADR 099's
breadth denominator, 105 adds `cell_stats.arm`, 106 supersedes ADR 016 and
removes the short side's regime filter). ADR 011 is superseded in part by 102
and ADR 016 by 106.

ADRs 107-109 added 2026-08-12 to 2026-08-13 (serving-layer cell selection, the
close-confirmed reversal signal, and the band-date correction to it); ADRs
110-111 added 2026-08-16 to 2026-08-17 and were missing from the index table
until 2026-08-17 — the same defect ADR 094 had, and the reason the index and
body counts are now checked together. ADR 112 added 2026-08-17 recording that
ADR 033's first kill criterion fired: three configurations measured end to end,
zero cells surviving FDR correction on either split. ADRs 013, 017, and the
Phase 6 gate block were corrected the same day to drop "regime-filtered short",
a category ADR 106 superseded on 2026-08-11 and which survived in three places.
ADR 113 added the same day answering ADR 093's Provisional condition, which
ADR 112 had triggered: Phase 6 opens, at twenty heads rather than fifty-six,
with a fifth promotion check and a kill criterion of its own fixed in advance.

---

## Index

| ID | Title | Status |
|---|---|---|
| 001 | Two universes: train wide, trade narrow | Pinned |
| 002 | Ten years of history, 2015 forward | Superseded by 035 and 040 |
| 003 | Compute indicators in-house | Pinned |
| 004 | Indicator parameters pinned once | Pinned |
| 005 | Signal fires on intraday touch, not close | Pinned. Scope narrowed by 108 |
| 006 | One signal implementation, shared by backtest and live | Pinned |
| 007 | Five exit conditions, measured separately | Pinned |
| 008 | Stop scaled to ATR, fixed 3% as control | Pinned |
| 009 | Target swept over 3% to 5% | Pinned |
| 010 | Intrabar ordering resolved conservatively | Pinned |
| 011 | Continuous features, headline grid capped at 12 cells | Superseded in part by 102 (the cap survives as a number; the reasoning does not) |
| 012 | Benchmark is three arms plus DCA comparison | Pinned |
| 013 | Baseline computed per ticker-year | Pinned |
| 014 | Health filter is causal and re-evaluated quarterly | Pinned |
| 015 | Drawdown from 52-week high is a first-class dimension | Pinned |
| 016 | Short side restricted to failed-health regime | Superseded by 106 |
| 017 | Upper-band signal drives trim, not naive short | Pinned. Expected ranking amended by 106 |
| 018 | Options deferred to Phase 7 | Pinned |
| 019 | Train, validate, holdout splits by date | Pinned |
| 020 | FDR correction on the cell grid | Pinned |
| 021 | Nothing heavy runs in a request path | Pinned |
| 022 | Neon Postgres as the single store | Superseded by 053 |
| 023 | Job hosting split across Actions and Modal | Superseded by 039/080 |
| 024 | Five-minute polling during market hours | Provisional |
| 025 | No text-to-SQL, six typed tools only | Superseded by 074 |
| 026 | Guardrails enforced in code, not in the prompt | Pinned |
| 027 | MCP server built after Phase 4 | Provisional |
| 028 | Quantile LightGBM, no reinforcement learning | Pinned |
| 029 | Forward log is immutable | Pinned |
| 030 | Model promotion gated on calibration | Superseded by 067 |
| 031 | VIX stored as a daily column | Pinned |
| 032 | Tax reported pre and post | Provisional. Amended 2026-08-13: long-term rate added |
| 033 | Kill criteria fixed in advance | Pinned |
| 034 | Provenance on every generated row | Pinned |
| 035 | Union universe, 2010 start, ADRs only | Pinned |
| 036 | SEC EDGAR historical earnings, Finnhub forward | Pinned |
| 037 | Hourly bar archive starts now | Pinned |
| 038 | Neon Launch tier | Pinned |
| 039 | Local hardware for development | Pinned |
| 040 | Ingest 2009, events 2010 | Pinned |
| 041 | float64 in compute, numeric in storage | Pinned |
| 042 | Delisted tickers retained, era reporting instead | Pinned |
| 043 | Advisory only, no execution path in v1 | Pinned |
| 044 | Fast Stochastic agreement as a tolerance | Pinned |
| 045 | Stochastic crossover is a feature, never a gate | Pinned |
| 046 | Mid-band exit optional, default off | Pinned |
| 047 | Three-number output contract: terminal, peak, reachability | Pinned |
| 048 | Execution-ready seams built now, execution absent | Pinned |
| 049 | v1 scope is detection, scanning, notification | Pinned |
| 050 | Live call overlay, no historical option backtest | Pinned |
| 051 | Indicator registry with declared warmup | Pinned |
| 052 | Progress reporting standard | Pinned |
| 053 | Two-store split: local research, cloud serving | Pinned |
| 054 | Notifier protocol, three channels | Pinned |
| 055 | Frozen universe CSV committed to the repo | Pinned |
| 056 | Cluster tagging replaces position blocking | Pinned |
| 057 | Multi-signal emits one row with strength | Pinned. Short ranking extended by 108 |
| 058 | Both sides computed, only long surfaced | Pinned. Surfacing clause superseded 2026-08-13 |
| 059 | Default config validated before sweeping | Pinned |
| 060 | Backtest is deterministic, config-hashed | Pinned |
| 061 | Randomization null over parametric tests | Pinned |
| 062 | Dual baselines, empirical headline | Pinned |
| 063 | LightGBM over sequence models | Pinned |
| 064 | Eleven independent heads, timeout-return targets | Pinned |
| 065 | Purged walk-forward CV, cluster-weighted | Pinned |
| 066 | Nightly inference, t-1 invariant | Pinned |
| 067 | Four-check promotion gate | Pinned |
| 068 | Single model, sector as categorical | Pinned |
| 069 | Live inference at trigger, nightly batch for screener | Pinned. Supersedes 066 |
| 070 | Serving API is pure SQL, ten endpoints | Pinned |
| 071 | Auth deferred to final development stage | Pinned |
| 072 | Shared handler layer, two transports | Pinned |
| 073 | Personal trade log and exit push notifications | Pinned |
| 074 | Seven tools, closed enums | Pinned |
| 075 | Validator requires sourcing, not advice avoidance | Pinned |
| 076 | Database views are the shared contract | Pinned. Refines 072 |
| 077 | Model choice is config, Haiku-first routing | Pinned |
| 078 | Inference stays with the poller, not on Vercel | Pinned |
| 079 | Three environments, no staging | Pinned |
| 080 | Task Scheduler with catch-up, scheduled_runs table | Pinned |
| 081 | Native Windows, Linux only in the Postgres container | Pinned |
| 082 | Allowlist WIP snapshots, no full-repo auto-commit | Pinned |
| 083 | Backup tiers, forward log is the irreplaceable asset | Pinned |
| 084 | Poller gaps accepted and logged | Pinned |
| 085 | Test tiers weighted by failure silence | Pinned |
| 086 | External indicator verification, semi-automated | Pinned |
| 087 | Random-walk null test is the primary statistical guard | Pinned |
| 088 | cell_id is derived; serving views pin split to validate | Pinned |
| 089 | MFE unclamped; look-ahead guarded by signature, not shift alone | Pinned |
| 090 | Absence is None, never NaN; debounce keys on a tuple | Pinned |
| 091 | Config resolution lives in jobs, not core | Pinned |
| 092 | Exit thresholds are ExitParams fields, never literals | Pinned |
| 093 | Terminal quantiles expand to five horizons; peak-within-horizon family added 2026-08-09 | Provisional. Authorizes a Phase 6 rewrite of DESIGN §7.4 |
| 094 | Path table is source of truth; labels are derived views | Pinned |
| 095 | Exit thresholds in SQL are a second exit implementation | Provisional. Authorizes a Phase 5 rebuild of `v_positions` |
| 096 | `cell_stats` is keyed by config, not by cell alone | Pinned |
| 097 | Backtest reads events only within a rolling 2-year window | Superseded by 019/009. Reverted 2026-08-09, never in effect |
| 098 | Effective sample size: overlapping windows, co-fire weighting, stored per-era table, factor-implied diagnostic | Pinned |
| 099 | Market breadth as a reporting split, not a grid dimension | Pinned |
| 100 | Three Phase 4 contract corrections: v_screen config scope, cell_key wording, exit_mix semantics | Pinned |
| 101 | Drawdown buckets reduced from four to two; 20%+ measured and permanently suppressed | Pinned |
| 102 | Headline grid is 2 sides x 3 signals x 2 buckets, pooled across eras | Pinned. Supersedes ADR 011's grid composition. Amended by 108: fourteen cells |
| 103 | Era is reporting only, never a tested family; era 2024+ is the holdout | Pinned |
| 104 | Breadth denominator is the train universe, not the trade universe | Pinned. Amends ADR 099 |
| 105 | `cell_stats.arm` separates measured populations from recommended ones | Pinned |
| 106 | Short entries use the same universe and criteria as long | Pinned. Supersedes ADR 016 |
| 107 | `v_screen` selects the cell pooled over `signal_strength` | Pinned |
| 108 | Close-confirmed detection, alongside intraday touch | Pinned. Amends 005's scope and 057's ranking. Band date amended by 109 |
| 109 | Close-confirmed band is the same day's, not the prior day's | Pinned. Amends 108 |
| 110 | The raw %K is the trigger; the smoothed %K must agree within 5 points | Pinned. Supersedes the 2026-08-05 `stoch_source` A/B note |
| 111 | `--actionable`: a long needs confluence, a short needs confluence and confirmation | Pinned. Serving-layer only |
| 112 | ADR 033's first kill criterion fired: the two-indicator hypothesis is retired as a standalone returns predictor | Pinned. Fires 033. Retires 015's central claim; downgrades 093's prior |
| 113 | Phase 6 opens: the model is a distinct hypothesis from the cell grid, sized down and gated harder | Pinned. Answers 093's Provisional condition; amends 064's head count and 067's gate |
| 114 | The screener's default is the event feed; statistics are one action away | Pinned. Follows 112. Governs `screen_signals` and `/` |
| 115 | `v_positions` reads a settings row, not a generated DDL | Pinned. Resolves 095's deferred rebuild; reverses its stated preference |
| 116 | `v_ticker_state` reads one row per ticker instead of sorting three million | Pinned. 1000x on `v_positions`; corrects a Session 15 measurement |
| 117 | The poller reports every confluence and says how far it is from a reversal | Pinned. Amends 108's display half; diverges from 111 on purpose |
| 118 | MCP is for LLM callers; deterministic reads go straight to the views | Pinned. Resolves the Session 17 blocker; confirms 070 and 076 |
| 119 | The screener shows what fired today; `market_date()` defines "today" | Pinned. Adds `v_screen_live`; fixes `v_screen`'s missing config filter |
| 120 | `v_chart` returns one row per bar; markers are arrays | Pinned. 963 rows for 275 days; applies 119 to the last view missing it |
| 121 | The ticker chart pages its own history through an API route | Pinned. Right edge stops at today; dragging left fetches a year |
| 122 | Detection records trade-universe membership instead of being filtered by it | Pinned. SMCI: 192 band touches, 0 events. Statistics population unchanged |
| 123 | The screener shows the poller's intraday reversal, marked as intraday | Pinned. Surfaces 117's live judgement; three renderings, not two |
| 124 | The screener is a record of the session, not a clustered sample | Pinned. 19 fires became 4 overnight; a feed is not a sample |
| 125 | A migration carries its SQL as a literal, never as an import | Pinned. ADR 122 broke every fresh replay; applied to all seven |
| 126 | `v_stats` projects `arm` | Pinned. The screener's stats toggle had been broken since Session 17 |
| 127 | The poller clock is timezone-aware | Pinned. Every stored poller timestamp was four hours early |
| 128 | Today's session is a candle in its own table | Pinned. `bars_live`; a partial row in `bars` would be silent look-ahead |
| 129 | `in_trade` fails closed | Accepted, not executed. 11.9% of train was admitted by a missing snapshot |
| 130 | The chat route holds the tool loop, and holds nothing else | Pinned. Implements ADR 118 for `/chat`; no Python runs in the request path |
| 131 | What moves during a session is polled by the client, not re-rendered by the server | Pinned. Completes ADR 128 |
| 132 | The caller names the grain; the default is the compatible one | Pinned. Closes backlog item 2; `GRID_ENTRY_KIND` unchanged |
| 133 | The ticker history reads a grain that is defined for every signal type | Pinned. Adds `v_ticker_events` |
| 134 | A live price exists only during the session | Pinned. Adds `market_is_open()`; completes ADR 131 |
| 135 | A universe evaluation must rest on data from inside the period it describes | Pinned. First consumer for `UniverseParams.rebalance_freq`; no `config_hash` move |
| 136 | No edge interval is stored, and the rate interval answers the question | Pinned. Rejects DESIGN 11.2's edge bar as specified |
| 137 | The served subset is three years, and ADR 053's sizing predates the chart | Pinned. Amends ADR 053's estimate, not its decision; implements `cscan sync` |
| 138 | The deployment authenticates itself, and opening it is an explicit act | Pinned. Currently disabled by request — `SITE_AUTH_DISABLED=1`, open in `BACKLOG.md` |
| 139 | The screener links out to a stored chart, and the indicators live on someone else's server | Pinned |
| 140 | The nightly is authoritative for every grain, and the poller's observation is not an entry price | Pinned |
| 141 | The screener sorts in SQL over the whole day, and shows all of it | Pinned |
| 142 | Agreement means agreeing about the state, not being close | Pinned. Supersedes ADR 044's rule as the sole test; forces a `config_hash` move and a full rebuild |
| 143 | The universe is seeded from two sources, and the floor drops to $20B | Pinned. Moves `config_hash` to `f66729c7eda212a4` |
| 144 | The long side gets its own close-confirmed type, defined dormant | Pinned, **not enabled**. Mirrors ADR 108/109 onto the long side |
| 145 | Market cap prices split-adjusted closes against split-adjusted shares | Pinned. Extends ADR 014's filter; same defect class as `adr_adjusted_shares` |
| 146 | The x1,000 share-scale class is caught by local shape, not by bounds | Pinned. Closes the gap `SharesPlausibility` documented and left open; extends ADR 145's rebuild |
| 147 | ETFs do not train; a missing sector stops the build | Pinned. Implements ADR 068 for Phase 6; blocks Session 22; no `config_hash` move |
| 148 | `tickers.sector` speaks one vocabulary, and it is GICS | Pinned. Completes 147 and tightens its gate; unblocks Session 22; no `config_hash` move |
| 149 | The watch universe is a sibling of the trade universe, not a relaxation of it | Pinned. Extends ADR 001 to three universes; `in_trade` and ADR 112's population untouched; no `config_hash` move |
| 150 | A provisional poller row is superseded at the nightly, not indefinitely | Pinned. Completes ADR 140; the nightly sweeps unreconciled `poll_` rows; no `config_hash` move |
| 151 | `run_events` does not tag clusters | Pinned. Enforces Ruling C5; cluster-head filtering in `cell_stats` is unchanged and still load-bearing for ADR 112 |
| 152 | Postgres on the Pi waits for the network rather than binding a wildcard | Pinned. Enforces PI_MIGRATION §2; boot-order drop-in on the cluster unit; no `config_hash` move |
| 153 | The poller pushes to serving every tick | Pinned. Extends ADR 053 with a second sync path; gives ADR 150's sweep a serving target; no `config_hash` move |
| 154 | An ETF is in the trade universe unconditionally | Pinned. Amends 149's `crit_mcap` requirement for funds; completes 147; ADR 112 wants re-measuring |
| 155 | Quantile coverage fails and DESIGN §7.6 has no repair for it | **Decided 2026-08-26: option C**, conformalised, calibrated on normal years excluding 2022 |
| 156 | ETF market cap: `netAssets` disagrees with `sharesOutstanding` | **Decided 2026-08-26: option B via (i)** — store `netAssets / close` with its own `source` |
| 157 | `events.sector` is a current snapshot, and that is accepted look-ahead | **Decided 2026-08-28.** Moved out of `BACKLOG.md`; no point-in-time GICS source exists, `universe.mcap_usd` shows the shape a fix would take |
| 158 | The poller should write serving directly, not research | **Proposed 2026-08-28, not implemented.** Removes research from the live write path; frees the workstation during market hours |
| 159 | Superseded sweep generations are archived out of the database, not kept in it | **Decided 2026-08-31, applied.** Narrows ADR 096; 12 arms moved to gzipped CSV, DB 48 GB -> 19 GB; no `config_hash` move |

---

## 001. Two universes: train wide, trade narrow

Status: Pinned

Decision. Ingest 500 tickers for statistical power. Trade and display a filtered subset of 30 to 60 mega-caps chosen by the rule in ADR 014.

Rationale. Confluence signals fire on roughly 4% of days. Ten tickers over two years yields around 200 raw events, and clustering on market-wide down days cuts the effective sample to about 38. Standard error at $n_{eff} = 38$ and $p = 0.4$ is 7.9 points, so the detectable effect is 22 points. The target effect sits between 5 and 15 points. Widening the training universe is the only way to see it.

Cost. Ingest and storage grow, both to a level with no practical impact. Roughly 1.25M daily bars, under 200MB.

Schema. `universe_train` and `universe_trade` as separate flags on the `universe` table.

---

## 002. Ten years of history, 2015 forward

Status: Superseded by 035 and 040 (marked 2026-08-06)

**Superseded.** ADR 035 moved the training universe to the union of every S&P
500 member with a 2010-01-01 event start. ADR 040 then moved bar ingest back
to 2009-01-01, using the extra year as indicator warmup. Neither date below is
current: the pipeline ingests from 2009 and detects events from 2010. This
entry stayed marked Pinned until 2026-08-06, so anything written against it
before that date may carry the 2015 assumption.

Decision. Ingest daily bars from 2015-01-01. Extend to 2005 if the effective sample falls short after clustering adjustment.

Rationale. Two years covers 2024 to 2026 only, a period where dip-buying worked. Testing a dip-buying rule on a window selected for dip-buying success produces a result with no forward validity. Ten years covers 2018 Q4, 2020, and 2022, which is where you learn whether the pattern survives a drawdown.

Cost. Older data carries more corporate-action risk and more delisting gaps.

---

## 003. Compute indicators in-house

Status: Pinned

Decision. Compute all indicator values from raw adjusted OHLCV. Do not download precomputed indicator series.

Rationale. Vendor series carry unknown parameter choices (population versus sample standard deviation, SMA versus EMA middle band), unknown split handling, and no point-in-time guarantee. Computing them is a few lines of pandas. Downloading them adds a silent-bug class and a coverage dependency, and blocks derived fields like bandwidth percentile and drawdown.

Cost. One hour of implementation.

---

## 004. Indicator parameters pinned once

Status: Pinned

Decision. Written here, referenced everywhere, never duplicated in a second file.

Bollinger, 20-period, 2 standard deviations, population standard deviation (ddof=0), on adjusted close:

$$\text{mid} = SMA_{20}(C), \quad \text{upper} = \text{mid} + 2\sigma_{20}, \quad \text{lower} = \text{mid} - 2\sigma_{20}$$

$$\%B = \frac{C - \text{lower}}{\text{upper} - \text{lower}}, \qquad \text{BW} = \frac{\text{upper} - \text{lower}}{\text{mid}}$$

Fast Stochastic (14, 3):

$$\%K_{fast} = 100 \times \frac{C - L_{14}}{H_{14} - L_{14}}, \qquad \%D_{fast} = SMA_3(\%K_{fast})$$

Full Stochastic (14, 3, 3):

$$\%K_{full} = SMA_3(\%K_{fast}), \qquad \%D_{full} = SMA_3(\%K_{full})$$

Support:

$$SMA_{200}, \quad \text{slope}_{60}(SMA_{200}), \quad ATR_{14}, \quad RV_{20d}, \quad DD_{52w}$$

All returns use adjusted close. All indicator inputs use adjusted OHLC. Unadjusted prices fabricate signals across splits.

---

## 005. Signal fires on intraday touch, not close

Status: Pinned. Scope narrowed 2026-08-13 by ADR 108 — see the dated note below.

Decision. A band breach registers when the intraday high or low crosses the band level, not when the close does.

Rationale. Matches the live rule, where a 5-minute poll compares the current price against bands fixed at the prior close. Also roughly triples event count. Close-based breach runs 4 to 6% of days. Intraday breach runs 12 to 18%, and confluence with a Stochastic extreme lands near 3 to 6%.

Cost. Entry price is uncertain within the poll window. ADR 007 handles this by measuring four entry timings.

**Scope note, 2026-08-13 (ADR 108).** This entry describes the **band-touch family** — `BB_LOWER_TOUCH`, `BB_UPPER_TOUCH`, and the confluence types built on them — and stands unchanged for all of them. It is no longer a claim about every signal in the system: ADR 108 adds a close-confirmed model beside it, where the condition is a statement about where the bar finished relative to where it opened and therefore cannot be expressed as an intraday touch. Both models date their events to D and fill `next_open` at D+1; the 4-to-6% figure above is the argument against making the *band breach itself* close-based, not against close-confirmed detection generally.

---

## 006. One signal implementation, shared by backtest and live

Status: Pinned

Decision. `core/signals.py` exposes `detect(bar, ind)` and `breach(price, bands)`. The scheduler and the backtest both import it. No second implementation exists anywhere in the repo.

Rationale. Divergence between the live rule and the backtested rule is the failure mode where the live system fires on events the backtest never measured, and you find out months later through unexplained losses.

Enforcement. A test asserts the live path and the backtest path produce identical events on a shared fixture.

---

## 007. Five exit conditions, measured separately

Status: Pinned

Decision. Position exits at whichever fires first:

1. Timeout at day 5
2. Price touches the upper Bollinger band
3. %K reaches 80 or above
4. Price reaches the profit target, swept 3% to 5%
5. Stop loss, per ADR 008

$$\text{exit} = \min(t_{upper}, t_{stoch80}, t_{target}, t_{stop}, t+5)$$

Each condition is backtested alone and then in combination, so you know which one carries the result.

Consequence worth noting. Holding for up to 5 days with no re-entry while holding makes events non-overlapping by construction. The standard error inflation from overlapping forward windows disappears. No Newey-West adjustment needed.

Also required. Record max favorable excursion, max adverse excursion, and time-to-MFE per event. The time-to-MFE distribution tells you whether 5 days is the right cap. Expect the median near 2 to 3 days.

Entry timings measured, all four: at touch, touch plus 5 minutes, touch plus 30 minutes, next open. If edge is flat across all four, polling speed does not matter. If edge decays sharply between 5 and 30 minutes, the signal is microstructure noise rather than a 5-day effect and the premise needs rework.

---

## 008. Stop scaled to ATR, fixed 3% as control

Status: Pinned

Decision. Primary stop is $k \times ATR_{14}$ with $k$ swept over 1.0, 1.5, 2.0, 2.5. Fixed 3% and no-stop are both run as controls.

Rationale. Entry happens at a volatility extreme, where realized vol sits above the name's average. At 2.5% daily vol, first-passage probability of touching minus 3% within 5 days is

$$P \approx 2\Phi\left(\frac{-0.03}{0.025\sqrt{5}}\right) \approx 59\%$$

against 47% for a plus 4% target. The fixed stop fires more often than the target, and past 3% daily vol the stop-out rate exceeds 70%. A fixed percentage converts a mean-reversion trade into a trade ending mostly in small losses. ATR scaling holds the stop-out rate roughly constant across names and regimes.

Alternative kept on the bench. Structural stop, exiting below the entry-day low or on %B falling a set amount below its entry value. Encodes setup failure rather than an arbitrary number.

Expected result. A stop improves worst-case drawdown and reduces mean return. ATR dominates fixed 3% on both. Whether the drawdown reduction justifies the return cost is a judgment call made after seeing the numbers.

---

## 009. Target swept over 3% to 5%

Status: Pinned

Decision. Profit target is a swept parameter, not a constant. Sweep on train, select on validate, report on holdout once.

Rationale. Reporting the full target-by-stop surface shows whether the result is robust or balanced on one lucky parameter pair. Reporting only the best cell hides the difference.

Interim note. Until the quantile model exists in Phase 3, the "predicted peak" exit uses the fixed ladder. MFE data replaces it later.

---

## 010. Intrabar ordering resolved conservatively

Status: Pinned

Decision. When a daily bar's low hits the stop and its high hits the target, assume the stop fired first.

Rationale. Daily OHLC carries no ordering information. The conservative assumption avoids inventing profit.

Escalation. Log ambiguity frequency. Above 10% of events, pull hourly bars for those dates and resolve ordering properly.

---

## 011. Continuous features, headline grid capped at 12 cells

Status: Superseded in part by 102, 2026-08-11. The twelve-cell cap survives as a number by coincidence; the composition and the reasoning behind it do not. The `signal_strength` cut this entry assumed cannot exist (`signal_strength` is constant per signal type), a third signal type was omitted, and the "~2,000 effective events" figure was an assumption that the first `n_eff` measurement replaced. The principle that most conditioning stays continuous and feeds the model is unchanged and still governs.

Decision. Store %B, %K, bandwidth percentile, drawdown, and SMA slope as continuous numbers. The empirical headline table holds at most 12 cells. Interactions get found by the model in Phase 3, not by pre-binning.

Rationale. Full binning across band level, stochastic state, fast-full agreement, drawdown bucket, and trend produces $3 \times 3 \times 2 \times 4 \times 2 = 144$ cells. With around 2,000 effective events, the average cell holds 14, and clustering means roughly 20 cells carry 90% of events while the rest hold zero to three. A hit rate on 3 events is noise wearing a percentage sign. Pre-binning discards information and multiplies the multiple-testing burden at the same time.

Note. Feature width is not the issue. Ten fields per ticker-day is 12.5M numbers total, under 200MB. Storage never constrains this project.

---

## 012. Benchmark is three arms plus DCA comparison

Status: Pinned

Decision. Every result reports against three arms on the identical universe and dates:

1. Buy and hold from day one
2. Random entry at the signal's firing frequency, same holding rule
3. Signal entry

Arm 3 against arm 2 isolates the indicator. Arm 3 against arm 1 decides whether the strategy is worth running.

Additional comparison. Fixed-schedule monthly DCA against signal-triggered DCA, equal total capital, same window.

Rationale. Drift is part of the claimed mechanism rather than a confound, so an unconditional baseline is meaningless. Beating buy-and-hold is hard by construction: a signal firing on 4% of days with a 5-day hold sits in the market roughly 20% of the time and needs about 5x the annualized rate during deployed periods to match a name compounding at 30%.

Reporting requirement. Report Sharpe, max drawdown, and capital efficiency, not raw return alone.

$$\text{Capital efficiency} = \frac{\text{total return}}{\text{fraction of time deployed}}$$

Expected outcome. Signal-timed accumulation beats fixed-schedule accumulation by a small real margin, and both lose to lump sum. If so, the product is an accumulation timing rule rather than a standalone trading system, which matches the actual use case.

---

## 013. Baseline computed per ticker-year

Status: Pinned

Decision. Compute the unconditional hit rate per ticker per year from trailing realized drift and volatility. No pooled cross-sectional baseline.

Rationale. Selecting on growth hands you free baseline points. At 30% annualized drift and 40% annualized vol:

$$\mu_{5d} \approx 0.60\%, \quad \sigma_{5d} \approx 5.6\%, \quad P(R_{5d} \geq 2\%) \approx 40.1\%$$

The same calculation at zero drift gives 36.1%. Four points arrive before any indicator fires. A pooled baseline overstates edge on exactly the names under study.

---

## 014. Health filter is causal and re-evaluated quarterly

Status: Pinned

Decision. Trade-universe membership follows a rule evaluated only on information available at $t-1$. Re-evaluated quarterly. Membership changes on its own with no manual review.

Criteria, each stored separately so any one is ablatable:

- Market cap above $200B as of the prior quarter-end
- Close above the 200-day SMA
- 200-day SMA slope positive over the trailing 60 days
- Trailing 3-year total return above the sector median
- Revenue growth positive over the last four reported quarters

Rationale. Picking names in 2026 knowing their outcome measures whether those names went up. The filter has to select Intel in 2019, Cisco in 2007, and Netflix in 2021, then record what happened. Those are the cases where the filter said yes and the pattern broke.

Role. The 200-day SMA and its slope are a health filter, not a signal component. Bollinger and Stochastic generate the entry. The filter decides which names are eligible.

Note, 2026-08-02. The market-cap criterion's threshold moved from $200B nominal to $100B nominal (`core.config.UniverseParams.min_mcap_usd`, Session 9 config-thresholds task, user's decision). CPI deflation was considered and rejected as the mechanism: deflation factors across the ingest window span only 0.677 (2010) to 1.000 (2025), a 47% move, far short of the 2x change made here. The actual motivation is candidate-pool size: in 2010, 23 tickers already cleared $100B nominal and 6 cleared $200B, yet `in_trade` was zero for the entire universe that year regardless, because `is_tradeable` requires all four criteria in `required_criteria` and the SMA-200 / slope / relative-return conditions were the binding ones, not market cap. Widening the threshold widens the candidate pool; it does not, by itself, fix the historical emptiness this note describes. This changes `config_hash` for every `Config` (ADR 060) since `UniverseParams` is a field of it.

Note, 2026-08-03. The threshold moved again, $100B nominal to $30B nominal (`core.config.UniverseParams.min_mcap_usd`, Session 10, user's decision). Same motivation as the 2026-08-02 note: wider candidate-pool coverage, admitting large-cap names below the mega-cap band rather than fixing any specific historical emptiness. Also changes `config_hash` for every `Config` (ADR 060).

---

## 015. Drawdown from 52-week high is a first-class dimension

Status: Pinned

Decision. Report edge separately across drawdown buckets: 0 to 10%, 10 to 20%, 20 to 35%, above 35%.

Rationale. This is the Meta-versus-Intel problem. Meta 2022 fell about 75% and recovered fully. Intel 2020 through 2024 fell about 70% and did not. Same size filter, same trend filter at entry, opposite outcomes. Cisco 2000 fell 86% and every step down was a lower-band confluence touch. A regime break and a routine dip look identical at the moment of the touch, so the separating information has to live in the feature set or the strategy carries an unmeasured left tail.

Hypothesis under test. Edge is positive and stable in the first two buckets and turns negative past 35%, because deep mega-cap drawdowns are usually thesis breaks. If that holds, the rule is: trade shallow dips, stand down on deep ones. That single cut is potentially worth more than either indicator.

---

## 016. Short side restricted to failed-health regime

Status: Superseded by 106, 2026-08-11. Retained in full deliberately: this entry made a falsifiable prediction (band walking) before any measurement existed, and Session 12 measures exactly the quantities that settle it. A prediction on record from before the result is worth more than one reconstructed after. The regime filter it specifies was never implemented in code.

Decision. Short entries require at least one of: close below the 200-day SMA, 200-day slope negative, or drawdown past 20%. Naive always-short on upper-band confluence is run only as a control.

Rationale. The short is not the mirror of the long, for three reasons.

Drift runs against the position. On a name compounding at 25%, the 5-day drift near 0.50% helps a long and hurts a short, costing roughly a point of hit rate before anything else.

Band walking is the larger issue. During strong advances, price rides the upper band for weeks while %K stays pinned above 80, printing touches nearly every day. The upper signal fires most often during exactly the periods a short bleeds worst, and most often in the names the health filter selected. Fear-driven selloffs resolve in days. Greed-driven advances grind for months. There is no lower-band equivalent.

Loss geometry. A losing short grows as a share of capital while a losing long shrinks. Mega-cap earnings gaps of 8 to 15% are routine, and a 5-day window contains earnings roughly 8% of the time.

Coherence. The long trades dips inside uptrends. The short trades rips inside downtrends. Same indicators, opposite regime, non-overlapping universes at any given time.

---

## 017. Upper-band signal drives trim, not naive short

Status: Pinned. Expected ranking amended by 106, 2026-08-11: "regime-filtered short" no longer exists as a category, so the comparison is trim-and-redeploy against unfiltered short. Everything else in this entry stands, and trim-and-redeploy remains a Session 13 arm.

Decision. Primary use of the upper-band and %K-above-80 signal is trimming a held core position, with redeployment on the next lower-band signal. Backtested directly against buy-and-hold on the same names.

Rationale. Carries no borrow cost, no gap risk, no wash-sale interaction, and no undefined loss. Matches the actual described behavior of selling into strength and buying back on weakness.

Expected ranking. Trim-and-redeploy beats the short on risk-adjusted terms, and the short likely shows negative edge from band walking. (Amended 2026-08-11 by ADR 106: "regime-filtered short" no longer names a distinct category, so the original three-way ranking — trim-and-redeploy above regime-filtered short above naive short — collapses to two-way.)

---

## 018. Options deferred to Phase 7

Status: Pinned

Decision. v1 trades long shares and short shares only. No calls, puts, or spreads. (Amended 2026-08-11 by ADR 106: this originally read "regime-filtered short shares", a category ADR 016 defined and 106 superseded. The scope this entry actually fixes is shares versus derivatives, which is unchanged.)

Rationale. Yahoo provides current option chains and no history. Any options backtest built on reconstructed prices is fiction unless labeled as such. Synthetic Black-Scholes pricing gets IV wrong on skew, misses post-earnings IV crush of 30 to 50%, and ignores spreads of 1 to 3% of premium on liquid weeklies. Theta alone costs a 30-day ATM option roughly 8.7% of value over a 5-day hold before any move.

Sequence when resumed. Build the synthetic layer, label it synthetic in the UI and the writeup, then buy one month of Polygon and measure synthetic pricing error against real chains. The error measurement is itself the deliverable.

---

## 019. Train, validate, holdout splits by date

Status: Pinned

Decision.

- Train: 2015 through 2021
- Validate: 2022 through 2023
- Holdout: 2024 to present, touched exactly once, at the end

`split_key` is assigned to each event at creation time by date. It is never computed at query time.

Rationale. Query-time splitting leaks. Assignment at creation makes leakage a schema violation rather than a discipline problem.

---

## 020. FDR correction on the cell grid

Status: Pinned

Decision. Apply Benjamini-Hochberg false discovery rate control across all tested cells. Store both `p_value` and `q_value`.

Rationale. Testing 72 cells at $\alpha = 0.05$ yields roughly 3.6 false positives by chance. Without correction, the headline finding is likely one of them.

Power reference, standard error of a proportion:

$$SE = \sqrt{\frac{p(1-p)}{n}}, \qquad n = \frac{(z_{\alpha/2} + z_\beta)^2 p(1-p)}{\delta^2}$$

At $p = 0.4$, 80% power, $\alpha = 0.05$: a 10-point edge needs about 190 events, 5-point needs about 750, 3-point needs about 2,100.

Clustering adjustment, applied to every reported $n$:

$$n_{eff} \approx \frac{n}{1 + (\bar{k} - 1)\bar{\rho}}$$

where $\bar{k}$ is average same-day co-firing count. Report $n_{eff}$ alongside raw $n$ everywhere. Log co-firing count per event.

---

## 021. Nothing heavy runs in a request path

Status: Pinned

Decision. Every number shown in the UI or returned by a tool was computed offline and written to a table. The serving layer does indexed lookups only.

Rationale. Keeps p95 latency under 200ms, keeps Vercel inside its function limits, and makes the chat layer cheap. Indicator values for closed bars never change, so cache TTLs are long with invalidation only on the current day.

Consequence. Model inference happens nightly for all tickers and writes to `predictions`. No inference in the request path.

---

## 022. Neon Postgres as the single store

Status: Superseded by 053. The branching workflow below still applies, but on the local research instance.

Decision. One Postgres database. (Table list from this draft is stale; the authoritative schema is DESIGN §2.5, §5.7, §6.9, §6.10, §7.8, §8.5. Notably the proposed `regime` table was never built, since `universe` already stores quarterly per-criterion evaluation.)

Rationale. Scale-to-zero pricing, branching, and one query language across research and serving.

Branching workflow. Each weekly backtest writes `cell_stats` to a database branch. Diff against main. Merge manually after review. Backtest results become reviewable artifacts instead of silent overwrites.

---

## 023. Job hosting split across Actions and Modal

Status: Superseded by 039, 080, and 081. All jobs run locally on Windows under Task Scheduler. Modal is not used. GitHub Actions runs exactly one workflow, the 6-hourly health check.

Decision. GitHub Actions runs nightly ingest, indicator computation, event detection, outcome resolution, and scoring. Modal runs backtests, training, and parameter sweeps.

Rationale. Actions is free at 2,000 minutes monthly with cron built in. Modal handles long-running jobs with no timeout and per-second billing. Neither belongs on Vercel.

Known issue. Actions cron drifts 5 to 15 minutes under platform load. Acceptable for nightly work, not for market-hours polling. See ADR 024.

---

## 024. Five-minute polling during market hours

Status: Provisional

Decision. Poll every 5 minutes using batched quote requests of 50 to 100 symbols per call. Roughly 12 calls per hour covers 600 tickers.

Rationale. Compute is not the constraint. Bands are fixed after the prior close, so a tick is two comparisons per ticker. The constraints are the Yahoo rate limit, undocumented and roughly 100 to 2,000 requests hourly, and quote staleness of up to 15 minutes on some symbols. A one-minute poll against 15-minute-stale data is theater.

Debounce. One event per ticker per band per day, first touch only.

Revisit trigger. Tighten the interval only if the entry-timing sweep in ADR 007 shows edge decaying between 5 and 30 minutes. Move polling to a persistent Modal function once the live forward log matters, since Actions cron drift corrupts touch timestamps.

---

## 025. No text-to-SQL, six typed tools only

Status: Superseded by 074 (seven tools). The no-text-to-SQL principle and the closed-enum requirement remain in force.

Decision. The chat layer calls six functions with closed enums and validated ranges. No model-generated SQL reaches the database.

- `screen_signals(date, signal_types, universe, bw_regime?, trend?, min_adv?, limit)`
- `get_indicators(ticker, start, end, fields)`
- `get_stats(signal_type, horizon_days, target_pct, entry_kind, bw_regime?, trend?, ticker?)`
- `predict(ticker, as_of?)`
- `compare_instruments(ticker, as_of?, candidates)`
- `explain_signal(ticker, date)`

All enums closed. Dates rejected outside the data range. `limit` capped server-side. No string parameter reaches a query builder.

Rationale. Text-to-SQL hallucinates column semantics, breaks on schema change, and is an access-control problem. A fixed tool surface is testable and auditable.

---

## 026. Guardrails enforced in code, not in the prompt

Status: Pinned

Decision. Four rules, all implemented outside the model.

- Any response containing a probability must include $n_{eff}$ and the confidence interval. The tool returns them in the same object, so no path exists to a naked number. A post-response validator scans for percentage patterns without an adjacent sample size and rejects the turn.
- Cells below the $n$ threshold return a `suppressed` flag with a reason, never a number.
- The model performs no arithmetic. A tool computes it or the answer states the tool does not support it.
- Refusal on advice framing. Tools return historical frequencies conditional on past states, not forecasts of individual outcomes, and every answer carries that framing.

Rationale. Prompt-level guardrails degrade under adversarial or unusual phrasing. Schema-level and validator-level ones do not.

Model choice. Sonnet. Routing across six tools is easy work and the intelligence lives in `cell_stats`.

---

## 027. MCP server built after Phase 4

Status: Provisional

Decision. Wrap the same six tools as an MCP server over streamable HTTP once `cell_stats` holds real numbers.

Rationale. Three payoffs. Claude Desktop and Claude Code query the research database directly, converting the analysis loop from script-writing to asking questions. One tool implementation serves both the web chat and local clients. And a typed MCP server with auth and rate limiting is infrastructure work rather than a wrapper.

Auth. Bearer token minimum, scoped read-only, rate limited per token. An unauthenticated MCP endpoint on the public internet is an open database proxy.

Timing. Built after Phase 4, shipping within Phase 5 alongside the chat layer. An MCP server over an empty `cell_stats` demonstrates nothing. (Phase numbers per ADR 049.)

---

## 028. Quantile LightGBM, no reinforcement learning

Status: Pinned

Decision. The model predicts a distribution, not a number. LightGBM quantile objective, one model per $\tau \in \{0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95\}$, monotonicity enforced by sorting after fit. A second head predicts realized volatility over the horizon.

Metric is calibration, not accuracy. Reliability diagram plus Brier score plus pinball loss. A model saying 60% and being right 60% of the time beats a model saying 85% and being right 62% of the time.

RL rejected. Reinforcement learning solves sequential decision problems where actions influence state transitions. This is supervised prediction over near-independent events with a fixed horizon and no position-sizing feedback. RL would deliver worse results at ten times the effort and be unexplainable in review. If portfolio-level position sizing under capital constraints gets added later, that is a real sequential problem and RL earns reconsideration.

Feedback loop without RL. Log every prediction with its full feature vector, join realized outcomes after the horizon closes, retrain monthly on an expanding window.

---

## 029. Forward log is immutable

Status: Pinned

Decision. Predictions are never regenerated or overwritten. `model_version` increments and old rows stay. `features_json` stores the exact feature vector at prediction time.

Rationale. Recomputing features from history silently picks up revised data. The forward log is the single most convincing artifact in this project, and it stays convincing only if it is never rewritten. Most projects show a backtest. Almost none show a forward log.

---

## 030. Model promotion gated on calibration

Status: Superseded by 067, which expands this to four checks.

Decision. A retrained model serves only after its calibration error on the validation split falls below the incumbent's. Version bump, no in-place replacement.

Rationale. Prevents a model with better average loss and worse calibration from shipping. For a probability display, calibration is the product.

---

## 031. VIX stored as a daily column

Status: Pinned

Decision. Store VIX close alongside indicators, plus its trailing percentile, as a market-regime feature.

Rationale. Free, one series, and it separates single-name dips from market-wide ones. Combined with `spx_ret_1d` and the co-firing count, it captures whether an event is idiosyncratic or macro. Cluster risk is the main hidden exposure in a mega-cap universe, since the strategy is one macro bet wearing twenty tickers.

---

## 032. Tax reported pre and post

Status: Provisional. Amended 2026-08-13 to add a long-term rate — see the dated note below. Still Provisional.

Decision. Backtest reports pre-tax and after-tax results separately. Sleeve gains taxed at short-term ordinary rates. Wash-sale interaction flagged.

Rationale. The core-plus-sleeve structure trades the same tickers held long term. Selling a sleeve position at a loss triggers wash-sale disallowance if the ticker was bought within 30 days either side, including the core position and dividend reinvestment. This is a real accounting cost of the strategy, not a footnote.

Note. This is a modeling assumption for evaluation, not tax advice. Rates and rules vary by situation.

**Amended 2026-08-13 (Session 13): a long-term rate is added, and holding period decides which applies.**

The decision text above names only a short-term rate, correctly, because it describes the **sleeve**. Session 13 taxes something the ADR never contemplated: the benchmark arms. Buy-and-hold and trim-and-redeploy hold their positions for years, and taxing them at 37% was measurably wrong.

Measured on the train split before the amendment, buy-and-hold reported `post_tax_ret = +239.22%` against `pre_tax_ret = +383.66%`. Its 784 holding stints average roughly four and a half years and would be taxed near 20%, not 37%. The error ran in one direction: it **overstated the benchmark's tax and flattered every high-turnover arm measured against it**, including the signal arm the comparison exists to judge.

The amendment:

- `BenchmarkParams.long_term_tax_rate` defaults to 20%, the top US federal long-term bracket, pairing with the existing 37% top ordinary bracket. Neither carries state tax, NIIT, or bracket progression. `0.238` adds NIIT and is a one-field change.
- `BenchmarkParams.long_term_holding_days` is 365, and the comparison is **strictly greater than**. The statutory rule is "more than one year," so a position held exactly a year stays short-term.
- The two buckets net within themselves first and against each other only when one is negative, which is the actual netting rule. Two positive buckets each pay their own rate.
- A deferred wash-sale loss keeps its character into the following year, so a short-term loss returns to shelter short-term gains.

**Nothing about the sleeve changes.** The signal and random arms hold positions for five days or fewer, so 37% was and remains correct for them. This amendment moves the benchmarks only, and it moves them **against** the signal arm's relative standing.

The status stays Provisional. The gaps listed in DESIGN §8.8 are unchanged: no carry-forward past the window, nominal tax rather than compounded, rebalance partial disposals untaxed, and dividend reinvestment not modeled as a purchase.

---

## 033. Kill criteria fixed in advance

Status: Pinned

Decision. Written now, before sunk cost exists.

- No cell beats its per-ticker-year baseline by the power-adjusted threshold at sufficient $n_{eff}$ after FDR correction: the two-indicator hypothesis is dead as a standalone claim.
- Validation edge under half the training edge: overfit. Cut features, widen cells.
- Holdout edge negative: report it.

What survives a null result. The event-study engine with correct look-ahead handling and MFE/MAE tracking, the tool-restricted chat layer, the calibration methodology, and the ingest and scheduling pipeline. The pivot keeps the engine and swaps the input signal. Volatility term structure, earnings drift, cross-sectional momentum residuals, and volume-price divergence are all testable in the same framework with no rewrite.

Framing. The strongest version of this project is a rigorous event-study engine with a natural language interface and honest confidence intervals. Built for that outcome, a null result stops being a failure. A project reporting "I tested this rigorously and found no edge, here is the infrastructure proving it" reads better than a suspiciously profitable backtest.

---

## 034. Provenance on every generated row

Status: Pinned

Decision. `run_id` and `git_sha` on every row in `cell_stats`, `predictions`, and `events`. The `runs` table stores job name, parameters, timestamps, and status.

Rationale. When numbers change between runs, you need to know whether the data moved or the code moved. Without provenance that question is unanswerable.

---

## 035. Union universe, 2010 start, ADRs only for non-US

Status: Pinned

Decision. Training universe is the union of every S&P 500 member between 2010 and today, roughly 750 tickers, including names delisted or removed. Start date 2010-01-01 for events. Non-US exposure comes through US-listed ADRs only (TSM, ASML, SAP, NVO and comparable), never foreign primary listings.

Rationale. The union directly removes survivorship bias, which ADR 015 depends on. Cisco after 2007, Intel after 2019, and every removed name stay in the sample. A 2010 start covers the 2011 correction, the 2015 and 2018 selloffs, 2020, and 2022, which is four distinct drawdown regimes. 2008 is excluded because its microstructure differs enough to need separate treatment and its data quality costs more to clean than the added power is worth. ADRs come through the same pipeline with no extra plumbing, trade on US exchanges in USD, and settle on the US calendar.

Cost. Roughly 3M daily bars. Delisted tickers need explicit handling in every join.

---

## 036. SEC EDGAR for historical earnings dates, Finnhub for forward

Status: Pinned

Decision. Historical earnings dates come from SEC EDGAR 8-K filing dates, backfilled once and frozen. Forward earnings dates come from the Finnhub calendar on a 90-day rolling window, refreshed weekly.

Rationale. Finnhub's free tier caps historical earnings depth near 4 years, leaving a 12-year hole against a 2010 start. EDGAR's submissions API is free, unauthenticated, rate limited at 10 requests per second, and reaches past 2009. Earnings releases file as an 8-K carrying an EX-99.1 press release, and the 8-K filing date matches the earnings date closely. The 10-Q or 10-K filing date bounds it where the 8-K is ambiguous.

Storage. Every row carries `source` and `confidence`. Session timing (before or after market) is best-effort and comes from Finnhub where available. A 5-day window containing an earnings report is contaminated regardless of session.

---

## 037. Hourly bar archive starts now

Status: Pinned

Decision. Ingest 730 days of hourly bars immediately and continue accumulating nightly, even though nothing in Phase 1 or 2 reads them.

Rationale. Yahoo caps hourly history at roughly 730 days, so the archive only exists if you start it. ADR 010's intrabar ordering escalation needs hourly data, and the entry-timing sweep in ADR 007 is far more credible with intraday resolution. Waiting until the need is proven means the data no longer exists to fetch.

Cost. Roughly 5M rows. One additional nightly job.

---

## 038. Neon Launch tier

Status: Superseded by 053

Decision. Budget for Neon Launch at $19 monthly rather than the free tier.

Rationale. Daily plus hourly bars total roughly 8M rows, near 1GB with indexes, against a 0.5GB free-tier cap. Launch provides 10GB and supports branching, which ADR 022 depends on for reviewable backtest results.

---

## 039. Development runs on local hardware, cloud only for parallel sweeps

Status: Pinned

Decision. Backfill, indicator computation, backtests, and model training run on the local workstation: Ryzen 7 3700X (8 cores, 16 threads), 32GB DDR4-3600, RTX 3060 Ti 8GB, NVMe storage. Modal is reserved for parallel parameter sweeps and scheduled market-hours polling.

Rationale. The full daily bar set is roughly 3M rows, near 400MB in memory as float64. It fits in RAM with room for the entire backtest state. LightGBM on 3M rows with 20 features trains in minutes on 16 CPU threads and needs no GPU. Renting compute for work a local machine handles in the same wall-clock time adds cost and a deployment step for nothing.

Consequence. Design the backtest to run single-machine with per-ticker parallelism across 14 workers, leaving two of the 16 hardware threads for Postgres and the OS. Do not build for distributed execution. Modal jobs reuse the same code path with a different worker count.

---

## 040. Ingest starts 2009-01-01, events start 2010-01-01

Status: Pinned

Decision. Bar ingest begins 2009-01-01. Event detection begins 2010-01-01. The gap is warmup.

Rationale. The longest indicator chain is `rv_pct_252d`, a 252-day percentile of a 20-day realized volatility, requiring 272 prior bars. `sma200_slope_60` requires 260. Starting event detection on the first ingested bar produces a year of null indicator values, and null features silently drop events rather than failing loudly. One extra year of bars costs nothing and removes the boundary entirely.

Enforcement. A test asserts zero null values in every indicator column on or after 2010-01-01 for any ticker with continuous coverage.

---

## 041. float64 in compute, numeric in storage

Status: Pinned

Decision. All pandas computation uses float64. Postgres stores prices as `numeric(12,4)` and indicators as `numeric(12,6)`. Parquet caches on disk may use float32, since they are read-only artifacts never used for band comparison.

Rationale. Storage savings from float32 total roughly 120MB against a 10GB plan. Compute savings are unmeasurable, since rolling operations at this scale are memory-bandwidth bound. The risk is concentrated in rolling standard deviation: with prices near $400, float32 gives about 7 significant digits, and the variance calculation squares those, so catastrophic cancellation is possible. The band level is the exact number `_breach` compares against, so an error in the 5th digit flips events at the margin. `numeric` rather than `double precision` in Postgres means a stored value reads back identically with no representation drift.

---

## 042. Delisted tickers retained, era reporting instead of deletion

Status: Pinned

Decision. Tickers that left the index or delisted stay in the training universe. Trade eligibility is decided by the causal quarterly filter in ADR 014, never by present-day existence. An `is_terminal` flag on the final bar makes open positions exit explicitly.

Rationale. Roughly 250 of the 750 union names left the index between 2010 and 2026. They did not all fail: acquisitions tend to end above the last signal, declines end below, and deleting both removes the population the strategy is most exposed to. The class of "mega-cap that passed the health filter and then broke" is precisely the class that exits the index, and ADR 015 depends on measuring it.

Handling regime skew, which is the legitimate concern behind the deletion instinct. Report edge per era (2010-2014, 2015-2019, 2020-2023, 2024-2026) so imbalance is visible rather than hidden. Never weight by ticker. Cap per-ticker contribution in pooled reporting. Run the with-and-without comparison once as a config change and report the delta.

---

## 043. Advisory only, no execution path in v1

Status: Pinned

Decision. The system emits notifications and recommendations. It does not place orders. No broker client, no brokerage credential storage, and no code path capable of execution exists in the repository.

Consequences. Position state is user-declared through a `positions` table, not broker-synced. Live fill prices are user-entered rather than modeled. Every recommendation renders alongside the historical frequency, effective sample size, and confidence interval that produced it, per ADR 026.

Rationale. The absence of an execution path is the safety property, not a disabled flag. It also frames the project accurately: decision support with calibrated uncertainty rather than an automated trading system.

---

## 044. Fast Stochastic agreement as a tolerance

Status: Pinned

Decision. Agreement between fast and full Stochastic is a tolerance band, not equality:

$$\text{agree} \iff |\%K^{fast}_{t-1} - \%K^{full}_{t-1}| \leq \delta, \quad \delta = 5.0$$

`require_fast_agreement` defaults to False. `fast_agreement_tol` defaults to 5.0. One config flag enables it. Ablation runs after Phase 4.

Rationale. Requiring agreement cuts event count roughly 30%. Keeping it off preserves events and lets the model decide whether fast adds information. Storing both series costs nothing.

---

## 045. Stochastic crossover is a feature, never a gate

Status: Pinned

Decision. `k_crosses_above_d` and `k_crosses_below_d` are computed, stored, displayed in the audit log and detail panel, and available to the model as features. They never enter `detect()` and never affect a recommendation.

Rationale. A crossover rule delays entry by a day or two and interacts badly with a 5-day cap. Surfacing it as an additional data point lets the user apply their own judgment without the program's recommendation depending on it.

---

## 046. Mid-band exit optional, default off

Status: Pinned

Decision. Exit on a long touching `bb_mid` is a sixth exit condition. `exit_on_mid_band` defaults to False and is user-toggleable. Priority slots between target and upper band, since it is the nearer level.

Rationale. Mean-reversion trades frequently resolve at the mean rather than the far band. Costs one config flag to test.

---

## 047. Three-number output contract: terminal, peak, reachability

Status: Pinned

Decision. Forecast output never collapses to a single number. Three separate quantities:

$$\text{Terminal: } \hat{Q}_\tau(R_5) \qquad \text{Peak: } \hat{Q}_\tau(\text{MFE}_5) \qquad \text{Reachability: } P(\max_{t\leq 5} R_t \geq X)$$

Reachability is reported as a ladder with median time-to-touch per rung. MFE quantiles appear only in the detail panel, labeled as unattainable in practice.

Rationale. MFE is not an achievable return, because the peak is identifiable only afterward. Displaying a predicted peak as a headline invites treating it as a target, and realized results will land far below. Reachability is the honest number, since a limit order at the rung genuinely fills if the path touches it.

Reporting requirement, from Phase 4 onward: capture ratio per exit rule.

$$\eta = \frac{\mathbb{E}[R_{\text{exit}}]}{\mathbb{E}[\text{MFE}]}$$

Timeout-only rules typically land near 0.4. This number decides whether early-exit rules help.

---

## 048. Execution-ready seams built now, execution absent

Status: Pinned

Decision. Four structures exist from day one so that adding execution later is an adapter rather than a rewrite.

1. Order intent as a first-class object: side, ticker, quantity basis, limit level, stop level, time-in-force. Rendered as a notification today, serialized to a broker adapter later.
2. `positions` table with a `source` column, valued `user_declared` now and `broker_synced` later. All exit logic reads position state from this table.
3. Idempotency key on every intent: deterministic hash of ticker, date, signal type, and side. Prevents duplicate orders on retry.
4. Append-only audit log covering every signal, recommendation, and user action, carrying `run_id` and `git_sha`.

Explicitly not built: broker clients, brokerage credentials, and any code path that could place an order. See ADR 043.

---

## 049. v1 scope is detection, scanning, notification

Status: Pinned

Decision. Version one delivers ingest, indicator computation, event detection, and a scan function. No statistics, no predictions, no exit engine in the shipped surface.

Acceptance criterion:

```python
scan(tickers, signal_types, start, end, universe="trade") -> pd.DataFrame
```

One row per event with ticker, timestamp, signal type, touch level, %B, %K full, %K fast, crossover flags, drawdown bucket, and trend state.

Revised phase order. 1: ingest, indicators, scan, CLI. 2: live poller, notifications, positions. 3: exit engine, MFE/MAE, event table. 4: statistics, baselines, benchmarks, FDR. 5: frontend, chat, tools, MCP. 6: quantile model, calibration, forward log. 7: options.

Note. The exit engine is still designed before Phase 1 ships, because the event table schema must accommodate it. Designing it later forces a migration.

---

## 050. Live call overlay, no historical option backtest

Status: Pinned

Decision. Phase 2 adds a live call overlay. When a long signal fires, the system pulls the current option chain from yfinance and displays, for candidate strikes at the nearest expiry past day 5: cost at the actual ask, breakeven, payoff at each rung of the reachability ladder, and max loss. Labeled as priced from live quotes and explicitly not backtested. The decision to use it is deferred to the user, like every other recommendation.

Historical option backtesting stays deferred to Phase 7 per ADR 018.

Rationale correction. ADR 018 deferred options for a data reason, not a risk reason. A long call has defined risk capped at the premium, so the risk argument that defers puts and shorts does not apply to it. The actual blocker is that reconstructing 2015 implied volatility gets skew wrong, misses post-earnings IV crush of 30 to 50%, and ignores spreads of 1 to 3% of premium. Those errors are the same magnitude as the effect under measurement. Live chains carry none of those problems because the quotes are real.

Boundary. The overlay translates the shares-based reachability ladder into an option payoff. It never reports a historical hit rate for the option trade itself. All statistical claims come from the shares model.

---

## 051. Indicator registry with declared warmup

Status: Pinned

Decision. Indicators register through a decorator declaring dependencies, warmup length, and storage type.

```python
@register("rsi_14", deps=["close"], warmup=14, dtype="numeric(12,6)")
def rsi_14(bars: pd.DataFrame, p: IndicatorParams) -> pd.Series: ...
```

`compute_all` iterates the registry. The read-window expansion in the indicators job computes `max(warmup) * 1.6` calendar days rather than a literal constant, so adding a longer-warmup indicator adapts the job automatically.

Schema stays wide. Adding an indicator costs one function, one decorator, one migration, and a targeted recompute via `cscan indicators --only <name>`, roughly two minutes end to end. Entity-attribute-value storage is rejected: it would cost query readability permanently to save a migration.

Rationale. More indicators are planned but unspecified. The registry makes addition mechanical while keeping the friction where it belongs, in `core/signals.py`, where using a new indicator in a signal is a deliberate change.

---

## 052. Progress reporting standard

Status: Pinned

Decision. Every job expected to exceed 30 seconds reports progress through `rich.progress`, showing current item, completed over total, elapsed, ETA, and a running error count. Parallel jobs render a live per-worker table. `--quiet` emits one structured JSON line per 100 items for cron contexts.

The hourly backfill checkpoints to disk after each ticker so an interrupt resumes rather than restarting. Applies to any job over roughly ten minutes.

Rationale. `rich` over bare `tqdm` for nested bars and live tables. A 5.4-hour job with no output is indistinguishable from a hung one.

---

## 053. Two-store split: local research, cloud serving

Status: Pinned. Supersedes 038.

Decision. Two Postgres instances with different jobs.

Research store, local Docker: daily and hourly bars, indicators for all tickers, `bar_rejects`, `runs`. Roughly 10M rows, 1.5GB. Never leaves the workstation.

Serving store, Neon free tier: `tickers`, `universe`, `events`, indicators restricted to the trade universe over the last 3 years, `cell_stats`, `predictions`, `positions`. Roughly 200k rows, under 150MB.

Same migrations applied to both. A nightly export builds the subset locally and upserts to cloud, about 20 seconds.

Rationale. ADR 038 sized a cloud database for the full research dataset, which never needs to be remote. The serving layer never queries a raw bar: the screener reads `events`, charts read roughly 45k indicator rows, statistics read a few hundred `cell_stats` rows. Free tier is sufficient permanently.

Neon over Supabase because the requirement is exactly one thing, Postgres over the network, and Supabase's auth, storage, and realtime features are unused surface area. Migration between them is a `pg_dump`. Revisit only if hosted auth becomes wanted.

No EC2 and no persistent cloud compute. Vercel serves frontend and API, Neon serves data, GitHub Actions and the local workstation run jobs.

Steady-state cost through Phase 6: under $15 monthly, consisting only of Anthropic API usage.

**Superseded on sizing by ADR 137, 2026-08-20.** The note below is correct
about *why* 14.6M rows is not the sync payload, and wrong about the total:
it predates `v_chart` reading `FROM bars`. Full history measures 2,149MB,
not 150MB, and the served subset is three years. The decision — two stores,
local research authoritative, cloud derived — is unchanged.

**Sizing re-verified 2026-08-19, and the headline number is alarming until
you read which rows it counts.** `events` is now **14,604,735 rows / 10 GB**
locally — 70x this ADR's whole-serving-store estimate, and enough to make a
reader assume the free tier is out.

It is not. That table holds **23 config hashes x 4 entry kinds**. The
serving store carries one config at one grain, and measured:

    events, all configs x all entry kinds    14,604,735 rows,  10 GB
    events, live config, next_open only         157,915 rows

157,915 against this ADR's "roughly 200k rows, under 150MB" for the whole
subset. The estimate holds; the growth is entirely in slices that never
leave the workstation — the config sweep (ADR 059) and ADR 122's
out-of-trade events, which exist to keep the ticker page complete and are
excluded from every statistical read.

Recorded because `docs/BACKLOG.md` carried an item asserting the opposite
("`events` at 14.6M rows does not fit Neon's free tier, so what gets synced
is a paid decision"). That read the local row count as the sync payload.
The item is removed; the sync job remains unbuilt but undecided-nothing.

---

## 054. Notifier protocol, three channels

Status: Pinned

Decision. `Notifier` protocol with three implementations: SMTP email, Discord webhook, and ntfy. Each independently toggleable in config, any combination active simultaneously.

Rationale. Different urgency levels suit different channels, and the protocol makes adding a fourth trivial. Discord and ntfy both deliver mobile push with no auth flow.

---

## 055. Frozen universe CSV committed to the repo

Status: Pinned

Decision. The training universe is built once from a Wikipedia scrape of S&P 500 component changes, reviewed manually, and frozen to `data/universe_union.csv` in version control. Roughly 600 to 750 tickers with add and remove dates.

Rationale. The universe must be reproducible and reviewable. Depending on a live scrape means the training set silently changes when the page edits. A committed CSV makes universe composition a reviewable diff. The manual review pass catches the inconsistent formatting in older rows and post-removal ticker changes without smuggling in hand-picked selection, since the scrape defines membership and review only corrects parse errors.

---

## 056. Cluster tagging replaces position blocking

Status: Pinned

Decision. The engine emits every candidate event. Overlapping events on the same ticker are tagged rather than suppressed.

```sql
cluster_id       bigint,   -- shared across overlapping events on one ticker
seq_in_cluster   int,      -- 1 = first touch, 2 = second
is_cluster_head  boolean,
days_since_head  int
```

Consumers filter as needed. Primary statistics use `is_cluster_head = true` for non-overlapping windows and clean standard errors. Secondary statistics use all events to measure whether later touches outperform. The live advisory fires on every touch and defers the decision to the user. `skipped_candidates` is removed.

Rationale. The original design conflated a statistical concern with a product concern. Overlapping windows correlate observations, which matters for inference. But a second touch inside an open position is a legitimate add-to-position signal, and suppressing it silences the system exactly when averaging down is under consideration. Tagging satisfies both.

New question this makes answerable: does the second touch outperform the first? A higher hit rate on `seq_in_cluster = 2` validates averaging down with a number. A lower one identifies the falling-knife case. Either result is useful.

Note. Portfolio-level pyramiding needs position sizing and lands after Phase 4. Phase 4 measures per-event outcomes by sequence only.

---

## 057. Multi-signal emits one row with strength

Status: Pinned. Short-side ranking extended 2026-08-13 by ADR 108 — see the dated note below.

Decision. Multiple signals firing on the same ticker within the same trading day emit one event row.

```sql
signal_type       text,      -- most specific that fired
signal_types_all  text[],    -- all concurrent
signal_strength   int        -- count of distinct concurrent signals
```

Specificity ranking, fixed: `CONFLUENCE_LOW` > `BB_LOWER_TOUCH` > `STOCH_OVERSOLD`, mirrored for the high side. `signal_strength` becomes both a model feature and a headline grid dimension.

Rationale. The strategy operates on a days-to-months horizon, so sub-day granularity is noise. Two indicators firing hours apart is one alignment event, and the advisory action is identical either way. Emitting separate rows would double-count in pooled statistics. Whether strength 2 outperforms strength 1 becomes measurable rather than assumed.

**Ranking extended 2026-08-13 (ADR 108).** The short side becomes `BEAR_CLOSE_ABOVE_UPPER` > `CONFLUENCE_HIGH` > `BB_UPPER_TOUCH` > `STOCH_OVERBOUGHT`. The new type ranks first because it requires a band breach *and* a close-confirmed rejection of it, which is strictly more specific than a band touch plus a stochastic extreme.

**The two sides are no longer mirrors,** and the "mirrored for the high side" phrasing above no longer describes the ranking. That is deliberate rather than an oversight: ADR 106 already rejected long/short symmetry as an assumption, and no bullish counterpart to this pattern was requested. The long side stands at `CONFLUENCE_LOW` > `BB_LOWER_TOUCH` > `STOCH_OVERSOLD`.

**`signal_strength` shifts for existing events on days the new type fires,** which is what forces a new `config_hash` rather than an in-place backfill.

---

## 058. Both sides computed, only long surfaced

Status: Pinned. Surfacing clause superseded 2026-08-13 — see the dated note below. The both-sides-computed clause stands unchanged.

Decision. The engine constructs short events alongside long ones from the first run, at the cost of a sign flip and borrow accounting. Only long events surface in v1's UI and notifications.

Rationale. Zero marginal cost, and it gives the statistics layer long-versus-short asymmetry data immediately. ADR 016's band-walking hypothesis becomes testable before any short is ever recommended.

**Amended 2026-08-13: both sides surface, and have all along.**

The "only long events surface" clause was never implemented. `jobs/poll.py` loops over both sides and sends a notification for each, with no side filter anywhere in the path, and `v_screen` joins `c.side = e.side` rather than pinning `side = 'long'`. Found by reading the code against this ADR while scoping a new short-side signal, and confirmed against a live poller session: `reports/poller_session_2026_08_13_083548.csv` holds 21 `confluence_high` short rows, all notified.

The code is amended into the ADR rather than the reverse, for three reasons:

1. **ADR 106 removed the premise.** Short entries now use the same universe and criteria as long, so "before any short is ever recommended" describes a gate the project no longer has.
2. **ADR 017 makes the upper-band signal actionable.** Trimming a held position on `CONFLUENCE_HIGH` is the primary use, and a trim signal a person never sees is inert.
3. **v1 is over.** The project is past Session 9, past the Phase 3 gate, and past Sessions 10 through 13. The package version moves to 0.2.0 on this commit to mark the line, so "in v1" stops being a live constraint that reads as current.

One asymmetry is retained and is deliberate: `jobs/poll.py` builds a call overlay for long signals only (ADR 050). That is about the option chain, not about surfacing.

Nothing about advisory-only changes. ADR 043 and invariant 7 stand: a surfaced short signal states what fired and what historically followed, exactly as a long one does, and recommends no action.

---

## 059. Default config validated before sweeping

Status: Pinned

Decision. The first backtest run uses a single default config: ATR stop at k=1.5, target 4%, `NEXT_OPEN` entry. The full validation harness runs against it and roughly 20 events get inspected by hand against charts before any sweep executes.

The full 18-config sweep is mandatory before any result is published or acted on. Validation-first is about ordering, not about skipping coverage.

Rationale. Sweeping over a buggy engine produces eighteen confidently wrong answers and no signal that anything is wrong.

---

## 060. Backtest is deterministic, config-hashed

Status: Pinned

Decision. `backtest(config, bars, indicators, universe) -> events_df` is a pure function. Identical config plus identical data produces byte-identical output.

Requirements. Sort tickers before dispatch and sort collected results before writing, since `as_completed` returns nondeterministically. No wall-clock reads inside the engine; `run_id` and timestamps are injected by the caller. Randomized benchmark arms seed from `config_hash`. Every event row carries `config_hash`, a deterministic hash of the serialized config, so sweep results coexist in one table.

Enforcement. A test asserts two runs with identical config produce identical event content, ignoring `run_id`.

Separation. The engine computes no statistics, selects no parameters, and knows nothing about baselines. A sweep runs the engine eighteen times; the statistics layer runs once over the union.

---

## 061. Randomization null over parametric tests

Status: Pinned

Decision. The primary significance test is a randomization null from benchmark arm 2. Entry dates are sampled matching the signal's empirical firing rate per ticker-year, identical exit rules applied, seeded from `config_hash`. Signal performance above the 97.5th percentile of the null distribution is the criterion. Parametric p-values are stored as a cross-check.

Replication counts: 200 on the default config and on validation runs, 50 during parameter sweeps where ranking rather than significance is the goal. Any anomalous sweep result escalates to the full 200.

Rationale. The randomization null absorbs clustering, drift, and exit-rule path dependence automatically. No closed-form test handles all three cleanly.

---

## 062. Dual baselines, empirical headline

Status: Pinned

Decision. Two baselines computed per ticker-year and both stored. Empirical: the realized hit rate across all trading days in that ticker-year. Parametric: from trailing 252-day drift and volatility,

$$P_{base} = 1 - \Phi\left(\frac{X - \mu_{5d}}{\sigma_{5d}}\right)$$

Edge is reported against the empirical baseline. The parametric one is a diagnostic: large disagreement flags a non-normal return distribution for that ticker-year.

A cell's baseline is the event-weighted mean of its constituent events' per-ticker-year baselines, never a pooled rate over all days.

Reachability targets are reported at both fixed percentages (2, 3, 5, 10%) and volatility-scaled levels (0.5, 1.0, 1.5 times $\sigma_{5d}$). Fixed is the headline because it matches how a limit order is placed. Scaled is the diagnostic that reveals whether an apparent edge is really a volatility effect.

---

## 063. LightGBM over sequence models

Status: Pinned

Decision. The Phase 6 model is gradient-boosted trees on tabular event features. No LSTM, no transformer, no sequence architecture.

Rationale. The data is roughly 40,000 independent rows with about 20 tabular features. Temporal structure is already compressed into those features: %K encodes 14 days, `rv_pct_252d` encodes a year. Feeding raw sequences would force the model to relearn indicators already computed, from a sample far too small to support it. Gradient boosting dominates tabular data at this scale, trains in seconds on CPU, handles missing values natively, calibrates cleanly with isotonic regression, and provides interpretable feature attribution.

The GPU stays idle through Phase 6. That is the correct outcome, not a waste.

Relationship to the statistics layer. `cell_stats` is the coarse, human-readable answer and gates the model: if no cell shows edge, the model has nothing to find. The model's contribution is resolution, separating cases the cell pools together, plus the terminal quantile fan that no lookup can produce.

---

## 064. Eleven independent heads, timeout-return targets

Status: Pinned

Decision. Eleven models on a shared feature set: five quantile heads for terminal return, four binary heads for upside reachability, two binary heads for adverse excursion.

$$\hat{Q}_\tau(R_5), \tau \in \{0.05, 0.25, 0.50, 0.75, 0.95\}$$
$$\hat{P}(\max_{t\leq5} R_t \geq X), X \in \{2,3,5,10\}\%$$
$$\hat{P}(\min_{t\leq5} R_t \leq -Y), Y \in \{3,5\}\%$$

Not a mixture of experts: no gate, no routing. Eleven different questions from identical features. Separation is required because quantile regression and binary classification need different loss functions, and the binary targets are genuinely distinct events rather than one event at shifted thresholds.

The adverse heads are the stop-loss warning. They tell a user how likely a stop is to trigger before the target, which is the quantity ADR 008's first-passage math identified as decisive.

Targets use timeout return, meaning the raw 5-day forward return ignoring all exit rules. Exit policy is a separate layer applied on top. Training on exit-rule returns would couple the model to config, forcing a retrain on every stop or target change.

Quantile crossing is fixed post-fit by sorting.

Total training time roughly 5 minutes for all eleven.

---

## 065. Purged walk-forward CV, cluster-weighted

Status: Pinned

Decision. Cross-validation inside the training split is expanding-window walk-forward, never random K-fold.

```
fold 1: train 2010-2014 -> validate 2015
fold 7: train 2010-2020 -> validate 2021
```

Purge: drop training events whose 5-day forward window overlaps the validation fold start. Embargo: drop validation events in the fold's first 5 trading days.

Sample weighting: cluster members weighted 1/|cluster| so a four-touch cluster does not count as four independent observations. Co-firing is deliberately not weighted here, since the randomization null in ADR 061 accounts for it at the reporting layer and double-correcting would understate confidence.

Hyperparameters kept conservative given an effective sample near 8,000 after clustering: `num_leaves=15, max_depth=4, min_child_samples=100, learning_rate=0.03, feature_fraction=0.7, bagging_fraction=0.7, lambda_l2=5.0`, early stopping with patience 50.

Monotonic constraints applied only where the sign is known a priori. Deeper drawdown must not increase upside reachability. `days_to_earnings` stays unconstrained.

Excluded features and why: raw price and band levels (non-stationary across tickers and eras), `era` (invites regime memorization and is unavailable for future prediction), `ticker` identity (60 names over 40k events permits memorizing individual histories; sector is the right granularity).

---

## 066. Nightly inference, t-1 invariant

Status: Superseded by 069

Decision. Inference runs once nightly over the trade universe, roughly 60 tickers times 11 models, completing in about 2 seconds. Results write to `predictions` keyed by `(ticker, as_of)`. Signal-time inference is a database lookup, never a model call.

The invariant that makes this correct: every feature is evaluated at t-1 and bands are fixed at the prior close, so the prediction computed after Monday's close is valid for any signal firing at any time on Tuesday.

This invariant must be stated in code comments at the feature-construction boundary. Adding any intraday-varying feature breaks it and would require live inference. Do not add one without revisiting this ADR.

---

## 067. Four-check promotion gate

Status: Pinned

Decision. A retrained model serves only after passing all four checks on the validation split against the incumbent.

1. Expected calibration error strictly decreased
2. Brier score did not increase by more than 0.002
3. Quantile coverage within 5 points of nominal for every tau
4. No feature's relative importance shifted by more than 40%

Check 2 prevents the degenerate win where a model achieves perfect calibration by predicting the base rate everywhere, gaining ECE while losing all discrimination. Check 4 catches feature-pipeline changes, which present as model changes and are otherwise invisible.

Failure leaves the incumbent serving; the candidate is logged with full metrics. Success increments `model_version`. Predictions are never regenerated, per ADR 029.

Baseline requirement. Every head is also compared against the corresponding `cell_stats` frequency. If the model does not beat the empirical lookup on Brier score, the lookup ships alone and the model does not serve.

---

## 068. Single model, sector as categorical

Status: Pinned

Decision. One model per head, with `sector` as a categorical feature. No per-sector models.

Rationale. Gradient boosting splits on the categorical feature wherever the data supports it, producing sector-specific behavior inside a single model fit only where justified. Separate models force the split everywhere, including where roughly 3,600 events per sector cannot support it. The trade universe is also heavily tech-weighted, so several sectors would have too few names for a separate model regardless.

Verification. After training, inspect whether `sector` appears in high-importance splits and whether SHAP interaction values between sector and top features are large. Strong interactions plus adequate per-sector volume would justify revisiting.

---

## 069. Live inference at trigger, nightly batch for screener

Status: Pinned. Supersedes 066.

Decision. Two inference paths.

Live inference runs inside the polling loop at the moment a signal triggers. The poller loads all eleven model artifacts at 09:15 alongside the band levels. Inference on one row across eleven models takes well under a millisecond on CPU, so it fits inside the polling tick.

Nightly batch inference still runs after close over the whole trade universe, feeding the screener view for tickers with no active signal.

Rationale. With the current feature set both paths produce identical numbers, since every feature is evaluated at t-1. Live inference matters because it unlocks a feature class that nightly computation cannot express:

$$\%B_{live} = \frac{P_{now} - \text{lower}_{t-1}}{\text{upper}_{t-1} - \text{lower}_{t-1}}$$

A touch poking 0.2% below the band is a different event from one crashing 4% through it, and depth-of-breach is plausibly among the stronger available features. The same applies to live excursion measured in ATR units.

Precedent. This is the HandVol decision applied again: small model, CPU inference, real-time path, no GPU bussing latency.

Backtest consequence. If breach-depth features are adopted, the backtest must construct them. Daily bars approximate breach depth from the bar low relative to the band. Hourly bars from 2024 forward give it exactly. The feature therefore enters with a documented approximation before 2024, which is a further argument for ADR 037.

Sequencing. Breach-depth features are added in Phase 6 after the base model exists, so their contribution is measurable against a baseline rather than assumed.

Note. ADR 066's t-1 invariant is now explicitly relaxed for the live path only. The nightly path retains it.

---

## 070. Serving API is pure SQL, ten endpoints

Status: Pinned

Decision. Every endpoint is an indexed SELECT executed by Next.js against Neon through the Postgres driver. No Python functions on Vercel, no computation, no model calls in the request path. Backtests, training, and batch inference all run locally per ADR 039, so nothing needs hosting.

Endpoints: `/api/screen`, `/api/ticker/:sym`, `/api/stats`, `/api/predict/:sym`, `/api/events`, `/api/forward`, `/api/universe`, `/api/health`, and `/api/positions` for read and write.

Response schema requirement. Any response carrying a probability structurally requires `n_eff`, confidence interval, and baseline. Cell and prediction data are nested objects rather than flattened columns, so absence is structurally obvious.

Caching keys on `run_id` rather than time, since closed-session data never changes. Nightly jobs call `revalidateTag()` after writing. Indicator state, including all band levels and stochastic values, caches per ticker-day with effectively infinite TTL and invalidation only for the current day.

Error contract. `SUPPRESSED` returns HTTP 200 because insufficient data is a valid answer, not a failure. Every response carries `meta.as_of` and `meta.staleness_days`, and the UI banners above 2 days, so a failed nightly job cannot silently serve stale data as current.

Rate limiting is a monitoring note, not a build item. API spend is watched directly.

---

## 071. Auth deferred to final development stage

Status: Pinned

Decision. No authentication through Phase 6. Read and write endpoints are open. The `positions` table carries an unused `user_id` column reserved for later.

Rationale. Two known users, both trusted, and the data derives from public market sources. Building auth early costs development time against zero current risk. The column reservation makes the later addition a filter clause rather than a migration.

---

## 072. Shared handler layer, two transports

Status: Pinned

Decision. Business logic lives in handler functions. The HTTP API and the MCP server both call those handlers directly. The MCP server does not call the HTTP endpoints over the network.

Rationale. One implementation, one contract, no network hop, and the tool schema stays automatically identical to the API schema. Divergence between the two surfaces becomes structurally impossible rather than a discipline problem, mirroring ADR 006's treatment of the signal contract.

---

## 073. Personal trade log and exit push notifications

Status: Pinned

Decision. `/api/positions` tracks closed positions with realized returns, building a personal trade log alongside the model's forward log. Exit signals push through the configured notification channels rather than waiting for the user to check.

Rationale. The trade log is a second forward record measuring user decisions rather than model predictions, and comparing the two is directly informative about whether the system helps. An upper-band touch on day 2 of a 5-day hold is precisely the moment a notification carries value.

Both land in Phase 2.

---

## 074. Seven tools, closed enums

Status: Pinned

Decision. The chat and MCP surface exposes exactly seven tools: `screen_signals`, `get_stats`, `get_indicators`, `get_events`, `predict`, `explain_signal`, `get_universe`. `compare_instruments` from the original design is removed, since options moved to a live overlay.

Every enum is closed. Dates validate against the ingested window. `limit` is capped server-side at 200 regardless of the value passed.

Tool schemas dominate input tokens, so a small tool count is a cost decision as well as a correctness one. `compare_positions` joins in Phase 2 with the trade log.

---

## 075. Validator requires sourcing, not advice avoidance

Status: Pinned. Corrects an earlier draft rule.

Decision. The response validator does not ban advisory language. It requires that every advisory statement sit within the same paragraph as a tool-sourced statistic.

Checks, all post-turn and pre-delivery:

| Check | Rule |
|---|---|
| Naked probability | A percentage with no `n=` or sample reference within 200 characters |
| Unsourced claim | A statistic present with zero tool calls in the turn |
| Unsourced advisory | An advisory sentence with no tool-sourced number in the same paragraph |
| Suppressed leak | A rate stated for a cell whose tool result was `suppressed` |
| Fabricated ticker | An unrecognized uppercase token; flagged and logged, not rejected |

One retry with the failure reason appended. A second failure returns a fixed message plus the raw tool results.

Rationale for the correction. ADR 043 makes this an advisory system. Notifications and generated reports both state what fired and what historically followed. A chat layer forbidden from doing the same would be inconsistent with the product. The actual risk is unsourced advice, not advice.

Absolute carve-out, no exceptions: no claims about a specific person's financial situation, tax position, or suitability. Nothing in the database sources those.

Web fetch remains available for company context. Fetched content is attributed explicitly and never merged into a statistical claim.

---

## 076. Database views are the shared contract

Status: Pinned. Refines 072.

Decision. Query logic lives in Postgres views. TypeScript API routes select from those views. Python MCP handlers select from the same views. Neither language contains query logic.

Rationale. ADR 072 named a Python handler layer as the shared contract, which would have required either duplicating queries in TypeScript or hosting a Python service. Views achieve the same guarantee at the database level: changing a response shape means changing one view, and both consumers change simultaneously. Drift becomes impossible rather than merely discouraged.

Secondary benefit. ADR 070's requirement that any probability response carry `n_eff` and a confidence interval becomes structural, since those are columns in the view. Returning a bare probability requires deliberately dropping columns.

---

## 077. Model choice is config, Haiku-first routing

Status: Pinned

Decision. `ANTHROPIC_MODEL` is an environment variable. Sonnet is the default. Haiku is available and used first for turns likely to need a single tool call and a table render, escalating to Sonnet when the turn requires comparing cells or explaining reasoning.

Cost is monitored directly rather than rate-limited. Current pricing should be checked against Anthropic's documentation before budgeting, since published rates change.

---

## 078. Inference stays with the poller, not on Vercel

Status: Pinned

Decision. Model inference runs in the polling process, which loads all eleven artifacts at market open. Vercel reads predictions from the database and never runs a model.

Rationale. LightGBM artifacts and their dependency stack would fit inside Vercel's Python bundle limit, so this is a choice rather than a constraint. A second inference path would create a second place where model version, feature construction, and calibration could drift, in exchange for capability the poller already provides.

Revisit only if a "predict for an arbitrary hypothetical state" endpoint becomes wanted, which is the one case a stateless function serves better.

---

## 079. Three environments, no staging

Status: Pinned

Decision. `local` (Postgres in Docker on the workstation, running all research, backtests, training, and the poller), `preview` (Neon branch, Vercel preview builds per PR), and `prod` (Neon main, Vercel production). No staging environment.

Rationale. One user plus branch-per-PR databases means staging would add ceremony without catching anything.

Deploy pipeline. Push to main triggers ruff, mypy, and pytest in Actions, then Vercel builds and deploys a preview against a database branch. Merge migrates prod and deploys. Migrations run before deploy, never after, and are forward-compatible: a column drop ships one release after the code stops reading it. Alembic handles versioning, and both databases receive identical migrations, which is what makes the research-to-serving sync safe.

Secrets. Vercel receives only `DATABASE_URL_SERVING` and `ANTHROPIC_API_KEY`. Everything else stays local. `SEC_USER_AGENT` must contain a real email; SEC blocks requests without it at the IP level, persistently. Config precedence: CLI flag, then environment variable, then `config.toml`, then dataclass default, resolved by one tested function.

Cost ceiling. Under $15 monthly, consisting only of Anthropic API usage. If the Neon serving database approaches 400MB, tighten the sync filters rather than upgrading; growth past that means the filters drifted from ADR 053's intent.

---

## 080. Windows Task Scheduler with catch-up, scheduled_runs table

Status: Pinned. Revised: systemd replaced by Task Scheduler when WSL2 was dropped (ADR 081).

Decision. Jobs run under Windows Task Scheduler with **"Run task as soon as possible after a scheduled start is missed"** enabled, so a job missed while the machine was off fires at next boot regardless of time of day.

```
16:30 ET  nightly   ingest, indicators, events, outcomes, sync
09:15 ET  poller    runs until 16:00
Sun 02:00 weekly    backtest, cell_stats, sync
1st 03:00 monthly   retrain, calibrate, promote or hold
```

Each task invokes `cscan <job>` through a `.bat` wrapper in `scripts/` that activates the virtualenv and sets `CAPSCAN_ENV`. Task definitions are exported to `scripts/tasks/*.xml` and committed, so the schedule is reproducible rather than living only in a GUI.

A `scheduled_runs` table records `(job, scheduled_for, actual_start, delay_seconds, status, run_id)`. A delay above 3600 seconds means the machine was off, and `cscan status` surfaces those explicitly rather than leaving the gap to be inferred.

This is safe because every job is idempotent per DESIGN §2.6: overlapping fetch windows, upserts everywhere, and windowed recomputes. A nightly job running at 11 AM the following day produces correct results.

GitHub Actions runs exactly one workflow: a health check every 6 hours that hits `/api/health` and alerts if staleness exceeds 2 days.

Rationale for the switch. systemd's `Persistent=true` was the sole reason WSL2 was in the design. Task Scheduler provides the identical behavior natively, so the WSL2 layer bought nothing and cost a layer of indirection.

---

## 081. Native Windows, Linux only inside the Postgres container

Status: Pinned. Supersedes the earlier WSL2 layout.

Decision. All Python jobs, backtests, training, and the poller run natively on Windows. WSL2 is not a development environment. The only Linux in the local stack is the `postgres:16` container image, which Docker Desktop runs in its own VM transparently.

```
C:/Users/daris/Desktop/School/capitalscan/
  data/cache/     gitignored, ~11,300 files, ~1.2 GB
  logs/           gitignored
  models/         Git LFS on promotion
  scripts/        .bat wrappers + exported Task Scheduler XML
```

Postgres data lives in a Docker named volume, independent of repo location.

**Consequence that must be handled in code: `ProcessPoolExecutor` uses spawn on Windows, not fork.**

- Every job module must be importable without side effects. No top-level execution.
- Every entry point needs `if __name__ == "__main__":`.
- Worker startup costs roughly 1 second each because spawn re-imports the module. This is a one-time cost per job since the pool reuses workers, not a per-task cost.
- Anything passed to a worker must be picklable. Frozen dataclasses from `core/config.py` are; database connections and open file handles are not. Workers open their own connections.

Rationale. WSL2 existed in the design solely for systemd's `Persistent=true`. Task Scheduler provides the same catch-up behavior (ADR 080), so WSL2 was a layer with no remaining justification. Dropping it also removes the `/mnt/c` 9p filesystem question entirely, which the measurements in the earlier draft had already shown to be negligible but which no longer needs discussing.

Precedent. Mini-Stockfish disk-streamed an 80 GB Parquet dataset natively on Windows, which is far heavier IO than this project performs.

Postgres tuning for 32 GB shared with other applications: `shared_buffers=1GB`, `work_mem=32MB`, `maintenance_work_mem=512MB`, `effective_cache_size=4GB`, `max_parallel_workers=8`. Peak footprint during backfill near 4 GB including Python workers.

---

## 082. Allowlist WIP snapshots, no full-repo auto-commit

Status: Pinned

Decision. A background script commits an explicit path allowlist to a throwaway `wip` branch every 4 hours, force-pushed and never merged. `git add -A` is never used.

```bash
git add core/ jobs/ research/ web/src/ handlers/ tests/ docs/
git commit -m "wip $(date -Iseconds)" || exit 0
git push -f origin HEAD:wip
```

Rejected: automated full-repo commits to main. Five failure modes. Mid-edit snapshots destroy bisect usefulness. `git add -A` will eventually catch a secret file, and a leaked API key in public history is permanent. Model artifacts and caches are binaries that git stores as full copies, growing the repo by hundreds of megabytes per year for files that get overwritten. GitHub blocks files above 100MB, warns past 1GB, and caps at 5GB, none of which accommodate a 1.5GB research database. And automated commits conflict with manual ones.

Storage tiers. Code, config, migrations, docs, `universe_union.csv`, and fixtures in git with manual commits. Promoted model artifacts in Git LFS, roughly 22MB per version, monthly. `cell_stats` exports as CSV in git weekly, since the diffs are meaningful. Research database dumps as GitHub Release assets monthly, `pg_dump -Fc` compressing 1.5GB to roughly 300MB, plus nightly dumps to a second local disk. Raw fetch cache nowhere, since it is refetchable.

---

## 083. Backup tiers, forward log is the irreplaceable asset

Status: Pinned

Decision. Research database backs up nightly via `pg_dump` to a second local disk, 7 daily plus 4 weekly, with monthly copies to GitHub Releases. Serving database relies on Neon point-in-time restore. Raw cache is not backed up. Model artifacts go to Git LFS on promotion. Code and config are in git.

Full disaster recovery from workstation loss is roughly 4 hours: clone, backfill, retrain. Everything is reproducible from public data plus committed code, which is what makes this light posture acceptable.

The one exception. `predictions` and `outcomes` cannot be recreated after the fact, because a prediction made on a past date cannot be honestly regenerated later. They live in the serving database on Neon, which is managed and backed up, rather than only locally.

Monitoring is unhosted. Every job writes a `runs` row; `ie status` prints last run and staleness per job; failures notify through the same `Notifier` protocol as signals. Logs are rotating JSON lines in `logs/` plus stdout, read with `cscan logs --job nightly --tail 100`. No aggregation service.

---

## 084. Poller gaps accepted and logged

Status: Pinned

Decision. The poller runs only while the workstation is on. Gaps are accepted in v1 and recorded explicitly in a `poller_sessions` table so the forward log knows which market days had no intraday coverage.

Rationale. Phase 2's poller proves the mechanism. The forward log does not become load-bearing until Phase 6, and by then moving the poller to Modal is a small change. Recording gaps explicitly prevents a future analysis from treating missing coverage as an absence of signals.

---

## 085. Test tiers weighted by failure silence

Status: Pinned

Decision. Coverage is allocated by how silently a failure would propagate, not uniformly.

| Area | Coverage |
|---|---|
| `core/signals.py`, `core/exits.py` | Exhaustive: unit, property, golden |
| Data validation and ingest | High |
| Statistics layer | High |
| API routes, UI, CLI | Smoke only |

Rationale. A bug in `exits.py` produces a plausible-looking wrong number that could persist for months. A bug in the screener produces a blank screen noticed immediately.

Tiers: `tests/unit` (pure functions, in-memory, under 1s), `tests/property` (hypothesis invariants), `tests/golden` (hand-verified known answers), `tests/integration` (real Postgres via testcontainers), `tests/acceptance` (phase gates, run manually).

Five tests carry the correctness load, each catching a failure that would otherwise ship silently:

1. Look-ahead detection. Shift all indicators forward one bar, rerun detection, assert Jaccard overlap below 0.5. High overlap means the signal is not reading the indicators or is already using future data.
2. Signal path parity. `detect()` on a bar must produce the same signal set as `breach_live()` walked across a simulated intraday path (open, low, high, close). This is ADR 006's enforcement.
3. Determinism. Two runs with identical config produce identical frames ignoring `run_id`, with test-ordering randomization enabled to catch hidden global state.
4. Exit resolver invariants, property-based: exit price within the bar's range, holding days in bounds, `mae <= 0 <= mfe`, and critically `mfe >= realized_return`. A violation there means path metrics and the exit disagree about what happened.
5. Split leakage, structural: date bounds per split, and no training event's forward window entering a validation fold.

CI: fast tier (ruff, mypy, unit, property) under 60 seconds; slow tier (golden, integration) under 5 minutes. Acceptance tests never run in CI. Coverage gate of 90% on `core/` only, no repo-wide target.

---

## 086. External indicator verification, semi-automated

Status: Pinned

Decision. Indicator values are verified once against an external reference (StockCharts or TradingView) for five dates across two tickers, and the comparison is frozen as a test.

Workflow, so the manual portion is minimal:

```bash
cscan verify-indicators --ticker TSM --ticker NVDA --dates 2026-07-29,...
```

prints a table of computed `bb_upper`, `bb_mid`, `bb_lower`, `k_full`, `d_full` and writes `tests/golden/external_reference.csv` with an empty `external_*` column per row. The external values get filled in once by hand, roughly 30 minutes. A test then asserts agreement within 0.1%.

Rationale. The formulas in ADR 004 are standard, but ddof choice and the middle-band SMA-versus-EMA question are exactly where implementations silently diverge, and every downstream number inherits the error. Thirty minutes against a class of error that would stay invisible for months.

---

## 087. Random-walk null test is the primary statistical guard

Status: Pinned

Decision. The statistics layer is validated against synthetic data with known properties before any real result is trusted.

Null test. Feed a driftless geometric Brownian motion series for 50 synthetic tickers over 2,500 days. Every cell's edge must be statistically indistinguishable from zero, with the fraction of cells at `q < 0.05` not exceeding 5%.

Recovery test. Feed a series with known drift, and assert the computed parametric baseline matches the analytical value within 1 percentage point.

Rationale. If a random walk produces significant cells, the statistics layer has a bug and every real result is suspect. This is the highest-value test in the suite, because it validates the reasoning rather than the code, and no unit test can catch the failure it targets.

---

## 088. cell_id is derived; serving views pin split to validate

Status: Pinned

Decision. `events` carries no `cell_id` column. Cell identity is computed by an immutable SQL function `cell_key(signal_type, side, dd_bucket, strength, entry_kind, split, era, horizon, target)`, used by both the statistics job and every view. Serving views join `cell_stats` on component columns with `split_key = 'validate'`, `era IS NULL`, `horizon_days = 5`, and `target_pct = 0.03` pinned.

Rationale, first reason. `cell_stats` carries `horizon_days` and `target_pct`, which are report parameters rather than event properties and do not exist on `events`. One event therefore belongs to many cells, one per horizon-target pair. A single `cell_id` column would store one arbitrary member of a set, and any later change to bucket boundaries would require rewriting every event row.

Rationale, second reason, and the more serious one. A live event fired today carries `split_key = 'holdout'` by date assignment. A view joining `USING (cell_id)` or on `e.split_key` would surface holdout statistics in the screener every day, silently and indefinitely, destroying the guarantee in ADR 019 and ADR 033 that holdout is evaluated exactly once at the end. The failure would be invisible because the numbers would look entirely reasonable.

Enforcement. `split_key = 'validate'` is hardcoded in `v_screen` and in any future serving view that reports statistics. This is a firewall, not a default, and must not be parameterized.

Consequence for the statistics job. It must write a pooled row per cell with `era IS NULL` alongside the four per-era rows. `v_screen` reads the pooled row; the research page reads the per-era ones.

Provenance. Found by a Claude Code session during migration 004, which correctly identified that `v_screen` referenced a column `events` did not have, and proposed adding it. The proposal was rejected for the two reasons above.

---

## 089. MFE unclamped; look-ahead guarded by signature, not shift alone

Status: Pinned

Two corrections found by a Claude Code session completing `core/exits.py` and `core/signals.py`.

**MFE is not clamped at zero.** An earlier test draft asserted `mae <= 0 <= mfe`, contradicting DESIGN §5.6. A position gapping down at t+1 that never trades back above entry has a genuinely negative MFE, since every forward high sits below the entry price. Clamping would render DESIGN §5.6's "`capture_ratio` stored null when MFE <= 0" clause dead and inflate every capture ratio. The correct invariants are `mae <= mfe` and the load-bearing `mfe >= realized_return`.

**The shift test cannot enforce the t-1 rule.** A single Jaccard threshold at 0.5 fails on real bars, measuring near 0.59 for a one-bar shift. The cause is arithmetic rather than a bug: bands drift roughly 0.35% of price per day against a bar range of roughly 1.59%, so a bar touching yesterday's band usually touches the day-before's too. Row-shuffled indicators collapse Jaccard to 0.067 and the ladder decays cleanly (1 to 0.59, 2 to 0.37, 5 to 0.14, 20 to 0.06).

More importantly, the shift design is structurally incapable of the check it was written for. If `detect` wrongly read bar t's indicators, shifting forward one bar would make it read t-1, producing the same magnitude of change and a similar Jaccard. The test catches a detector that ignores indicators entirely; it cannot catch reading the wrong bar.

Resolution. The shift test becomes a four-bound ladder anchored to a shuffled control, plus a blind-detector guard proving the suite can fail. The **actual** t-1 guarantee is structural and is asserted directly: `detect(bar, ind, sp)` receives one indicator row as an argument and may read only `low`, `high`, `ts`, and `ticker` from the bar, verified by a tracking probe that records attribute access. Bar t's indicators are not in scope, so no path to them exists.

The probe is the load-bearing part. It fails immediately if a future refactor passes the full indicator frame for convenience, or if someone adds `bar.close` to a band comparison.

**CI consequence, corrected after measurement.** `max_examples` is per test, not per suite. With 9 property tests, a naive 10,000-example run measures 509 seconds, not the 105 originally estimated.

Resolution: `full` is scoped to the four exit invariants via a pytest marker, which is what TESTS §3.4 and the Phase 3 gate actually name. That lands near 226 seconds, inside the slow-tier budget. Everything else runs at `ci_fast`. `ci_fast` is 250 examples rather than 1,000, since 1,000 measures 56.9 seconds for the property suite alone and exceeds the fast tier before unit tests, ruff, and mypy run.

**Packaging.** `packages = ["capitalscan"]` under `[tool.setuptools]` lists only the top-level package, excluding `core`, `jobs`, `research`, `handlers`, and `mcp` from a built wheel. Editable installs mask this. Use `[tool.setuptools.packages.find] include = ["capitalscan*"]`. The related mypy ambiguity had the same root cause: `capitalscan/__init__.py` did not exist, so `packages = ["capitalscan"]` resolved against a namespace package.

---

## 090. Absence is None, never NaN; debounce keys on a tuple

Status: Pinned

Two related corrections found by a Claude Code session while testing determinism in `core/signals.py`.

**`SignalHit.touch_level` is `float | None`, not `float`.** A stochastic-only signal touches no band, and that absence is `None`. DESIGN §3.4 previously pinned `float`, which forced NaN as the sentinel.

NaN as a sentinel for absence breaks equality in a way that is silent and consequential. `nan != nan`, so two structurally identical hits compare unequal. Meanwhile frozen dataclasses derive `__hash__` from their fields and `hash(nan)` is stable in CPython, so those same two hits hash identically. A set therefore keeps both. Identity comparison masks it further: `a == a` returns True because tuple comparison short-circuits on identity, while `a == b` returns False for structurally identical objects.

This is also §3.11 in spirit. That rule forbids filling nulls; encoding absence as NaN is the same error wearing a float.

**Debounce keys on `(ticker, signal_date, bound)`, never on the `SignalHit` dataclass.** DESIGN §4.7 always specified the key as one event per ticker per bound per day. Deduping on the whole dataclass is an implementation temptation that would have produced silent duplicate events in the events job, and it would break again on any future float field. Explicit keying is immune to field additions.

Both fixes are required. Either alone leaves the other failure mode live: fixing the type without fixing the key leaves the debounce fragile to the next float field, and fixing the key without fixing the type leaves `hit in seen_set` broken anywhere else it is used.

**CI budget, corrected again.** The marked split saved only 50 seconds because the exit invariants are themselves the expensive part. `test_stop_exits_land_at_or_beyond_the_stop_level` alone measures 193 s, since `assume(r.reason is ExitReason.STOP)` discards roughly two of every three draws. The slow-tier budget is raised to **10 minutes** rather than targeting the generator: a hand-built stop-hitting generator would test the stops someone thought to construct rather than the ones the parameter space produces. If the tier becomes painful, the fix is additive — keep the unfiltered generator at a lower count and add a targeted one alongside — never substitutive.

The ADR 060 determinism sweep is unmarked and runs at `ci_fast`. It is not one of the four §3.4 invariants and 250 cases establish it.

---

## 091. Config resolution lives in jobs, not core

Status: Pinned. Corrects BUILD §0.5.

Decision. `core/config.py` contains frozen dataclasses only. Its sole import is `dataclasses`. No `open()`, no `os.getenv()`, no `Path.exists()`, no `dotenv.load_dotenv()`.

Resolution moves to `jobs/config.py`, which owns the precedence chain (CLI flag, environment variable, `config.toml`, dataclass default) and returns populated frozen dataclasses. `core/` receives config objects as arguments and never fetches them.

Rationale. BUILD §0.5 asked for a resolution function with environment and TOML precedence and placed it in `core/`, which directly contradicts invariant 1 and DESIGN §3.1. The conflict was in the spec, not the implementation. Purity in `core/` is what makes the backtest and the poller callable with identical code paths and testable without fixtures, and a config module reading the environment breaks that for the module every other module imports.

**Malformed config fails loudly.** `resolve_config` swallowed `json.JSONDecodeError` with a bare `pass`, so `CAPSCAN_INDICATORS='{bad json'` silently produced `bb_window = 20` while the operator believed an override had applied.

This is worse than an ordinary silent failure because of provenance. `config_hash` is computed from the resolved config and stamped on every event row, every `cell_stats` row, and every `RESULTS.md` entry. A silently-defaulted config produces a hash asserting "these were the parameters" when they were not, and the record is internally consistent, so nothing later can detect it. ADR 034's provenance guarantee depends on the hash describing what actually ran.

Three rules, all raising rather than defaulting:

1. Parse failure raises `ConfigError` naming the variable and the underlying error.
2. Unknown keys raise. `{"bb_windwo": 20}` parses as valid JSON and applies nothing; validate against dataclass fields.
3. Type coercion failure raises. `{"bb_window": "twenty"}` must not land a string in a field typed `int`.

Testing consequence. Resolver tests must pass an explicit `config_file` and clear `CAPSCAN_*` via `monkeypatch.delenv`. A resolver defaulting to ambient CWD and environment makes the suite depend on the machine it runs on. Demonstrated during the session 1-3 audit: running with `CAPSCAN_INDICATORS='{"bb_window": 99}'` set fails two tests. With no `config.toml` committed, CI was green by luck rather than by construction.

---

## 092. Exit thresholds are ExitParams fields, never literals

Status: Pinned

Decision. `ExitParams` gains two independent fields: `exit_stoch_threshold: float = 80.0` for the long exit and `exit_stoch_threshold_short: float = 20.0` for the short. The exit path reads them and never uses a bare literal or a derivation.

**No `100 - threshold` coupling.** Measured on a 4x4 long/short grid, the derivation reaches 4 of 16 combinations, the diagonal only. `(long 70, short 20)` is unreachable — the coupling forces short to 30. That assumes exactly the symmetry ADR 016 rejects: drift runs against the short, band walking hurts it, and loss geometry differs. Shorts are computed but not surfaced in v1 (ADR 058), and their statistics feed the Phase 4 long-vs-short asymmetry analysis, so a coupled threshold would bias that comparison before it is ever read.

Rationale. `exits.py` hardcoded `80.0` and `20.0` for the stochastic exit. `SignalParams.stoch_overbought` is a sweepable parameter, so sweeping it would move the entry threshold while the exit stayed at the literal. Entry and exit would then disagree about what "overbought" means inside a single backtest, and every resulting number would look reasonable. This is invariant 9 and the silent-bias class TESTS §1 allocates coverage to.

Why a new `ExitParams` field rather than threading `SignalParams` into `resolve_exit`. The exit threshold is exit policy; the entry threshold is signal detection. They are conceptually independent even though they default to the same value, and coupling them would make an exit-rule sweep require a signal-config change. The `resolve_exit` signature pinned in DESIGN §3.7 also stays intact.

Enforcement. A source assertion that no bare numeric literal appears in the exit threshold comparison, plus boundary tests on each side of the configured value.

**What that assertion actually covers, measured 2026-08-09.** It is
`test_exits.py::test_exits_module_contains_no_bare_stochastic_literal`, and
the sentence above overstates it in three ways worth knowing before trusting
it:

1. **One module.** It calls `inspect.getsource(exits)`, so its universe is
   `core/exits.py` and nothing else. No other Python module, no SQL.
2. **Two spellings.** The body is `assert "80.0" not in body and "20.0" not
   in body`. A bare `80`, `80.00`, or `int(80)` passes untouched.
3. **No SQL at all**, which is how ADR 095's `v_positions` defect survived
   in `db/schema.sql` while this ADR read as solved.

The third is the one that already cost something, and the first two are why
ADR 095's proposed remedy is insufficient as written. That ADR says to
"extend ADR 092's source assertion to scan checked-in SQL." Widening the
file list is not enough: `db/schema.sql:1005` spells the threshold
`(s.k_full >= (80)::numeric)` and line 1008 spells the timeout `>= 5`.
Neither string is `"80.0"` or `"20.0"`, so the existing matcher finds
nothing even pointed directly at the file. **The matcher needs replacing,
not just re-aiming** — a numeric-literal pattern next to a comparison
operator on a threshold-bearing column, not a substring search for two
specific float spellings.

This is recorded here rather than only in ADR 095 because this ADR is the
one whose Enforcement line a reader consults to decide whether the rule is
guarded. It is guarded in one file, against two spellings.

Found during the session 1-3 audit. The audit also verified independently that every DESIGN §3.5 formula matches a from-scratch reimplementation, that `ddof=0` genuinely differs from `ddof=1` so the population-std choice is real rather than incidental, that `realized_vol` provably reads `adj_close` (the §2.2 exception) by giving `adj_close` an independent path from `close` and confirming the output followed it, and that all 19 output columns declare warmups at or above their actual first-valid index with `max_warmup() = 272` matching §2.7.

---

## 093. Terminal quantiles expand to five horizons

Status: Provisional. Condition answered by 113 (2026-08-17); head count reduced by 113 from ~56 to 20, and the reachability heads this ADR's amendment left open are retired there. Authorizes a Phase 6 rewrite of DESIGN §7.4

Decision. Terminal-return prediction moves from one horizon to five. Where DESIGN §7.4 currently pins a single terminal-quantile head, `Q̂_τ(R_5)` for τ ∈ {0.05, 0.25, 0.50, 0.75, 0.95}, Phase 6 instead fits that quantile fan independently at each of `h ∈ {1, 2, 3, 5, 10}` trading days:

$$\hat{Q}_\tau(R_h), \quad \tau \in \{0.05, 0.25, 0.50, 0.75, 0.95\}, \quad h \in \{1, 2, 3, 5, 10\}$$

Twenty-five terminal-quantile outputs in place of five. Reachability (4 heads) and adverse excursion (2 heads) are unchanged, so the eleven-head count in ADR 064 becomes roughly thirty-one.

Rationale. `touched_Xpct` answers whether a move happens inside the window, not when. A signal whose favorable excursion typically peaks on day 2 and one that typically peaks on day 9 both read as "touched" under a single 5-day reachability flag, and the current single-horizon terminal quantile carries no timing information either — `Q̂_τ(R_5)` is silent on the path between t and t+5. The two signals call for different holding behavior and look identical to every head in the current design. Fitting the quantile fan at each of the five horizons already carried by `events.fwd_ret_1d/2d/3d/5d/10d` (populated by session 9) makes peak timing a first-class model output instead of information the label set discards.

This is not a departure from §7.4's existing position, it is the same argument carried one step further. §7.4 already states the model describes the underlying path and exit policy sits as a separate layer on top, specifically to avoid coupling the model to a config that changes on every stop or target sweep. A per-horizon quantile fan is a more complete description of that same path, not a different kind of target. The horizon set is not a new invented number either: it is exactly the five horizons `events` already carries, so this decision asks the model to predict a distribution at each horizon that already exists as a point value, rather than introducing horizons the pipeline has never computed.

Why now, not at Phase 6. Session 10 builds the forward path store (`docs/sessions/session10.md`). A path table sized to the exit window (`ExitParams.max_hold_days`, 5 days) would make any label past day 5 permanently underivable, including day 10 terminal quantiles. The path table has to be built to the full ten-trading-day window before that decision is foreclosed by schema, even though the model that consumes it is several sessions away. Session 10 already fixes its window at ten trading days for this reason; this ADR is the record of why that window has to reach that far, not a decision Session 10 needed to make on its own.

Cost. Roughly triple the terminal-quantile heads, in line with training time (5 minutes for eleven heads per ADR 064 scales toward, not exactly with, head count, since heads share the walk-forward CV loop and feature construction). More calibration work: DESIGN §7.6 checks quantile-head coverage per τ, and ADR 067's four-check promotion gate evaluates coverage within 5 points of nominal for every τ, so twenty-five coverage checks replace five. Quantile crossing within a horizon is already fixed post-fit by sorting (§7.4); five horizons adds a second, previously nonexistent question of monotonicity *across* horizons for a fixed τ — whether $\hat{Q}_\tau(R_1) \le \hat{Q}_\tau(R_{10})$ should hold in expectation and what to do when a fit violates it. Phase 6 resolves that when it writes the rewritten §7.4, not here.

What this does not change. Reachability and adverse-excursion head definitions are untouched, still `touched_Xpct` and `touched_-Ypct` over the existing horizon and threshold sets. Exit policy remains a separate layer on top of timeout return, per §7.4's existing rule. Nothing about signal detection, entry, or exit resolution moves. `DESIGN §7.4` itself is not rewritten by this ADR — that table is a Phase 6 implementation spec, and pinning its exact head list now would front-run Phase 4's statistics, which ADR 033's kill criteria could still retire the two-indicator hypothesis before Phase 6 opens. This ADR authorizes the rewrite when Phase 6 begins; it does not perform it.

Status rationale. Provisional rather than Pinned: this constrains a schema decision Session 10 makes now (the path table's window), but the model surface itself is not built until Phase 6, and ADR 033's kill criteria sit between here and there. If Phase 4 finds no cell survives FDR correction, there may be no model to expand.

### Amendment, 2026-08-09: the peak-within-horizon target family

**Decision.** Phase 6 fits a second quantile fan, over the running maximum of the entry-anchored return, at the same five horizons this ADR already names. For an event with entry-anchored return $R_t$:

$$M_h = \max_{1 \le t \le h} R_t, \qquad h \in \{1, 2, 3, 5, 10\}$$

$$\hat{Q}_\tau(M_h), \quad \tau \in \{0.05, 0.25, 0.50, 0.75, 0.95\}$$

Twenty-five peak heads alongside the twenty-five terminal heads above. With reachability (4) and adverse excursion (2), the surface reaches roughly **fifty-six heads**, against the ~31 this ADR previously implied and the 11 in ADR 064.

**Why it was missing.** This ADR was written to force Session 10's path window to eleven trading days, and it argued that case entirely in terms of *terminal* return, because a ten-day window would have made `fwd_ret_10d` underivable for `NEXT_OPEN` entries. Session 10 then built `path` with a per-day `favorable` extreme, which makes the peak process derivable at full resolution — and nobody wrote that down. The gap is documentary, not technical: the data has been complete since Session 10 shipped. Recorded now (user's decision, 2026-08-09) rather than at Phase 6, because an undocumented target family is one a Phase 6 planner builds to this ADR's head list and silently omits.

**Why quantiles, not a point estimate.** Same reason this ADR gives for terminal return. "The median peak inside three days is 2.1%, the 75th percentile 3.8%" sizes a limit order. A mean peak does not, and a peak distribution is skewed enough that the mean is not near the median.

**Why this is not `mfe`.** `events.mfe` is bounded by `exit_idx` and therefore by `ExitParams.max_hold_days`, so it cannot see day 10, and it moves whenever a sweep changes the stop or target. $M_h$ is a property of the price path with no exit policy in it. Training on `mfe` would couple the model to config and force a retrain on every exit sweep, which is exactly the coupling ADR 064 rejects for terminal targets. The same rule applies here and is easy to violate by accident, since `mfe` is the column that already exists.

**Three structural properties, free constraints for the fit.**

1. $M_h \ge R_h$ for every $h$. The peak inside a window is at least its terminal value.
2. $M_h$ is non-decreasing in $h$. A longer window cannot lower a maximum.
3. $Q_\tau(M_h) - Q_\tau(R_h)$ is giveback at the distribution level — the quantity `events.giveback` would have carried before ADR 094 dropped it as unserved.

Property 2 is the cross-horizon monotonicity question this ADR's main body defers to Phase 6 for the terminal fan. For the peak fan it is not a modeling judgment but an arithmetic fact, so a fit violating it is a bug rather than a tolerance.

**Open decision this creates, to be made deliberately.** The four reachability heads become redundant. $P(\max_t R_t \ge X)$ is the survival function of $M_5$, so the peak fan expresses the same distribution at fixed quantiles rather than fixed thresholds. Phase 6 must either retire those heads or keep them explicitly as a calibrated, human-readable slice of the peak distribution. Keeping both without deciding means training six heads against one distribution and reporting them as independent evidence.

**Materialized, corrected same day (2026-08-09, user's decision).** An earlier draft of this amendment argued $M_h$ should stay derived because it is a *target* rather than a *feature*, and that materializing it would add a cache-coherency obligation. Both halves were wrong, and the counter-argument is the one this repo already answers by example.

`touched_Xpct` is materialized and derives from the same `path.favorable` column. If "targets stay derived" were the operative rule, that column would not exist. And the coherency obligation is not new: `mfe`, `touched_Xpct`, `day_touched_Xpct`, and `fwd_ret_*d` already carry it, and ADR 094 already built the fix by putting `run_backtest` in the weekly chain. Five more columns ride that same refresh.

ADR 094 also provides for this directly, in the clause the earlier draft read past: "any future label family that needs efficiency can add a materialized column in a separate migration." Materializing is the sanctioned path, gated on a migration rather than forbidden.

The deciding reason is Phase 4, not Phase 6. DESIGN §6.1 pins the statistics job as reading `events`; joining `path` (57M rows) inside it is far heavier than reading a column. Materializing now keeps open the option for Phase 4 to report peak statistics per cell, and specifically to answer this amendment's own reachability-redundancy question — whether `touched_Xpct` carries information $M_5$ does not — which is answerable in Phase 4 only if the column exists. Adding it later means a migration plus a re-run mid-phase.

`events` gains `peak_ret_1d`, `peak_ret_2d`, `peak_ret_3d`, `peak_ret_5d`, `peak_ret_10d`, `numeric(12,6)` nullable, matching `path.favorable`'s precision and the nullability of every other per-event label column. `path` is unchanged and remains the source of truth; these are a cache of it, refreshed on the same schedule as the rest.

One trap to avoid, learned from `699cb410d219_events_giveback.py`: that migration added `events.giveback` and no population path ever wrote it, so the column has sat NULL on every row since. The migration and the write path ship together here.

**Canonical derivation.** One implementation, per invariant 2. `research/path_labels.py` owns it; nothing recomputes `max(favorable)` inline.

```sql
SELECT p.event_id,
       max(p.favorable) FILTER (WHERE p.day_offset <= e.entry_offset + 1)  AS peak_1d,
       max(p.favorable) FILTER (WHERE p.day_offset <= e.entry_offset + 2)  AS peak_2d,
       max(p.favorable) FILTER (WHERE p.day_offset <= e.entry_offset + 3)  AS peak_3d,
       max(p.favorable) FILTER (WHERE p.day_offset <= e.entry_offset + 5)  AS peak_5d,
       max(p.favorable) FILTER (WHERE p.day_offset <= e.entry_offset + 10) AS peak_10d
FROM path p JOIN events e ON e.id = p.event_id
GROUP BY p.event_id;
```

`entry_offset` is 1 for `NEXT_OPEN` and 0 otherwise, the same convention `derive_labels_from_path` uses. Measured on 20,000 filled `touch` entries under `1835688bf7d760ba` (2026-08-09), mean $M_h$ runs 1.14% / 1.69% / 2.10% / 2.75% / 3.97% across the five horizons, monotone as property 2 requires.

**Status.** Provisional, inheriting this ADR's own rationale. ADR 033's kill criteria still sit between here and Phase 6.

---

## 094. Path table is source of truth; labels are derived views

Status: Pinned (Session 10, task 10.3)

Decision. The `path` table stores the atomic forward price data for every event — per-day favorable extreme, adverse extreme, and terminal close — over a fixed eleven-trading-day window. All event-table label columns (MFE, MAE, time-to-MFE, touched_Xpct, day_touched_Xpct, fwd_ret_Xd, capture_ratio, giveback, etc.) are derived from the path rows, not independently computed and cached. Labels are recomputed on demand as queries against the path table, except for a small materialized cache on the event table for hot serving columns.

Rationale. Session 9 computed labels once at backtest time and stored them on the event table. That coupling meant adding a new label family required a code change and a full re-derivation from price history — a high friction loop that discouraged exploration and prevented labels from tracking config changes (new thresholds, new horizons) without re-running the entire backtest. The path table decouples label definition from the event computation: price windows are fixed once at ingestion (DESIGN §7.4), and label families are config-parametrized queries on that immutable path data.

Two windows, different meanings. The path table's window is eleven trading days: `max(StatsParams.fwd_ret_horizons)` (10) plus the largest entry-to-signal offset across `EntryKind` (1, for `NEXT_OPEN`). Corrected 2026-08-05 — this ADR and session10.md §0 both originally said ten, and ten is wrong. `path.day_offset` counts from `signal_date` while every label is entry-anchored, so a `NEXT_OPEN` event's `fwd_ret_10d` sits at `day_offset=11`. A ten-day window would leave that offset permanently absent and every `NEXT_OPEN` event's 10-day horizon structurally NaN. `research.path_backfill.window_days_for_config` derives the number; nothing hardcodes it. This is a measurement span independent of exit policy — if `ExitParams.max_hold_days` changes from 5 to 3, the path data remains valid because the path table measures what happened, not what any particular exit rule chose to capture. Materialized labels like MFE/MAE cover only the first five days (the exit window), but the underlying path data persists for any future label that needs it.

Enforcement. The path table has a foreign key constraint to events with ON DELETE CASCADE, ensuring consistency. Reconciliation tests (DESIGN §5.10, expanded in TESTS §5.a) verify that labels derived from the path table match labels Session 9 produced directly from price history, with tolerance only for numeric rounding as specified in ADR 089 (MFE computation) and ADR 090 (capture_ratio near-zero handling). Any mismatch is investigated before proceeding, not adjusted away.

Cost. Materialized redundancy: the event table carries precomputed labels for serving and Phase 4 statistics, duplicating what the path table can supply. This is deliberate. The labels are the *precompute*, and the path table is the *source of truth*. Phase 4 statistics read the materialized columns for performance; any future label family that needs efficiency can add a materialized column in a separate migration. The redundancy is not removed until Phase 4 has executed against the new layer and confirmed identical results.

Label families in scope today. Session 9 labels (MFE, MAE, time-to-MFE, reachability across all configured thresholds and the five existing horizons, capture_ratio, giveback). Session 10 adds first-touch-day flags for each threshold and horizon combination. Terminal-return distributions across horizons (ADR 093's multi-horizon quantile model) are derived from the path table but materialized as new columns only once Phase 6 determines the exact surface — until then, the five existing horizons (fwd_ret_1d through fwd_ret_10d) are the only terminal-return columns, and any new horizon would be added as a config parameter that feeds Phase 6's retraining.

What this changes from Session 9. Session 9 computed and stored all labels at event creation. Session 10 separates concerns: the `path` table is written at event creation (Tasks 10.2, 10.6) and labels are recomputed from it whenever the label definition changes (Task 10.3).

Path rows are append-mostly, not immutable (corrected 2026-08-05). A day's row, once its bar is final, does not change in practice, but `run_path_capture` writes through `ON CONFLICT (event_id, day_offset) DO UPDATE` and a re-ingested bar therefore rewrites the affected day. Two consequences worth stating rather than discovering: restart safety comes from that upsert rather than from immutability, and a stored Session 9 label frozen before its window closed will disagree with the grown path — which is why reconciliation excludes still-accumulating and recently-signalled events instead of treating the disagreement as a defect (`research/path_reconcile.py`, `RECENT_BARS_REVISION_DAYS`). The event table's label columns stay in place through Session 10 to support reconciliation and Phase 4 execution. Cleanup (dropping the old columns) happens only after Phase 4 has run once against the new layer and published its results (deferred to post-Phase 4).

Materialized labels need a refresh job, not just an exclusion filter (added
2026-08-06). The paragraph above states that a label frozen before its window
closed will disagree with the grown path, and points at reconciliation's
exclusion filters. Those filters make the *gate* pass; they do not make the
*label* correct. `research/enrich.py` is reachable only through
`run_backtest`, and `run_backtest` was wired into no schedule, so a frozen
label stayed frozen forever. Measured 2026-08-06: event 2775021 (CAT,
signal_date 2026-07-29) carried `touched_5pct = false` and `mfe = 0.042601`
against a `path` whose `favorable` had since reached 0.153993. Worse, the
exclusion is time-bounded — `_drop_recent_events` filters on a 45-day
`signal_date` window measured from `date.today()`, so a stale event ages out
of the exclusion while staying stale, and `cscan path reconcile` begins
failing with no code change behind it. The refresh is therefore scheduled:
`run_backtest` runs in the weekly chain (`jobs/cli.py::weekly`), matching
DESIGN §9.4's schedule table, which listed it all along. Phase 4 additionally
filters on `fwd_window_days >= entry_offset + max_hold_days` so an
unrefreshed event cannot enter a statistic regardless of scheduling.

One limit, found while wiring this and worth stating so nobody plans around a
refresh that cannot happen. A backtest re-run refreshes labels only for the
config it runs under, and the default config moved on 2026-08-05
(`min_mcap_usd` 100e9 → 30e9, plus the new `SignalParams.stoch_source` field),
taking the default hash from `3e598c59e7d71eae` to `1835688bf7d760ba`. The old
hash is unreachable: reverting `min_mcap_usd` yields `a6c54c878368cd29`,
because `stoch_source`'s presence changes the hashed dataclass shape and the
field cannot be un-added. The Phase 3 run of record therefore keeps its labels
as they stand, permanently. That is acceptable — it is published evidence
rather than a live serving surface — but it means the refresh guarantee here
applies to configs the pipeline still runs, not retroactively to every
`config_hash` in `events`.

Provenance on `path` (added 2026-08-06). `path` shipped with `event_id`,
`day_offset`, `favorable`, `adverse`, `terminal` and nothing else, which
violates ADR 034. Every other generated table carries `run_id`
(`events`, `indicators`, `bars`, `cell_stats`, `benchmarks`); this was an
omission in Task 10.1's minimum shape, not a decision. It already cost real
time: reconciliation findings 3 and 7 both needed to know which `bars`
snapshot a path row was computed against, could not read it from `path`,
and were resolved by cross-referencing `bars.run_id` and hardcoding a ticker
list plus a specific `run_id` into `path_reconcile.py` as evidence. Because
the capture upserts, a row's origin was otherwise unrecoverable. `path` now
carries `run_id text` and `computed_at timestamptz`, both rewritten by the
upsert so they describe the write that produced the row's current values.

`events.giveback` is dropped, not populated (decided 2026-08-06). The column
exists and is NULL on all 5.57M rows. It stays derivable —
`research/path_labels.py::derive_labels_from_path` computes it, and
`test_path_labels.py` verifies that computation against in-memory frames —
but nothing serves it hot, so materializing it would contradict this ADR's
own "materialize only what serving needs" rule. The post-Phase-4 cleanup
migration drops the column alongside the other Session 9 label columns.
Reversible until that migration runs: if Phase 4 finds it wants giveback in a
cell statistic, populate it instead and amend this paragraph.

---

## 095. Exit thresholds in SQL are a second exit implementation

Status: Provisional. Authorizes a Phase 5 rebuild of `v_positions`

Decision. ADR 092 is not fully enforced. Its enforcement is a source assertion
scoped to the Python exit comparison in `core/exits.py`, and it never inspected
SQL. `v_positions` carries a second exit implementation with the thresholds
baked in as literals:

```sql
(s.k_full >= (80)::numeric)          AS exit_signal_stoch,
((CURRENT_DATE - p.entry_date) >= 5) AS exit_signal_timeout
```

Those are `ExitParams.exit_stoch_threshold` (80.0) and
`ExitParams.max_hold_days` (5), written as constants. The view is corrected in
Phase 5, when the serving layer is built. It is **not** rebuilt now: Phase 4 is
statistics and does not read `v_positions`, and rebuilding a serving view
ahead of the session that owns it invites a second rewrite. This ADR exists
because ADR 092 currently reads as though the problem is solved, and the
reasoning is cheaper to write down now than to rediscover.

Why it matters. Sweep `exit_stoch_threshold` to 70 and the backtest moves while
the position page still reports exits at 80. Entry and exit surfaces then
disagree about what "overbought" means, and every number on both sides looks
reasonable. That is exactly the silent-bias class ADR 092 and invariant 9
exist to prevent — ADR 092 removed one instance of it and left another
standing in a different language.

Two further defects in the same expression, found 2026-08-06.

`exit_signal_stoch` ignores `p.side`. `positions` carries a `side` column and
the view applies `k_full >= 80` to every row. A short position's stochastic
exit is `k_full <= exit_stoch_threshold_short` (20.0), the field ADR 092
introduced specifically so the two sides stay independent. As written, an open
short reads its exit signal off the long threshold and is wrong in the
direction that suppresses the exit. Shorts are computed but not surfaced in v1
(ADR 058), so nothing reads this today; it is a latent defect that activates
the moment shorts are surfaced.

`exit_signal_mid_band` is exposed unconditionally, though
`ExitParams.exit_on_mid_band` defaults to `False` per ADR 046. The view
publishes a signal the policy has switched off, with no indication that it is
inactive.

Fix options for Phase 5, neither chosen here. Pass the thresholds in through a
settings table the view joins, which keeps the view a plain view but adds a
row-existence dependency to every read. Or generate the view DDL from
`ExitParams` inside the migration, which keeps the numbers single-sourced at
the cost of making the DDL a build artifact rather than a checked-in file. The
second is the better fit for this codebase's "one implementation" rule; the
first is easier to sweep without a migration. Phase 5 decides with the
serving-layer requirements in hand.

Enforcement, once fixed. Extend ADR 092's source assertion to scan checked-in
SQL (`db/schema.sql`, `db/migrations/`) for numeric literals in exit
comparisons, not just Python. The assertion's blind spot is the reason this
survived.

**The assertion extension is not deferred to Phase 5, and this ADR reads as
though it is (clarified 2026-08-09).** Two separable pieces got bundled into
one deferral above:

- **Rebuilding `v_positions`** genuinely waits for Phase 5. Phase 4 does not
  read the view, and rebuilding a serving surface ahead of the session that
  owns it invites a second rewrite. That reasoning holds.
- **Extending the assertion** waits on nothing. It is a test change, it
  guards every future instance of this defect class rather than the one
  instance found here, and leaving it until Phase 5 means the next SQL exit
  literal lands with the same silence. Nothing about Phase 4's scope
  prevents writing it.

**And the extension as described here would not work.** Measured
2026-08-09: the existing assertion is a substring search for `"80.0"` and
`"20.0"` inside `inspect.getsource(core.exits)`. The literals in this very
view are spelled `(80)::numeric` (`db/schema.sql:1005`) and `>= 5` (line
1008). Pointing that matcher at SQL files finds nothing, because neither
string appears. The fix is a different matcher — a numeric literal adjacent
to a comparison on a threshold-bearing column — not a wider file list. See
ADR 092's Enforcement section for the full accounting of what the current
assertion covers.

---

## 096. `cell_stats` is keyed by config, not by cell alone

Status: Pinned

Decision. `cell_stats`' primary key becomes `(cell_id, config_hash)`, and
`cell_key()` — Phase 4's key derivation, unwritten as of this ADR — takes
`config_hash` as an input alongside signal type, side, drawdown bucket,
strength, entry kind, split, era, horizon, and target. This amends the
`cell_id text PRIMARY KEY` line in DESIGN §6.9.

Rationale. The single-key design assumed one current statistics snapshot
serving the frontend, and `/api/stats` caching "until `cell_stats.run_id`
changes" reads naturally under that assumption. The assumption stopped holding
when Session 9's sweep wrote 18 distinct `config_hash` values into `events`.
Under the old key, each Phase 4 run overwrites the previous one, so comparing
18 exit configs statistically means running Phase 4 eighteen times and
exporting between runs — a manual, error-prone loop whose intermediate state
lives in files rather than the database. Comparing configs is the entire point
of having swept them.

Cost. `/api/stats` must now pick a `config_hash` rather than reading whatever
is present. It selects the run's config by default, so the frontend behavior
is unchanged; the parameter exists for the research page. Row count multiplies
by the number of configs held, which at 18 configs is still small against the
cell grid.

Why now. `cell_stats` is empty (verified 2026-08-06: 0 rows) and `cell_key()`
does not exist yet, so this costs one migration and nothing else. Retrofitting
after Phase 4 runs would cost the same migration plus a full Phase 4 re-run.

Not changed. `cell_id` stays derived from its component columns and is still
never stored on `events` (invariant 5b, ADR 088). Serving views still hardcode
`split_key = 'validate'`. `config_hash` joining the key does not make it a
component of `cell_id` itself — the two are separate columns in a composite
key, so an existing `cell_id` string means the same thing it always did.

---

## 097. Backtest reads events only within a rolling 2-year window

Status: **Superseded by ADR 019 and ADR 009, which it cannot coexist with. Implemented 2026-08-08, reverted 2026-08-09, never in effect for a completed backtest.**

**Why it was reverted, stated first because the rest of this entry describes something that does not exist.** A 756-day window measured from 2026-08-09 floors at **2024-07-14**. Every split boundary in `SplitParams` sits behind that floor: `train_end` is 2021-12-31 and `validate_end` is 2023-12-31, so the window contains holdout dates and nothing else. Measured on the 299 rows the nightly job wrote under the short-lived hash: **100% `split_key = 'holdout'`, zero train, zero validate.**

Four things break at once, none of them recoverable by tuning the number:

- **Phase 4 cannot run.** Its headline deliverable is the cell grid with BH correction *on the validation split* (Phase gates, below). There is no validation split.
- **`v_screen` returns nothing.** ADR 088 hardcodes `split_key = 'validate'` in every serving view as a firewall, explicitly not parameterizable.
- **ADR 009's discipline collapses.** "Sweep on train, select on validate, report on holdout once" requires all three to be non-empty.
- **The holdout rule inverts.** CLAUDE.md pins holdout as evaluated exactly once at the end. A config producing only holdout events makes every routine run an evaluation of it.

Any window short enough to satisfy this ADR's intent is shorter than the distance from today back to `validate_end`, so no value of `max_lookback_days` fixes this. Only moving the split boundaries would, and that trade is rejected below.

**The concern behind it is legitimate and already has a mechanism.** `StatsParams.era_bounds` is `("2014-12-31", "2019-12-31", "2023-12-31")` and Phase 4's deliverables include an era breakdown. That answers "does the edge hold in the recent regime?" against the full sample, instead of answering it against a sample too small and too recent to carry a train/validate split. Recency is a reporting dimension here, not an ingestion filter.

**Precedent that should have been read first.** ADR 002, superseded, argued: "Two years covers 2024 to 2026 only, a period where dip-buying worked. Testing a dip-buying rule on a window selected for dip-buying success produces a result with no forward validity." It was superseded by ADRs 035 and 040 in the direction of *more* history, not less. This ADR reopened a settled question without engaging the reasoning that settled it. CLAUDE.md's instruction to stop and ask when a task appears to contradict an ADR applied here and was not followed.

**What was rejected, and why.** Re-cutting `SplitParams` inside the two-year window (train 2024-07 to 2025-06, validate 2025-07 to 2026-01, holdout 2026-02 onward) would preserve the window at roughly eight months per split. ADR 001 sized the training universe wide specifically because effective sample after clustering is the binding constraint on detecting a 5-15 point effect. Trading 373k train and 76k validate events for something near 17k and 10k signals moves in the wrong direction on the one axis that decides whether anything is measurable.

**Residue.** 299 events under `4630b12a84ff52de` remain in `events` from the one nightly run that fired while the field existed. That hash is unreachable now and the rows serve nothing.

---

The original entry follows, unchanged except for this header, per this file's no-deletion rule.

Decision. `SplitParams.max_lookback_days` is set to 756 days (approximately 2 years). The backtest reads indicators and detects events only within this window from today's date, regardless of the `event_start` date in config.

Rationale. Market regimes shift substantially over longer horizons. Growth-to-value rotations, sector leadership changes, and macroeconomic regime changes render 5+ year-old data less relevant for predicting current-market behavior. Testing a dip-buying signal on a 15-year backfill that includes 2024's crash and 2020's pandemic recovery applies the signal across regimes it was never designed for, inflating historical Sharpe ratios without improving forward validity.

A rolling window keeps the backtest trained on recent market conditions. At the `max_lookback_days` boundary, old events age out without cluttering the code with explicit windowing logic. The 756-day default covers roughly 2 years of trading days, sufficient to capture seasonal patterns and one full market cycle while excluding regime shifts older than the last 3-4 market years.

Implementation. `_backtest_one_ticker` resolves one `effective_start = max(event_start, as_of - max_lookback_days)` and applies it to all three of its reads: daily bars, indicators, and hourly bars. The floor is safe because every bar access downstream sits at or forward of the entry — `resolve_exit_for_entry` slices `bars.iloc[entry_idx + 1:]`, `forward_returns` shifts negative, `_first_hourly_touch` is scoped to the entry date. No consumer reads history earlier than the window, so the event set is identical to an unfloored read.

That identity is load-bearing rather than incidental. `max_lookback_days` is a hashed field, so `config_hash` cannot distinguish a floored run from an unfloored one. Had the event sets differed, two outputs would sit under one provenance key, which is the failure ADR 060 exists to prevent.

**`as_of` is data-derived, never `date.today()`.** The first implementation of this ADR (2026-08-08, same day) used the wall clock, which breaks ADR 060 outright: the same config against the same database produces a different event set tomorrow, stamped with an unchanged `config_hash`. `research/backtest.py::_last_bar_date` resolves the anchor from `max(bars.ts)` for the ticker, the same bound `apply_eligibility` already uses, read one query earlier because the floor now bounds the bar read itself. An explicit `today=` passed by the caller still overrides it uniformly. This is recorded rather than quietly fixed because the module's own docstrings already forbade a clock read in two places and the mistake was made anyway.

**The harness is deliberately exempt.** `jobs/cli.py::_load_bars_by_ticker` is a separate read, floored at `ingest_start`, feeding the Phase 3 gate (DESIGN §5.10). It stays on full history. The gate validates the engine's structural properties, and `_check_no_lookahead`'s Jaccard bounds (ADR 089: 1 to 0.59, 2 to 0.37, 5 to 0.14, 20 to 0.06) were measured against full history. A 20-bar shift over a two-year window is a materially different test, so moving that floor requires re-measuring those bounds first. Not done here.

Cost, measured rather than estimated. Indicator rows drop from 2,510,112 to 314,231 and daily bars from 2,909,180 to 314,545. Hourly is unchanged at 2,094,898, since the archive starts 2024 (ADR 037) and already sits entirely inside two years.

The runtime baseline stands at CLAUDE.md's **2h48m** for `cscan backtest`: a 20-38 min write phase (what `runs` records) plus the ~2h28m single-threaded harness, which runs outside `run_job`'s timed block and is recorded nowhere. An earlier draft of this paragraph claimed ~36 min, having read a `runs` duration as a whole-job timing; that was wrong. `cscan weekly` skips the harness by design and genuinely is ~36 min, which is the run that misled the reading.

Since the harness is exempt from this ADR's floor, the saving lands only in the write phase, and no post-ADR-097 run exists yet to measure it.

**The scoping is the reason for this ADR, not the speedup.** A backtest scoped to two years reading fifteen years of bars is incoherent on its own terms. The harness exemption above rests on the Jaccard bounds needing re-measurement, which holds regardless of what the timing turns out to be.

Cost, provenance. This moves the default `config_hash` from `1835688bf7d760ba` to `4630b12a84ff52de`, the third move (see `test_default_config_hash_is_pinned`). The `capitalscan.default_config_hash` GUC, which `v_events` and `compute.scan()` both read, must be re-pinned by hand — no migration records it. Until it is, the serving surface keeps returning the 622,053 rows under the old hash, stale rather than empty.

Note the asymmetry this exposes. `max_lookback_days` is read only by `research/backtest.py`, so `run_events` and the poller write rows byte-identical to their old ones under the new hash. Only the stamp differs. The hash still has to move, because it covers the whole `Config` and cannot express "this field matters to one job and not another." Re-stamping 622k rows with no analytical change behind it is the price ADR 060 charges to keep one hash meaning exactly one config.

What this does not change. The research store continues ingesting from 2009 and computing indicators back to 2010, so the 200-day SMA and 252-day drawdown windows are unaffected. Indicators are precomputed and stored by a separate job with full history behind them (`max_warmup() = 272`, ADR 092); reading a two-year slice reads finished values, never a recomputation from truncated input. The train/validate/holdout splits by date (ADR 019) remain fixed calendar dates.

---

## 098. Effective sample size: rho-bar definition, computation, and storage

**Status.** Pinned, 2026-08-10.

**Context.**

DESIGN §6.3 defines the effective sample size correction as

```
n_eff = n / (1 + (k_bar - 1) * rho_bar)
```

and specifies `k_bar` as the mean `cofire_count` across a cell's events, which `events.cofire_count` already stores. It specifies `rho_bar` only as "the mean pairwise correlation of 5-day returns among co-firing tickers, computed once per era and stored as a per-era constant."

Three things were left open, and every confidence interval and every Benjamini-Hochberg q-value in Phase 4 depends on all three. A `rho_bar` set too low understates clustering, narrows every interval, and turns noise into apparent significance. Set too high, it suppresses cells carrying real information.

The correction exists because a signal firing on twelve names the same day does not deliver twelve independent observations. If the market fell that day, it delivers something closer to one. DESIGN §6.3's power table assumes `n_eff`, not `n`: detecting a 3-point edge at 80% power needs roughly 2,100 events, and a wrong `rho_bar` moves that number by a multiple, not a margin.

**Decision.**

Four parts.

**1. Overlapping 5-day windows.** Measure the 5-day return starting on every trading day, yielding roughly 250 observations per ticker-year, rather than every fifth day yielding roughly 50.

Overlapping windows share days between adjacent observations, which inflates the measured correlation above the true independent-sample correlation. That bias is accepted deliberately, because it runs in the conservative direction: a higher `rho_bar` produces a smaller `n_eff`, a wider interval, and a higher bar for significance. Fifty non-overlapping observations per ticker-year is a thin base for a correlation estimate, and the noise in that estimate is not sign-controlled the way the overlap bias is. Between a bias whose direction is known and safe and a variance whose direction is not, take the bias.

**2. Weighted by co-fire frequency.** `rho_bar` is the mean over ticker pairs, weighted by the number of days the pair fired together, not an unweighted mean over every pair appearing together at least once.

The correction is a statement about the clustering actually present in the event population. A pair co-firing 200 times contributes 200 times as much clustering to the sample as a pair co-firing once, and an unweighted mean gives them equal say. The unweighted version also lets thin, noisy pair estimates dominate: two co-firings produce a correlation estimate with almost no information in it, and there are far more such pairs than dense ones.

Pairs never co-firing are excluded entirely. Their correlation says nothing about the dependence structure of the event set.

**3. Stored in a new table, not in `StatsParams`.** The four per-era values live in a `rho_era` table with `run_id` and `computed_at`.

These are measurements, not chosen parameters. Putting them in `StatsParams` would fold a measured quantity into `config_hash`, meaning a recomputation against more data would move the hash and appear to be a config change when no configuration was chosen differently. It would also strand the values without provenance, which ADR 034 requires for every generated row and which the `path` table's omission already cost real investigation time in session 10's reconciliation findings 3 and 7.

```sql
CREATE TABLE rho_era (
  era text NOT NULL,
  run_id text NOT NULL,
  config_hash text NOT NULL,
  PRIMARY KEY (era, config_hash),

  rho_empirical numeric NOT NULL,
  rho_factor_implied numeric,
  rho_gap numeric,

  n_pairs int NOT NULL,
  n_cofire_days int NOT NULL,
  mean_beta numeric,

  computed_at timestamptz NOT NULL,
  git_sha text NOT NULL
);
```

`config_hash` sits in the primary key for the same reason ADR 096 put it in `cell_stats`: an 18-config sweep produces 18 event populations with different co-firing structures, and one snapshot per era would make each Phase 4 run overwrite the last.

**4. A factor-implied second estimate, stored as a diagnostic only.** Alongside the empirical value, compute `rho_bar` a second way from a single-factor decomposition against the S&P 500 series already in `market_days`:

```
r_i = alpha_i + beta_i * r_m + eps_i

rho_ij = (beta_i * beta_j * sigma_m^2) / (sigma_i * sigma_j)
```

This costs 750 betas rather than roughly 280,000 pairwise correlations per era, and is stored in `rho_factor_implied` with the difference in `rho_gap`.

It is not the value feeding `n_eff`. The derivation assumes the residuals `eps_i` are mutually uncorrelated, and they are not: two semiconductor names co-firing share a sector move on top of the market move. The factor version therefore understates `rho_bar`, inflating `n_eff` and narrowing intervals, which is the opposite of the direction part 1 deliberately chose.

Its value is diagnostic and reportable:

| Observation | Reading |
|---|---|
| `rho_gap` near zero | Co-firing is largely a market effect. The signal fires when the index falls |
| `rho_gap` clearly positive | Sector or residual co-movement exists beyond the market factor. The signal clusters within industries |

Either result is a finding about the signal, and the check costs two columns.

**Consequences.**

Every `n_eff` in Phase 4 depends on a stored measurement rather than a constant, so `cell_stats` rows are only interpretable alongside the `rho_era` row sharing their `config_hash`. Phase 4's output contract should surface the `rho_bar` used, not only the resulting `n_eff`.

Recomputing `rho_bar` on more data changes every downstream interval without changing `config_hash`. That is intended, and it is why `run_id` and `computed_at` are mandatory: two `cell_stats` runs against the same config can legitimately disagree, and the `rho_era` provenance is what explains the difference.

The overlap bias is not quantified. It is known to be positive and known to be conservative, which is enough to justify the choice, but a future session wanting a tighter estimate should measure the two side by side rather than assume the gap is small.

The single-factor diagnostic uses the S&P series only. See ADR 099's consequences for QQQ.

**Implementation notes, added 2026-08-11 after Session 11's review.** Two things this ADR left implicit, resolved in the direction the ADR already argues for. Neither changes the DDL above.

`n_cofire_days` is the count of **days** on which two or more tickers fired together, not the sum over pairs of their co-fire counts. The first implementation stored the pair total, which at ~10 co-firing names a day reads roughly 47x the number of days it names — a day with `k` names produces `C(k, 2)` pairs. The column feeds no computation; it exists so a reader can size the evidence behind a stored `rho_bar`, and a number 47x too large defeats that.

`sigma_m` is measured per ticker, on that ticker's own joint sample with the market, and `rho_ij` is written as `(beta_i sigma_m_i)(beta_j sigma_m_j) / (sigma_i sigma_j)` — the product of the two systematic volatilities. Algebraically identical to the formula above whenever both tickers were observed on the same days, which is the case this ADR was written against. It differs when they were not, and on real `bars` they are not: names list and delist mid-era. The first implementation took `beta_i` and `sigma_i` from each ticker's own sample while taking `sigma_m` from the full index, so `rho_ij` mixed three samples. Restricting every ticker to the days where all of them are observed was the alternative and is worse, since one late-listing name would truncate the sample for every other.

The first measurement under this ADR, 2026-08-11, `config_hash = 1835688bf7d760ba`: `rho_gap` is positive in all four eras and widens monotonically from +0.014 (2010-2014) to +0.086 (2024+). Under the reading table above, that is sector co-movement beyond the market factor, growing. See `RESULTS.md` and ADR 099's deferred QQQ decision, which this is the evidence for.

**Alternatives rejected.**

Non-overlapping windows. Cleaner statistically, but roughly 50 observations per ticker-year, and the resulting estimation noise has no controlled direction.

Unweighted pairwise mean. Lets sparse, noisy pairs dominate a quantity meant to describe dense clustering.

All universe pairs regardless of co-firing. Measures market correlation rather than signal clustering, and would apply a correction unrelated to the dependence in the event set.

Factor-implied as the primary estimate. Cheaper and more stable, but understates `rho_bar` by omitting residual co-movement, in the unsafe direction.

Recompute fresh every Phase 4 run without storing. Never stale, but leaves no record of which value produced a published interval, and repeats the provenance mistake ADR 034 exists to prevent.

---

## 099. Market breadth is a reporting split, not a grid dimension

**Status.** Pinned, 2026-08-10.

**Context.**

`n_eff` corrects a statistical problem: how much independent information a clustered sample carries. It does not answer a separate and more consequential question, which is why the co-firing happened.

A `CONFLUENCE_LOW` firing on 3 of 60 trade-universe names is a claim about those three names. The same signal firing on 40 of 60 is a claim about the market. Both currently land in the same cell and are averaged together.

The distinction matters because of the benchmark structure in DESIGN §6.4. Buy-and-hold is the number to beat, and a signal firing on 4% of days with a 5-day hold sits in the market roughly 20% of the time, needing about 5x the annualized rate while deployed to match a name compounding at 30%. A signal whose edge lives entirely on broad-selloff days is not a stock-selection signal. It is a market-timing signal, and it fires precisely when buy-and-hold is also buying cheaply. Pooled reporting cannot distinguish the two, and a 4-point pooled edge looks identical either way.

CapitalScan is a detection engine and the humans decide. A pooled number conflating stock-specific dislocation with market timing gives them a worse basis for deciding.

**Decision.**

Breadth is defined as the fraction of the trade universe firing the same signal type on the same day. The numerator is `events.cofire_count`, already stored. The denominator is the trade universe size on that date, available from `universe`. No new ingestion.

Every headline cell is additionally reported by breadth tercile, in the same manner DESIGN §6.11 already reports every headline cell by era. Terciles are cut on the empirical breadth distribution per era, not on fixed thresholds, since firing rates differ across regimes.

Breadth does not become a headline grid dimension. ADR 011 caps the grid at twelve cells against roughly 2,000 effective events, and DESIGN §6.7's reasoning applies unchanged: a third dimension triples the cell count and divides the events, and clustering means the split is uneven, so most new cells would land under the `n_eff < 30` suppression floor and render nothing. Breadth joins bandwidth regime, trend, VIX percentile, and days-to-earnings as a continuous quantity reported and fed forward, rather than a cut.

The session 13 three-arm comparison additionally runs on the high-breadth subset alone. Comparing signal entry to buy-and-hold on exactly the days the signal goes market-wide is the direct test of the market-timing hypothesis. An inference from the cell-level breadth split is weaker than the arm comparison answering it head-on.

Two secondary breadth measures are stored but not used as the primary split. `events.spx_ret_1d` and `market_days.vix_pct_252d` are already present on every event. Co-fire fraction is primary because it measures whether this signal went market-wide, which is the question, rather than whether the market moved, which is a proxy for it.

**Consequences.**

Three possible outcomes, all reportable:

| Pattern | Reading |
|---|---|
| Edge concentrates at low breadth | The signal finds stock-specific dislocation. The most useful result |
| Edge flat across terciles | The signal works independently of market context |
| Edge concentrates at high breadth | The apparent edge is substantially market timing, and the three-arm comparison on the high-breadth subset is where it is confirmed or denied |

The third outcome is the one this ADR exists to make visible. It is also the one most likely to be missed, because a strong pooled result invites no further questions.

Breadth terciles multiply the reported rows per headline cell by three without multiplying the Benjamini-Hochberg test family. Per DESIGN §6.8, the family is all headline-grid cells across all ladder targets for one config. Breadth splits are descriptive reporting on an already-tested cell, not additional hypotheses, and treating them as such would over-correct and suppress the pooled results the family exists to test.

The pattern this system was built around has years of informal observed use. That use may have selected for one breadth regime without anyone stating it, and the split is what would reveal it. Any such finding is a description of historical outcomes under a regime, not a validated edge, and it does not change that all decisions remain with the humans.

On QQQ. QQQ is ingested through the Yahoo path as an ordinary ticker and is correctly excluded from the SEC shares pull via `SEC_NON_FILER_TICKERS`, since it files nothing. It is not in `market_days` as an index series, so ADR 098's factor diagnostic uses the S&P series alone.

Adding QQQ as a second factor is deferred, not rejected. The universe is mega-cap equities and skews toward Nasdaq names, so QQQ beta would plausibly explain more co-movement than S&P beta for a large slice of it. Against that, the two indices correlate above 0.9 on daily returns, so the second factor buys less than it appears to, and adding it costs a `market_days` migration, an ingest change, a backfill to 2009, and a second beta per ticker.

ADR 098's `rho_gap` is the evidence for deciding. A large gap means one factor is insufficient and a second is worth the cost. A small gap means it is not. Decide on the measurement rather than in advance.

**Alternatives rejected.**

Breadth as a fourth grid dimension. Triples cell count against a fixed event population, and clustering means most resulting cells fall under the suppression floor.

Breadth as a suppression filter, discarding high-breadth events. Throws away data and presupposes the answer. Whether high-breadth firings behave differently is the question, not the premise.

S&P 1-day return as the primary breadth measure. Measures the market, not the signal's own concentration. Kept as a secondary cut.

---

## 100. Three Phase 4 contract corrections

**Status.** Pinned, 2026-08-10.

**Context.**

Three small defects sit in the Phase 4 contract. Each is independently minor, and each would produce a wrong result or a wrong belief if carried into the statistics layer. Grouped into one ADR because they share a cause: ADR 096 changed the `cell_stats` key while the table was still empty, and the downstream consequences were not fully traced.

**Decision.**

**1. `v_screen` gains a `config_hash` scope, matching `v_events`.**

The view currently joins `cell_stats` on nine component columns with no `config_hash` predicate:

```sql
LEFT JOIN cell_stats c ON c.signal_type = e.signal_type
  AND c.side = e.side AND c.dd_bucket = e.dd_bucket
  AND c.signal_strength = e.signal_strength
  AND c.entry_kind = e.entry_kind
  AND c.split_key = 'validate' AND c.era IS NULL
  AND c.horizon_days = 5 AND c.target_pct = 0.03
```

That was correct while `cell_id` alone was the primary key and one statistics snapshot existed at a time. ADR 096 made the key `(cell_id, config_hash)` specifically so multiple configs coexist. The first Phase 4 run writing two configs duplicates every screener row, three configs triples it, and duplicate rows in a screener read as duplicate signals.

The fix pins the GUC, the same mechanism `v_events` already uses:

```sql
AND c.config_hash = current_setting('capitalscan.default_config_hash', true)
```

Joining `cell_stats` to each event's own `config_hash` was considered and rejected. It would give each event statistics from whichever generation produced it, so a screener showing events from several generations would silently mix statistical regimes. A screener should present one config's view of the world.

This lands in session 12, alongside the first write to `cell_stats`, rather than being deferred. The defect is dormant only until the table is populated, and session 12 is what populates it.

**2. DESIGN §6.9's amendment note is corrected.**

The note reads "`cell_key()` takes `config_hash` as an input." The live function takes nine parameters and `config_hash` is not among them, and migration `b2e5d81a4c76`'s docstring states the opposite: the two are separate columns in a composite key, not a concatenated string. DESIGN §8.2's own canonical definition of the function agrees with the migration.

The function and the migration agree with each other and with the built artifact. DESIGN §6.9 is the outlier and is corrected to read that `config_hash` is a separate primary key column, with `cell_id` unchanged and still derived from its component columns only.

Changing the function to match the doc was rejected: it would concatenate `config_hash` into `cell_id`, making the composite key redundant and the identifier less readable, to satisfy a sentence written after the fact.

**3. `exit_mix` is a config-dependent diagnostic, and says so.**

`cell_stats.exit_mix` holds the fraction of positions leaving by each exit reason, read from `events.exit_reason`, which the backtest writes using `ExitParams`. Moving the stop from 3% to 5% changes `exit_mix` while the signal is unchanged.

This is coherent under ADR 096's composite key, since each config gets its own row and comparing `exit_mix` across configs is a legitimate use. The risk is interpretive: a reader encountering `exit_mix` in a single-config context can mistake it for a property of the signal.

DESIGN §6.9 already describes `exit_mix` as the diagnostic separating "a few large winners carrying many small losses" from "consistently positive." That description gains an explicit sentence: `exit_mix` describes the signal under a specific exit policy, and comparing it across cells is only meaningful within one `config_hash`.

Defining `exit_mix` against a fixed reference exit policy was rejected. It would make the column comparable across configs at the cost of describing a policy nobody runs, and the composite key already provides the comparison.

**Consequences.**

Correction 1 requires a view migration in session 12 and a test asserting one row per event in `v_screen` when more than one config has `cell_stats` rows. A test asserting the view's shape under a single config would pass today and still pass after the defect returns.

Corrections 2 and 3 are documentation only. Neither changes behavior, and both prevent a wrong belief about behavior, which is the class of defect session 10's audit found most of.

The shared cause is worth naming. ADR 096 was a correct decision whose downstream consumers were not enumerated. Any future change to a primary key or a shared identifier should list every view, join, and document referencing it before the migration is written, not after.

---

## 101. Drawdown buckets reduced from four to two

**Status.** Pinned, 2026-08-11.

**Context.**

`StatsParams.dd_buckets` cuts drawdown at 10%, 20%, and 35%, giving four
buckets. DESIGN §6.7 crosses those four against signal type to build the
headline grid. Nothing had ever measured whether the resulting cells carry
enough effective sample to render.

The first measurement, against `config_hash = 1835688bf7d760ba` on the train
split with `rho_era` supplying `rho_bar`, covered 59 cells across three eras,
two splits, and both sides.

**Decision.**

The headline grid uses `0-10` and `10-20` only. Events in `20-35`, `35+`, and
the null bucket are still detected, still labelled, still stored, and still
available to the model layer. They do not appear as headline cells.

The suppression is recorded as a measured fact with its numbers, not as an
absence.

**Rationale.**

Across all 59 measured cells, `20-35` and `35+` cleared `min_n_eff = 30`
exactly zero times.

| Bucket | Best `n_eff` observed | Cells clearing 30 |
|---|---|---|
| 0-10 | 716.8 | most |
| 10-20 | 73.5 | most |
| 20-35 | 13.6 | 0 |
| 35+ | 1.7 | 0 |
| null | 7.8 | 0 |

Two mechanisms compound, and they push the same way.

Deep-drawdown events are scarce. A mega-cap down 35% from its 52-week high is
uncommon by construction, and the trade universe's health criteria make it
more so.

Deep-drawdown events are unusually clustered. `k_bar` in those buckets runs 21
to 122 against 8 to 42 in the shallow ones, because a 30% drawdown across many
names at once is a market event by definition. The clustering correction
therefore hits hardest exactly where the raw counts are already thinnest.

Merging `20-35` and `35+` into a single `20+` bucket was tested arithmetically
and does not rescue them. The best combined case, `stoch_oversold` in
2015-2019, holds 71 events at `k_bar` near 11.9 and yields `n_eff` of 14.4.
Pooling across eras does not rescue them either, since `k_bar` rises with the
pool.

**Consequences.**

`StatsParams.dd_buckets` is unchanged. The boundaries still assign
`events.dd_bucket` for every event, and the model layer still sees the full
four-way split as a continuous conditioning variable. Only the headline grid
narrows, which is a reporting decision rather than a detection one.

Phase 4 reporting must state the exclusion with its measurement rather than
silently omitting the rows. A reader seeing two buckets where DESIGN once
described four should find the reason in `RESULTS.md`, not infer a bug.

Should the trade universe widen or the drawdown boundaries move, this is
re-measurable in one query. The decision is contingent on the numbers, and the
numbers are recorded so a future session can check whether they still hold.

**Alternatives rejected.**

Merge `20-35` and `35+` into `20+`. Arithmetically insufficient, shown above.

Lower `min_n_eff` for deep buckets. A floor that moves per cell is not a floor.
The whole purpose of `min_n_eff` is that it is the same everywhere.

Report deep buckets with a wide interval instead of suppressing. An interval on
`n_eff` of 3 spans nearly the whole unit range and communicates nothing, while
still presenting a number that invites reading.

---

## 102. Headline grid is two sides, three signals, two buckets

**Status.** Pinned, 2026-08-11. Supersedes ADR 011's grid composition. **Amended 2026-08-13 by ADR 108: the grid is fourteen cells, not twelve** — see the dated note at the end of this entry.

**Context.**

DESIGN §6.7 specifies twelve headline cells: two signal types crossed with four
drawdown buckets on the long side, plus four more from a secondary cut on
`signal_strength` (1 versus 2) within `CONFLUENCE_LOW`. ADR 011 caps the grid
at twelve against an assumed population of roughly 2,000 effective events.

Three of those specifications turned out not to describe the system as built.

**The `signal_strength` cut cannot exist.** `core/signals.py:242` sets
`signal_strength = len(types)` where `types` is `signal_types_all`, the list of
concurrent signal types at that bar. A `confluence_low` hit carries
`[confluence_low, bb_lower_touch, stoch_oversold]`, so its strength is always
3. A `bb_lower_touch` or `stoch_oversold` hit carries one type, so its strength
is always 1. There is no 1-versus-2 split available, and four of the twelve
cells describe something the detector cannot produce.

**A third signal type is present and was never in the grid.** §6.7 names
`CONFLUENCE_LOW` and `BB_LOWER_TOUCH`. `stoch_oversold` fires 1,444 long
train-split events in the `0-10` bucket alone and produces two of the strongest
surviving cells. Its absence from §6.7 was an omission rather than an exclusion.

**The population is larger than assumed, and `n_eff` smaller.** ADR 011's
reasoning assumed roughly 2,000 effective events. The train split holds far
more raw events than that, but `k_bar` between 8 and 122 against `rho_bar`
between 0.36 and 0.47 divides `n_eff` by 5 to 50.

**Decision.**

The headline grid is `2 sides x 3 signal types x 2 drawdown buckets` = **12
cells**.

| Dimension | Levels |
|---|---|
| Side | `long`, `short` |
| Signal type | `bb_lower_touch` / `bb_upper_touch`, `stoch_oversold` / `stoch_overbought`, `confluence_low` / `confluence_high` |
| Drawdown bucket | `0-10`, `10-20` (ADR 101) |

Signal type pairs with side: a long cell reads `bb_lower_touch` and its short
counterpart reads `bb_upper_touch`. The grid is twelve cells, not thirty-six.

`signal_strength` is removed as a grid dimension. It remains stored on every
event and available to the model layer, where a constant-per-signal-type value
is harmless.

Cells are computed **pooled across eras** on the train split. See ADR 103.

**Rationale.**

Ten of the twelve render at `min_n_eff = 30`. Two suppress with a reason, which
is what suppression is for.

Long side, pooled across the three train eras:

| Signal | Bucket | n | `k_bar` | `rho` | `n_eff` |
|---|---|---|---|---|---|
| `bb_lower_touch` | 0-10 | 3,593 | 22.4 | 0.408 | 369 |
| `stoch_oversold` | 0-10 | 1,444 | 22.7 | 0.408 | 147 |
| `bb_lower_touch` | 10-20 | 775 | 23.9 | 0.417 | 74 |
| `confluence_low` | 0-10 | 725 | 30.3 | 0.408 | 56 |
| `stoch_oversold` | 10-20 | 900 | 42.1 | 0.409 | 51 |
| `confluence_low` | 10-20 | 620 | 36.6 | 0.406 | 40 |

Short side, pooled:

| Signal | Bucket | n | `k_bar` | `rho` | `n_eff` | |
|---|---|---|---|---|---|---|
| `bb_upper_touch` | 0-10 | 4,116 | 12.57 | 0.410 | 717 | renders |
| `stoch_overbought` | 0-10 | 5,266 | 38.37 | 0.411 | 322 | renders |
| `confluence_high` | 0-10 | 2,986 | 32.12 | 0.414 | 215 | renders |
| `bb_upper_touch` | 10-20 | 432 | 20.02 | 0.423 | 48 | renders |
| `stoch_overbought` | 10-20 | 371 | 60.53 | 0.418 | 14 | suppressed |
| `confluence_high` | 10-20 | 139 | 64.61 | 0.421 | 5 | suppressed |

`confluence_low` is the reason pooling matters. It suppresses in every bucket
on the validate split and in two of three train eras measured separately. Only
pooled does the signal this system was built around become measurable at all.

**Consequences.**

The Benjamini-Hochberg family is twelve cells across four ladder targets, 48
tests, replacing the twelve-times-four the old grid implied. Per DESIGN §6.8
the family is one config's headline cells across all targets, and that
definition is unchanged.

`cell_key()`'s nine-parameter signature is unchanged. `p_strength` is still an
argument and is still coalesced to `'all'` when null. Session 12 passes null
for it in every headline cell, which is the existing mechanism for a dimension
not being cut. No migration.

ADR 011's twelve-cell cap survives as a number by coincidence. Its reasoning
does not, and the reasoning is what mattered. ADR 011 is marked superseded in
part.

Null `dd_bucket` events exist, three on the long side and roughly 90 on the
short. `cell_key()` coalesces null to `'all'`, so they would silently merge
into an aggregate cell. Session 12 excludes null `dd_bucket` from the headline
grid explicitly and reports the excluded count.

**Alternatives rejected.**

Keep `signal_strength` as a dimension. It cannot be cut. Retaining it would
produce four cells that are either empty or duplicates of their parents.

Exclude `stoch_oversold` to preserve §6.7's two-signal shape. It contributes
two of the six strongest long cells and 5,266 short events. Excluding a signal
because a document predates it is the wrong direction of fit.

Long-only grid, shorts deferred to Session 14. Loses the asymmetry measurement
ADR 093 built short events to provide, and gains nothing: the short cells cost
one `arm` column and no additional computation.

---

**Amended 2026-08-13 (ADR 108): fourteen cells.**

`BEAR_CLOSE_ABOVE_UPPER` joins the short side, giving `3×2 + 4×2 = 14`. The Benjamini-Hochberg family in DESIGN §6.8 grows from 48 tests to 56.

Three things this entry asserted that no longer hold verbatim:

1. **The grid is not a clean cross product.** "Two sides × three families × two buckets" becomes three long families and four short ones. `LONG_SIGNALS` and `SHORT_SIGNALS` were positionally paired — index *i* of each was the same family from the opposite side — and they are not any more. `headline_grid` never zipped them, so the code already tolerated it, but the pairing was load-bearing documentation.

2. **The sides are deliberately asymmetric.** No bullish counterpart to the close-confirmed pattern was requested, and ADR 106 already rejects long/short symmetry as an assumption rather than a default. Inventing one to preserve the shape would add two cells measuring something nobody asked for.

3. **The confluence cells lose events to the new one.** ADR 057 emits one row per ticker-day carrying the most specific type, and ADR 108 ranks the reversal above confluence. On the train split, 409 bars fire the new signal and 286 (70%) also fire `confluence_high`; those 286 count in the new cell. `confluence_high` 0-10 goes 2,986 → ~2,710 and 10-20 goes 139 → ~131, and **neither changes its render/suppress verdict**. `signal_types_all` still carries both types on every affected row, so nothing is lost — only the cell assignment moves.

**Why the ranking is not reversed to protect continuity.** Ranking the new type *below* confluence would leave Session 12's cells byte-identical, at the cost of the new cell holding only the 123 bars where confluence did **not** fire. After clustering, that lands `n_eff` under the 30 floor, so the cell would suppress and measure nothing. Preserving a published number by making a new one unmeasurable is the wrong trade, and ADR 057's more-specific-wins rule already decides it.

**What this entry's core claim still holds.** ADR 102 removed `signal_strength` as a *cut* and capped the grid against roughly 2,000 effective events. Both stand: fourteen cells against that event budget is the same order of magnitude as twelve, and strength is still not a dimension.

---

## 103. Era is reporting only, never a tested family

**Status.** Pinned, 2026-08-11.

**Context.**

DESIGN §6.11 says every headline cell is additionally reported by era.
`StatsParams.era_bounds` cuts at 2014-12-31, 2019-12-31, and 2023-12-31.
`SplitParams` cuts train at 2021-12-31 and validate at 2023-12-31.

Two problems surfaced on the first measurement, and the second is the serious
one.

**Splitting by era destroys the grid.** Measured per era on the train split,
10 of 45 cells clear `min_n_eff`. Pooled, 10 of 12 clear. The 2010-2014 era is
the binding constraint: `k_bar` of 63.95 with `k_max` of 266 means a cell
holding 300 events carries roughly 13 effective. That is not a data defect. It
is the risk-on/risk-off regime, when cross-sectional equity correlation reached
historic highs and 266 names fired one signal on a single day.

**Era 2024+ is exactly the holdout split.** Both begin 2024-01-01 and run to
the present. Reporting a headline cell for era 2024+ is reporting on holdout.
DESIGN §6.11 and `test_holdout_firewall.py` therefore contradict each other,
and nobody noticed because no cell had ever been computed.

**Decision.**

Headline cells are computed pooled across eras. Era breakdowns are descriptive
reporting on cells already tested pooled, never a tested family of their own.
Era rows carry no `q_value` and enter no Benjamini-Hochberg family.

Era 2024+ is excluded from all Phase 4 reporting on the same terms as the
holdout split. Phase 4 reports eras 2010-2014, 2015-2019, and 2020-2023 only.

`rho_era`'s 2024+ row stays and is not a firewall breach. `rho_bar` is a
nuisance parameter measured from co-firing tickers' return correlations, never
from event outcomes, and `n_eff` requires it before any outcome is read.
Recording that explicitly because it is the first thing in the system to touch
holdout dates, and an implicit exemption is the kind that gets widened later.

**Rationale.**

This is the treatment ADR 099 already gave breadth, for the same reason: a
dimension worth seeing is not automatically a dimension worth testing.
Multiplying the test family by four while dividing the effective sample by more
than four is a strictly losing trade.

Descriptive era rows keep everything the breakdown was for. If an effect lives
only in 2010-2014, the era rows show it and a reader can judge. What they no
longer do is claim significance on 13 effective events.

**Consequences.**

`cell_stats.era` stays nullable and keeps its existing meaning: null for the
pooled row, populated for a descriptive row. `cell_key()`'s `p_era` parameter
already coalesces null to `'pooled'`. No schema change.

`v_screen`'s join already pins `c.era IS NULL`, so it selects the pooled row
and needs no change on this account. It does need the `config_hash` fix from
ADR 100.

A future session widening the train split past 2023 inherits this decision and
should re-measure rather than assume. If a later era's clustering falls enough,
era-level testing becomes affordable and this ADR should be revisited with
numbers.

**Alternatives rejected.**

Test eras and accept widespread suppression. Produces mostly empty cells and
loses the pooled result that does work.

Test eras with a lower floor. Same objection as ADR 101: a floor that varies is
not a floor.

Report era 2024+ as a headline cell. Breaks the holdout firewall, which exists
so the final evaluation means something.

---

## 104. Breadth denominator is the train universe

**Status.** Pinned, 2026-08-11. Amends ADR 099.

**Context.**

ADR 099 defines breadth as the fraction of the **trade** universe firing the
same signal type on the same day, with `events.cofire_count` as the numerator.

The two come from different populations. `research/backtest.py:741` computes
`cofire_count` as
`groupby(["signal_date", "signal_type"])["ticker"].transform("nunique")` over
the event population, which is the **train** universe of up to 750 names. The
trade universe holds roughly 62 names per quarter: 4,099 `in_trade` rows across
66 quarters from 2010-03-31 to 2026-06-30.

Two symptoms confirmed it. The ratio reached exactly 1.0000 in 2010-2014, the
boundary a genuine fraction should not brush. And 1,531 train-split events in
2010-2014 dropped out of the breadth query entirely because their quarter had
too few `in_trade` names to form a denominator.

**Decision.**

Breadth is the fraction of the **train** universe firing the same signal type
on the same day. The denominator is the count of distinct tickers with any
event in that quarter, matching the numerator's existing scope.

ADR 099's decision text is amended in place with a dated note. Everything else
in it stands: terciles cut on the empirical distribution per era, breadth
reported as a split rather than a grid dimension, and Session 13's three-arm
comparison run additionally on the high-breadth subset.

**Rationale.**

Two ways to fix a mismatched numerator and denominator, and only one is cheap.

Restricting the numerator to trade-universe names is correct by ADR 099's
original wording, but `cofire_count` is written once at backtest time and
invariant 5 forbids re-deriving it at query time. It would need a new column, a
migration, and a full backtest rerun measured at 2h48m.

Widening the denominator needs neither. It also measures the better thing. ADR
099's stated intent is whether the signal went market-wide, and 750 observable
names answer that more informatively than 62 tradeable ones. The trade universe
is a liquidity and health filter, not a measure of market breadth.

**Consequences.**

Breadth is now defined for every event from the first event onward, with no
dependence on `universe.as_of` coverage. The 1,531 events dropped by the old
definition return.

Breadth values shift downward across the board, since the denominator grew by
roughly an order of magnitude. The tercile boundaries in `RESULTS.md` measured
under the old definition are superseded and must be re-measured before Session
12 uses them.

ADR 099's reading table is unchanged in substance. Near-zero `rho_gap` still
reads as a market effect and a clearly positive gap still reads as
within-industry clustering. Those are statements about correlation, not about
the breadth denominator.

**Alternatives rejected.**

New `cofire_count_trade` column. Correct but costs a migration plus a 2h48m
rerun for a measure whose purpose the cheaper definition serves better.

Clip breadth at 1.0. Hides the mismatch rather than fixing it, and a value
pinned at exactly 1.0 would be indistinguishable from a genuine full-universe
firing.

`cofire_count` over a fixed constant. Removes the universe-size drift the
fraction exists to normalize away.

---

## 105. `cell_stats.arm` separates measured from recommended

**Status.** Pinned, 2026-08-11.

**Context.**

Phase 4 measures populations it will never recommend. Session 13's benchmark
arms include a random-entry null across 200 replications, a naive always-short
control, and DCA variants, none of which are signals. ADR 102 adds six short
headline cells, and Session 13 adds trim-and-redeploy per ADR 017.

`cell_stats` currently has no way to say which is which. A row holding
`p_hit = 0.61` looks identical whether it describes a signal or a control.

**Decision.**

`cell_stats` gains `arm text NOT NULL DEFAULT 'signal'`, with permitted values
`signal`, `control`, and `benchmark`. `benchmarks` gains nothing, since its
`arm` column already exists.

`v_screen` selects `arm = 'signal'` only. Nothing labelled `control` or
`benchmark` reaches the screener, a notification, or any surface a person would
read as advice.

**Rationale.**

The distinction is a property of the row and belongs on the row. Encoding it in
`cell_id`'s string, or inferring it from `split_key`, or maintaining it in a
consumer's WHERE clause are all ways of storing the same fact somewhere it can
drift from the data it describes.

`NOT NULL DEFAULT 'signal'` means an existing writer that never heard of this
column produces correct rows. A nullable column would let an unlabelled row
reach a consumer that filters on `arm = 'signal'` and silently vanish, or reach
one that does not filter and silently appear.

**Consequences.**

Session 12 writes `arm = 'signal'` for all twelve headline cells, including the
short ones. Per ADR 106 the short side is a signal population, not a control.

Session 13's benchmark arms write `arm = 'benchmark'`. ADR 017's naive-short
control, if computed, writes `arm = 'control'`.

`v_screen` needs the `arm` predicate in the same migration as ADR 100's
`config_hash` predicate. Both are one-line additions to one view, and doing
them together avoids two view rebuilds.

A test must assert `v_screen` returns nothing for a `control` or `benchmark`
row. A test asserting only that `signal` rows appear would pass on a view with
no predicate at all.

---

## 106. Short entries use the same universe and criteria as long

**Status.** Pinned, 2026-08-11. Supersedes ADR 016.

**Context.**

ADR 016 required short entries to satisfy at least one of: close below the
200-day SMA, negative 200-day slope, or drawdown past 20%. It called naive
always-short a control.

That filter was never implemented. Nothing in `core/signals.py` or the event
pipeline applies it, and the only code referencing ADR 016 is `ExitParams`
keeping long and short exit thresholds independent, which is unrelated. The
database holds 4,116 unfiltered `bb_upper_touch` events in the `0-10` bucket
on the train split alone.

The first measurement showed the filter would have been self-defeating.

| Cell | n | Passing ADR 016 | % |
|---|---|---|---|
| `bb_upper_touch` 0-10 | 4,116 | 43 | 1.0% |
| `confluence_high` 0-10 | 2,986 | 19 | 0.6% |
| `stoch_overbought` 0-10 | 5,266 | 104 | 2.0% |
| `bb_upper_touch` 10-20 | 432 | 111 | 25.7% |
| all `20-35` and `35+` | 3 to 69 | all | 100% |

Overbought means near highs means healthy, and healthy fails the filter. Every
short cell clearing `min_n_eff` is one ADR 016 forbade. Every short cell ADR
016 permitted is suppressed. Filtering `bb_upper_touch` 0-10 to its 43
permitted events drops `n_eff` to roughly 7.5.

The user's intent from the outset was to run shorts and puts on the same names
as longs and calls, driven by the same signals on the same universe. ADR 016
recorded a more cautious position than the one actually held.

**Decision.**

Short entries use the same universe and the same selection criteria as long
entries. No regime filter. The universe criteria in ADR 014 and
`UniverseParams` are the entire entry gate for both sides.

Both sides are measured as `arm = 'signal'` under ADR 105.

ADR 016's original text stays in the file, marked superseded 2026-08-11, for
the reason in the consequences below.

**Rationale.**

Three arguments were made in ADR 016 and they are not equally strong.

*Drift against the position*, roughly a point of hit rate on a name compounding
at 25%. Real, small, and directly measurable in Session 12's long-versus-short
comparison. It is a magnitude to quantify, not a reason to exclude a
population.

*Loss geometry*, that a losing short grows as a share of capital while a losing
long shrinks. Real, and a position sizing concern. CapitalScan has no execution
path and sizes nothing. This argues for how a person sizes a short, not for
whether the system may measure one.

*Band walking* is the serious one, and it survives. During strong advances price
rides the upper band for weeks while %K stays pinned above 80, printing touches
nearly daily, so the upper signal fires most often when a short bleeds worst.
Fear-driven selloffs resolve in days while greed-driven advances grind for
months, and there is no lower-band equivalent.

Band walking is a testable claim, not a policy. Session 12 measures exactly the
quantities that settle it: `p_hit`, `mae`, and `exit_mix` on the six short
cells. If it holds, those cells show low `p_hit`, high `mae`, and an `exit_mix`
dominated by `timeout` and `stop`. Superseding ADR 016 does not dismiss the
hypothesis. It replaces a filter derived from the hypothesis with a measurement
of it.

The signal counts already hint at the mechanism: `bb_upper_touch` fires 4,116
times in `0-10` against `bb_lower_touch`'s 3,593. The upper band does fire more
often, in healthier names, as ADR 016 predicted.

**Consequences.**

No code change and no backtest rerun. The filter was never built.

ADR 016's text is retained deliberately. It made a falsifiable prediction
before the measurement existed, and a prediction on record from before the
result is worth more than one reconstructed after. If Session 12's short cells
show the band-walking signature, ADR 016 was right on the mechanism and wrong
only about what to do with it, and that distinction is only visible if the
original text survives.

ADR 017 stands unchanged. Trim-and-redeploy remains the primary use of the
upper-band signal and remains a Session 13 arm. Its expected ranking placed
trim-and-redeploy above regime-filtered short above naive short; the middle
term no longer exists, and the comparison becomes trim-and-redeploy against
unfiltered short. ADR 017 gains a one-line note to that effect.

Session 12's long-versus-short asymmetry becomes a headline result rather than
a Session 14 afterthought. ADR 016's own reasoning gives it three specific
predictions to check against, which is a better test than an open-ended
comparison.

Nothing here recommends a short to anyone. Every decision remains with the
people reading the output, and the measurement exists so those decisions rest
on observed outcomes rather than on a filter nobody validated.

**Alternatives rejected.**

Keep ADR 016 and label short cells `control`. Preserves a filter that was never
implemented, never validated, and that the measurement shows would suppress
every cell it permits.

Make the filter provisional pending Session 12. A provisional filter still has
to be implemented to be provisional about, and implementing a filter in order
to measure whether to remove it is work in the wrong direction.

Drop ADR 016 from the file. Loses the band-walking prediction at exactly the
moment it becomes checkable.

---

## 107. `v_screen` selects the cell pooled over `signal_strength`

**Status.** Pinned, 2026-08-13. Migration `f1a8d3b62c07`.

**Context.**

`v_screen` was built by `6d86bf1f668e` under ADR 011's grid, where
`signal_strength` was a real dimension — the 1-versus-2 cut inside
`CONFLUENCE_LOW`. Its join therefore carried `c.signal_strength =
e.signal_strength`.

ADR 102 established that cut cannot exist. `core/signals.py:242` sets
`signal_strength = len(signal_types_all)`, and `confluence_*` fires exactly
when both primitives fire, so the value goes 1 to 3 with no 2 reachable.
Session 12 accordingly writes `signal_strength = NULL` on every `cell_stats`
row, meaning pooled over strength.

`NULL = 1` is never true. **No Session 12 statistic could reach the
screener.** Measured 2026-08-11: `v_screen` returned 683,653 rows with zero
non-null `cell_id`. The view worked, returned rows, raised nothing, and
showed no numbers.

Confirmed against all 626,791 events under `1835688bf7d760ba`: strength is a
pure function of `signal_type`, six combinations, no exceptions.

| `signal_types_all` | `signal_strength` |
|---|---|
| `bb_lower_touch` / `bb_upper_touch` / `stoch_oversold` / `stoch_overbought` | 1 |
| `confluence_low + bb_lower_touch + stoch_oversold` | 3 |
| `confluence_high + bb_upper_touch + stoch_overbought` | 3 |

**Decision.**

`v_screen` pins `c.signal_strength IS NULL`, the exact parallel of the
`c.era IS NULL` line two rows below it. Both say the same thing: select the
pooled row, not a split one. `era` pins it for time, `signal_strength` for
strength.

ADR 102 is **not** amended. It removed strength as a *cut* — a dimension
splitting one cell into several — and that claim holds. Reinstating strength
as a dimension would not produce one additional cell, because every cell's
events share a single strength value. This entry fixes the view that was
never updated to match, which is a different thing.

**Rationale.**

*Why not drop the condition entirely.* `cell_id` embeds the strength slot,
so a strength-split row and a pooled row are two distinct rows describing
one cell. A view with no strength condition matches both and duplicates
every screener row — the ADR 100 fan-out arriving through a different
column. `IS NULL` keeps the guard.

*Why not populate the column instead.* Writing `3 if confluence else 1`
satisfies the old equality join with no migration, and the claim would be
true today. It bakes today's arithmetic into the writer. More trigger
families are planned beyond Bollinger and stochastic, and a fourth primitive
outside the confluence definition makes strength 2 reachable: a
`bb_lower_touch` event would carry 1 or 2 depending on whether the new
trigger also fired. A cell then holds mixed strengths and the derived value
stamps it with one of them, silently. `IS NULL` describes how the cell was
constructed rather than what it happens to contain, so it stays true at any
number of triggers.

*What this does not fix.* Session 12's rows remain invisible to `v_screen`
because the view pins `c.split_key = 'validate'` and Session 12 measured
train. That is invariant 5b working as designed, not a defect. With the
strength predicate corrected, the join matches 35,281 events under a train
split, measured — so the remaining zero is the split filter alone.

**Alternatives rejected.**

Amend ADR 102 to reinstate `signal_strength` as a grid dimension. Records a
1-versus-2 split the detector cannot produce, and yields the same twelve
cells.

Populate `cell_stats.signal_strength` with the derived value. Covered above:
correct today, silently wrong at the first new trigger family.

Write both a pooled and a strength-split row per cell. Stores one
measurement under two identities, doubles the Benjamini-Hochberg family from
48 tests to 96 for no gain, and leaves the fan-out hazard live.

---

## 108. Close-confirmed detection, alongside intraday touch

**Status.** Pinned, 2026-08-13. Amends ADR 005's scope and ADR 057's specificity ranking.

**Context.**

Every signal in the system is an *intraday touch*: `detect(bar_t, ind_{t-1})` compares bar `t`'s `low` and `high` against bar `t-1`'s bands. ADR 005 pins that deliberately, and `tests/unit/test_signature_guarantee.py` enforces it structurally — `detect` may read only `low`, `high`, `ts`, and `ticker` from the bar. CLAUDE.md calls that probe "the real guarantee" and says never to widen it.

A new signal was requested (user's decision, 2026-08-13): **a down bar closing at or above the upper band**, `open > close AND close >= bb_upper`. It is a rejection pattern — the price pushed through the band and gave it back by the close — and it cannot be expressed as an intraday touch, because it is a statement about where the bar *finished* relative to where it *opened*.

Two implementations were tried and the first was rejected.

**The rejected version: route it through the indicator row.** Compute the boolean in `core/indicators.py`, and let `detect` read it off the `ind` row it already receives. This keeps `PERMITTED_ON_BAR` untouched and needs no ADR. It was built, tested, and committed (`6be3cae`) before the flaw was traced.

Indicator rows are read at **t-1**. So a pattern completing at the close of day D produces an event dated **D+1**, and a `next_open` entry filling **D+2**. Every existing signal is dated D and fills D+1. The signal would have been measured as a strategy one full session slower than the one a person would actually run, for no reason other than where the condition was evaluated.

**Decision.**

The system carries **two detection models**, named and separated:

| Model | Condition reads | Fires | ADR |
|---|---|---|---|
| Intraday touch | bar `t`'s `low`/`high` vs `ind_{t-1}` bands | during session D | 005 |
| Close-confirmed | bar `t`'s close-derived boolean vs `ind_{t-1}` bands | at close of D | 108 |

Both are dated **D** and fill `next_open` at **D+1**. A close-confirmed signal is not surfaced by the poller until the session closes, which is a real difference in *when a person learns of it*, not in how it is measured.

`PERMITTED_ON_BAR` gains exactly one field: `bear_close_above_upper`, the precomputed boolean. **Raw `open` and `close` remain forbidden on the bar.** This is the load-bearing part of the decision. The probe's purpose is not to keep `detect` ignorant of the close for its own sake — it is to make an intraday condition written against the close *impossible to express*. A precomputed, close-confirmed boolean cannot be misused that way: it carries its own causality in its name and in its dtype. Handing `detect` raw `close` would restore the hazard in full.

The pattern itself is still computed in `core/indicators.py`, against `bb_upper[t-1]`. Today's band is a 20-day mean and standard deviation *ending at today's close*, so testing today's close against today's band is circular — the close helps set the level it is measured against. The shift is what makes the flag a statement about a level fixed before the bar opened. Invariant 3 is untouched.

**`BEAR_CLOSE_ABOVE_UPPER` ranks first on the short side**, above `CONFLUENCE_HIGH`: it requires a band breach *and* a close-confirmed rejection of it, which is strictly more specific than a band touch plus a stochastic extreme. ADR 057's ranking becomes `BEAR_CLOSE_ABOVE_UPPER > CONFLUENCE_HIGH > BB_UPPER_TOUCH > STOCH_OVERBOUGHT`. The long side is unchanged and deliberately **not** mirrored — ADR 016's symmetry assumption is already rejected by ADR 106, and no bullish counterpart was requested.

**Entry kinds: `next_open` only.** `touch`, `touch_5m`, and `touch_30m` fill at an intraday band level, and this signal has no such level — the pattern is not confirmed until the close, so there is nothing to fill against during the session. This costs nothing where it matters: ADR 102's measured population is already `entry_kind = 'next_open'` (`GRID_ENTRY_KIND`), which is the filter Sessions 12 and 13 both read, so the signal lands directly in the population the statistics measure. A `close` entry kind filling at D's close is a coherent future addition and is deliberately not built now, since no current analysis reads one.

**Rationale.**

The alternative to a second detection model is refusing the signal, and the signal is well-motivated: ADR 017 makes the upper-band signal drive *trimming* a held position, and a close-confirmed rejection is a stronger trim trigger than a bare touch, precisely because the touch was given back.

The alternative to widening the probe by one field is the D+2 delay, which measures a strategy nobody would run. Between "the probe names one exception" and "every statistic describes a slower strategy than the real one," the exception is plainly the smaller cost.

**Consequences.**

- **A new `config_hash`.** `signal_strength` counts concurrent types, so every event on a day this fires shifts. Sessions 12 and 13's measurements under `1835688bf7d760ba` remain valid measurements *of that config* — ADR 096's composite key means a second config adds rows rather than replacing them — but the new config needs its own run of `rho`, `cell_stats`, and `benchmarks` to be described.
- **A full backtest**, roughly 2h48m including the harness.
- **`max_warmup()` moves 272 to 273**, which widens the indicators job's read window. Load-bearing rather than cosmetic: an unchanged 272 leaves the first flagged bar of every window null.
- **`indicators` gains a boolean column**, and `events` inherits the new `signal_type` value. One migration.
- **A structural guarantee worth recording.** `bars_check1` enforces `close <= high`, so `close >= bb_upper[t-1]` implies `high >= bb_upper[t-1]`. Every bar this flags necessarily also fires `BB_UPPER_TOUCH`, so the new type refines an existing population rather than creating a disjoint one. That is what makes its cell directly comparable to the `bb_upper_touch` cell beside it.
- **The signature probe must keep failing on raw `open`/`close`.** A test asserting only that the new field is permitted would pass on a probe with no restrictions at all, so the negative assertions are the ones that carry the guarantee.

**Amends ADR 005.** That ADR's decision text describes the band-touch family and stands unchanged for it. It is no longer a claim about every signal in the system.

---

## 109. The close-confirmed band is the same day's, not the prior day's

**Status.** Pinned, 2026-08-14. Amends ADR 108.

**Context.**

ADR 108 defines `BEAR_CLOSE_ABOVE_UPPER` as `open > close AND close >= bb_upper[t-1]`, and defends the shift on the grounds that today's band is a 20-day mean and standard deviation *ending at today's close*, so testing one against the other is circular.

The user found the defect by checking a signal against a chart. STT fired on 2026-08-13 with a close of 189.85. The prior band stood at 189.758 and the same-day band at 190.451: the close cleared the older level by nine cents and missed the current one by sixty. On the chart the signal exists to describe, that bar never closed above the band.

**The scale of it, measured over 2010-2026 before deciding.**

| Band | Fires |
|---|---|
| `bb_upper[t-1]` (ADR 108) | 44,114 |
| `bb_upper[t]` (this ADR) | 20,146 |
| Fire on the shifted band but **not** the same-day band | **24,104 (55%)** |
| Fire on the same-day band but not the shifted one | 136 |

Bands rise in an uptrend, so `bb_upper[t-1]` sits below `bb_upper[t]` and presents a systematically lower bar. More than half of the signal's history was firing on a level the chart had already moved past.

**Decision.**

The comparison is `close >= bb_upper[t]`. The `.shift(1)` is removed, and `bear_close_above_upper`'s warmup returns from 273 to 272 — the extra bar existed only to feed the shift.

**Rationale.**

*Invariant 3 was over-applied.* It exists to stop the close deciding an **intraday** event: bar t's bands embed bar t's close, so comparing an intraday price against them uses information the moment did not have. Here the event **is** the close. At the closing bell, `bb_upper[t]` is fully computable from information that already exists, so reading it consumes nothing from the future. The resulting position still enters at t+1's open.

*The circularity is conservative, not permissive.* A high close raises the band it is measured against, making the condition **harder** to satisfy — which is the whole reason the shifted version fired twice as often. Circularity that biases against firing is not the hazard invariant 3 guards.

*It is also the universal convention.* Every charting platform computes the band including the current bar. "Closed above the upper band" means today's close against today's band, everywhere. ADR 108's version was the non-standard one, and a detection signal that disagrees with the chart it describes has failed at its only job.

**A property worth recording, because it explains the shape of the change.**

With `bb_window=5`, `bb_std=2`, `ddof=0`, four flat values and one outlier `d`, the algebra collapses exactly:

```
mean  = c + d/5
std   = 0.4d
upper = c + d/5 + 0.8d = c + d = the close
```

A lone spike lands **precisely on** its own band for any spike size — `d` cancels. So it fires only because `_breach` is "at or beyond"; a strict `>` would never fire on that shape. What actually prevents a fire is a *second* elevated bar, which widens the standard deviation past the close. Both are asserted in `test_indicators.py`.

**Consequences.**

- **A new `config_hash`: `541f84a384b07ba2`.** The signal's population changes by more than half, so every event, cell, and arm measured under `697f3ae71428d392` describes the superseded rule. That hash is retained and stays reproducible (ADR 096's composite key); the new one gets its own run.
- **`IndicatorParams.bear_close_band_lag`, defaulting to 0**, is what actually moves the hash. This decision changed a *formula* in `core/indicators.py`, and `jobs.config.config_hash` hashes `dataclasses.asdict(Config)` — a formula is not a `Config` field, so the hash did **not** move on its own. A backtest would have written new-rule measurements over `697f3ae71428d392` in place, silently, and every Session 14 number would have stopped reproducing. This is the third instance of the same trap, after `stoch_source` and ADR 108's `enabled_signal_types`; the pattern is that any decision reachable without touching a `Config` field needs a field invented for it.
- **`bear_close_band_lag=1` reconstructs ADR 108's population** (`3f9b74da68e4573e`), which is what makes the superseded rule reproducible from a config rather than only from a database snapshot. `indicators` carries no `config_hash` column and stores one generation, so without the field the old values would exist nowhere once the table is recomputed. DESIGN §3.10 asks for an ablated criterion to be a config change rather than a code change.
- **A full backtest**, roughly five hours including the harness.
- **`max_warmup()` returns to 272**, which narrows the indicators job's read window back to its pre-ADR-108 value.
- **The subset guarantee gets stronger.** `bars_check1` enforces `close <= high`, so `close >= bb_upper[t]` implies `high >= bb_upper[t]` — now a statement about the same day's band on both sides rather than across two days.
- **ADR 108 is not withdrawn.** Its architecture stands entirely: the close-confirmed model, the precomputed boolean crossing to `detect`, the allowlist, the D-to-D+1 dating, and the specificity ranking. Only the band's date changes.

**What this says about the original reasoning.**

ADR 108 reached the wrong answer by applying a correct invariant outside its scope, and the error survived a passing test suite, a clean five-check harness, and a full session gate — because every test asserted the shifted behaviour it was written alongside. It was caught by one person comparing one signal against a chart. That is worth recording: the project's tests verify internal consistency, and internal consistency cannot detect a specification that disagrees with the world.

---

## 110. The raw %K is the trigger; the smoothed %K must agree within 5 points

**Date:** 2026-08-16. **Status:** accepted. **Supersedes** the 2026-08-05 note on `SignalParams.stoch_source` that called it "an A/B test, not a permanent swap."

**Decision.** `stoch_source = "k_fast"` and `require_fast_agreement = True`. Oversold and overbought are decided by the raw, unsmoothed %K, and a confluence additionally requires the `stoch_smooth_k`-smoothed %K to sit within `fast_agreement_tol` (5.0) of it.

**No code changed.** Both are `SignalParams` fields that already existed and were already honoured by `core/signals.py`. `_fast_agrees` compares `abs(k_fast - k_full)`, which is symmetric, so "the smoothed value within 5 of the raw one" and "the raw within 5 of the smoothed" are the same predicate — the requested rule needed no new comparison.

**Scope.** The agreement check is attached to the confluence definitions only (DESIGN §3.6). Bare `stoch_oversold` and `stoch_overbought` never consult it, so this narrows `confluence_low` and `confluence_high` while leaving the single-condition types untouched.

**Consequences.**

- **A new `config_hash`: `86e91448a65aa40b`**, superseding `1b97abf7e458d537`. Both fields are hashed `Config` fields, so the identity moves without any special handling — unlike ADR 108 and ADR 109, which each needed a field invented for them.
- **A full chain re-run**: backtest, then `path backfill`, `path peak-labels`, and the four `stats` commands. Roughly eight hours.
- **`k_full` stays one env var away.** `CAPSCAN_SIGNALS='{"stoch_source":"k_full","require_fast_agreement":false}'` reconstructs the pre-flip population, so it remains derivable from a config rather than only from a database snapshot.
- **On the sample measured** (2026-08-03 to 2026-08-14, universe members, bear-reversal rows): 13 rows under the old rule, 15 under `k_fast` alone, **7** with the agreement check added. The agreement check, not the column swap, does the narrowing.
- **`SignalHit.k_full` is now a reported value that is not the deciding one.** `core/signals.py` records it unconditionally, so a scan CSV's "Full Stochastic Value" no longer names the number that fired the signal. Both columns are stored and both are exported, so nothing is lost; the column labelling is what needs to say which is live.

**The rows the agreement check removes are not noise.** `k_full` is `k_fast` smoothed over `stoch_smooth_k`, so a wide gap means %K is accelerating. On the measured sample the dropped rows were APH (gap 9.5), AVGO (10.5), DAL (10.3), HPE (9.3), and UAL (8.0) — the sharpest momentum in the set. This decision deliberately trades those away for agreement between the two series. That is a preference about signal character, not a correctness fix, and it should be revisited against measured outcomes once this hash has a backtest.

**What this cost, and why it is recorded.** The decision was made on 2026-08-15 and took a day to land, because every test in `test_signals.py` set its extreme on `k_full` and read `SignalParams()` for the source. The file depended on `k_full` being the *default* rather than on `k_full` being the column under test, so flipping the default broke 21 tests that were about confluence rules, specificity ranking, and debounce keys — none of them about defaults.

The fix was to make those tests *follow* `stoch_source` rather than pin it, which then surfaced two further fixture defects that a conditional skip would have hidden permanently:

- The ADR 044 agreement tests wrote `k_full=15, k_fast=90`, pinning the roles. Under a `k_fast` source the 90 became the trigger, firing an overbought short beside the long band touch and returning two hits where the test unpacked one.
- `test_backtest_candidates` never spelled `k_fast` at all, so the trigger read a missing column and one test dropped from `confluence_low` to `bb_lower_touch` — which reads as a t−1 pairing bug and is not one.

The general lesson is the same one ADR 109 recorded from the other direction: a test that inherits a default is testing the default, whatever its name says. Both fixtures named a %K *column* where they meant a %K *value*.

---

## 111. `--actionable`: a long needs confluence, a short needs confluence and confirmation

**Date:** 2026-08-17. **Status:** accepted. Serving-layer only — no change to what is detected or stored.

**Decision.** `cscan scan --actionable` selects

```
confluence_low  OR  (confluence_high AND bear_close_above_upper)
```

A long qualifies on confluence alone. A short qualifies only when a close-confirmed reversal agrees with it.

**Why asymmetric.** This is ADR 016's position applied to serving rather than to exits. A bounce off the lower band is a single observable event: the price went down and turned. A push through the upper band is not the mirror image — it is ambiguous until the session ends, because a stock can ride the upper band for days in a genuine uptrend. `bear_close_above_upper` is exactly the disambiguator: the day opened above where it closed while still holding the band, which is the giveback that separates exhaustion from continuation.

So the two sides are asked different questions because they *are* different questions, not because the short side is arbitrarily held to a higher bar.

**Why a new flag rather than composing the existing two.** `--confluence-only` and `--bear-close-only` compose as an AND. No combination of them expresses an OR of two conditions, and the closest attempt (`--confluence-only --bear-close-only`) yields bear ∩ `confluence_high`, which drops every long.

**`--actionable` refuses to compose with either.** Intersecting an OR-of-two with an AND-of-two-others produces a set that cannot be stated in one sentence, and a filter whose meaning cannot be stated is one that gets misread. Passing them together exits 2 rather than silently returning something surprising.

**What it excludes, and the honest cost.** On 2026-08-03..2026-08-14 it selected 37 rows — 30 longs and 7 shorts — while excluding **73 `confluence_high` rows with no reversal to confirm them**. Those 73 are not noise; they are real confluence signals that this view deliberately withholds. Anyone reading `--actionable` as "every signal worth knowing about" will miss them. `--confluence-only` remains the way to see the full picture.

It also excludes bare `bear_close_above_upper` rows with no stochastic extreme, which `--bear-close-only` still surfaces. The flag is the "act on it" view, not the "it fired" view.

**What it leans on.** `core.signals.detect` builds `fired` as a dict keyed by `Side` and emits one hit per side, so `signal_types_all` never mixes long and short types. That is why the short clause needs no explicit side check. Measured across 2,541 `bear_close_above_upper` rows in the live population: zero carry `confluence_low`, and they span exactly one `side`. The property was structural all along and nothing asserted it; `test_scan_actionable.py` now does.

**Not a recommendation.** Per CLAUDE.md's sourcing rule, this flag narrows a view. It does not assert that the rows it keeps are good trades, and no claim about suitability attaches to it.

---

## 112. ADR 033's first kill criterion fired

**Date:** 2026-08-17. **Status:** Pinned.

**Context.**

ADR 033 fixed three kill criteria before any code existed, explicitly "before sunk cost exists." The first reads:

> No cell beats its per-ticker-year baseline by the power-adjusted threshold at sufficient `n_eff` after FDR correction: the two-indicator hypothesis is dead as a standalone claim.

Phase 4 closed 2026-08-15 with all five `TESTS.md` §10 gate criteria passing. The gate asserts the machinery is complete and correct, not that the strategy works, and Session 14's own `BUILD.md` entry says so.

Three configurations have now been measured end to end, each with a different signal definition.

| Config | Session | Tests | Min q | Survive FDR |
|---|---|---|---|---|
| `1835688bf7d760ba` | 12, 13 | 48 | 0.769 | 0 |
| `697f3ae71428d392` | 14 | 56 | 0.790 | 0 |
| `86e91448a65aa40b` | ADR 110 | 224 train / 224 validate | 0.849 / 0.706 | 0 / 0 |

Session 13 declined to fire the criterion on its own evidence, correctly: the criterion is worded about cells, and Session 13 measured arms. The ADR 110 run is the first to satisfy every clause of the criterion simultaneously.

| Clause | Train | Validate |
|---|---|---|
| Cells enumerated | 224 | 224 |
| At sufficient `n_eff` (unsuppressed) | 124 | 56 |
| Mean `n_eff` | 93.3 | 20.8 |
| Minimum q-value | 0.8492 | 0.7061 |
| Surviving FDR at α 0.05 | **0** | **0** |

**Decision.**

Kill criterion 1 has fired. The two-indicator hypothesis — Bollinger band touch plus stochastic extreme, alone or in confluence — is retired as a standalone returns predictor.

The measurement is recorded in `RESULTS.md` under "Kill criteria status" with the same detail a positive result would receive, per ADR 033's own instruction.

Kill criterion 2 is recorded **not applicable** rather than pending. Both splits were measured, so it is evaluable rather than unevaluated; it cannot fire because it presupposes a positive training edge to be half of, and the train signal arm sits 298 points behind buy-and-hold and below its own null's median.

Kill criterion 3 remains **unevaluated**, and holdout stays sealed pending a direction decision. See consequences.

**Rationale.**

Three independent lines of evidence point the same way on the same run.

*Cells.* 124 unsuppressed train cells at a mean `n_eff` of 93.3 is "sufficient `n_eff`" by any reading. The minimum q-value misses α by a factor of seventeen. This is not a near miss, and it is not a power problem at the sample sizes reached.

*Arms.* Train signal +85.75% against buy-and-hold's +383.66%, below the null's median of +102.50%. Validate's signal arm clears its null's 97.5th percentile for the first time, and is not read as an edge: it performs better out-of-sample than in-sample, which is backwards from a real effect, on a sample that just halved. When an aggregate looks better than every one of its components, the aggregate is the artifact — the arm is one number against 200 replications, the grid corrects across 224 tests.

*Costs.* Session 13 measured short-term tax removing the strategy's entire pre-tax return. Session 14 found every drawdown-slice interval crossing zero, which retires ADR 015's central claim at this sample.

Three different signal definitions produced this. ADR 110's flip to `k_fast` with agreement gating cut `confluence_low` from 19,701 events to 10,346 — a materially different population — and the answer did not move.

**The result is a measurement, not an artifact.** `stats self-validate` passes on the same run: the null test finds 0.42% of cells at q < 0.05 against a 5% threshold, the recovery test matches the analytical baseline within 0.039 pp against a 1.0 pp tolerance, and a deliberately broken variant computing standard errors on raw `n` instead of `n_eff` is caught at 11.67%. A pipeline that detects a planted bug of exactly the kind that manufactures false significance, and still reports zero, is reporting a fact about the data. The backtest harness passes 5/5 on every run.

**Consequences.**

*Retired.* The two-indicator hypothesis as a standalone returns predictor. ADR 015's drawdown-slice claim, at this sample. Any Phase 5 or Phase 6 work premised on a measured edge.

*Not retired, and the distinction matters.*

**Detection is not prediction.** The detector fires correctly and deterministically with verified lookahead handling across 630,592 events. "This event does not predict a 5-day return better than the ticker's own base rate" is a different claim from "this event is not worth seeing." Attention-directing use — surfacing events for a human to judge — is **untested rather than disproven**, and it stays untested, because nothing measured so far bears on it. Any such use is human judgment on an event feed, not a validated edge, and every surface must describe it that way. This ADR does not license reframing a null result as a weaker positive one.

**Phase 6's model surface is a related but distinct hypothesis.** The grid tests fourteen fixed cells on `p_hit` against a baseline. ADR 093's model predicts eleven heads over continuous features, including interactions no fixed grid expresses, and terminal-return quantiles as well as reachability. Criterion 1 does not test that directly.

ADR 093 wrote its own answer to this question into its status rationale before the measurement existed: *"Provisional rather than Pinned: ... ADR 033's kill criteria sit between here and there. If Phase 4 finds no cell survives FDR correction, there may be no model to expand."* That condition is now met. Fourteen cells at a mean `n_eff` of 93 showing nothing is weak prior support for a model conditioning on more features with less effective data per condition. ADR 093 and ADR 094 stay Provisional, and any Phase 6 decision must cite this measurement rather than route around it.

**The engine.** Per ADR 033: the event-study engine with correct look-ahead handling and MFE/MAE tracking, the tool-restricted chat layer, the calibration methodology, and the ingest and scheduling pipeline all survive. Four alternative signal families are testable in the same framework with no rewrite — volatility term structure, earnings drift, cross-sectional momentum residuals, volume-price divergence.

*Holdout stays sealed.* Era 2024+ is the holdout split and is the same date range whatever signal is tested. Spending it to confirm a hypothesis train and validate already retired buys little and costs the firewall for whatever replaces it. Per ADR 019 and ADR 033 it is evaluated exactly once. Opening it is a deliberate decision to close the two-indicator hypothesis permanently rather than pivot, and it requires its own ADR.

*Direction is not decided here.* This ADR records what was measured. Whether the project proceeds to Phase 5 on the current engine, pivots the input signal, or both, is a separate decision with its own entry.

**What this ADR is for.**

ADR 033 anticipated this outcome and named it in advance so that reaching it would not become a negotiation:

> Built for that outcome, a null result stops being a failure. A project reporting "I tested this rigorously and found no edge, here is the infrastructure proving it" reads better than a suspiciously profitable backtest.

The criteria were fixed before sunk cost existed precisely so they could be applied after it did. Recording the firing plainly, in the same file and the same format as every other decision, is the whole mechanism working.

---

## 113. Phase 6 opens; ADR 093's condition answered

**Date:** 2026-08-17. **Status:** Pinned. Answers ADR 093's Provisional condition. Amends ADR 064's head count and ADR 067's promotion gate.

**Context.**

ADR 093 made Phase 6 conditional in its own status line, written before any statistics existed:

> Provisional rather than Pinned: this constrains a schema decision Session 10 makes now (the path table's window), but the model surface itself is not built until Phase 6, and ADR 033's kill criteria sit between here and there. **If Phase 4 finds no cell survives FDR correction, there may be no model to expand.**

That condition triggered on 2026-08-16 and is recorded in ADR 112: three configurations, 630,592 events, zero cells surviving FDR correction on either split, minimum q-value 0.706.

ADR 093 wrote "there may be no model to expand," not "there is none." It deferred the judgment rather than making it in advance. This ADR makes it.

**Decision.**

Phase 6 opens. The model is built.

It is built smaller than ADR 093's amendment specifies, and gated harder than ADR 067 specifies. Both changes follow from ADR 112 rather than from anything about the model itself.

**1. The head count drops from ~56 to 20.**

| Family | ADR 093 amended | Here | Reason |
|---|---|---|---|
| Terminal quantiles $\hat{Q}_\tau(R_h)$ | 25 (5 τ × 5 h) | 10 (5 τ × h ∈ {5, 10}) | Horizons 1, 2, 3 are the thinnest signal at the shortest windows |
| Peak quantiles $\hat{Q}_\tau(M_h)$ | 25 | 10 (5 τ × h ∈ {5, 10}) | Same |
| Reachability | 4 | **0** | Retired. See below |
| Adverse excursion | 2 | 0 | Subsumed by the terminal fan's lower quantiles |
| **Total** | **~56** | **20** | |

**2. The reachability heads are retired, not kept.**

ADR 093's amendment left this open: "Phase 6 must either retire those heads or keep them explicitly as a calibrated, human-readable slice of the peak distribution. Keeping both without deciding means training six heads against one distribution and reporting them as independent evidence."

They are retired. $P(\max_t R_t \ge X)$ is the survival function of $M_5$, so the peak fan already expresses it. Keeping both would report one distribution twice, and after ADR 112 the last thing this project needs is a surface that makes one piece of evidence look like two.

**3. The promotion gate gains a fifth check.**

ADR 067's four checks compare a retrained model against an incumbent. They cannot answer the question ADR 112 raises, which is whether the model beats no model at all.

Fifth check: **the model's out-of-sample pinball loss must beat the unconditional baseline** — the same per-ticker-year empirical distribution the cell grid measured against, fit with no features. A model that cannot beat the base rate is not promoted, regardless of how it compares to an incumbent.

This is the model-layer analogue of the cell grid's baseline comparison, and it is the check that makes Phase 6 falsifiable in the same way Phase 4 was.

**4. A kill criterion, fixed in advance, in ADR 033's spirit.**

> If the model fails check 5 on the validation split — no better than the unconditional baseline in pinball loss at any horizon — the two-indicator hypothesis is retired at the model layer as well as the cell layer, and Phase 6 closes with that recorded.

Written now, before the model exists, for the same reason ADR 033's criteria were written before any code existed.

**Rationale.**

*Why the model is a distinct hypothesis.* The cell grid tests fourteen fixed cells on one quantity: $P(\text{hit})$ against a per-ticker-year baseline, at four fixed thresholds. Three things sit outside that test.

The grid conditions on three discrete dimensions. The model conditions on the continuous feature set — `bb_pctb`, `bb_width_pct`, `k_full`, `k_fast`, `dd_52w`, `sma200_slope_60`, `rv_pct_252d`, `vol_z_20d`, `days_to_earnings`, `vix_close`, `cofire_count` — which DESIGN §6.7 deliberately kept continuous precisely because bucketing them would have exploded the grid. An effect living in an interaction between bandwidth regime and days-to-earnings is expressible by a gradient-boosted model and inexpressible by a fourteen-cell grid.

The grid tests a binary hit rate. The model predicts a distribution. A signal that does not change $P(R_5 \ge 3\%)$ can still narrow or widen the spread of $R_5$, and the grid is blind to that by construction.

The grid's suppression floor discards data. 100 of 224 train cells fell below `n_eff < 30` and contributed nothing. A model fits on all 630,592 events at once, with the clustering handled by purged walk-forward CV rather than by discarding thin strata.

*Why smaller.* ADR 112 is the reason, and it cuts against ADR 093's expansion rather than for it. Fifty-six heads against a population where fourteen cells found nothing is a large surface fit to a weak prior, and ADR 067's gate would then run twenty-five coverage checks per fan — a multiple-comparison problem at the model layer of exactly the kind FDR exists to handle at the cell layer. Ten terminal and ten peak heads at the two horizons that carry the most data is the version whose results can be read without a second correction.

Horizons 1, 2, and 3 are dropped rather than 5 and 10 because the shortest windows carry the least movement relative to noise, and because ADR 093's own argument for reaching to day 10 was that peak timing is the information the label set discards. Two horizons preserve that contrast at a fifth of the surface.

*Why the fifth check is necessary.* ADR 067's four checks are all relative to an incumbent. On a first model there is no incumbent, so a model could pass all four by default and serve while being worse than a constant. That gap existed before ADR 112 and was harmless while a measured edge was expected. It is not harmless now.

*What this ADR is not.* It is not a claim that the model will find something. ADR 112's measurement is weak prior support and this ADR says so plainly: fourteen cells at a mean `n_eff` of 93 finding nothing means a model conditioning on more features with less effective data per condition is unlikely to find much. The argument for building it is that it tests a different hypothesis, not that the hypothesis is likely.

**Consequences.**

ADR 064's eleven heads and ADR 093's ~56 are both superseded on count. The definitions in both stand: quantile fans over entry-anchored returns, exit policy as a separate layer, $M_h$ from `path` rather than from `events.mfe`.

ADR 067's gate becomes five checks. The fifth is the only one evaluable on a first model, so it is the only one that gates the first promotion.

DESIGN §7.4's rewrite, which ADR 093 authorizes and does not perform, is performed by Phase 6 against the twenty-head surface here rather than ADR 093's fifty-six.

The cross-horizon monotonicity question ADR 093 defers is now two horizons rather than five, so $\hat{Q}_\tau(R_5) \le \hat{Q}_\tau(R_{10})$ is one comparison per τ. For the peak fan, $M_5 \le M_{10}$ is arithmetic and a violation is a bug, per ADR 093's amendment property 2.

The `path` table's eleven-day window, which ADR 093 exists to have forced, is still required. Dropping horizons 1 through 3 does not shorten it; day 10 still needs day 11 for a `NEXT_OPEN` entry.

`predict()` returns `NotFound` until this phase ships, per Session 15's contract, and the test asserting that is expected to fail when Phase 6 changes it. That failure is the deliberate signal, not an accident.

**Alternatives rejected.**

*Do not build Phase 6.* Defensible, and ADR 093 left room for it. Rejected because the model tests a hypothesis the grid could not, and because closing on an untested claim is weaker than closing on a tested one. ADR 033's framing applies: the deliverable is a rigorous engine and an honest result, and one more honest result costs one phase.

*Build all fifty-six heads as ADR 093 specifies.* A large surface against a weak prior, with a fifty-coverage-check gate that would need its own multiple-testing correction.

*Keep the reachability heads alongside the peak fan.* Reports one distribution twice as if it were two pieces of evidence. ADR 093's amendment named this trap explicitly.

*Skip the fifth check and use ADR 067 as written.* Would let a first model serve without ever being compared to no model.

---

## 114. The screener's default is the event feed; statistics are one action away

**Date:** 2026-08-18. **Status:** Pinned. Governs `screen_signals` and the `/` route. Follows from ADR 112.

**Context.**

`v_screen` returns twenty-six columns. Nine describe the event — ticker, date, type, strength, `%B`, `%K`, drawdown bucket, cofire count, sector. Six describe the cell the event lands in: `p_hit`, `baseline`, `edge`, `ci_low`, `ci_high`, `q_value`, plus `n_eff`, `suppressed`, and `suppress_reason`. The original design showed all of them.

ADR 112 changed what those six columns contain. Measured on the live config, `86e91448a65aa40b`:

| | Train | Validate |
|---|---|---|
| Cells suppressed at `n_eff < 30` | 100 of 224 | 100 of 224 |
| Cells surviving FDR correction | 0 of 124 | 0 of 124 |
| Minimum q-value | 0.8492 | 0.7061 |
| Edge intervals excluding zero | 0 | 0 |

So on a given screener day, roughly 45% of rows render four blanks and a reason string, and the other 55% render a hit rate whose interval spans zero next to a q-value of 0.85.

**Decision.**

The default screener view is the **event feed**: what fired, on what ticker, in what state.

```
Ticker | Signal | Str | %B | %K | DD | Cofire | Fired
```

The statistical fields sit behind a deliberate action — an expander on the web route, `with_stats=True` on the handler:

```
Cell hit rate | Baseline | n_eff | Interval | q-value | Suppressed reason
```

Three rules govern the expanded view:

- **Whole or nothing.** Every statistical field renders, or none does. A partial set invites a reader to fill the gap.
- **A suppressed cell renders its stored reason, never a number and never a blank.**
- **A q-value above `fdr_alpha` renders alongside the hit rate**, not instead of it and not hidden. The reader learns the cell did not survive correction from the row itself.

**Why not the other options.**

*Statistics as default columns.* Four columns that are blank or meaningless on every row, every day, teach a reader to stop looking at the row. The row is the part that carries information — a `confluence_low` on a name in the 10-20% drawdown bucket is a real observation about today's market whatever the cell says. Training the reader to skip it costs the product its only working surface.

*Drop the statistical fields entirely.* Overcorrects. The measurement is the honest half of this project (ADR 033), and a screener that never shows it is a screener pretending the question was never asked. It also makes `/research` the only place a number appears, which separates the number from the row it describes.

*Render "not significant" instead of the q-value.* A label is less informative than the number and reads as a verdict. `q = 0.849` says something specific about how far from significant this is; "not significant" says only that someone applied a threshold.

*Grey out a suppressed cell's `p_hit`.* `v_screen` already nulls it, which is why the handler reads `cell_stats` instead. A greyed number is still a number on a screen and gets read by someone in a hurry. `handlers/types.py` makes it impossible rather than discouraged: `Suppressed` has no probability field at all.

**Consequences.**

`handlers.screen_signals` defaults `with_stats=False` and does not query `cell_stats` at all in that mode — not merely hiding the result, but not paying for the join. `ScreenRow.stats` is `CellStats | Suppressed | None`, a union, so a consumer that forgets to branch gets a type error rather than a blank cell.

`ScreenResult.total_matched` reports the pre-`limit` count, because a page showing 200 of 640 rows and saying only "200" misstates how much fired.

This ADR is a display decision and changes no measurement. If a later config produces cells that survive correction, the default can be revisited — but the revisit should cite a measurement, not a preference.

---

## 115. `v_positions` reads a settings row, not a generated DDL

**Date:** 2026-08-18. **Status:** Pinned. Resolves ADR 095's deferred Phase 5 rebuild. Chooses ADR 095's *first* option over its stated preference.

**Context.**

ADR 095 found a second exit implementation in SQL: `v_positions` compared `k_full` against a bare `80` and `CURRENT_DATE - entry_date` against a bare `5`, which are `ExitParams.exit_stoch_threshold` and `ExitParams.max_hold_days` written as constants. It deferred the rebuild to Phase 5 and offered two fixes:

> Pass the thresholds in through a settings table the view joins, which keeps the view a plain view but adds a row-existence dependency to every read. Or generate the view DDL from `ExitParams` inside the migration, which keeps the numbers single-sourced at the cost of making the DDL a build artifact rather than a checked-in file. **The second is the better fit for this codebase's "one implementation" rule**; the first is easier to sweep without a migration.

**Decision.**

The settings row. ADR 095's preference is reversed, and the reason is a thing that did not exist when ADR 095 was written.

`jobs/threshold_lint.py` shipped in Session 14 as ADR 092's replacement matcher. It scans checked-in Python and SQL for a numeric literal adjacent to a comparison on a threshold-bearing column, and it carries a `KNOWN_EXCEPTIONS` list with this note:

> Remove this entry the moment `v_positions` is rebuilt (Phase 5) — its continued presence past that point is itself a signal the rebuild missed a spot.

**A generated DDL cannot satisfy that.** Generating the view's SQL from `ExitParams` at migration time still writes `80` into the database. `pg_dump` then writes `(80)::numeric` back into `db/schema.sql`, the linter finds it, and the `db/schema.sql` exemption has to stay forever. The defect class would remain permanently allowlisted in the file most likely to grow the next instance of it — and the exemption would no longer be describing a known defect, but hiding a working one, which is worse because nobody would think to look.

The settings row leaves no literal in any checked-in SQL. The exemption was deleted on 2026-08-18 rather than renewed.

**What was built.**

`serving_config`, a single-row table (`only_row boolean PRIMARY KEY DEFAULT true CHECK (only_row)`) holding `exit_stoch_source`, `exit_stoch_threshold`, `exit_stoch_threshold_short`, `exit_on_stoch_80`, `exit_on_upper_band`, `exit_on_mid_band`, `max_hold_days`, and the `config_hash` they came from. `v_positions` joins it `LEFT ... ON true`.

Four defects fixed in the same rebuild:

1. **Thresholds come from the row.** Sweeping `exit_stoch_threshold` to 70 now moves the position page with the backtest, via `cscan db sync-config` and no migration.
2. **The stochastic exit respects `p.side`.** The old view applied the long threshold to every row, so an open short read its exit off the wrong number — and wrong in the direction that *suppresses* the exit. `exit_stoch_threshold_short` exists precisely so the two sides sweep independently (ADR 016, ADR 092).
3. **The `%K` column follows `exit_stoch_source`.** ADR 110 moved the entry trigger to `k_fast`. The view read `k_full` unconditionally, so the position page and the backtest could disagree about what "overbought" means.
4. **`exit_signal_mid_band` is gated on `exit_on_mid_band`**, which defaults False (ADR 046). Gated to NULL, not to false: "this rule is not in force" and "this rule is in force and has not fired" are different facts and a reader acts differently on each.

**A fifth defect, not in ADR 095, found while writing the parity fixture.** `days_held` was `CURRENT_DATE - p.entry_date` — **calendar** days — while `max_hold_days` counts bars: `core/exits.py` walks `fwd_bars` and `holding_days` equals `exit_idx + 1`. A position opened on a Thursday reads 4 calendar days and 2 sessions on the following Monday, so `exit_signal_timeout` fired a session early over every weekend and by two over a holiday weekend. The view now counts `trading_days`.

`exit_stoch_k` is exposed as a new trailing column so the comparison is auditable from the row: a reader sees which `%K` produced the flag without knowing what `exit_stoch_source` currently says. It is appended last so every existing column keeps its ordinal.

**The cost, stated rather than minimised.**

ADR 095 named the row-existence dependency and it is real. Bounded three ways:

- The join is `LEFT ... ON true`, so a missing row renders every exit flag NULL and the position still appears. "Unknown" is the honest failure; a plain join would return zero rows, which reads as "you have no positions".
- The migration seeds the row from `ExitParams()` defaults, so a freshly migrated database is never in that state.
- `test_v_positions_config.py::test_the_stored_row_matches_the_live_config` fails when `ExitParams` moves and the row does not, naming `cscan db sync-config` in the failure message. Without it the row would go stale silently — which is ADR 095's own defect, one indirection further out.

**Why the migration imports application code.** `capitalscan/jobs/views.py` holds the DDL and the row payload so the staleness test above can compare the deployed policy against the live `ExitParams`. A migration runs once; the test is what makes the single-sourcing durable rather than momentary. The import is narrow — `views.py` imports only `core/config.py`, which imports only `dataclasses`.

**Rejected.**

*Generate the DDL from `ExitParams` in the migration.* ADR 095's preference. See above: it does not remove the literal from `db/schema.sql`, so it does not let the linter exemption be deleted, and a threshold sweep still needs a migration.

*Leave the view alone and document the discrepancy.* ADR 092 and invariant 9 exist because a documented discrepancy is one nobody reads at the moment it matters.

*Drop the exit-signal columns from the view entirely and compute them in `handlers/`.* Attractive — it would put the one implementation in Python where `core/exits.py` already lives. Rejected because `v_positions` is a Phase 2 surface with existing consumers, and the change is a rewrite of the position page rather than a fix to a view. Worth reconsidering when session 17 builds `/positions` against handlers.

---

## 116. `v_ticker_state` reads one row per ticker instead of sorting three million

**Date:** 2026-08-18. **Status:** Pinned. A performance change to a view ADR 076 makes a shared contract, with no change in output.

**Context.**

ADR 076 makes Postgres views the contract both consumers read, so a view's cost is a cost every surface pays. `v_ticker_state` is read by `v_positions`, by the ticker page Session 17 will build, and by `cscan poll`.

It joined `bars`, `tickers`, `market_days`, and a LATERAL over `universe` across every daily indicator row, then applied `DISTINCT ON (ticker)` to the result. 2,912,426 rows in, 612 out.

Measured 2026-08-18, `max_parallel_workers_per_gather = 0`:

| Query | Before |
|---|---|
| `SELECT count(*) FROM v_ticker_state` | 23.8 s |
| `SELECT * FROM v_positions WHERE id = 44` | 24.5 s |
| `SELECT * FROM v_ticker_state WHERE ticker = 'TSM'` | **17 ms** |

**That third row corrects a claim Session 15 made.** `RESULTS.md`'s Session 15 note said "nothing pushes the position's ticker down through the `DISTINCT ON`", generalising from one measurement of the *whole* view. It is wrong: Postgres pushes a **constant** predicate down through `DISTINCT ON` and the single-ticker read was already fast. What it cannot push down is a **correlated** one, which is why `v_positions` — joining on `p.ticker` — paid the full 24.5 s to return one row, and why the ticker page was never in danger. Session 17's plan repeated the wrong version and is corrected with this ADR.

**Decision.**

Rewrite the view as a loose index scan: drive off `tickers` (712 rows) and take one indicator row per ticker with `ORDER BY ts DESC LIMIT 1`, supported by a new partial index `indicators (ticker, ts DESC) WHERE interval = '1d'`.

| Query | After |
|---|---|
| `SELECT count(*) FROM v_ticker_state` | 27 ms |
| `SELECT * FROM v_positions WHERE id = 44` | 23.5 ms |
| `SELECT * FROM v_ticker_state WHERE ticker = 'TSM'` | 1.4 ms |

**Two rejected attempts, both measured rather than reasoned about.**

*A LATERAL over the unchanged view.* Replacing `v_positions`' `LEFT JOIN v_ticker_state s ON s.ticker = p.ticker` with a correlated `LEFT JOIN LATERAL (SELECT * FROM v_ticker_state WHERE ticker = p.ticker)` produced a byte-identical plan at 22.7 s. A correlated subquery with no volatile function and no `LIMIT` is pulled up and the lateral flattened away. **The `LIMIT` in the shipped version is load-bearing twice** — it prevents that flattening, and it is what makes the index scan stop at the first qualifying row instead of reading all ~4,800 rows for that ticker.

*Moving the joins inside a `DISTINCT ON`.* Correct, and only 1.7x faster at 13.7 s. It reintroduced the 2.9M-row join to `bars` and an external merge sort of 74 MB to disk. Being correct is not the same as being fast, and this version was almost shipped on the strength of a 62x measurement taken against a variant that had dropped the join entirely.

**Why the rewrite is necessary at all.** Postgres cannot apply `DISTINCT ON` before the joins on its own. Semantically the distinct applies to the joined row, and although these particular joins can neither multiply nor eliminate rows, nothing in the schema tells the planner that. Saying it requires rewriting the query.

**The elimination case, kept structurally.**

The lateral filters on `EXISTS (bars ...)` rather than selecting from `indicators` alone. The old view inner-joined `bars` and *then* took the latest surviving row per ticker, so a ticker whose newest indicator row had no matching bar fell through to the next one down. A rewrite without the `EXISTS` would drop that ticker entirely.

Measured across all 2,912,426 daily indicator rows: **zero** lack a matching bar, **zero** lack a matching ticker. The filter is kept anyway. `indicators` is derived from `bars`, so the invariant is real, and it is enforced by nothing — no foreign key, no constraint. Relying on a measurement that is true today to justify a behaviour change tomorrow is how this class of defect gets in, and the `EXISTS` costs nothing because the index serves it.

**Equivalence is measured, not argued.** `tests/integration/test_v_ticker_state_rewrite.py` rebuilds the pre-116 view from `jobs/views.py::V_TICKER_STATE_DDL_PRE_116` under a second name and diffs both directions with `EXCEPT` over whole rows. Zero differences across all 612. It also re-derives the expected `as_of` from `indicators` and `bars` independently, so the test does not merely confirm that two views agree.

**Consequences.**

`v_positions` inherits the fix with no change of its own — it was 24.5 s per row for the same reason. Session 17's ticker page no longer needs the mitigation its plan called for; the "measure before building an interactive page on it" instruction stands, but the number it was reacting to is gone.

The index costs one entry per daily indicator write. `cscan indicators` writes ~600 rows a night, so the maintenance is negligible against a 62x read.

Migration `a3c8e15d40b7`, reversible. `downgrade()` restores the slow view verbatim and drops the index, because a downgrade that quietly kept the optimisation would not be a downgrade.

**Rejected.**

*A materialized view.* Faster still, and it introduces a refresh that can be stale, a `REFRESH` to schedule, and a second definition of "current". 27 ms does not justify any of that.

*A foreign key from `indicators` to `bars`.* Would make the `EXISTS` provably redundant and let the lateral read `indicators` alone. It is the right long-term shape of the invariant and it changes ingest behaviour — deletes and rejects on `bars` would start blocking or cascading — which is a larger change than a read optimisation should carry. Worth revisiting on its own terms.

---

## 117. The poller reports every confluence and says how far it is from a reversal

**Date:** 2026-08-18. **Status:** Pinned. Amends the display half of ADR 108. Deliberately diverges from ADR 111.

**Context.**

ADR 111 gave `cscan scan` an `--actionable` filter: a long needs confluence, a short needs confluence **and** the close-confirmed reversal. The poller kept notifying every `confluence_high`, and the reversal appeared only as a tag when it was confirmed.

That divergence was noticed on 2026-08-18 (user's observation): five `confluence_high` alerts fired, none tagged, and there was no way to tell from the alert whether a name had missed the reversal narrowly or was running hard away from its open. MPC fired at 364.70 against a band of 358.56 while trading a little above its open. The user's judgement was that it was "good enough to buy puts". The alert could not have supported that judgement either way, because it contained neither the open nor any measure of distance.

**Decision.**

Three parts.

**1. The poller keeps alerting on every confluence.** No `--actionable` equivalent, and this is a deliberate divergence from ADR 111 rather than an oversight. The two surfaces answer different questions. `cscan scan --actionable` is a decision surface and should be narrow. The poller is where a person watches the session and makes the call, so it needs to show the near misses. A reversal that has not formed yet is not the same as one that will not.

**2. A short-side confluence always says where it sits relative to the rule.** The subject line carries one of four states rather than a tag-or-silence:

| State | Tag |
|---|---|
| Confirmed | `[bear reversal: above band, below today's open]` |
| Not confirmed | `[no reversal: +0.39 ATR vs today's open]` |
| Open unknown | `[no reversal: today's open unavailable]` |
| Back inside the band | `[no reversal: back inside the band]` |

The confirmed wording is unchanged from ADR 108's live half, so anything grepping notification history for it keeps matching.

**3. The gap is measured in ATR, and the sign carries the verdict.** `open_gap_atr = (price - day_open) / atr_14`. Negative means price is below the open, which *is* the reversal. Small positive is a near miss. Large positive is a name trending away from the rule.

ATR rather than dollars or percent because the reader is comparing five tickers at once: on 2026-08-18 the same five alerts spanned MPC at $364 and WBD at $28, and a dollar gap is meaningless across them. `atr_14` is already on the band the poller holds, so it costs nothing to compute.

**What is stored.**

`signal_reports.state_json` gains `day_open` and a `bear_reversal` block (`above_band`, `confirmed`, `band_gap`, `open_gap`, `open_gap_atr`). No migration: the column is already `jsonb`.

This matters more than the alert text. Without `day_open` the stored report carried the band and the price but not the third number the rule compares, so "how close was that?" could only be answered from a quote feed that no longer exists by the evening. `scripts/wait_and_poll.ps1` reads the three new fields into its console line and its session CSV.

**What did not change.**

The signal definitions. `breach_live` still cannot emit `bear_close_above_upper` — the stored type is close-confirmed and mid-session there is no close — and `is_bear_reversal` is still the live analogue, `price >= bb_upper AND price < day_open`. `reversal_state` is presentation and is kept separate from `is_bear_reversal` so a display change cannot alter a signal definition; a test asserts the two agree on every combination it checks.

Every confluence still fires, is still written to `events`, and is still notified. Nothing about what reaches the database moved, which is what keeps the poller's day reproducible by that night's `run_events`.

**Rejected.**

*Filter the poller to actionable shorts (option A when this was raised).* It would make the two surfaces agree, and it would hide exactly the names this ADR exists to surface. The live reversal flag is unstable intraday by construction: a name can satisfy it at 10am and fail it at the bell, or the reverse. Suppressing on an unstable flag drops signals that confirm later.

*Report the gap in dollars or percent.* Not comparable across the five names that fire on the same morning.

*Report only "near" or "far" rather than a number.* A threshold nobody chose, applied to a judgement the reader is making. The number is the input to their decision, not a verdict to be pre-computed.

---

## 118. MCP is for LLM callers; deterministic reads go straight to the views

**Date:** 2026-08-18. **Status:** Pinned. Resolves the Session 17 blocker. Confirms ADR 070 and ADR 076 as written; corrects `session16-18-phase5.md` §17.1.

**Context.**

Session 15 built the Python handler layer and Session 16 wrapped it in an MCP server. `session16-18-phase5.md` §17.1 then said the web routes should call those handlers:

> Routes call handlers. No route constructs SQL or imports `db_io`.

That is ADR 072's arrangement, and **ADR 076 had already withdrawn it** in favour of views as the shared contract, with ADR 070 fixing the routes in TypeScript on Vercel. The session plan and three Pinned ADRs disagreed, and Sessions 17 and 18 were stopped on 2026-08-18 rather than picking one (CLAUDE.md: "if a task appears to require contradicting one, stop and ask").

Three options were put up, plus a fourth raised in discussion: route the web UI through the MCP server, so the handler layer serves every surface.

**Decision.**

Option A. TypeScript routes select from the Postgres views. The handler layer and the MCP server serve LLM callers only.

```
/  /ticker  /research   ──SELECT──►  v_screen, v_chart, v_events, v_ticker_state
                                            ▲
/chat, Claude Code, Claude Desktop          │
        └── MCP ──► handlers/ ──────────────┘
```

**The rule, in one line: MCP is for LLM callers, not for deterministic retrieval** (user's decision, 2026-08-18).

*Why that line and not another.* A screener query is deterministic. It has one right answer, no tool-selection step, and no model in the path. Sending it through a protocol built for LLM tool calls means an `initialize` handshake, a session id, an SSE frame, and a tool-call envelope, in order to run a `SELECT` that Postgres already answers in 27 ms. The protocol earns its overhead when a model is choosing which tool to call and needs a schema to choose from. It earns nothing when a route already knows.

MCP stays valuable exactly where that is true: `/chat`, and driving the engine from Claude Code or Claude Desktop. Session 16 is not diminished by this. It is the surface for the callers that need it.

**The fourth option, and why not.**

Routing the web UI through the handlers was argued on the facts as they stand: the database is `localhost:5432`, `DATABASE_URL_SERVING` is unset, nothing is on Vercel or Neon, so ADR 076's "would have required hosting a Python service" costs nothing today, and in-process calls would also void ADR 072's "no network hop".

That reasoning is sound about today and wrong about the intent. The Vercel deployment is still the plan, and building the serving layer against a topology nobody intends to keep would mean rewriting it at the moment it first mattered. ADR 070 and ADR 076 are confirmed as written rather than amended.

**Consequences.**

*`session16-18-phase5.md` §17.1 is corrected*, along with the §0 blockers in `session17-screener-and-ticker.md` and `session18-research-and-chat.md`. `capitalscan/web/` stays empty; the Next application lives at the repo root.

*Invariant 8 changes form on the web surface and this is the real cost.* In Python `handlers/validate.py` raises. In TypeScript the guarantee is that `n_eff`, `ci_low`, `ci_high`, and `q_value` are columns on `v_screen` and `v_stats`, so returning a bare probability requires deliberately dropping columns (ADR 076). That is structural but weaker than a raise, and it is the price of A. **A test asserts the serving views carry the four companion columns**, so "structural" is checked rather than assumed.

*ADR 114's event-feed default gets a second implementation.* The handler's `with_stats=False` does not help a route that never calls it, so the TypeScript screener must default to the feed on its own. Named here so it is not discovered as a bug.

*Session 18's chat layer routes through MCP*, which answers that session's own open question. Tool results reach the model having passed `handlers/validate.py`, because the MCP server calls the handlers. One validator on the path that carries a model, which is the path that needs it.

**Nothing else blocks Session 17.**

This ADR originally closed by naming a second blocker: that the `frontend-design` skill CLAUDE.md requires was unavailable. **That was wrong, corrected 2026-08-18 the same day.** It is a plugin skill, installed and enabled, living under `~/.claude/plugins/` rather than `~/.claude/skills/`; the claim came from checking one directory and generalising.

Reading it surfaced a second error, in CLAUDE.md and `DESIGN.md` §11.7 rather than here: both said the skill "carries this environment's design tokens". It does not. It is general design guidance and holds no palette, type scale, or spacing system. The design constraints were always in `DESIGN.md` §11.6-11.9 and CLAUDE.md's own Frontend section.

**Rejected.**

*B, a Python web layer on the handlers.* Contradicts ADR 070 and needs a Python host that is not Vercel.

*C, Next.js over a Python API.* ADR 076's hosting cost plus the network hop ADR 072 rejected.

*D, the web UI over MCP.* Above. Cleanest single-contract story, wrong protocol for a deterministic read, and it bets on a topology that is not the plan.

---

## 119. The screener shows what fired today; `market_date()` defines "today"

**Date:** 2026-08-19. **Status:** Pinned. Resolves the Session 17 open item. Amends ADR 100's predicate on `v_screen`.

**Context.**

DESIGN §11.1 calls `/` "today's screener, the default landing route". Building it showed it could not be one, and two further defects behind that.

**`v_screen` filters `entry_kind = 'next_open'`, and only `cscan backtest` writes that kind.** `run_events` writes `touch` (`compute.py:801`) and so does the poller. The screener therefore trailed the last full backtest — a five-hour job. Measured 2026-08-18: newest `next_open` was 2026-08-13 while **67 events had fired that day**, all `touch`.

**`v_screen` also showed events from every config.** ADR 100 says it carries a `config_hash` predicate, and it did — on the `cell_stats` join only, never on `events`. `v_events` filters correctly; this one did not. Measured on the newest date it could show: 46 rows, of which **17 belonged to a superseded config**, mixed in with nothing distinguishing them. Across the view, 23 distinct hashes and 799,455 rows; with the predicate, 40,819.

**`CURRENT_DATE` is not the market's date.** The database runs `Etc/UTC`. At 2026-08-19 02:15 UTC it returned **2026-08-19** while the session that had just closed was **2026-08-18**. Every use of it to mean "today" is wrong from 00:00 UTC until midnight ET — about seven hours a day, 5pm to midnight Pacific, which is exactly when someone reviews the session that just ended.

**Decision.**

Three parts.

**1. `v_screen_live` is the feed.** `entry_kind = 'touch'`, config-scoped, joined to statistics on the grid's `next_open` cell. `/` reads it.

**The feed's grain and the statistics' grain differ, on purpose.** A feed answers "what fired", which is a detection-time question. A hit rate answers "what followed an entry we simulated", and `GRID_ENTRY_KIND` is `next_open`. Forcing one grain on both would either report statistics against an entry nobody measured, or show a screener that trails a five-hour job. This is the one place ADR 076's "one view, one contract" is deliberately two views, and the reason is that they answer different questions rather than the same question twice.

**2. `is_cluster_head IS NOT FALSE`, not `is_cluster_head`.** The poller writes one row per breach and *cannot* cluster: ADR 054's gap window needs the whole session, which does not exist at 09:35. Its rows carry NULL. `WHERE is_cluster_head` therefore returns **zero rows intraday** — measured, 0 of today's 67. NULL means "not yet clustered", which is not "not a head", and the honest treatment is to show it and hide only the known non-heads. The column is projected so a consumer can tell the two apart.

**3. `market_date()` is the one definition of today.** `SELECT (now() AT TIME ZONE 'America/New_York')::date`, `STABLE` because it reads the clock. `v_positions` adopts it, which fixes `days_held` and `exit_signal_timeout` over-counting by one session every evening.

**Consequences.**

`v_screen` keeps its `next_open` grain and is now correctly config-scoped, so `handlers/screen.py` and the MCP `screen_signals` tool stop returning superseded-config rows. That is a behaviour change to a shared contract and it is a fix, not a regression: those rows were never supposed to be there.

The screener can now show signals *ahead* of bars — the poller writes today's events before that night's ingest. The status strip carries both dates rather than one.

**Rejected.**

*Widen `v_screen` to prefer `next_open` and fall back to `touch`.* Both kinds exist for backtested days, so it double-counts every historical row unless the fallback is written as a lateral per event. Achievable and harder to read than two views.

*Put the join in the route.* Considered, and it was the original lean. Rejected once the config-filter defect surfaced: the fix belonged in SQL either way, and having made one view correct there was no reason to put the second one somewhere else.

*Use `CURRENT_DATE` and set the database timezone to ET.* Moves the problem into cluster configuration, where nothing in the repository records it and a restored dump silently reverts it.

---

## 120. `v_chart` returns one row per bar; markers are arrays

**Date:** 2026-08-19. **Status:** Pinned. Applies ADR 119's three predicates to the last serving view missing them. Amends DESIGN §8.2's `v_chart` DDL.

**Context.**

DESIGN §8.2 introduces the view as "bars + indicators + event markers, **one row per bar**". Building `/ticker/[sym]` was the first time anything read it. Measured 2026-08-19 on TSM's last 400 sessions: **963 rows for 275 trading days.**

Four causes, three of them the ones ADR 119 had just found next door.

**No `config_hash` predicate.** The events join matched every sweep config, and there are **22** of them on `next_open`. A bar that ever produced an event joined once per config, so a chart would stack 22 candles and 22 markers on one date. `v_events` has always filtered config and ADR 119 fixed `v_screen`; this was the third and last serving view missing it.

**`entry_kind = 'next_open'`.** Only `cscan backtest` writes that kind, so markers stopped at 2026-08-13 while events had fired through 2026-08-18. A chart whose newest marker is five sessions old is not showing what fired.

**`AND e.is_cluster_head`.** The poller cannot cluster — ADR 054's gap window needs the whole session — so its rows carry NULL and a bare truth test drops every one of them.

**And one that is only a chart's problem: a bar can carry two events.** Measured, **116 dates** hold both a long and a short head under the live config. ADBE 2016-06-22 is `bb_lower_touch` (long) and `stoch_overbought` (short) on the same bar. Both fired. No predicate removes them, because removing either would be dropping a signal.

**Decision.**

The three predicates change to match `v_screen_live`: config-scoped, `entry_kind = 'touch'`, `is_cluster_head IS NOT FALSE`.

The marker columns become arrays — `event_ids`, `signal_types`, `sides` — aggregated in a `LEFT JOIN LATERAL`, and the bar stays one row. An aggregate with no `GROUP BY` returns exactly one row over zero input, so a bar with no events is one row of NULLs rather than a missing row.

`exit_date`, `exit_reason` and `net_ret` are **dropped**. They are per-event outcomes, and carrying per-event columns on a bar grain is the shape mismatch that produced the duplication. `v_events` has them keyed by event, and the ticker page reads it for the history table under the chart.

**Why the grain matters more here than on a table.** A duplicated row in a table is visible: you see the same ticker twice. A charting library indexes its series by time and keeps the last value for a duplicate key, silently. The 22x duplication would have rendered as a chart that looked entirely correct and drew whichever config's marker sorted last. That is the failure this view has to be structurally incapable of, not merely free of today.

**Consequences.**

The column change breaks no caller: grep across `capitalscan/`, `web/` and `db/` at the time of writing finds no consumer other than this session's ticker page.

The ticker page needs two queries where the plan assumed one — series from `v_chart`, outcomes from `v_events`. Measured at 9.7 ms for 275 bars with markers, against `events_config_hash_ticker_signal_date_signal_type_entry_kin_key`, so the second query is not the cost.

**Rejected.**

*Pick one event per bar with a deterministic ranking.* One line, and it drops a real signal on 116 dates while the chart still looks complete. The 116 are exactly the bars where both sides fired, which is the most interesting thing a bar can do.

*Keep the scalars and let the page deduplicate.* Puts the grain contract in TypeScript, where the next consumer will not know it exists. ADR 076 puts query logic in the view for this reason.

*A `jsonb` marker column.* One column instead of three and it reads well, but it takes every marker out of the type system and out of `\d v_chart`, which is where someone looks first.

---

## 121. The ticker chart pages its own history through an API route

**Date:** 2026-08-19. **Status:** Pinned. First application of ADR 076's "TypeScript API routes select from those views". Does not amend ADR 118.

**Context.**

`/ticker/[sym]` server-renders a fixed window — 252 sessions by default — and a chart is a thing people drag. Two complaints, both correct.

Dragging right ran off the end of the data into blank future, which reads as missing data rather than as the edge of the series. Dragging left stopped at the oldest server-rendered bar with nothing to say it had stopped.

The same shape, one axis over, in the event table: TSLA has **272 `touch` events** and the page rendered 40, so five of six were unreachable and nothing on the page said how deep the list went.

**Decision.**

Three API routes — `/api/chart`, `/api/events`, `/api/tickers` — each a `SELECT` against the same views the page reads, with a bound the page does not have.

**ADR 118 is unaffected.** It says MCP is for LLM callers and deterministic reads go straight to the views. These *are* deterministic reads going straight to the views; they run in the Next process, not through a handler. ADR 076 names this shape explicitly.

**The right edge is pinned, the left edge is not.** `rightOffset: 0` with `fixRightEdge` stops the scroll at the newest bar. `fixLeftEdge` stays off, because the left edge is where more history arrives, and pinning it would pin the thing that is supposed to move.

**Paging is `OFFSET`, not a keyset cursor.** A keyset is the right answer when the offset can reach the millions. This is one ticker's own history and the deepest in the database is a few hundred rows, so `OFFSET 200` on an index-ordered scan costs nothing and the two-column cursor costs a tuple comparison in the SQL and in the client, forever.

**The trigger is a negative logical `from`, not a positive threshold.**

This is the part worth recording. The common recipe for lazy-loading this library is `if (logicalRange.from < 10) loadMore()`. It is wrong for a chart that calls `fitContent()`: fitting puts the whole loaded array on screen, so `from` is 0 at rest and the test is true the moment the chart mounts. Measured in the network log — `?range=6m` fetched a full extra year before anyone touched it, which makes the range switch decorative.

A negative `from` means the viewport extends left of the first bar: the reader is looking at empty space where older data belongs. It cannot fire at rest, it needs no "has the user interacted yet" flag, and it costs a moment of blank before the page lands against a ~10 ms query.

**Consequences.**

The chart component became stateful: built once per mount, with the bar array in a ref rather than in state, because the scroll handler reads it every frame and a stale closure would page from the wrong cursor. Prepending shifts every logical index, so the visible range is captured before the repaint and restored by the shift — without that the chart jumps backwards under the cursor each time a page lands.

Three client components now exist where there was one. `test_boundary` asserts the list whole rather than counting, so a fourth is a decision someone makes deliberately.

Pages merge on **event id**, not on index. A concurrent write between two requests shifts every offset by one, and a duplicate React key is a silent rendering fault rather than an error.

**Rejected.**

*Server-render the whole history.* TSLA's 272 events is nothing; a ticker with fifteen years of daily bars and no range limit is 3,800 rows of chart data in the RSC payload on every navigation, for a reader who will look at one year.

*A "load more" button.* Honest and slower. The chart already has a continuous gesture — the drag — and a button would ask the reader to leave it.

*Infinite scroll on the screener too.* The screener is a one-day view (ADR 119); there is nothing below it to load.

---

## 122. Detection records trade-universe membership instead of being filtered by it

**Date:** 2026-08-19. **Status:** Pinned. Amends DESIGN §4.7 step 3. Does not amend ADR 001.

**Context.**

Building `/ticker/[sym]` made a ten-year-old behaviour visible for the first time. Pull up SMCI and the event history stops at **2010-03-24**, while the chart above it shows price clearing the upper band repeatedly through 2026. Measured on SMCI since 2024-01-01: **659 bars, 72 lower-band touches, 120 upper-band touches, 0 events.**

`run_events` skipped any bar where the ticker was outside the trade universe that day:

```python
# Step 3: skip if not in the trade universe.
if not core_universe.in_trade(universe_flags, ticker, bar_date):
    continue
```

SMCI has **never** been in the trade universe — 0 of 66 quarterly snapshots. It is `in_train`, which is why it has bars and indicators at all.

Two consequences, one obvious and one not.

**The ticker page is unusable for 481 of 622 names.** 141 tickers are in the trade universe now. The page exists to look at a ticker, and for the rest it showed a full price series under an empty history, which reads as a broken job rather than as a policy.

**The 2010 block is an artifact.** The earliest `universe` snapshot is 2010-03-31 and `core.universe.in_trade` returns `True` when no evaluation exists on or before the bar — the documented v1 fail-open simplification. So every bar before that date passed a filter that had nothing to evaluate. That is why 187 tickers have a dense block of 2010 Q1 events and nothing after, and why **17,919 events, 11.4% of the live config's `touch` rows across 512 tickers**, entered the training population under a vacuous check.

**Decision.**

Membership stops being a filter on detection and becomes a column on the detection. `events.in_trade`, stamped point-in-time from the same `universe_flags` lookup the skip used.

**A signal that fired is a fact about the ticker. Whether you would have traded it is a different fact.** Storing the second lets every consumer decide, instead of one decision being frozen into what the table is able to contain.

**Point-in-time, not current.** A name that left the trade universe in 2019 carries `true` on its 2015 events and `false` on its 2021 ones. Current membership lives in `v_universe` and answers a different question; only the stored flag lets a historical population be reconstructed from the table.

**Nothing else changes, and that is enforced rather than intended.** `cscan scan`, `cscan poll`, `wait_and_poll.ps1`, the screener, and every statistic keep exactly the population they had. The measured numbers must not move, so every read that decides a population carries the predicate.

**Consequences.**

*The guarantee moved from one place to eighteen, so it became a test.* Before this, `events` could not contain an out-of-trade row and no consumer needed a predicate. After it, every read widens by roughly 4.4x without one, and **nothing about the output looks wrong**: `cell_stats` returns more events per cell, `n_eff` rises, and every number stays internally consistent. `test_events_in_trade_filter.py` sweeps `jobs/`, `research/` and `handlers/` and fails on any read of `events` that neither filters nor appears on an allowlist with a stated reason — the `threshold_lint.KNOWN_EXCEPTIONS` pattern, including the test that every allowlist entry still describes something real.

*`jobs/compute.py::scan` was the trap.* Its docstring named this skip as the reason it accepted a `universe` argument without filtering on it. `cscan scan` would have started listing out-of-trade names on the first run after this change, silently. It now carries `e.in_trade` explicitly, and a test asserts it by name rather than leaving it to the sweep.

*`v_screen` and `v_screen_live` take the predicate too.* Both consumers already join `v_universe` and filter there, so this is defence in depth — but `v_universe` answers "in the universe now" and the column answers "in the universe on that bar", which is the right question for a historical row. `v_chart` and `v_events` deliberately do **not**: they are what the ticker page reads. A test asserts the negative, because adding the predicate there is the obvious thing to do and would undo this ADR entirely.

*The fail-open branch stays, and the 17,919 rows stay in `train`.* Closing it is a separate decision with its own blast radius — it would drop 11.4% of the training population and move every measured cell. Recorded in `RESULTS.md` with the number, not fixed here.

*Cost.* `events` grows by roughly 4.4x on the live config's `touch` rows. `ADD COLUMN ... NOT NULL DEFAULT true` is a catalog-only change on Postgres 11+, so the migration is instant on 13.5M rows, and `true` is not a guess: every pre-existing row was written by the gated path.

**Rejected.**

*A second table for the wider detections.* Zero risk to the eighteen consumers, and it makes "which one is authoritative" a question someone has to ask forever. The predicate plus a lint test buys the same safety without splitting the meaning of "an event".

*Gate on `in_train` instead of `in_trade`.* Narrower blast radius, and it just moves the same wall: the next name someone wants to look at is the one outside the train universe.

*Recompute the statistics over the wider population.* A different study, not a fix. ADR 001's two-universe split exists so what you look at is narrower than what you measure on; this ADR widens neither of those, it widens what is *recorded*.

---

## 123. The screener shows the poller's intraday reversal, marked as intraday

**Date:** 2026-08-19. **Status:** Pinned. Surfaces ADR 117's live judgement. Does not amend ADR 108, ADR 111, or ADR 117.

**Context.**

Two different things carry the word "reversal", and the screener could only ever see one of them.

`bear_close_above_upper` is **close-confirmed** (ADR 108, ADR 109): `open > close AND close >= bb_upper[t−1]`, resolvable only once the session ends. It reaches `events.signal_types_all` when `cscan events` runs that night.

`poll.py::reversal_state` is the **live analogue** (ADR 117): price above the band but below today's open, evaluated on every quote and written to `signal_reports.state_json->'bear_reversal'`.

`v_screen_live` joined `signal_reports` for `min(fired_at)` and never touched `state_json`. So during a session the poller's terminal printed `no reversal (+0.42 ATR vs open)` and the home page could say nothing at all. The badge appeared the following morning, about a session that had already closed.

Measured on OKE, 2026-08-18: open 96.54, low 96.40, close 97.07, prior `bb_upper` 95.62. The intraday window is (95.62, 96.54) and **the low sits inside it** — price was in reversal territory during the session. The close-confirmed flag is correctly false, because it closed at 97.07, back above its open. One bar, two honest answers.

**Decision.**

`v_screen_live` projects `rev_confirmed`, `rev_above_band`, `rev_open_gap_atr` and `rev_ts` from the newest `signal_reports` row for the event.

**The newest report, not the first.** `fired_at` keeps `min()` — when the signal was first detected — while the reversal takes the latest row, because it describes where price is *now* and the session's earliest quote is the least useful one. Two laterals, for exactly that reason.

**Three renderings, not two.** Close-confirmed is a solid badge. Live-confirmed is the same badge dashed and prefixed `live`. A row the poller evaluated and found *not* reversing renders its distance in ATR — muted, unbordered, no badge.

That third state is the whole point. ADR 117 chose to show every confluence and say how far each was from confirming rather than hide the near-misses, and a badge that appeared only on confirmation would quietly put that decision back.

**Close-confirmed wins when both exist.** It is a settled fact about a finished session; the poller's is a statement about a moment, and once the close has spoken the moment no longer matters.

**Consequences.**

`state_json ? 'bear_reversal'` guards the lateral. ADR 117 merged 2026-08-18 11:18 PT and the last poller run wrote at 08:34 PT, so **every existing report lacks the key**. Without the guard a missing key casts to NULL and renders as "not reversing", which is a claim the data does not support — the distinction between *evaluated and false* and *never evaluated* is the one this view has to preserve, and it is why presence is decided by `rev_ts` rather than by `rev_confirmed`.

The ticker page does not get this. Its chart is a history of closed sessions and its event history is close-confirmed outcomes; a live intraday judgement on that page would be the only thing on it that changed between refreshes.

`v_screen` is untouched. It is the `next_open` statistics grain and has no live half.

**Rejected.**

*One badge, whichever reversal is available.* Simpler, and it hides which kind of claim the reader is looking at. ADR 111 makes the close-confirmed one the actionable condition; a reader deciding on a short needs to know whether the badge in front of them is settled or provisional.

*Store the live judgement on `events`.* It would need a write per quote against a 13.5M-row table, and it is a property of a moment rather than of the event. `signal_reports` is already the per-quote record.

*Recompute the reversal in the view from `quotes_live` and `bars.open`.* Second implementation of a rule `core`/`jobs` already owns, and it would drift the first time `is_bear_reversal` changed. Invariant 2's reasoning applies to the live half too.

---

## 124. The screener is a record of the session, not a clustered sample

**Date:** 2026-08-19. **Status:** Pinned. Amends ADR 054's reach, not its rule. Amends ADR 119's `v_screen_live` predicate.

**Context.**

`v_screen_live` filtered `is_cluster_head IS NOT FALSE`. That predicate was correct intraday and wrong the next morning, and the gap between the two is visible to anyone watching.

The poller **cannot** cluster: ADR 054's gap window needs the whole session, which does not exist at 09:35. So its rows carry NULL, `IS NOT FALSE` admits them, and every fire is on the screener while the session runs. Overnight `cscan events` clusters, the repeats become `false`, and they disappear.

Measured on Thursday 2026-08-06: **19 confluence fires, 4 heads, 15 repeats.** A reader watching live saw 19 and came back the next morning to 4, with nothing on the page accounting for the other 15. The report that prompted this was "i remember having way more".

**Decision.**

The predicate leaves the view. `is_cluster_head` is still projected, so nothing is lost, and the screener shows every fire with repeats marked.

**ADR 054 is untouched.** Clustering exists so a name hugging a band for three weeks is not counted as fifteen independent observations — that reasoning is about *measurement*, and `v_screen`, `cell_stats`, `benchmarks` and the rest keep it. What this ADR says is narrower: **a feed is not a sample.** The screener answers "what happened today", and the second day of a cluster happened.

Two things fall out and both are improvements.

**The `v_universe` join goes too.** The screener query joined `v_universe` and filtered `u.in_trade`, which is `DISTINCT ON (ticker) ORDER BY as_of DESC` — membership *now*. So a historical row whose ticker had since left the universe was silently dropped. ADR 122 put point-in-time `in_trade` on the event itself, which answers the right question: was it tradeable *on that bar*. The join was redundant and worse.

**The neighbour and calendar queries take the same predicates.** A date the arrows or the calendar call populated must be populated under the filters the reader is using, or a click lands on an empty screen.

**Consequences.**

Row counts on historical dates rise. On 2026-08-18: 5 poller rows plus 3 repeats the nightly added, where before only the heads showed. That is the intended effect and it makes the page agree with the poller's own log.

**A poller CSV and the screener are still not comparable across a config change**, and this ADR does not fix that. Measured on 2026-08-05: the CSV records 30 confluences detected under `1835688bf7d760ba`; the live config finds 13 on the same bars, because ADR 110's `require_fast_agreement` refuses to call it a confluence when raw and smoothed %K differ by more than `fast_agreement_tol`. NUE that day was 99.1 against 81.8. Both records are honest; they answer questions asked under different rules.

**Rejected.**

*Keep heads-only and add a toggle, defaulting to heads.* The safe change, and it preserves the defect: a reader would still watch 19 become 4 overnight unless they knew to flip something. The default is the thing that was wrong.

*Cluster the poller's rows intraday.* Impossible without the session, which is ADR 054's own reasoning.

*Show heads and count the repeats in a badge.* Considered. It compresses well and it hides which ticker repeated, which is the part a reader wants when a name fires three times in a morning.

---

## 125. A migration carries its SQL as a literal, never as an import

**Date:** 2026-08-19. **Status:** Pinned. Corrects a pattern used by seven existing migrations.

**Context.**

`jobs/views.py` holds each view's DDL as a module constant, and migrations imported them:

```python
from capitalscan.jobs.views import V_SCREEN_LIVE_DDL

op.execute(V_SCREEN_LIVE_DDL)
```

That reads as DRY and is a latent break. **The constant is the current definition; a migration is a statement about one point in history.** The two are the same only until the next time the view changes.

ADR 122 added `events.in_trade` in `f2d16b47c093`. Four migrations that run *before* it had imported `V_SCREEN_LIVE_DDL`, and all four immediately began emitting `AND e.in_trade` against a table without the column. Every from-scratch replay died:

```
ProgrammingError: column e.in_trade does not exist
```

**It could not have been caught locally, and that is the important part.** A developer applies only the new migrations; the broken path is never taken. Only a full replay from an empty database hits it — which is precisely what CI does, and what a new deployment would do. It ran green on the developer machine and failed on the first CI run.

**Decision.**

A migration carries the SQL it emits as a **literal**, named `_*_AT_THIS_REVISION`. It may import from `jobs/views.py` only a constant whose name pins it to a superseded form — the existing `_PRE_115`, `_PRE_119`, `_PRE_120` convention — because those exist to record something that has already stopped changing.

`test_migrations_freeze_ddl.py` parses every migration's imports and refuses a live name. A second test asserts that anything named `_AT_THIS_REVISION` is `literal_eval`-able, so the name cannot be attached to a computed value and keep the defect.

**Applied to all seven**, not only the four that broke. The other three — `a3c8e15d40b7`, `c4a7e91b53d8`, `d7f4b91c26ea` — were the same defect waiting on the next edit to `v_ticker_state`, `v_chart`, or `v_positions`.

`d7f4b91c26ea` also called `serving_config_values()`, the live builder, to seed the settings row. Frozen to a literal dict for the same reason: a field added to `ExitParams` would change what a past migration inserts, and a field removed would make it insert a key the frozen upsert does not name.

**Consequences.**

The duplication is real and is the point. `views.py` and the migrations will diverge, and that is correct — one describes now, the others describe then.

**Verified by replay, not by reading.** A scratch database, `alembic upgrade head`, and an md5 over `pg_get_viewdef` for every view compared against production: `fac6d6b13ea438277600a88f8d6dfc0e` both ways. Then `downgrade -4` and back up, same digest.

That comparison is worth keeping as a habit. It is the only check that proves the chain reproduces the live schema, and no amount of reading a migration establishes it.

**Rejected.**

*Version the constants in `views.py` — `V_SCREEN_LIVE_DDL_V3`, `_V4`.* Keeps them in one file and puts the history somewhere nobody reads it. It also leaves the trap intact: the unversioned name still exists and is still importable.

*A CI step that replays migrations from empty.* Worth adding on its own merits and does not replace this. It catches the failure; the test catches the *cause*, names the file, and says what to do.

*Generate migrations from a schema differ.* A larger change, and it does not address this: a generated migration that referenced a live constant would have exactly the same problem.

---

## 126. `v_stats` projects `arm`

**Date:** 2026-08-19. **Status:** Pinned. Closes a gap ADR 105 opened.

**Context.**

`v_stats` predates `cell_stats.arm`. ADR 105 added the column to separate a *measured* arm from a *recommended* one; the view was written before that and DESIGN §8.2's DDL still omitted it.

So the serving surface for statistics could not express the one predicate that keeps the control and benchmark arms out of a rate.

**Nothing caught it because every path that mattered carried its own copy of the filter.** `v_screen` and `v_screen_live` join `cell_stats` directly and pin `arm = 'signal'` themselves, so the view nobody had queried yet was the only one missing it.

**Found by running the page.** `web/lib/screen.ts` filters `arm = 'signal'` on `v_stats`, so every `?stats=1` request returned `column "arm" does not exist` and rendered the error state. **The screener's statistics toggle had been broken since Session 17 shipped** — `states.test.tsx` covers the component with a `CellStats` fixture and never runs the query behind it, which is exactly why a fixture test is not a substitute for one round trip against the real view.

It surfaced now rather than later because `/research` needs the same predicate for its cell grid.

**Decision.**

`arm` is projected, positioned with the other grain columns rather than appended.

`live.test.ts` gains a round trip: `screen({ withStats: true })` against the live view, asserting that a returned cell is either suppressed with a reason or carries `n_eff`, both interval bounds, and a q-value — invariant 8 checked against the database rather than against a fixture.

**Consequences.**

`DROP`/`CREATE` rather than `CREATE OR REPLACE`, which cannot insert a column mid-list.

The lesson generalises and is worth stating: a view is only as tested as its callers, and a caller that is tested against a fixture tests nothing about the view. Every serving view now has at least one live-database assertion.

---

## 127. The poller clock is timezone-aware

**Date:** 2026-08-19. **Status:** Pinned.

**Context.**

`poll.py::_now_et` returned a **naive** datetime holding ET wall clock. It is written to `signal_reports.fired_at` and `quotes_live.ts`, both `timestamptz` on a database running `Etc/UTC`, so Postgres read the naive value as UTC and stored an offset it never had. Every poller timestamp landed four hours early, five under EST.

One moment, two tables, measured 2026-08-13:

    runs.started_at          13:30:41+00   (SQL wrote it: correct)
    signal_reports.fired_at  09:30:43+00   (the poller wrote it)

Two seconds apart in reality, four hours apart in storage. On the screener a 09:30 market open rendered as **05:30**, which is how it was noticed.

`wait_and_poll.ps1` compensated with a three-step conversion — strip the false UTC label, reinterpret as ET, convert to Pacific — so **its CSV was correct while the page was not**. Its own comment said: *"delete this chain if `_now_et` is fixed to return an aware datetime; it becomes wrong the moment the stored data is right."*

**Decision.**

`return datetime.now(ZoneInfo("America/New_York"))`.

The manual offset arithmetic goes with it. `time.daylight` is true when a zone *observes* DST at all, not when DST is *in effect*, so `et_offset = -4 if time.daylight else -5` was an hour wrong every January in any DST-observing zone.

**The three changes are coupled and must land together**: the clock, the backfill of stored rows, and the script's chain. Fixing any one alone moves the operator's CSV four hours. This was briefly committed apart and reverted before it bit — nothing broke only because PowerShell had read the script at startup.

**Consequences.**

`scripts/backfill_poller_timestamps.py` corrected **1,752 rows** — 876 `signal_reports`, 876 `quotes_live`. It refuses to write while a poll run is `running`, because it cannot tell an uncorrected row from a correct one; the dry run is not blocked, since a plan you cannot see until the moment of applying it is a plan nobody reads.

Verified from both consumers afterwards: the CSV query reads `06:30:40 PT` and the screener reads `09:41` ET for the same session.

**It broke the scheduler, correctly.** `scheduled_runs._scheduled_for` compared the now-aware value against a naive `datetime.combine` and the poller died on its first tick with `can't compare offset-naive and offset-aware datetimes`. That is the failure worth having — the alternative is a naive comparison silently treating ET as UTC, which is this bug one module over. Every candidate now inherits `as_of`'s tzinfo; a naive caller is unaffected.

---

## 128. Today's session is a candle in its own table

**Date:** 2026-08-19. **Status:** Pinned. Adds `bars_live`. Does not amend invariant 3 — it exists to keep it.

**Context.**

The ticker chart stopped at yesterday's close, because `bars` gains today's row only after the nightly ingest.

**The data was already being fetched and thrown away, twice.** `fetch_quotes` calls Yahoo's batch quote endpoint with `params={"symbols": ...}` and **no field restriction**, so every response carries `regularMarketDayHigh`, `regularMarketDayLow` and `regularMarketVolume`. `_QUOTE_COLUMNS` kept four fields and dropped the rest at parse time. Then `quotes_live` rows were written **inside the breach loop**, so only a ticker that fired got one at all — measured 2026-08-19, 128 rows across 86 tickers, at most 2 per ticker.

So a live candle needed no new data source and no new request. It needed the parser to stop discarding, and the writer to run before the filter.

**Decision.**

`bars_live`: one row per `(ticker, session_date)`, upserted every tick.

**A separate table, and that is the entire design.** The obvious place is a partial row in `bars`, and it would be a silent look-ahead bug. `cscan indicators` computes on whatever is in `bars`; a partial row gets an indicator row; `run_events` and `poll` read the latest indicator *strictly before* the bar — so today's unfinished values become tomorrow's t−1. Every band would tighten around a price that had not finished happening, every stochastic would read a close that was still moving, and **nothing would raise**.

**Rejected: an `is_partial` flag on `bars`.** Smaller, and it needs eighteen readers to carry a predicate that fails silently when one forgets. ADR 122 is the cautionary case — that guarantee moved from one place to eighteen and needed a lint test to hold it. A separate table needs no predicate anywhere.

**One row per session, not a tape.** Yahoo's `regularMarketDayHigh`/`Low` are cumulative session extremes rather than interval values, so each tick overwrites with strictly better information. Nothing accumulates, nothing reconstructs a gap: a poller down for an hour still writes the correct session high on its next quote. ~141 rows a day instead of ~11,000.

**`close` is `NOT NULL`, everything else nullable.** `close` is the current price and a row without one describes nothing. A pre-market quote can carry a price and no high, and invariant 4 says absent stays absent — a high defaulted to the price would assert a range that never happened.

**Consequences.**

The chart appends the partial candle in the data layer rather than unioning it into `v_chart`. The view joins `indicators`, so a row with no indicator row would arrive with null bands that read as a data gap rather than an open session; and keeping the join out of SQL keeps `bars_live` invisible to anything that computes an indicator.

It renders **hollow** — transparent body, coloured border — as a second candlestick series, because body colour is per point in `lightweight-charts` while border and wick styling is a series option. Its bands and stochastic are null and the lines simply stop at yesterday. That is correct rather than missing: yesterday's bands are exactly what a live signal compares against.

Across the nightly handover both rows exist for a moment. The closed bar wins — it is settled and it carries indicators.

`test_bars_live_isolation.py` holds the separation: no module under `core/` or `research/` may name `bars_live`, only `poll.py` and `views.py` may under `jobs/`, and the poller's `db_io.upsert` targets are parsed to assert `bars` is not among them.

**A second bug fell out of the same work.** ADR 127 made `_now_et` aware, and `scheduled_runs._scheduled_for` compared it against a naive `datetime.combine` — the poller died on its first tick with `can't compare offset-naive and offset-aware datetimes`. That is the right failure: the alternative is a naive comparison silently treating an ET wall clock as UTC, which is the four-hour bug ADR 127 had just fixed, reappearing one module over. Every candidate now inherits `as_of`'s tzinfo, and a naive caller still works unchanged.

---

## 129. `in_trade` fails closed: no universe evaluation means not tradeable

**Date:** 2026-08-19. **Status:** Accepted, not yet executed. Closes ADR 122's open item. Amends `core/universe.py`.

**Context.**

`core.universe.in_trade` returns `True` when no universe evaluation exists on or before the bar — the documented v1 simplification, so `run_events` worked before `run_universe` had ever run for a name.

**It is not only the pre-2010 window, which is how it was first measured and reported.** The check is per *ticker*: a name with no snapshot on or before a given bar fails open on that bar, so a ticker that entered the universe late fails open across all its earlier history. Measured on the live config's `touch` slice:

| Split | Events | Tickers | Range |
|---|---|---|---|
| train | **18,805** | 566 | 2010-01-04 → 2021-09-23 |
| validate | 45 | 3 | 2022-02-18 → 2023-12-29 |
| holdout | 35 | 3 | 2024-09-13 → 2025-03-21 |

18,805 of 158,156 in-trade events is **11.9% of the training population**. An earlier note recorded 17,919 and said validate and holdout were clean; both were artifacts of measuring only `signal_date < 2010-03-31`.

Across every config and entry kind the same predicate covers **1,672,092 rows**.

**Decision.**

Fail closed. No evaluation on or before the bar means not tradeable.

**Rationale, in the user's words: "why train on tickers I'm not trading".** The training population is supposed to be the trade universe; a name admitted because the universe job had not reached it yet is in the sample by accident of scheduling, not by criteria.

**ADR 122 makes this cheap.** Membership is now *recorded* on the event rather than *filtering* detection, so flipping the default changes a stamp rather than deleting rows. The 18,885 events stay in the table with `in_trade = false`, and every statistical consumer already carries the predicate that excludes them (ADR 122, `test_events_in_trade_filter.py`). Nothing needs a new filter and nothing is destroyed — the events remain visible on the ticker page, which is what ADR 122 built.

**Consequences.**

**Every measured cell moves, on all three splits.** `cell_stats`, `benchmarks`, `rho`, `peak_labels` and `reachability` all re-derive from a population 11.9% smaller on train. ADR 112's result — zero cells surviving FDR correction — must be re-established rather than assumed; it is likely to hold and is not entitled to.

**Holdout is touched, and that needs care.** 35 events across 3 tickers. Holdout is evaluated once and published whatever it says (ADR 019/033); re-stamping is a change to the *population definition* rather than a look at the data, which is legitimate, but it must happen before any holdout evaluation rather than after.

**Execution.** Change the fallback in `core/universe.py`, then re-run `cscan events` for the full window — roughly three hours, and it must not overlap the poller. Then `cscan cell-stats` and the benchmark arms. The `cell_stats` digest is expected to move; that is the point, and it should be recorded before and after.

**Rejected.**

*`UPDATE events SET in_trade = false WHERE ...`.* Same end state in seconds, and it makes the stored value disagree with what the code would derive. The next rebuild would silently revert it. Re-deriving is slower and is the only version that stays true.

*Keep failing open and document it.* What ADR 122 did, as a holding position. The user's reasoning retires it: a training population that includes names outside the trade criteria measures something other than the strategy.

---

## 130. The chat route holds the tool loop, and holds nothing else

**Date:** 2026-08-19. **Status:** Pinned. Implements ADR 118 for `/chat`. Does not amend ADR 070 — no Python runs in the request path.

**Context.**

ADR 118 settled where `/chat` reads from: the MCP server, which calls the handlers, which validate. That leaves the harder question, which is what the route is allowed to *hold*. A chat route is a natural place to accumulate things: a copy of the tool schemas so the model can be given them, a system prompt string, a parse of the tool result so the answer can be shaped, a rounding helper so a rate reads as a percentage. Each is one line and each is a second copy of a contract that already exists.

**Decision.**

Four things the route does not hold, each with the thing that would otherwise drift.

**No tool schemas.** They are read from the server on every request via `listTools`. `mcp/tools.py::SplitArg` has two members and `holdout` is not one of them; a copy in TypeScript would be a second contract, and the way a copy fails is that it keeps working after the original changes. `test_mcp_schemas.py` already asserts the schema is generated from `core.types.SignalType` — reading it at runtime extends that guarantee to the web layer for free.

**No prompt text.** `lib/prompt.ts` reads `docs/SYSTEM_PROMPT.md` and extracts the fenced block under `## The prompt`. The document is where the reasoning behind each rule lives, and Session 18.3 requires the prompt's changes to be reviewable — which holds only while there is one text. The loader **refuses to start** on a prompt that no longer names ADR 112's result, whitespace-normalised so a reflowed paragraph does not read as a deletion. Gate item 6 is enforced rather than checked.

**No parse of a tool result.** `contentToText` joins the server's text blocks and nothing else touches the payload. It reaches the model byte-identical, which is how `n_eff`, the interval, and the q-value survive: not by being carried carefully, but by never being read. A `JSON.parse` round trip would narrow a `numeric` the database stores with more precision than a double holds — the same reason `lib/db.ts` has one `num()` rather than a `parseFloat` per component. A test asserts the module never calls `JSON.parse`, over source with comments stripped, because the comment explaining the rule failed the test checking it.

**No arithmetic, and no arithmetic to reach for.** DESIGN §10.2 forbids the model computing; the loop has no calculator to offer and the handlers return finished numbers.

**Conversation state across a request boundary is text only.** `sanitizeHistory` drops every block that is not a plain string and every role that is not user or assistant. Tool results exist inside one call to `runTurn` and nowhere else, so a browser cannot post a fabricated `tool_result` and have it read with a tool's authority. This is a security property rather than a tidiness one, and it costs the model its tool context between turns — the right trade, because the alternative is trusting the client with the one thing on the page that carries the handlers' guarantee.

**A tool budget of eight per turn, with a documented ending.** At the limit the tools are withdrawn — one final turn with `tool_choice: none` — rather than the request being cut off. The model answers from what it has or says it cannot; both are honest, and neither requires it to invent the fetch it did not get. Every `tool_use` block still receives a `tool_result`, because the API rejects the next request otherwise, and the refusal in that slot names the limit and says *do not estimate the missing number*. The transcript says so too: a reader who cannot see that an answer stopped early has no way to tell it from one that was complete.

**Consequences.**

`tests/boundary.test.ts` gained a carve-out. Session 17's rule was "no web module calls the MCP server", correct while no LLM caller existed; ADR 118 always had two halves and only one was testable. Six files are now exempt, listed by name rather than matched by directory, and the exemption is **asserted from both sides** — a test checks that `lib/mcp.ts` still reaches the server, so deleting the client and querying Postgres from `/chat` cannot leave the suite green by simply not using the exemption.

**A duplicate key in `.env.local` broke this before any of it ran.** The file assigns `MCP_BEARER_TOKEN` twice with different values. `python-dotenv` takes the last, so `cscan mcp serve` came up on one token; `next.config.ts` took the first and skipped every later line for that key, so `/chat` presented the other and got a flat 401 — which the MCP SDK surfaces as a transport failure, reading like a server that is down. Both readers were behaving correctly and neither said the file was ambiguous. The parser moved to `lib/env.ts` (testable, which a config file is not), now resolves last-wins to match `python-dotenv` exactly, and prints the duplicated keys at startup. The process environment still wins over the whole file, so an injected secret is never overwritten.

**Live verification is opt-in, in `tests/mcp.live.test.ts`.** Fakes cover the loop; they cannot show that the generated schemas are ones a model can be given, or that a holdout request raises rather than merely being absent from a `Literal`. Six assertions against a running server, skipped unless `MCP_LIVE=1` so CI stays hermetic. Writing it found that `get_stats` refuses `target_pct=3` and names the four measured values — the handler validating, which is the layer this whole ADR exists to avoid duplicating.

---

## 131. What moves during a session is polled by the client, not re-rendered by the server

**Date:** 2026-08-19. **Status:** Pinned. Completes ADR 128.

**Context.**

ADR 128 put today's session in `bars_live` and drew it on the chart. It rendered correctly and then never changed again.

Every page in this app is a server component with `dynamic = "force-dynamic"`, which means *rendered per request* and not *kept current*. The poller rewrites `bars_live` and `quotes_live` every five minutes; a tab left open all session showed a candle and a live price that were both right when they were drawn and progressively wronger after, **with nothing on screen saying which**. Reported by the user, 2026-08-19: "I don't think the live prices are updating properly in the graph view."

That is the worst shape of stale. `meta.stalenessDays` exists because a database that stopped being updated has to say so; a number that is 40 minutes behind and looks identical to one that is current says nothing at all.

**Decision.**

`GET /api/live?sym=&bar=` returns today's partial candle and the last quote. Two client components poll it every 45 seconds and update in place.

**One endpoint for both, because they must move together.** Refreshing the candle alone would put a close on the chart that disagrees with the number printed above it, and two numbers that disagree read as broken data rather than as a race between two fetches. Two numbers stale in agreement are merely old.

**45 seconds against a five-minute write.** Deliberately faster than the writer rather than matched to it: matching would put the worst-case age at ten minutes. The cost is a primary-key lookup on a 139-row table.

**Only while the tab is visible**, with an immediate refresh on `visibilitychange`. A backgrounded tab polling forever is a request every 45 seconds against a database `cscan backtest` also wants (ADR 039), and returning to the tab is then current rather than up to a poll behind.

**`update()` on the two series that carry it, not a repaint.** `setData` across eight series to move one candle rebuilds the price scale on every tick, and the reader is usually mid-drag somewhere else on the chart.

**Rejected: `revalidate` on the route.** It re-renders the whole page — the chart remounts, the loaded history is discarded, and a reader who dragged back to 2019 is returned to today every 45 seconds. The stale thing is one candle and one number; re-rendering an event table and a 1,300-bar series to move them is the wrong unit.

**Rejected: a websocket or SSE push.** The write cycle is five minutes. A push channel would hold a connection per open tab to deliver twelve messages an hour, and the failure mode of a dropped socket is silence, which is the bug being fixed.

**Consequences.**

`LivePrice` is a new client component, and the ticker page's client boundary is now six files rather than four. That list is asserted whole in `boundary.test.ts` so adding one stays a decision.

The chart's footer says **when** it last re-read, in the accent colour. That line is the actual fix. Polling makes the number current; the timestamp is what makes a *failed* poll legible — the price holds at the last good value and the clock beside it stops advancing, which is an honest report rather than a frozen number pretending to be live.

`liveBar` was extracted from `chart()` so the first paint and every refresh return the same shape from the same query. A second read written separately is a second thing to keep in agreement, which is how the header and the candle would have started disagreeing.

`quotes_live` is still written inside the poller's breach loop, so `quote` is null for most tickers and the header simply omits the live price. ADR 128 fixed exactly this for bars and not for quotes. It is a two-line change to `_write_live_bars` and it is not made here, because it needs a poller restart and the poller is running.

---

## 132. The caller names the grain; the default is the compatible one

**Date:** 2026-08-19. **Status:** Pinned. Closes backlog item 2. Amends no ADR — `GRID_ENTRY_KIND` is unchanged.

**Context.**

`/chat` answered "what fired today" with **2026-08-13** while the poller had already recorded 63 events on 2026-08-19. The model named the date it used, so the answer was honest; it was still the wrong answer to the question.

`handlers/screen.py` read `v_screen` unconditionally. That view is `entry_kind = 'next_open'` and only `cscan backtest` writes it, so it ends at the last backtest. `v_screen_live` is the `touch` grain the poller writes continuously and reaches the current session. The screener has a `trails bars by N` badge for exactly this gap; a chat route has no layout to put one in.

**Decision.**

`screen_signals` takes `grain`, one of `next_open` or `touch`, defaulting to `next_open`.

**The default is the compatible one, not the most recent one.** MCP clients outside this repository call this handler. A default that silently moved them to a different entry timing would change every outcome they had already reported, and would do it without an error anywhere. The caller who wants today says so.

**The view is a dict lookup keyed by a parsed enum, never interpolated.** `_BASE` is a format string with a `{view}` hole, which is the shape that would be an injection site if caller input reached it. `parse_grain` runs first and only two keys exist; a test passes `grain="v_screen; DROP TABLE events"` through the argument and asserts the refusal.

**Rejected: switching the default to `touch`.** It answers "what fired today" correctly and answers "what did this historically do" against a population no statistic was computed on.

**Rejected: a freshness badge in the tool result.** It puts the fix in the model's reading of a field rather than in the query, and `meta` already carries staleness — which describes the *database*, not the feed, and was correct throughout the bug.

**What was checked and turned out to be a non-issue.** The stated risk was that `touch` rows would pair with statistics measured on `next_open`. `v_screen_live` already joins `cell_stats` on the literal `entry_kind = 'next_open'`, deliberately and documented, because those are the only cells measured. A live row's statistics describe what that *kind* of setup historically did, identically on both grains. Now asserted by test rather than assumed.

**Consequences.**

`v_screen_live` gained `bb_pctb` (migration `c1f7d92a6b45`) — the one column that differed between the two feeds. A handler whose row shape changes with the grain would be two contracts wearing one name.

The MCP tool description states the tradeoff, because the model is the caller most likely to want `touch` and least able to discover it: *"`grain` decides how recent the feed is, and the default is not the most recent one."*

---

## 133. The ticker history reads a grain that is defined for every signal type

**Date:** 2026-08-19. **Status:** Pinned. Adds `v_ticker_events`.

**Context.**

Reported by the user: old events, horizon long past, still showing a blank entry and exit. Measured across in-trade cluster heads at the `touch` grain:

```
stoch_overbought  11,780 rows  11,780 with no entry
stoch_oversold     7,438 rows   7,438 with no entry
bb_upper_touch     8,876 rows       0 with no entry
confluence_low     1,377 rows       0 with no entry
```

19,218 of 41,065 — **47%** — and every one a stochastic signal.

**Not a data fault.** A `touch` entry means *enter at the level you touched*. A stochastic threshold crossing has no level, so `touch_level` is NULL on every such row by construction, no entry price can be assigned, and no exit can follow. The same events carry complete outcomes at the other three grains, where entry does not need a band:

```
AMZN 2024-12-31 stoch_oversold
  next_open  entry 2025-01-02 @ 222.0966  exit 2025-01-10 @ 218.94  timeout  −1.48%
  touch      —                             —                         —        —
```

The page read a grain undefined for two of the seven signal types and rendered the undefined half as an em-dash — indistinguishable from data that should be there and is not.

**Decision.**

`v_ticker_events`: `next_open` rows, union the `touch` rows that have no `next_open` sibling.

`next_open` is `GRID_ENTRY_KIND`, the grain every Phase 4 statistic used, so the history's outcomes now match the numbers the screener quotes. That closes the "same event, two entries, two outcomes" wart the previous code apologised for in a comment.

The union adds back the poller's fires from sessions `cscan backtest` has not reached — 246 of 41,065. Reading `next_open` alone would drop today's fires off the page, which is the row a reader most wants.

**Marker alignment survives.** `v_chart` marks bars at `entry_kind = 'touch' AND is_cluster_head IS NOT FALSE`, and `(config_hash, ticker, signal_date, signal_type, entry_kind)` is the natural key — so every historical touch row has an exact sibling and a marker with no history row under it stays impossible.

**Three states, and the first cut got that wrong.**

`pending` initially meant "no `next_open` sibling", which counted **47,310** rows where it should have counted 246. The other 179,286 are `in_trade = false`: events ADR 122 records so they stay visible here, and that the backtest deliberately never measures. Labelling those "not yet backtested" promises a number that is never coming — a different lie from the blank it replaced, but still a lie.

Corrected in `a4b8f2e619c3` twenty minutes after `a8d3e5c17f92` applied. `pending` is `in_trade AND no sibling`; `in_trade` is projected beside it; the page distinguishes **measured**, **not backtested**, and **outside universe**.

**Found by verification, not by a test.** The count after applying was 47,310 against an estimate of 246, and the two orders of magnitude were the whole signal. Nothing in the suite would have caught it: the view was valid, the join was right, and every row rendered.

**Consequences.**

78 in-trade rows still settle with no entry. 77 are the last-backtest boundary — the entry is the *next* open, which had not happened when the backtest ran — and self-heal. The one historical is AET 2018-11-29, acquired by CVS days later, where no next open ever existed. Both are correct and neither is worth a fourth state.

---

## 134. A live price exists only during the session

**Date:** 2026-08-19. **Status:** Pinned. Adds `market_is_open()`. Completes ADR 131.

**Context.**

Reported by the user: TSLA showed **349.58** as its live price at 15:25 PT against an official close of **351.12**.

```
bars_live  TSLA  last tick 15:55:46 ET  349.58
bars       TSLA  official close         351.12
now                          18:25 ET
```

The poller's final tick landed five minutes before the bell and TSLA moved $1.54 after it. `bars_live` was correct and the page was presenting a session snapshot two and a half hours after the session ended, with nothing on screen to say so.

ADR 131 fixed *staleness within* a session by polling. It did not ask what a live price means once there is no session, and the answer is that it means nothing: after 16:00 ET the poller stops, the row freezes, and the label keeps claiming currency the number no longer has.

**The date half was already handled and the clock half was not.** `market_date()` is the ET calendar date, so a weekend or holiday leaves `session_date = market_date()` unmatched and the price disappears by itself. A weekday evening matches — which is every evening, on every ticker.

**Decision.**

`market_is_open()`: ET `09:30 <= now < 16:00`. `live_price` is NULL outside it, on both surfaces.

**The bounds are the poller's**, `poll.py::MARKET_OPEN` and `MARKET_CLOSE`. They exist twice and cannot be shared — one is a Python `time` the poller compares against its own clock, one is SQL a view evaluates — so `test_market_hours.py` asserts they agree rather than trusting two literals to stay in step. That test also pins `AT TIME ZONE 'America/New_York'`: this machine runs on Pacific, and a missing cast would open the session at 06:30 local, which is a plausible-looking number wrong by three hours (ADR 127's failure, one module over).

**The close is exclusive and the open is not**, deliberately asymmetric with the poller's inclusive bounds. The poller may write one tick at 16:00:00 and that print belongs in the bar; a price *labelled live* may not outlast the session.

**`STABLE`, not `VOLATILE`.** Volatile re-evaluates per row, so one screener scan crossing 16:00:00 could give some rows a live price and others none.

**The candle keeps the row.** It is a real record of the session's OHLCV and remains the only representation of today until the nightly ingest writes the closed bar. Only the *price* goes, because "live" is a claim about now and OHLC is a claim about the day.

**Consequences.**

`live_price_ts` survives the guard, so a reader who wants to know when the last tick landed still can. What goes away is the number pretending to be current.

Both clients stop polling once the database reports the session closed. Nothing rewrites `bars_live` until tomorrow, so a timer left running is a query every 45 seconds for a row that cannot change for seventeen hours.

**Half-days are not modelled.** The market closes at 13:00 ET on roughly nine afternoons a year, and on those the live price stays visible for three hours after trading ends. Modelling them needs a holiday calendar carrying session lengths, which nothing here has. Recorded rather than silently accepted.

---

## 135. A universe evaluation must rest on data from inside the period it describes

**Date:** 2026-08-20. **Status:** Pinned. Gives `UniverseParams.rebalance_freq` its first consumer. Does not change `config_hash`.

**Context.**

`jobs.compute._latest_indicator_row` filtered `ts <= as_of` with **no lower bound**, so a ticker that stopped trading kept returning its final row for every later quarter and passed every criterion on frozen data.

AET (Aetna, acquired by CVS 2018-11-29):

```
as_of        in_trade  mcap  above_sma  slope  rel_return
2026-06-30      t        t       t        t        t      <- November 2018 data
2018-12-31      t        t       t        t        t
```

**31 consecutive quarters `in_trade` with no bars behind any of them.** The only ticker in that state, found while checking two backlog items that both turned out to be symptoms of it.

`QuarterNotEnded`'s docstring describes the *mirror* of this defect — an `as_of` in the future silently reading whatever the latest real data is — in the same file, about the same query, without noticing the past-facing version.

**Decision.**

An indicator row older than one rebalance period does not evaluate the quarter. `core.universe.evaluation_max_age_days` maps `rebalance_freq` to days; `_latest_indicator_row` gains `AND i.ts > :floor` and returns `None`, which already means "skip this ticker" — so an unevaluable name simply gets no row, which is what "we did not look" should always have meant.

**Derived from `rebalance_freq`, not declared as a new parameter.** That field existed in `core/config.py` since Session 9 and had **no consumer anywhere** until this ADR. A new `max_evaluation_staleness_days` would have been more explicit and would have changed `config_hash` (ADR 060), orphaning every measured row under the old hash and requiring ~8 hours of rebuild to re-key numbers that are provably identical.

**Provably identical, and this is the load-bearing claim: no event can change.** An event requires a bar; staleness means no bars; the two sets cannot intersect. Measured before the change at **0 rows**, so `events`, `cell_stats`, and ADR 112's result are untouched by construction rather than by luck.

`universe` has no `config_hash` column, so re-running it creates no ambiguous key. `benchmarks` does, and its rows *will* move — recorded below.

**One final quarter of membership is correct, not a rounding error.** AET's last bar is 2018-11-29; at `as_of` 2018-12-31 it traded inside the quarter and stays, and at 2019-03-31 it did not and drops. `core/arms.py` sells at the next rebalance rather than immediately, and this matches that rule exactly.

**Unknown frequencies raise.** A silent fallback would restore the unbounded behaviour this closes.

**Consequences.**

**The benchmark arms were the only thing actually harmed, and the harm was invisible.** `core/arms.py:588` selects `[t for t in members[i] if last_price.get(t, 0.0) > 0]`, and `last_price` persists once a price has been seen — so AET stayed a *priced* member and was **rebalanced into every quarter for seven years** at a frozen 2018 price. In an equal-weight book that is roughly 1/140th of capital that can neither gain nor lose, dragging every buy-and-hold and DCA measurement. `cscan stats benchmarks` must re-run, and this is a candidate explanation for the record-versus-database disagreement then open in `BACKLOG.md`. **It was not the explanation.** Re-measured 2026-08-20: the record and the database never disagreed — both figures were right and neither carried its `config_hash`. Recorded in `RESULTS.md`; the backlog item is closed and gone.

**`ArmWindow.delisted_on` is a dead field.** `research/benchmarks.py:195` selects `WHERE delisted_on IS NOT NULL` to resolve delisted positions, `arms.py:504` documents the mechanism in prose — and `delisted_on` is **read nowhere in the code and written nowhere in the tree**, so all 712 rows are NULL and the query returns zero every time. The mechanism was documented, plumbed, and never connected. This ADR does not fix that; it makes it redundant for the case that mattered. **Connected 2026-08-20**, after this ADR: `is_active` and `delisted_on` are derived from each ticker's `last_bar`, 18 names carry a date, and the sentence above stopped being true that day. `RESULTS.md` has the numbers.

---

## 136. No edge interval is stored, and the rate interval answers the question

**Date:** 2026-08-20. **Status:** Pinned. Rejects DESIGN 11.2's edge bar as specified. Closes the last open item in `BACKLOG.md` 5.

**Context.**

DESIGN 11.2 asks for edge rendered "as a bar with CI width, not a number". `cell_stats` stores `edge` as `p_hit - baseline` and a Wilson interval on `p_hit`. There is no interval on `edge`.

**Decision.**

Do not store one, and do not derive one.

**Deriving is the tempting half and it is wrong.** Differencing the two rates' bounds — `(ci_low - baseline, ci_high - baseline)` — treats the baseline as a constant. It is not: it is itself estimated, from the same ticker's own history, over a window that overlaps the cell's events. The two are positively correlated by construction, so a differenced interval is too wide by an unknown amount and its coverage is not 95% or anything else nameable. An interval whose coverage nobody can state is worse than no interval, because it will be read as one.

**Storing one is possible and buys nothing here.** It means a bootstrap over the clustered event set, a new column, a migration, and a full `cell_stats` recompute. The result would be an interval on a quantity that is already reported: `/research` draws the rate interval with the baseline marked *inside* it, so "does the edge exclude zero" is read directly as "does the baseline fall outside the interval".

**Verified rather than asserted, 2026-08-20**: across every reported cell on the live config, the baseline falls inside the rate interval — **48 of 48 on train, 28 of 28 on validate**. Zero cells separate from their baseline. The edge bar would have shown, for every cell, a bar crossing zero.

**Consequences.**

DESIGN 11.2's edge bar is not built. The rate interval with an inline baseline marker is the rendering, and `/research` already does this.

Revisit only if a cell ever separates from its baseline, at which point the width of that separation becomes a question worth a bootstrap. On the current data that question has no instances.

---

## 137. The served subset is three years, and ADR 053's sizing predates the chart

**Date:** 2026-08-20. **Status:** Pinned. Amends ADR 053's estimate, not its decision. Implements `cscan sync`.

**Context.**

ADR 053 sized the serving store at "roughly 200k rows, under 150MB" and justified it: *"The serving layer never queries a raw bar: the screener reads `events`, charts read roughly 45k indicator rows."*

That was true when written and has not been true since Session 17. `v_chart` reads `FROM bars`, because indicators cannot draw a candle. Measured 2026-08-20, full history:

```
events       1,452,564 rows   1027 MB
indicators   1,847,034 rows    745 MB
bars         1,847,034 rows    367 MB
reference data                  12 MB
                              --------
                              2,149 MB
```

**14x the estimate**, and 4.2x Neon's 512MB free tier. Three features landed after the estimate, each adding a multiple: the candlestick chart made `bars` a serving table, the chart's paging made indicators full-history rather than three years, and ADR 122 records out-of-trade events so the ticker page stays complete.

None of those were mistakes. The estimate simply describes a system that no longer exists.

**Confirmed against the live store, 2026-08-20.** The synced three-year
subset is **410MB, 80% of the 512MB free tier** — `pg_database_size` on
Neon, matching the estimate. A momentary reading of "5 GB" was misread and
briefly recorded here as a correction; it was wrong and is withdrawn. The
limit is 512MB and three years is a constraint, not a preference.

**80% is tight, and `run_sync` never deletes.** The cutoff bounds what is
*sent*, not what is *stored*, so rows that age past three years stay on
serving forever and the store grows with every nightly. See `BACKLOG.md`.

**Decision.**

`ServingParams.history_years = 3`. Measured against the free tier:

```
1 year   139 MB      3 years  393 MB      full  2,149 MB
2 years  266 MB      5 years  638 MB
```

Three fits with 23% headroom, covers every chart range through 1y and most of 2y, and spans the validate split (2022-2023) so `/research` is fully backed. The 5y range degrades by stopping early, which the chart already handles — it pages until the server returns nothing and marks the start of history.

**The cut is in dates, never in answers.** `cell_stats` and `benchmarks` ship whole because they are computed locally against full history. A reader of the deployed site sees fewer sessions on a chart; they never see a different hit rate. This is the property that makes a serving subset acceptable at all — the alternative, recomputing statistics from the subset, would publish numbers no local run could reproduce.

**`ServingParams` is not a `Config` field**, following `BaselineParams`. `jobs.config.config_hash` hashes `dataclasses.asdict(Config)`, so folding a deployment constant in would move the hash for every config already written to `events` in order to name a number the backtest never reads. Invariant 9 holds: the value is in `core/config.py` and `jobs/sync.py` contains no literals.

**Consequences.**

**One direction, always.** Local research is the source of truth; the cloud copy is derived and can be dropped and rebuilt at any time. That is what makes it safe to point a public site at.

**`DATABASE_URL_SERVING` unset raises rather than falling back.** Every other resolver here falls back to the research URL with a warning, which is correct for a read — the worst case is a developer reading local data. Here the worst case is a *write*: a sync that upserted the serving subset into the research store, on top of the rows it had just read.

**Upserts, never truncates, never deletes.** A truncate-and-reload leaves the site empty for the duration, and an interrupted one leaves it empty until the next run — a public page showing no signals is indistinguishable from a day when nothing fired. A row ageing past the cutoff stays until someone prunes it deliberately, because the alternative is a bug in the cutoff arithmetic silently emptying the served history.

**The table list is written down, not discovered.** It came from `pg_depend` on the serving views, but a table appearing in a view is not consent to publish it: `bar_rejects`, `runs` and `quotes_live` are local diagnostics. Adding a table is a deliberate edit, asserted by test.

**`cscan sync --to-serving` is gone.** The stub carried that flag, implying a second destination that never existed — ADR 053 defines exactly one serving store. A flag whose only valid value is `true` is one that eventually gets passed `false`.

---

## 138. The deployment authenticates itself, and opening it is an explicit act

**Date:** 2026-08-20. **Status:** Pinned. Currently disabled by request — see below and `BACKLOG.md`.

**Context.**

`capitalscan.vercel.app` went up carrying the screener, the ticker page, `/research` and `/chat`. Vercel Authentication cannot protect a production URL on the Hobby plan — the API refuses outright:

```
invalid_sso_protection: Vercel Authentication is not available
on your plan for production deployments
```

Enabling it for `prod_deployment_urls_and_all_previews` gated only the immutable deployment URL. The two URLs anyone would actually use, `capitalscan.vercel.app` and the branch alias, stayed open. That is not a lockdown, and the alternative was $20/month for a plan whose other features this project does not use.

**Decision.**

HTTP Basic auth in `web/middleware.ts`, so the control sits where the hosting plan has no say.

**Middleware runs before any page, route handler or server component**, which is the property that matters: an unauthenticated request never reaches `lib/db.ts` and never opens a connection. Authentication that runs after the query has already paid for the thing it was protecting.

**It fails closed.** An unset `SITE_PASSWORD` returns 503, not 200. Treating "no password configured" as "no password required" turns a deployment that forgot a variable into a public one, silently — and that is the exact state this exists to prevent. The site locked itself the moment it first deployed and stayed locked until the variable existed, which is the correct default for a control whose absence is invisible.

**It matches every path**, with two exemptions: `/_next/static` and `/favicon.ico`, both immutable build assets containing no data. A matcher enumerating protected routes goes stale the day someone adds one, and the new route is the unprotected one. `/_next/image` is deliberately not exempt — it proxies arbitrary URLs.

**Comparison is constant-time and every failure is identical.** `===` returns as soon as two bytes differ, so rejection latency leaks how many leading characters were right. A different response for "wrong password" than for "no header" tells an attacker which half they got.

**Opening it is its own variable.** `SITE_AUTH_DISABLED=1`, and only that exact string — `0`, `true` and `yes` are refused, because a truthiness check would make `=0` mean "open", the opposite of what someone typing it intends. It is not "unset `SITE_PASSWORD` means open", because forgetting a password and deciding against one are different intents and only one of them is safe to infer.

**Consequences.**

**Currently disabled**, at the user's request, 2026-08-20: a deployment URL is hard to discover and the content is public market data plus one operator's own analysis — no PII, no credentials, no user model at all. `BACKLOG.md` carries the decision and its reasoning.

**The thing to re-check before that becomes permanent is `/api/chat`.** It spends Anthropic tokens per request and is harmless today only because it reaches MCP on `127.0.0.1` and fails *before* any model call — an open page, not an open wallet. If ADR 118's boundary ever moves, restore auth or carve that route out of the opt-out **in the same change**.

**38 tests**, and the ones that matter are not "a good password works". They are the failure modes: an unset secret, a route nobody remembered to list, six near-miss flag values, and a response that differs depending on why it failed. The route sweep walks `app/` rather than a written list, so a new page joins it automatically.

---

---

## 139. The screener links out to a stored chart, and the indicators live on someone else's server

**Date:** 2026-08-20. **Status:** Pinned.

**Context.**

Reading a signal against a third-party chart meant re-entering `20,2.0` and `14,3,3` by hand, per ticker, every time. None of this project's parameters are a charting site's defaults, so the chart a reader reached for by habit was drawn with the wrong windows unless they remembered to fix it.

StockCharts retired the inline chart-args format that used to encode overlays in a URL. `def/servlet/SC.web?c=TSLA,uu[...][pb20!b50]` now returns nothing, and no current parameter names an indicator. What does exist is their split between symbol and style: a ChartStyle is documented as "everything about a chart except its ticker symbol", stored server-side behind an opaque key.

Measured 2026-08-20: `s=DAL&id=p79286883726` renders DAL as candlesticks with `BB(20,2.0)` and `MA(200)`, over a `Full STO %K(14,3) %D(3)` panel carrying lines at 20/80, from a cookieless request, with no account on either side. Their "Reload with Link" button mints that key and is available to anonymous visitors — the paid feature is *saving* a chart to an account, which this does not use.

**Decision.**

`web/lib/stockcharts.ts` builds the URL. `OpenSelected` puts a checkbox on each screener row and opens the selected charts.

**The interaction is two clicks.** The first arms selection and the checkboxes appear; the second opens what is ticked (user's request). On the common visit nobody is selecting anything, so the default is one button and a table with no checkbox column. The column is always in the server-rendered markup and revealed by a class on the form, so arming costs a CSS state change rather than a re-render of every row — and `display: none` rather than `visibility` keeps a hidden checkbox out of the tab order.

**Candlesticks, stochastic below price** (user's request). Layout rather than measurement, so neither can disagree with a config value and neither is pinned by a test. `p18455069802` is the superseded id: OHLC bars, panel above.

**The chart id is a source constant, not an environment variable.** It decides which indicators a reader sees beside this project's signals, so changing it changes what the page claims and belongs in a diff someone reads. It also lets a test assert which settings are being pointed at.

**`p=D` is pinned rather than inherited.** The stored chart's period lives somewhere this project does not control. A chart that silently became weekly would draw 20-*week* bands beside a signal computed on 20 days.

**Class-share symbols are translated.** The database follows Yahoo and writes `BRK-B`; StockCharts writes `BRK/B`. Asking for `BRK-B` does not error — it returns a 305x176 "not found" image where a 990x664 chart should be. Two of 712 tickers, which is few enough to never notice.

**Consequences.**

**This is a reference to a row in someone else's database, and that is the real cost.** If the stored chart is edited or expires, every link keeps resolving and quietly draws the wrong indicators. There is no error to catch and nothing in this repo changes.

`stockcharts.test.ts` closes the half that is checkable: it reads `core/config.py` and fails if `bb_window`, `bb_std`, `stoch_window`, `stoch_smooth_k`, `stoch_smooth_d`, `stoch_oversold`, `stoch_overbought` or `sma_long` moves away from what the stored chart draws. Sweeping `bb_std` to 2.5 fails there rather than silently mismatching a reader's screen. The fix is to rebuild the chart and move the id, never to edit the expected numbers. The other half — the chart changing while the config holds — is unobservable from here and stays a manual check.

**The table stayed a server component.** The checkboxes are plain form inputs it renders; the client island wraps them without rendering the table, so `/` grew about 1 kB rather than shipping the screener to the browser.

**Browsers allow one `window.open` per gesture.** Opening six tabs opens one and drops five, which reads as a broken button rather than a browser policy, so the refused tickers fall back to ordinary links — a direct click is its own gesture. `noopener` is not passed as a window feature, because Chrome then returns `null` whether it succeeded or was blocked, and that return value is the only way to detect the block; `opener` is severed on the handle instead.

**The numbers will not match, and that is invariant 3.** The screener shows indicators at t-1, the row the signal compared against. StockCharts draws bar t. DAL on 2026-08-20 reads `82.57 / 88.65 / 94.72` on the screener and `82.32 - 88.60 - 94.89` on the chart. Both are right.


---

## 140. The nightly is authoritative for every grain, and the poller's observation is not an entry price

**Date:** 2026-08-20. **Status:** Pinned.

**Context.**

Three jobs write `events`, all upserting on `(config_hash, ticker, signal_date, signal_type, entry_kind)`. `run_backtest` owns `touch_5m`, `touch_30m` and `next_open`. `run_events` hardcodes `entry_kind='touch'` (`jobs/compute.py:721`). So does the poller. Two writers share one grain, and `entry_price` is in `_RUN_EVENTS_UPDATE_COLUMNS`, so the nightly rewrites it every time.

Ruling C4 sanctioned two jobs owning the same column on the grounds that both derive it from the same t-1 indicator row and therefore cannot disagree. That reasoning holds for `run_events` against `run_backtest`. It does not extend to the poller, which is a third writer that means something different by the word:

| Writer | `entry_price` |
|---|---|
| `run_events` | where a touch order **would have filled** — the band level, or the open when the bar gapped through it (`core/returns.py::entry_price_for`) |
| `poller` | what the price **was** when the breach was seen, from a five-minute quote |

A modelled fill and an observation, in one column. Measured on 2026-08-20: of 210 poller-reported touch rows across the history, **46 still carry a `poll` run_id and 164 have been reassigned to the nightly.**

**Decision.**

**The nightly is authoritative for every grain, including `touch`.** The poller's row is provisional — written mid-session against a partial bar, by a process that samples every five minutes — and it yields to the version computed from the completed session.

**The observation is not lost and is not an entry price.** It lives in `signal_reports.state_json -> 'live_price'`, alongside the bands and the open that produced the call, and in that session's `reports/poller/*.csv`, which is written before the nightly runs and never rewritten.

**A `NULL` here is honest.** `run_events` sets no entry price when `touch_level` is `None`, and a `stoch_oversold` signal has no band touch — 30 of that day's 80 rows. "Where would a touch order fill" has no answer for a signal that is not a touch. The poller had put a real observed price in that column, and replacing it with `NULL` is the column becoming correct, not data being lost.

**The objection, and why it does not change this.**

Raised by the user, and it is the right question: the detected price is closer to what you could actually transact. A notification arrives, you act, and you fill near the price that fired it — where filling at the exact band needs a resting limit order sitting at a level you chose in advance.

**The answer is that an entry price is a measurement input, not an execution record**, and the user reached it first: this column exists to train and evaluate, and nothing in the system places an order.

That settles it on reproducibility rather than on preference. ADR 060 and TESTS.md 3.3 require that the same config over the same database produce the same output, asserted by `test_identical_config_identical_output` and run with ordering randomisation on. A poller-sourced entry price is a network quote taken at a wall-clock instant on a five-minute sampling grid. It cannot be reconstructed from `bars` and a config, so it cannot be recomputed — rebuild the table tomorrow and the number is different or absent, with an unchanged `config_hash` asserting it is the same. That is the failure ADR 034's provenance guarantee exists to prevent, reached through a column nobody was watching.

The population makes the same point from the other side. Measurement runs over the full history, and the poller has existed for a few weeks of it — so an observed price is available for a rounding error's worth of rows and undefined for the rest. A field that is real when a background process happened to be running and null otherwise is not a variable a model can be trained on; the presence of the value would encode "was the workstation on that morning".

Three further things follow.

**A modelled series has to stay one model.** `touch` exists to be compared against `touch_5m`, `touch_30m` and `next_open` across the whole history. Observed prices exist only for days the poller ran. Letting them into the grain would make recent rows mean something different from every row before them, and the comparison the four grains exist for would quietly stop being valid.

**The model is not flattering, which was the worry.** Signed against each side, where positive means the modelled fill is the better price:

| Side | n | Model advantage |
|---|---|---|
| long | 24 | **+1.0 bps** |
| short | 26 | **−10.8 bps** |

Roughly neutral for longs and conservative for shorts, because a short fires *above* its band and the poller usually saw a better price than the band the model fills at. The gap between the two is not small — 14.3 bps on average and 107.8 at the extreme — but it does not run in the direction that would flatter a result. One session, 50 rows: enough to answer the worry, not enough to be a finding.

**And nothing measured reads it.** `GRID_ENTRY_KIND` is `next_open`. No cell, baseline or benchmark has ever consulted a touch-grain entry price.

**Consequences.**

**A `touch` row's `run_id` names the job that last wrote it, not the one that detected it.** Invariant 6 is satisfied — the row carries a `run_id` and a `git_sha` — but the provenance question "who saw this first" is answered by `signal_reports`, not by the event.

**Two places hold the live price**, and a reader has to know that. `state_json` is the durable one; the CSV is the convenient one.

**If the notification workflow is ever worth measuring, it needs its own entry kind.** `EntryKind.NOTIFIED`, filled at the price the poller saw plus a reaction delay, sitting beside the other four rather than redefining one of them. That is a real question — it is the only entry timing that describes what a person using this system actually does — and it is a Phase 6 question, not a reason to blur `touch` now.

**It would have to be modelled too**, for the reason above: a reaction delay applied to the bar, not a replayed quote. "The price a notified reader could have got" is computable from `bars` for every event in the history; "the price the poller saw" is not computable for any event before the poller existed. The first is an entry timing. The second is a log line.


---

## 141. The screener sorts in SQL over the whole day, and shows all of it

**Date:** 2026-08-20. **Status:** Pinned.

**Context.**

Two user requests on the same surface, and they turn out to be one decision.

The screener rendered at most `DEFAULT_LIMIT = 50` rows. Measured over 4,108 trading days: the largest day is **134 rows**, the mean 34, the 95th percentile 79. So the cap truncated roughly one day in twenty, and on 2026-08-20 the page said `Showing 50 of 85` while 35 rows sat unreachable behind a parameter nobody types.

Sorting had to decide where to run. In the browser it is instant and needs no round trip — and it would have sorted **the 50 rows the previous ordering happened to select**. "Sort by volume" would then return the heaviest of an arbitrary 50 rather than of 85: a wrong answer that renders perfectly, which is the failure this codebase spends most of its effort on.

**Decision.**

**Every row for the date renders.** `clampLimit(undefined)` returns `null`, which Postgres reads as `LIMIT ALL`. An explicit `?limit=` still works and is still clamped.

**ADR 074's 200-row cap is the MCP tool surface, not this page.** That ADR closes the seven tools' enums and bounds their `limit` argument; ADR 118 makes `/` a separate surface that selects from the views directly. `MAX_LIMIT` stays as the ceiling for a hand-typed parameter, and stops being a default.

**Sorting runs in `ORDER BY`, carried in the URL.** `?sort=volume&dir=desc`, the same mechanism as `?date=`, `?stats=1` and `?all=1`. It sorts before it limits, survives a refresh, and can be shared. Headers are links, so the table stays a server component and `/` grew from 2.65 kB to 2.68 kB.

**The sort key is a closed enum, and that is what makes the interpolation safe.** `feedSql` interpolates its `ORDER BY` — `boundary.test.ts` permits `${order}` by name — and the string it interpolates comes from `SORT_COLUMNS`, a frozen map of twelve entries. A request chooses a *key*; `isSortKey` uses `hasOwnProperty` and rejects everything else, including inherited properties like `constructor`. Nothing a caller sends reaches the query. This is ADR 074's closed-enum discipline applied to a different surface for the same reason.

**Signal order, specified by the user**: confluence above single conditions, band above stochastic, short side above long within each pair. `bear_close_above_upper` was not in that list and takes rank 0 — ADR 057 makes it the most specific type and ADR 111 makes the close-confirmed reversal the actionable short condition, so "high above low" puts it above `confluence_high`.

**Consequences.**

**An unsorted page is byte-identical to before.** No `?sort=` yields `DEFAULT_ORDER`, still confluence rank then `fired_at DESC` then ticker.

**`NULLS LAST` in both directions.** Postgres puts nulls first on `DESC`, which would open a descending sort with the rows that have no value — an absent close is not a large close. `s.ticker` is the tiebreaker everywhere, so equal values never reorder between two renders of the same data.

**Every control carries the sort.** The date arrows, the calendar, the statistics toggle and the confluence toggle all rebuild the URL; any one of them dropping `sort` would reorder the table as a side effect of an unrelated click, and a reader would read the new order as new data. A `clear sort` link appears once a sort is active, because otherwise a sort can be changed but never removed.

**Two columns are ambiguous and were chosen deliberately.** Bollinger is three numbers in one cell and sorts on `bb_lower`; the stochastic is two and sorts on `k_fast`, because ADR 110 makes the raw %K the trigger. Both say so in their `title`.

**The headers do not look like links.** Twelve underlined words above a dense table would be the loudest thing on the page, and the header row is chrome. They inherit the header's own type and colour; only a 7px caret marks the active column, in a fixed-width slot so switching columns does not shift the row.


---

## 142. Agreement means agreeing about the state, not being close

**Date:** 2026-08-20. **Status:** Pinned. Supersedes ADR 044's rule as the sole test. Forces a `config_hash` move and a full rebuild.

**Context.**

ADR 044 defined fast/full Stochastic agreement as a tolerance band: `|k_fast - k_full| <= fast_agreement_tol`, default 5. The user noticed it rejects a case it should accept — %K_fast 99 with %K_full 89 is 10 points apart, so no confluence fires, while both columns are deeply overbought and saying the same thing as loudly as they can.

The rule measures **numeric proximity**. The question it is asked is whether the two columns *agree the stochastic is extreme*. Those come apart in both directions:

| %K_fast | %K_full | Both overbought? | Fired before this ADR? |
|---|---|---|---|
| 99 | 89 | yes | **no** — 10 apart |
| 81 | 77 | no, 77 is not | **yes** — 4 apart |

Measured across the daily indicator history, 2026-08-20:

| | bars |
|---|---|
| both overbought, rejected by the tolerance | **216,298** |
| both oversold, rejected by the tolerance | **140,653** |
| fired anyway with %K_full *not* overbought | 14,728 |
| fired anyway with %K_full *not* oversold | 11,058 |

So the rule was refusing agreement roughly fifteen times more often than it was granting it to a disagreeing pair.

**Decision.**

A second limb, additive. The two columns agree when **either** is true:

1. `|k_fast - k_full| <= fast_agreement_tol` — ADR 044, untouched.
2. Both beyond the same threshold, at any distance apart.

**Additive was the user's requirement**: nothing that fired before this stops firing. The 14,728 + 11,058 rows in the table above still fire on limb 1. This ADR is about what was being missed, not about tightening what was not.

**The second limb reads a side, and that is the part worth pinning.** Proximity is symmetric and needs no direction; "both extreme" does. %K_fast 15 with %K_full 90 has both columns beyond a threshold and is the most opposed pair they can produce — a rule phrased without naming *which* threshold would read maximal disagreement as agreement and fire a confluence on it. `_fast_agrees` therefore takes the `Bound` the price is already being tested against, and `_types_fired` asks it once per side.

**`SignalParams.fast_agreement_both_extreme` is what moves `config_hash`.** The change itself is a comparison inside `core/signals.py`, and `jobs.config.config_hash` hashes `dataclasses.asdict(Config)` — a formula is not a field. Without the flag the hash would not have moved and the backtest would have overwritten 139,253 measured rows in place. This is the fourth time that trap has been reached: ADR 108 through a new enum member, ADR 109 through a changed formula, ADR 115 through a threshold in SQL, and now this. Setting the flag to `False` reconstructs every event measured before today.

**`UniverseParams.min_price` was deleted in the same commit.** Dead config since Session 9 — declared, resolvable, read nowhere. `BACKLOG.md` scheduled its removal for "the next change that moves `config_hash` for a reason that stands on its own", and this is that change. Market cap subsumes it: SBNY trades at $0.64 with a $0.03B cap and fails `crit_mcap` by three orders of magnitude.

`config_hash` moves `86e91448a65aa40b` -> **`bbc99a02ebdc999f`**.

**Consequences.**

**The row count does not change; the labels do.** `detect` returns one hit per side, the most specific type, so a bar that gains agreement is not a new event — its existing row is relabelled from `bb_upper_touch` to `confluence_high`, and `signal_types_all` and `signal_strength` move with it. 10,172 short and 6,606 long events are affected, which is **+59%** and **+71%** on the two confluence populations against an unchanged ~139,253 rows.

**ADR 112 must be re-measured and republished.** Its result — no cell survives FDR correction — was computed over a confluence population this change grows by roughly 60%. It may still hold. It may not. It gets published either way, which is the standing rule for this project's headline result.

**The Postgres GUC moves only after the backtest has written rows under the new hash.** `v_screen` and `compute.scan` both read `capitalscan.default_config_hash`, and pointing them at a config with no events yet returns an empty screener rather than an error — invariant 5b's deliberate behaviour, and the reason the order matters.

**Nine tests**, and the ones that carry the weight are not "99/89 now fires". They are the 15/90 case, which is the only thing separating this rule from one that fires on contradiction; the tolerance limb still firing alone; and the flag-off case, which is what keeps the superseded population reachable from a config rather than only from a database snapshot.


---

---

## 143. The universe is seeded from two sources, and the floor drops to $20B

**Date:** 2026-08-21. **Status:** Pinned. Moves `config_hash` to `f66729c7eda212a4`.

**Context.**

NBIS did not resolve on the screener. It is not excluded by a criterion -- it is Netherlands-domiciled, so it is not an S&P 500 constituent, so it never enters `tickers` and is never evaluated at all. Investigating that turned up something the docs had wrong: **the universe was already not S&P-500-only.** QQQ had been added by hand and was participating fully -- 5,280 bars, 66 universe evaluations, `in_trade` true at $289B, 29,343 events -- with market cap resolved through a Yahoo fallback that already served 68 tickers without a CIK.

So "S&P 500" described where the ticker list was *seeded*, not what the engine accepted. Index membership was enforced nowhere: the four criteria in `required_criteria` read price, SMA200, slope and relative return.

**Decision.**

**A second seed source.** `jobs/fetch/nasdaq.py` reads the exchange screener, which returns every listed security with a market cap in one request, and keeps those at or above `INGEST_MIN_MCAP_USD`. Measured 2026-08-21 across 4,193 listings: 158 clear $30B, 212 clear $20B, 336 clear $10B, 529 clear $5B, and rank #1000 sits at **$1.60B**. A "top 1000" rule and a market-cap floor are therefore not the same request, and the floor is the one that binds.

**The ingest floor is $5B and the trade floor is $20B, deliberately apart.** `min_mcap_usd` decides tradeability quarter by quarter from point-in-time shares and price; the ingest floor decides only which tickers are worth holding bars for. A name at $6B today may have been above $20B earlier in the window, and ingesting it is the only way those quarters can ever be measured. Equal floors would silently delete that history.

**`min_mcap_usd` 30e9 -> 20e9**, the third value ADR 014's threshold has taken.

**Preferred series, warrants and units are excluded.** The screener gives every security the *issuer's* market cap, so AGNC Investment appears seven times -- the common plus six depositary-preferred series, each reporting the same $10B+. 29 of 176 new names were this shape. BTSGU is the clearest case: a "Tangible Equity Unit" whose reported cap ($38.8B) exceeds its own common's ($11.8B). **Ordinary ADRs survive**, because ADR 011 admits non-US exposure through exactly those, and matching on "depositary" alone dropped all 16 of ARM, ARGX, BNTX and the rest on the first attempt.

**Consequences.**

**Survivorship is weakened, knowingly.** The S&P union is *complete* -- every historical member, including the failures -- which is what ADR 035 depends on. A current-listings snapshot is a list of things that survived, so a company worth $60B in 2013 and $400M now never enters and contributes nothing to 2013. The user accepted this, and the reasoning is worth recording because it is not merely a shrug: `crit_above_sma200` and `crit_sma200_slope` exclude names in structural decline, so a Cisco-2000 collapse leaves the trade universe as it falls rather than sitting in it; and the model is trained on percent returns rather than prices. The $5B ingest floor is the other mitigation -- it carries names currently *below* the trade floor precisely so the quarters when they were above it remain measurable.

**The expansion was inert until shares ran, and nothing said so.** `cscan universe` succeeded across 66 quarters with 296 rows carrying a NULL market cap -- every new ticker failing `crit_mcap` for want of `shares_outstanding`. No error, no warning, and the only tell was a count nobody prints by default. The order is: bars, indicators, **shares**, then universe.

**Scale after ingest**: 1,029 tickers (929 active, 316 tagged NASDAQ), 1,024,046 new daily bars from 2005-10-11, 58,213 share rows with 79 tickers served by the Yahoo fallback, `null_mcap` 296 -> 2, and **541 tickers ever `in_trade`** against 378 before.

**ORKA is excluded** (`is_active = false`, nothing deleted). Oruka Therapeutics reverse-merged into ARCA biopharma's listing, and Yahoo back-adjusts the whole history through the accumulated reverse splits: $1,632,960 in 2005 decaying smoothly to $30 by 2022. It caused a `numeric(12,6)` overflow that aborted a 34-ticker batch. No reject rule catches it -- `unexplained_split_like_move` looks for a jump, and a back-adjustment cascade is smooth at every consecutive pair.

**`UniverseParams.min_price` was deleted** in the same rebuild window (see ADR 142), dead config since Session 9. Note that a *live* `price_below_min` rule exists in the ingest rejects and fired 5,454 times on the new bars -- same idea, different layer, and unaffected.


## 144. The long side gets its own close-confirmed type, defined dormant

**Date:** 2026-08-21. **Status:** Pinned, **not enabled**. Mirrors ADR 108/109 onto the long side.

**Context.**

ADR 108 added `bear_close_above_upper` and ADR 109 corrected its band to bar *t*'s own:

```
open > close  AND  close >= bb_upper[t]
```

No bullish counterpart existed, and `core/cells.py` said so in a comment: "no bullish counterpart was requested and ADR 106 already rejects long/short symmetry as an assumption." One was requested on 2026-08-21.

**Decision.**

```
close > open  AND  close <= bb_lower[t]
```

A mirror in every respect that could otherwise drift: the same `bear_close_band_lag` field governs both, the same same-day band, the same `_breach` call, the same null-through-warmup treatment. Where the two differ it is only by `Bound` and by the direction of the open/close comparison.

**ADR 106 is not contradicted, and the distinction matters.** That ADR forbids *assuming* the two sides pay alike. Ranking them alike in `_SPECIFICITY`, and giving them matching labels, is a statement about how rows are named -- not about what they return. Nothing here asserts the sides are symmetric in outcome; measuring that is still the open question ADR 016 poses.

**It is defined and deliberately not enabled.** `SignalParams.enabled_signal_types` does not list it, so `enabled_types` never resolves it and `_types_fired` filters it out before it reaches a hit.

**This is ADR 108's trap, used on purpose.** `config_hash` is taken over `dataclasses.asdict(Config)`, and an enum member is not a field -- ADR 108 records that adding `BEAR_CLOSE_ABOVE_UPPER` did *not* move the hash, which was the defect that ADR forced into the open. Here the same property is the feature: the type could be written, tested and reviewed while a five-hour backtest ran under `f66729c7eda212a4`, without invalidating a row of it. Enabling it is a one-line config change that moves the hash deliberately and requires a rebuild.

**Consequences.**

**The signature probe was widened, and this is the session's most consequential edit.** `detect` now reads a sixth bar field. CLAUDE.md calls that probe "the real guarantee" and says never to widen it, so the justification is written into the test rather than left to review:

- The field is a **boolean whose causality was fully resolved in `core/indicators.py`** before `detect` saw the bar -- the category ADR 108 already admitted, not a new one.
- It is **structurally forced**: the flag compares bar *t*'s close against bar *t*'s band, while `ind` carries t-1, so the bar is the only route.
- `FORBIDDEN_PRICES_ON_BAR` is unchanged. `open`, `close`, `adj_close` and `volume` remain unreachable, and that is the half of the pair that does the work.

The revert, if this is judged wrong: remove the field from `PERMITTED_ON_BAR` in both places it is pinned, drop `bull_close` from `_types_fired` and `detect`, and leave the indicator computing but unread.

**The headline grid grows 14 cells to 16**, and the BH family 56 to 64. Two of the new cells cannot fire while the type is dormant, and that costs the other cells nothing: an empty cell falls below `min_n_eff` and is suppressed, and BH corrects over *measured* cells rather than declared ones -- ADR 112 reports 48 measured of 56, not 56 of 56. If suppressed cells ever entered the family, adding tests that cannot produce a signal would make every real cell's q-value worse for nothing.

**The two sides are positionally paired again.** ADR 108 broke that pairing; this restores it. `headline_grid` still iterates the tuples independently and never zips them, so an asymmetry stays expressible.

**The live half mirrors too.** `is_bull_reversal` is `price <= bb_lower AND price > day_open`, and `bull_reversal_state` reuses `ReversalState` rather than defining a second shape. Two fields there are worth knowing: `above_band` means "beyond the band **on this signal's own side**", so for a long state it is true when price is *below* the lower band; and `open_gap_atr` keeps one sign convention, always `(price - open) / ATR`, so *negative* confirms a bear reversal and *positive* confirms a bull one. Negating it per side would make one column mean two things depending on a sibling field.

**A migration exists and was not applied.** `c3f91a70b8d4` adds `indicators.bull_close_below_lower`, nullable, no server default -- a default would populate every existing row with False, asserting a negative that was never measured. It was written while a backtest held the table and `cscan db migrate` takes an ACCESS EXCLUSIVE lock.


---


## 145. Market cap prices split-adjusted closes against split-adjusted shares

**Date:** 2026-08-21. **Status:** Pinned. Extends ADR 014's filter; same defect class as `adr_adjusted_shares`.

**Context.**

`jobs/compute.py` priced every quarter's market cap as:

```python
mcap = shares * float(ind_row["close"])
```

`close` comes from `bars` and is **split-adjusted**. Yahoo re-adjusts the
entire price history whenever a new split lands, so every close is expressed
in today's share basis. `shares` comes from `shares_outstanding` and is the
count **as filed** that quarter. The two agree only while no split has
occurred between the filing and today.

Found while explaining why CHRW carried only two months of events. It does,
and correctly — CHRW is in-trade for one of 66 quarters — but checking the
criterion that decides that surfaced this.

**Measurement.** AAPL, whose splits bracket the study window:

| `as_of` | Priced | Actual | Ratio | Splits after the filing |
|---|---|---|---|---|
| 2011-06-30 | $11.1B | ~$310B | 28x | 7:1 (2014-06-09), 4:1 (2020-08-31) |
| 2016-06-30 | $130.9B | ~$523B | 4x | 4:1 (2020-08-31) |
| 2021-06-30 | $2,285B | ~$2,285B | 1x | none |

The ratio is exactly the cumulative split factor and vanishes once the filed
count has absorbed those splits. KLAC shows the same signature at 10x (split
2026-06-12, latest filing 2026-04-30): $9.4B priced against ~$95B.
**446 of ~929 tickers carry at least one split.**

**Decision.**

`core.universe.split_adjusted_shares(shares, ratios)` restates the filed
count onto the price series' basis before pricing, alongside the existing ADR
correction. `jobs/compute.py::_latest_shares` now returns `(shares,
filed_on)`, and `_split_ratios_since` reads every split with `ex_date >
filed_on`.

**`ratios` includes splits dated after `as_of`, and that is not look-ahead.**
Market cap is split-invariant: adjust price and shares by the same factor and
the product does not move. The price side has *already* absorbed those later
splits, so the only requirement is that both sides share one basis. Filtering
to `ex_date <= as_of` would leave price adjusted further than shares, which
is the bug. `test_split_adjusted_shares.py` states this as a property rather
than as prose.

**Consequences.**

- **The historical trade universe was undersized and biased.** `crit_mcap`
  decides `in_trade`, `in_trade` gates `apply_eligibility` (DESIGN §5.2 step
  4), and eligibility decides which events the backtest writes at all. The
  bias favoured names that never split, and grew with distance into the past.
  AAPL's first in-trade quarter was 2012-09-30; it should be in-trade from
  the start.
- **Every published result predates the fix**, ADR 112 included. This does
  not by itself overturn ADR 112 — a larger, less biased universe can move a
  negative result either way — but ADR 112's third confirmation was measured
  on a distorted membership and must be re-measured before it is quoted
  again.
- **`mcap_usd` and `mcap_rank` are stored on every event as context tags**,
  so anything conditioning on company size inherited the error. Same
  consequence ADR 014's TSM defect had, at far greater scope.
- The fix changes no config field, so `config_hash` does not move. The
  universe must be rebuilt and the backtest re-run for the corrected
  membership to reach `events`.

**What this does not fix.** `bars.close` remains the split-adjusted series
and should: indicators depend on it (CLAUDE.md, Price series). The correction
belongs on the share count, which is the side that was stale.

---

## 146. The x1,000 share-scale class is caught by local shape, not by bounds

**Date:** 2026-08-22. **Status:** Pinned. Closes the gap `SharesPlausibility` documented and left open; extends ADR 145's rebuild.

**Context.**

`SharesPlausibility` tests one filing against an absolute band and states its
own blind spot precisely: *"a x1,000 error on a company with real shares in
the tens of millions (tens of billions after corruption) now lands inside
`[min_shares, max_shares]` and is accepted undetected."* It enumerates the
casualties by ticker — 26 filings across 12 tickers — and the widening from
32B to 320B that created the gap was itself deliberate and correct, because a
real 10:1 split on Citigroup would otherwise freeze that ticker forever.

`BACKLOG.md` deferred the fix to "the next rebuild, where it costs nothing
extra". This is that rebuild.

**The obvious fix is already refuted, in the code.** `SharesPlausibility`
argues against a test relative to the ticker's own history and gives the
counterexample: PSKY's median **is** the corruption (two of its three filings
are a placeholder-shaped `1,000`), so a median test flags its one genuine
filing. That argument stands and is not overturned here.

**Decision.**

`core.universe.scale_error_indices(shares, bounds)` compares each filing to
the median of its four nearest neighbours *per side* and flags it only when
both hold:

1. it exceeds that local median by more than 50x, and
2. dividing by **exactly 1,000** puts it back within 5x of its neighbours.

Tickers with fewer than 8 filings are not judged at all. `jobs.ingest`
applies it per ticker after the `(ticker, filed_on)` dedup and **rejects**
the rows to `bar_rejects` under rule `shares_scale_error_x1000`. Nothing is
divided or corrected: condition 2 is a precondition for rejection, not a
repair.

**Why a local window survives PSKY and a global one does not.**

- **PSKY has three filings.** The minimum-filings gate declines to rule
  rather than out-voting it. Silence is the correct answer from two data
  points.
- **WULF is the case a global test gets backwards.** TeraWulf diluted from a
  tiny base, so 16 consecutive **genuine** filings sit up to 247x its global
  median and a global rule would reject every one. Locally each is 1.0-1.3x
  its neighbours and no window looks at it twice.
- **A real split persists; a tagging error returns.** NVDA's 10:1 is the
  largest real step in the tracked universe and holds at ~1.8x a straddling
  window. The errors spike ~1,000x and drop back three to four filings later.

**Naming the factor is what keeps this inside invariant 4.**
`_implausible_shares_reason` refuses to infer a scale factor from the data's
own shape, because "a wrong guess turns into a plausible-looking wrong
number". Condition 2 tests one hard-coded factor and rejects the row when it
fits. An anomaly a x1,000 scale does not explain is left alone — a ~100x
spike is not touched, because removing it would require inferring some other
factor. The guard only removes rows whose corruption it can name.

**Measurement.** Swept over all 142,278 rows of `shares_outstanding`:
**33 filings across 17 tickers**, every one ending in exactly `000` and every
one recovering to a plausible count.

It reproduces the docstring's hand-curated list exactly — AAP (4), GRMN (5),
PKG (3), ALK (3), FTNT (2), SWKS (2), MAA (2), and one each for AIZ, CNX,
EOG, PNR, REG — and adds 7 across 5 tickers ingested after that note was
written: BNTX (2), ENSG (2), CRNX, SANM, WWD. BNTX and WWD had already been
confirmed by hand in `BACKLOG.md` as "the same class ... not in the note's
list", so the detector found them independently.

Zero false positives. WULF, PSKY, BRK-B and EXE are all absent.

**Consequences.**

- The four new fields live on `SharesPlausibility`, which is deliberately
  **not** a `Config` field, so `config_hash` does not move (same rationale as
  `SweepParams`). Propagating the fix still needs a `universe` rebuild and a
  backtest re-run, which is why it is done inside this one.
- **Ingest is fixed from now; the 33 rows already stored are not.** A future
  `run_shares` never re-offers a rejected accession, so the existing rows
  need a one-off delete before the universe rebuild that follows.
- This closes the `crit_mcap` half of the defect. Six `in_trade` quarters
  passed the market-cap criterion on a number wrong by three orders of
  magnitude.
- **The ADR-ratio class is untouched** and remains the more damaging of the
  two, being systematic rather than a rare filer error. It stays open in
  `BACKLOG.md`.

**Relationship to the `McapPlausibility` ceiling.** The $6T ceiling stays.
It is a last-resort catch on the *output* and covers corruption classes this
does not (the x10^5/x10^6 rows above 320B). This guard removes the cause;
that one logs whatever still gets through.


---

## Open items

| Item | Options | Current lean |
|---|---|---|
| **`cscan nightly` never ingests the session it runs after** | Pass `end + 1 day` to the fetcher, or make `_download_daily`'s `end` inclusive to match every other date range in the codebase | Make it inclusive — see below |
| **`v_forward` exposes probabilities with no interval or q-value** | Add `cell_ci_low`/`cell_ci_high`/`cell_q_value` to the view, or have the model carry its own interval | Whichever ADR 113's model produces. **Blocks `/forward`, not Phase 5.** Recorded in `test_serving_view_contract.py::KNOWN_GAPS` |
| Point-in-time index membership | Scrape Wikipedia history, or accept survivorship bias and state it | Scrape, note residual error in RESULTS.md |
| Historical earnings dates | Finnhub free tier, Nasdaq scrape, or drop the feature | Finnhub, since earnings contamination is the largest 5-day confound |
| Point-in-time market cap | Shares outstanding from filings, or price-times-current-shares approximation | Filings where available, approximation flagged elsewhere |
| Polling home | Actions cron with internal loop, or persistent Modal function | Actions until the live log matters, then Modal |
| Non-US mega-caps | Add ASML, SAP, Novo, Toyota, Samsung, LVMH for lower correlation | Add if effective sample falls short after clustering adjustment |

### Who serves `/` and `/ticker/[sym]` — RESOLVED by ADR 118, 2026-08-18

**Answered the same day: option A.** MCP is for LLM callers; deterministic
reads go straight to the views. ADR 070 and ADR 076 are confirmed as
written and the session plan was what needed correcting. The account below
is kept because it is how the question was framed and what each option
cost.


**Sessions 17 and 18 are blocked on this, and nothing in either was built.**
CLAUDE.md: *"If a task appears to require contradicting one [ADR], stop and
ask. Do not work around it."*

`session16-18-phase5.md` §17.1 says routes call the Python handler layer:

> Routes call handlers. No route constructs SQL or imports `db_io`,
> enforced by the same import test Session 16 uses.

That is ADR 072's arrangement. **ADR 076 refines ADR 072 and withdraws it:**

> ADR 072 named a Python handler layer as the shared contract, which would
> have required either duplicating queries in TypeScript **or hosting a
> Python service**. Views achieve the same guarantee at the database level.

ADR 070 says where the routes run — "every endpoint is an indexed SELECT
executed by Next.js against Neon through the Postgres driver. **No Python
functions on Vercel**" — and DESIGN §8.1, §11.8, and the architecture
diagram all agree. `lightweight-charts` and `recharts` are JavaScript.

**Session 15's handler layer is not in conflict** and does not need
revisiting. It already complies with ADR 076: `screen_signals` reads
`v_screen`, `get_events` reads `v_events`, `get_universe` reads
`v_universe`. Session 16's MCP server is the "Python MCP handlers" half of
ADR 076, built and passing. The conflict is confined to who serves the two
web routes.

| Option | Follows | Costs |
|---|---|---|
| **A.** Next.js + TypeScript selecting from the views | ADR 070, 076, DESIGN as written | A second toolchain and a third CI job. ADR 114's event-feed default is re-implemented in TypeScript. `handlers/validate.py`'s raise guards two of three surfaces instead of three |
| **B.** A Python web layer calling the handlers | The session plan, ADR 072 | Contradicts two Pinned ADRs and needs a Python host that is not Vercel — the cost ADR 076 named and declined |
| **C.** Next.js frontend, Python API behind it | Neither cleanly | ADR 076's hosting cost plus the network hop ADR 072 rejected |

**Current lean: A**, because ADR 070 and ADR 076 are Pinned and DESIGN was
written to them, and because the views genuinely do carry `n_eff`,
`ci_low`, `ci_high`, and `q_value` as columns. The honest cost of A is that
"no probability without its companions" becomes structural-by-column rather
than enforced-by-raise on the web surface, and ADR 114's default has a
second implementation. Both should be stated in whatever ADR resolves this
rather than discovered in session 19.

**A second blocker was claimed here and was not real.** The
`frontend-design` skill is installed and enabled; it is a *plugin* skill
under `~/.claude/plugins/`, and the claim came from checking
`~/.claude/skills/` alone. Corrected 2026-08-18. Reading it then showed that
CLAUDE.md and `DESIGN.md` §11.7 both described it wrongly: it carries no
CapitalScan tokens, only general design guidance.

**Chat has a version of the same question.** §18.2 requires tool results to
pass through `handlers/validate.py` before reaching the model. Under ADR 070
the chat route is TypeScript and cannot call it. Calling the MCP server —
built, authenticated, and passing since Session 16 — is the reading the
existing code most nearly supports, and it makes Session 16 load-bearing
rather than a side surface. Recorded in
`sessions/session18-research-and-chat.md` §0.

### `/` cannot show today's signals — RESOLVED by ADR 119, 2026-08-19

**Answered: `v_screen_live`, a detection-time feed on `touch` rows.** Two further defects surfaced with it: `v_screen` was not config-scoped, and `CURRENT_DATE` is not the market's date. The account below is how the question was framed.


**The screener route renders, and it structurally cannot render today.**

`v_screen` filters `is_cluster_head AND entry_kind = 'next_open'`. Only
`cscan backtest` writes `next_open` rows: `run_events` writes
`EntryKind.TOUCH` (`compute.py:801`) and so does the poller. So the
screener trails the last full backtest, which is a five-hour job.

Measured on 2026-08-18:

| | |
|---|---|
| Newest `next_open` in `events` | 2026-08-13 |
| Newest daily bar | 2026-08-17 |
| Events for 2026-08-18 | 67, all `entry_kind = 'touch'` |
| Events for 2026-08-17 | 76, all `entry_kind = 'touch'` |

DESIGN §11.1 calls `/` "today's screener, the default landing route". It is
currently a five-day-old screener, and would be a months-old one in normal
operation.

**The page does not hide this.** The status strip carries two dates,
`signals` and `bars`, with a badge when they diverge. That was added after
the first render showed `2026-08-17 · fresh · 0 sessions` above rows from
2026-08-13 — one label reporting the fresher of two different facts.

Three fixes, none taken yet because `v_screen` is the shared contract under
ADR 076 and changing it moves `handlers/screen.py` too:

- **Widen the view** to prefer `next_open` per event and fall back to
  `touch`. Keeps one view. Needs care: both kinds exist for backtested
  days, so a naive `IN (...)` double-counts.
- **A second live view.** Clean separation, two things to keep in step.
- **Change what the route reads.** The feed comes from `touch` rows, the
  statistics join on the `next_open` cell. This needs no view change and
  matches what `cscan scan` already does, which is the surface the operator
  uses daily. ADR 114 makes the default the *feed*, and a feed is a
  detection-time question, so the two grains are arguably right to differ.

The third is the current lean. It brushes against ADR 076's "query logic
lives in views" and that tension should be settled in whatever ADR resolves
this rather than left implicit.

### `events.sector` has never held a value — RESOLVED 2026-08-19

**Fixed in migration `b8f31c204e7a`.** Both screener views now join
`tickers` and select `t.sector`, matching `v_events`. Sector went from 0
to 38,685 of 40,906 rows with no change in row count, so the inner join
dropped nothing. `events.sector` stays in the table unread; dropping a
column is a separate decision.

`test_serving_view_contract.py` now fails on **any** serving-view column
that is null on every row, with an allowlist for the `predictions`
columns that wait on Phase 6. That guard is the durable part: this one
survived because nothing checked, not because it was hard to see.


`v_screen` and `v_screen_live` both project `e.sector`. It is **NULL on all
13,479,819 rows** — measured, not sampled. `tickers.sector` carries 502
populated rows, so the data exists and was simply never copied onto the
event.

`v_events` gets this right: it joins `tickers` and selects `t.sector`. The
two screener views select the events column instead, so any consumer reading
sector off them gets null and has no way to tell that apart from a ticker
whose sector is genuinely unknown.

Not fixed on the spot because it is a view change and `cscan nightly` was
running — CLAUDE.md forbids migrating against a live writer. The web route's
dead read was removed the same day so nothing depends on the null.

The fix is a join, not a backfill: copying sector onto 13.5M existing rows
would be a large write for data that a join already has, and it would go
stale the next time a ticker is reclassified.

### The nightly ingest gap, found 2026-08-17

**`cscan nightly` is scheduled for 16:30 local, after the close. It never ingests the session it just ran after.** Every scan, every screener read, and every poller band is one trading session stale.

`cli.py::nightly` sets `end = date.today()` and passes it to `ingest.run_bars_daily`, which reaches `jobs/fetch/yahoo.py::_download_daily`:

```python
yf.download(tickers, start=start, end=end, ...)
```

**yfinance's `end` is exclusive.** Run at 16:30 on 2026-08-17, it requests bars *through 2026-08-16*.

Measured that evening, after a clean 10-phase nightly:

| | |
|---|---|
| `max(bars.ts)` | 2026-08-14 |
| `bars` rows for 2026-08-17 | **0** |
| `max(indicators.ts)` | 2026-08-14 |
| `max(events.signal_date)` | 2026-08-14 |

2026-08-17 is a full trading day in `trading_days` and not an early close. The weekend explains 08-15 and 08-16; nothing explains 08-17 except the exclusive bound.

**Why it survived this long.** Nothing fails. Every phase reports `ok`, `bar_rejects` is empty, and `cscan scan --date <today>` prints "no events found" — which reads as *nothing fired today* rather than *today was never fetched*. The two are indistinguishable from the output, and the second is the one that keeps being true.

**The fix.** Make `_download_daily`'s `end` inclusive by adding a day internally, rather than patching `date.today() + 1` into every caller. Every other date range in this codebase is inclusive on both ends — `run_events`, `run_indicators`, `scan`, `split_key_for` — so the fetcher is the one place that disagrees, and fixing it there stops the disagreement from leaking further.

This is deferred rather than applied because it changes what data lands, and because a same-day daily bar from Yahoo is not always final immediately after the close. Whether to also shift the schedule later is a separate question from the off-by-one.

---

## Phase gates

**Corrected 2026-08-06.** This block previously described a six-phase plan
(Phase 3 quantile model, Phase 4 frontend and chat, Phase 5 synthetic options
plus MCP, Phase 6 real option chains). That numbering predates ADR 018
("Options deferred to Phase 7"), which sits in this same file and contradicts
it, and it was never updated when the plan shifted to seven phases. Every
other document — BUILD.md §"Later phases", DESIGN.md, RESULTS.md, TESTS.md
§10 — already used the seven-phase numbering below. `docs/BUILD.md` remains
the authority on session-level scope; this block states the gate for each
phase.

Phase 1 — Data and detection. Ingest the union universe from 2009 (ADR 040),
compute indicators, apply the health filter to build the trade universe,
detect events, expose `scan()`. Produce the three-arm comparison, the
drawdown slice, and the time-to-MFE distribution. Nothing downstream matters
if this shows no separation. Sessions 0-7.

Phase 2 — Poller and notifications. Live intraday breach detection, the
notifier, the positions surface, and the call overlay, all reading the one
signal implementation the backtest reads. Session 8.

Phase 3 — Engine validation. Full event table with MFE/MAE and four entry
timings. Target-by-stop exit sweep surface. Look-ahead, parity, determinism,
exit-invariant, and split-leakage tests all passing. Session 9. **Passed
2026-08-02.**

Phase 4 — Statistics. Baselines, effective sample size, three benchmark arms
against the 200-replication randomization null, trim-and-redeploy variant,
DCA comparison, headline cell grid with BH correction on the validation
split, era breakdown, cluster-sequence comparison, long-vs-short asymmetry,
pre/post-tax reporting, `cell_stats`. The random-walk null test (DESIGN
§6.12) runs before any real result is trusted.

Phase 5 — Frontend, tools, MCP. Screener, ticker page, research page, the
`handlers/` layer, the seven tools, the response validator, and the MCP
server (ADR 027), built after Phase 4 results exist.

Phase 6 — Model. Quantile heads on shares only, long and short, both on the
same universe and criteria (ADR 106; this block previously read "regime-filtered
short", a category ADR 016 defined and 106 superseded). Purged walk-forward CV,
isotonic calibration, the four-check promotion gate, forward log, live inference
in the poller. Calibration curves and pinball loss are the gate. ADR 093 expands
the terminal-quantile surface to five horizons here.

**Conditional on ADR 112.** ADR 093's own status rationale made this phase
contingent: "If Phase 4 finds no cell survives FDR correction, there may be no
model to expand." That condition was met on 2026-08-16. Phase 6 is not cancelled
— the model is a distinct hypothesis class from the fixed cell grid — but it no
longer proceeds by default, and any decision to open it must cite ADR 112's
measurement rather than route around it.

Phase 7 — Options. Synthetic Black-Scholes pricing, labeled synthetic. Then
one month of real Polygon chains to measure and publish the synthetic pricing
error. Deferred to this phase by ADR 018.

Holdout evaluation runs once, at the end, and gets published whatever it says.

---

## 147. ETFs do not train; a missing sector stops the build

**Date:** 2026-08-23. **Status:** Pinned. Implements ADR 068's granularity rule for Phase 6's training population. Blocks Session 22. No `config_hash` move.

**Context.**

DESIGN §7.3 lists `sector` among the twenty-two features, categorical. ADR
068 pins why: "One model per head, with `sector` as a categorical feature. No
per-sector models." The same section excludes `ticker` identity outright —
"60 names over 40k events permits memorizing individual histories. Sector is
the right granularity."

LightGBM gives a NULL its own categorical level. A level with one member is
ticker identity restored through a feature the design includes, which is
exactly what excluding `ticker` was meant to prevent. Treating NULL as
acceptable because the library handles missing natively gets this backwards:
it handles it by *learning the missing branch*.

**The framing this ADR was given was too narrow, and the measurement is why.**

The question posed was what to do about ETFs, on the premise that `sector` is
NULL for the three in `SEC_NON_FILER_TICKERS` (`QQQ`, `VOO`, `IBIT`). Two
facts measured 2026-08-22 against the live database:

- **Only QQQ exists.** `VOO` and `IBIT` have no `tickers` row and have never
  been ingested. The set names three; one is real.
- **32 tickers reach the training population with a NULL sector, and 31 are
  ordinary equities.** 11,826 priced in-trade events under
  `f66729c7eda212a4`, of which QQQ is 1,910. The rest are ASML, TSM, NVO,
  SAP, ILMN, VFC, MTCH, ENPH, M, EPAM, BBWI, ETSY, DXC, AAL, KMX, PAYC, CAG,
  NOV, FTI, CZR, MOH, BIO, QRVO, ATI, CPB, MHK, PRGO, POOL, NWL, MKTX, LUMN.

So an ETF decision alone cannot deliver a training frame free of NULL
sectors. Either option, implemented by itself, leaves 3.1% of training events
carrying the level this ADR exists to eliminate.

**Root cause of the 31.** `jobs/ingest.py::run_tickers_refresh` populates
`tickers.sector` solely from `wikipedia.fetch_current_constituents()` — the
**current** S&P 500 table. Constituents that have since been removed (kept
deliberately by ADR 035, because the historical union is what removes
survivorship bias) and the Nasdaq additions (ADR 143) therefore arrive blank.
`jobs/fetch/nasdaq.py` already returns a sector per listing and it is not
written to the column.

**Decision.**

Two rules, and they are deliberately not the same rule.

**1. ETFs are excluded from training and remain fully tradeable.** Option B.
The filter is an explicit ticker set in `core/training.py::ETF_TICKERS`,
never `sector IS NULL`. ETFs keep firing signals, keep appearing on the
screener, and keep carrying cell statistics. They do not train the model, and
`predict()` returns not-available for them.

**2. A NULL sector on an equity stops the build.** It is not filtered. The
training-frame builder raises, naming the tickers, and the sector is
backfilled from a real source before Session 22 proceeds.

`core.training.training_exclusion_reason` returns `'etf'` and
`'missing_sector'` as distinct values precisely so a caller cannot collapse
them.

**Rationale.**

*Why ETFs are excluded rather than given a sector value.* Option A — writing
`'ETF'` or `'Index Fund'` into the column — was rejected because naming a
level does not make it a category. It would hold one member today. Even at
its intended three, QQQ tracks the Nasdaq-100, VOO the S&P 500, and IBIT
holds spot Bitcoin; they share a legal wrapper and nothing about how they
move. A one-member level is ticker identity with a label on it, and ADR 068's
rule is about granularity, not about the column being populated.

The deeper point is that an ETF has **no** sector, while a blank equity has an
*unpopulated* one. The attribute does not apply to a fund. Both render as
NULL and only an explicit list can distinguish them.

Exclusion costs nothing at serve time. QQQ is `in_trade` at $289B across 66
universe evaluations and keeps every one of them; it simply does not
contribute training rows. Returning not-available for a fund is honest, where
returning a number learned from a one-member category would not be.

*Why the 31 equities are not excluded with them.* This is the load-bearing
half. A single `sector IS NULL` filter is the obvious implementation and it
is wrong: it would silently drop 9,916 events — **84% of the affected
population** — off names that are overwhelmingly *removed* S&P constituents.
The model would then train only on companies still in the index when the
table was last scraped, which is precisely the survivorship bias ADR 035
exists to prevent and ADR 015 depends on being absent. Trading one
identity-leak for a survivorship bias is not a fix.

It would also be a decision taken by accident. Any future ticker whose sector
lookup fails would leave the training population with no record that it had,
which is the same silent-narrowing failure as stage 7's stale-event sweep.

*Why raising rather than filtering.* A missing sector is a data defect with a
known source and an available fix. Filtering hides it; raising forces it to
be repaired. Nothing imputes a sector — under invariant 4 a guessed sector is
a fabricated one, and `is_depositary_listing` already records why inferring a
value from the data's own shape produces "a plausible-looking wrong number".

**Consequences.**

- `core/training.py` is new: pure, no IO, holding `ETF_TICKERS`, `is_etf`,
  `training_exclusion_reason`, `may_train`, `partition_for_training`.
- Session 22 task 22.4 must call it and must raise on `missing_sector`.
  Session 22's gate gains: the built frame carries no NULL `sector`.
- **A prerequisite Session 22 did not have: backfill `tickers.sector` for the
  31.** `jobs/fetch/nasdaq.py` already returns sector, so this is a lookup
  against a real source rather than an imputation. It is a `tickers` update
  only — sector is not in `config_hash`, is not read by any
  `UniverseParams.required_criteria`, and is not a component of `cell_id`
  (see below). **No universe rebuild and no backtest re-run.**
- `ETF_TICKERS` duplicates `SEC_NON_FILER_TICKERS` today and is kept
  separate. That set answers "does SEC serve companyfacts for this?"; this
  one answers "is this an instrument rather than a company?". They agree now
  and need not: a foreign private issuer can file nothing useful and still be
  a company.
- Phase 4 statistics are **unaffected and need no re-measurement.**
  `core/cells.py::cell_key` takes nine arguments — `signal_type`, `side`,
  `dd_bucket`, `strength`, `entry_kind`, `split`, `era`, `horizon`, `target`
  — and sector is not among them. `BACKLOG.md` claimed an ETF lands in a
  NULL-sector cell; that claim was wrong and is corrected there.
- `crit_rel_return` is untouched. `jobs/compute.py::_sector_median_return`
  already folds `sector is None` into its "too few members" fallback
  deliberately.

**Alternatives rejected.**

*Give ETFs a sector value (Option A).* A one-member category is ticker
identity renamed, and it does not address the 31 equities, so it fails the
training-frame gate on its own.

*Filter on `sector IS NULL`.* Drops 84% of the affected events off removed
index members and reintroduces survivorship bias. It is also indistinguishable
in code from the ETF decision, so the two could never be varied separately.

*Accept NULL because LightGBM handles missing values natively.* It does, by
learning the missing branch. That is the defect, not a mitigation.

---

## 148. `tickers.sector` speaks one vocabulary, and it is GICS

**Date:** 2026-08-24. **Status:** Pinned. Completes ADR 147 and tightens its gate. Unblocks Session 22. No `config_hash` move.

**Context.**

ADR 147 decided that ETFs do not train and that a NULL sector stops the
build, and left one prerequisite: backfill `sector` for the 31 equities that
reach the training population without one.

Writing that backfill surfaced a second defect, larger than the one it was
meant to fix. **`tickers.sector` held two vocabularies at once.** Measured
over the training population under `f66729c7eda212a4`:

| GICS | events | duplicate | events |
|---|---|---|---|
| Information Technology | 53,031 | `Technology` | 4,513 |
| Financials | 61,846 | `Finance` | 1,429 |
| Communication Services | 15,468 | `Telecommunications` | 791 |

That is not three pairs of related sectors. It is three sectors written down
twice, so the categorical feature carried fourteen levels where there are
eleven, with the smaller half of each pair holding four to eighteen tickers.
ADR 068 makes `sector` the granularity that replaces ticker identity, and a
level of four tickers is most of the way back to identity — the same defect
ADR 147 addresses, arriving from the other direction.

The Nasdaq-vocabulary rows are the ADR 143 additions, all with `cik IS NULL`.
No job writes them: `run_tickers_refresh` writes only Wikipedia's
`gics_sector`, so they were populated by a one-off path during that
expansion.

**ADR 147's gate did not catch this**, and would not have. It tested for a
NULL sector only, so a frame carrying both `Technology` and `Information
Technology` passed it while holding a split category.

**Decision.**

**1. GICS is canonical.** `tickers.sector` may hold only the eleven GICS
sector names, spelled as Wikipedia's constituent table spells them. That is
what `run_tickers_refresh` has always written and what the majority of the
population already carried, so canon follows the incumbent rather than
introducing a third vocabulary.

**2. Yahoo is the source of record for anything Wikipedia does not cover**,
crosswalked to GICS by `core.sectors.normalize_yahoo_sector`. Yahoo's scheme
has the same eleven top-level sectors and assigns membership the way GICS
does, so the crosswalk is a rename.

**3. A non-canonical stored value is re-resolved, never crosswalked.** This
is the load-bearing rule and the reason the function is named for its source.

**4. Anything that does not resolve stays blank**, and ADR 147's training
frame keeps raising on it.

**5. ADR 147's gate is tightened.** `training_exclusion_reason` returns a new
`non_canonical_sector` alongside `missing_sector`.

**Rationale.**

*Why stored values are re-resolved rather than mapped.* Nasdaq and Yahoo both
emit the string `"Technology"`, and **they do not mean the same set of
companies.** Nasdaq files NTES (Electronic Gaming & Multimedia) and BILI
(Internet Content & Information) under it; GICS and Yahoo both call those
Communication Services.

So a general `normalize(stored_value)` cannot exist. Given `"Technology"` it
cannot know whether the row came from Nasdaq, where NTES belongs in
Communication Services, or from Yahoo, where it belongs in Information
Technology. Mapping the stored label would move 473 NTES events and 89 BILI
events into the wrong sector on the strength of a matching string — a
fabricated classification, which invariant 4 forbids and which
`is_depositary_listing` already records as the way inference from the data's
own shape produces "a plausible-looking wrong number".

Treating the stored value as *unknown* and looking the ticker up again costs
one request per row and cannot make that error.

*Why Yahoo, given `fetch_shares_full` rejected `Ticker.info`.* That rejection
is specific and does not generalise. `info["sharesOutstanding"]` is one
number true only today, so stamping it onto every historical `as_of`
reproduces the "current constant held backward" error DESIGN §2.4 names.
Sector is not a series: DESIGN §7.3 lists it under **Static**, and a GICS
sector is a classification rather than a measurement. There is no as-of to
get wrong. `fetch_sector` records this next to the rejection so the two are
not read as contradictory.

*Why unresolved rows stay blank.* 98 of 352 candidates did not resolve, and
essentially all are delisted or renamed — YHOO, FB (now META), PCLN (now
BKNG), TWTR, ATVI, CERN, FRC, SIVB. Yahoo 404s on them. None reaches the
training population, so the correct outcome is a blank column and a build
that keeps refusing, not a plausible label.

**Measurement.**

`cscan`-invoked `run_sector_backfill`, 2026-08-24: **254 resolved of 352
candidates** (211 NULL, 142 non-canonical, less QQQ). Training population
after:

- Exactly **eleven** sector levels, all GICS.
- `Technology`, `Finance` and `Telecommunications` are gone; their events
  merged into the correct GICS levels (Information Technology 53,031 →
  61,757; Financials 61,846 → 63,318; Communication Services 15,468 →
  17,319).
- The ADR 147 partition over all 386,208 live training rows: **384,298
  trainable, 1,910 filtered as ETF, 0 must-fix.** The only remaining NULL is
  QQQ, which ADR 147 excludes by decision.

**Consequences.**

- `core/sectors.py` is new: pure, no IO. `GICS_SECTORS`, `is_canonical`,
  `normalize_yahoo_sector`, `needs_resolution`.
- `jobs/fetch/yahoo.py::fetch_sector`, cached under `yahoo_sector_v1`.
- `jobs/ingest.py::run_sector_backfill`. Writes `tickers.sector` and nothing
  else. Sector is not in `config_hash` and is not a component of `cell_id`.

  **CORRECTION, 2026-08-24.** This entry also claimed sector "is not read by
  any `UniverseParams.required_criteria`, so no universe rebuild and no
  backtest re-run". **That was wrong.** `crit_rel_return` is
  `_cmp(rel_return_756d, sector_median_return)`, and
  `_sector_median_return` compares a ticker against **its own sector's**
  median, falling back to the universe-wide median only when the sector has
  fewer than five members. Populating 254 sectors therefore changed which
  median those tickers are measured against, and `crit_rel_return` *is* in
  `required_criteria`.

  Measured after re-running all 66 quarters: `in_trade` moved **6,295 ->
  6,641** (+346), spread across every year rather than concentrated in
  recent data. Every GICS sector now holds 34-163 members, all above the
  five-member threshold, so the fallback that previously applied to most
  tickers no longer does.

  `config_hash` still does not move and `events` rows stay correctly keyed,
  so nothing is corrupted -- the criterion now measures what it was always
  meant to measure. What is stale is the **measurement**: ADR 112's stage 7
  result was computed on the pre-backfill population and must be re-run. Verified: `config_hash`
  unchanged at `f66729c7eda212a4`.
- **Session 22 is unblocked.** ADR 147's prerequisite is met.
- `run_tickers_refresh` still writes Wikipedia's `gics_sector` directly,
  which is already canonical. A future re-run cannot reintroduce the Nasdaq
  vocabulary, because no job ever wrote it.
- **Known gap, deliberate.** The backfill is a single transaction at the end
  of a ~20-minute network job rather than checkpointed, against CLAUDE.md's
  "checkpoint anything over 10 minutes". It is atomic, so a failure writes
  nothing rather than half a taxonomy, and the job is re-runnable because
  `needs_resolution` re-selects exactly the rows still unfixed. Checkpoint it
  before running it over a materially larger candidate set.

**Alternatives rejected.**

*Crosswalk the stored Nasdaq labels.* Cheaper, no network, and wrong: it
misclassifies NTES and BILI, and it cannot be made right because the two
vocabularies disagree on membership rather than spelling.

*Adopt Nasdaq's vocabulary as canonical.* It has no `Communication Services`,
carries a `Miscellaneous` bucket with no GICS equivalent, and would require
relabelling the 198 GICS rows Wikipedia maintains on every refresh.

*Leave the collision and fix only the NULLs.* What ADR 147 asked for, and it
fails on its own terms: the frame would satisfy the NULL gate while feeding
the model a category split in half.

---

## 149. The watch universe is a sibling of the trade universe, not a relaxation of it

**Date:** 2026-08-24. **Status:** Pinned. Extends ADR 001's two universes to three. Does not move `config_hash`; does not alter `in_trade` for any row.

**Context.**

`in_trade` requires all four of `UniverseParams.required_criteria`, and
`is_tradeable` treats `None` as failing — deliberately, with the comment
*"None must fail, not raise or pass"*. That rule is right in isolation:
admitting a name judged on three criteria would silently put weaker-filtered
rows in the same population.

Its consequence was never written down. `crit_rel_return` needs 757 daily
bars, so every large new listing is excluded for roughly three years
regardless of how it trades. Measured 2026-06-30: **$1.18T across five
names** — ARM $377.3B (IPO 2023-09), SNDK $336.7B (spun from WDC 2025-02),
GEV $315.7B (spun from GE 2024-04), ALAB $82.8B, NBIS $69.9B — each passing
every criterion that *can* be evaluated.

**This is the mirror of the bias ADR 035 exists to prevent.** That ADR keeps
delisted and removed names on purpose, because dropping them is survivorship
bias and ADR 015 depends on their presence. Careful work at one end of the
population. The other end — entrants — was silently truncated for three
years, and nothing recorded it.

**Decision.**

A third membership column, `universe.in_watch`, alongside `in_train` and
`in_trade`, with `universe.watch_reason` recording *why*.

`in_watch` is a **sibling** of `in_trade`, not a relaxation. The two are
disjoint by construction, so a name graduates from watch to trade and never
holds both, and "which population is this row in" always has one answer.

```
in_watch = NOT in_trade
       AND crit_mcap AND crit_sma200_slope       -- never waived
       AND evaluation is not stale               -- ADR 135
       AND ( (crit_above_sma200 AND crit_rel_return IS NULL AND bars < 757)
           OR (crit_above_sma200 IS FALSE AND crit_rel_return IS TRUE) )
```

Two reasons, stored not derived:

- **`history`** — every judgeable criterion passes and `crit_rel_return` is
  `None` because the ticker is too new. GEV has 603 bars; its trailing
  three-year return is *undefined*, not bad.
- **`pullback`** — `crit_sma200_slope` holds while `crit_above_sma200` does
  not, so price sits below a **rising** 200-day average. 22 names, $2.75T:
  WMT, COST, WFC, TJX, GILD.

**Everything is computed for these rows** — events, indicators, entries,
exits, paths — because the backtest processes `in_trade ∪ in_watch`. A name
that graduates therefore arrives with measured history rather than starting
from zero.

**Nothing statistical or model-facing reads them.** Every statistical read
hardcodes `in_trade` and `test_events_in_trade_filter.py` fails any read
that does not. Phase 6 training filters `in_trade` as well. ADR 112's
population is untouched and stays homogeneous — which is the property that
makes this affordable.

**Rationale.**

*Why not "any three of four".* Measured 2026-08-24 at the latest evaluation:
three passing with one `None` is **6 tickers, $1.18T**; three passing with
one `False` is **247 tickers, $14.85T**. The second is not a wider
watchlist, it is the universe with the filter switched off, and it admits
names *because* they failed the test that would have excluded them. `None`
is the absence of evidence; `False` is evidence.

*Why the pullback carve-out survives that objection.* It is not "any
failure". It is one specific failure conditioned on the trend gate still
passing, and the two are mechanically distinguishable. TSLA shows the
discrimination in both directions on the same ticker:

| `as_of` | `above_sma200` | `slope` | reading |
|---|---|---|---|
| 2024-06-30 | false | **false** | real downtrend, stays out |
| 2026-03-31 | false | **true** | $1.4T dip in an intact uptrend, admitted |

A rising 200-day average with price beneath it is a pullback inside an
uptrend, which for a Bollinger mean-reversion study is the canonical setup
rather than a risk.

*Why `crit_mcap` is never waived.* It is a designed floor, not a signal, and
it is already what excludes the genuinely dangerous names. CHGG has no
`universe` row **at all** — $20B filters it before any criterion runs — so
the "penny stock after a collapse" case never reaches this decision.

*Why `crit_sma200_slope` is never waived.* It is the gate that separates a
pullback from a collapse. Waiving it collapses the two cases the pullback
reason exists to distinguish.

*Why staleness is never waived.* A sibling universe inherits every
safeguard. ADR 135 exists because Aetna, acquired 2018-11-29, passed all
four criteria at 2026-06-30 on an indicator row frozen in November 2018 —
**31 consecutive quarters `in_trade` with no bars behind any of them**.
Without this clause the watchlist fills with delisted companies that look
like discoveries.

*Why the reason is stored rather than derived.* The two populations are
admitted by different arguments and will behave differently. One flag would
make it impossible to measure whether one works and the other does not, and
it would make the notification dishonest — "too new to judge" and "pullback
in an uptrend" are different things to read at 07:00.

*Why two shortfalls is not admitted.* A new name **also** below its average
has `crit_rel_return` unknowable and `crit_above_sma200` false: two of four,
not three. Either reason returned would be a claim the row does not support
— `history` implies the trend test passed, `pullback` implies the
relative-strength test did.

**Consequences.**

- `core.universe.watch_reason` is new: pure, no IO, with `WATCH_HISTORY` and
  `WATCH_PULLBACK`.
- Migration adds `universe.in_watch` (boolean) and `universe.watch_reason`
  (text). `cscan universe` must be re-run over all 66 quarters to populate
  them — ~15 minutes, and **not** a backtest rebuild.
- **`config_hash` does not move.** The predicate is derived from criteria
  that already exist; nothing enters `Config`.
- **`jobs/poll.py`'s allowlist entry becomes false and must be rewritten in
  the same change.** It currently reads *"the poller only ever polls
  `_load_in_trade_tickers`, so every row it can reach is in trade by
  construction"*. That stops being true the moment the poller polls watched
  names — the identical failure mode as the `path_backfill` allowlist going
  stale when ADR 122 changed `run_events`.
- **One invariant changes and is restated deliberately.** Today "priced ⇒
  `in_trade`" holds and is measured at zero violations. It becomes "priced ⇒
  `in_trade` OR `in_watch`". The backtest really does price these rows; what
  changes is the population the proxy names. `test_entry_price_single_writer
  .py` and the four allowlist reasons are updated with the new sentence
  rather than left to drift.
- Notifications for watched names must say which reason admitted them. A
  watch alert that looks identical to a tradeable one is how the distinction
  is forgotten.
- **This does not reopen ADR 112.** No measured row changes population, and
  no statistic reads `in_watch`.

**Alternatives rejected.**

*Relax `required_criteria` so these names become `in_trade`.* Moves
`config_hash`, forces a full rebuild, and makes the study population
heterogeneous: "zero cells survive FDR" would then be measured over rows
admitted by two different standards.

*Poll them and price them while leaving them out of `universe` entirely.*
Produces rows that look tradeable and are labelled ineligible — the same
"two meanings in one column" defect ADR 140 diagnosed for `entry_price`.

*Derive the watchlist at read time instead of storing it.* Cheaper, and it
loses the reason. It also puts the definition in whichever view happens to
need it, which is how two definitions eventually disagree.

---

## 150. A provisional poller row is superseded at the nightly, not indefinitely

**Date:** 2026-08-24. **Status:** Pinned. Completes ADR 140, which declared the poller's row provisional without saying when the provisionality ends. Does not move `config_hash`.

**Context.**

ADR 140 decided the nightly is authoritative for every grain and the
poller's row "is provisional -- written mid-session against a partial bar,
by a process that samples every five minutes -- and it yields to the version
computed from the completed session."

**It yields only when the two rows share a key.** `events` conflicts on
`(config_hash, ticker, signal_date, signal_type, entry_kind)`. When the
nightly writes a row under a *different* `signal_type`, nothing is
overwritten: both rows survive, and the poller's keeps its observed
`entry_price`, no exit, and no cluster tagging.

**The harness then judges it as engine output**, because
`_load_events_for_config` scopes on `config_hash` alone. Found 2026-08-24,
when the harness failed three of five checks on 1,109,993 events:

```
FAIL (8)  entry_sanity      `_pre_slippage_price` divides 3bps out of a
                            price that never had slippage added
PASS      exit_sanity
FAIL (2)  return_identity
FAIL (4)  non_overlap       32 rows with NULL cluster columns
```

Every violation came from **31 poller rows written that day**, none from the
ADR 149 watch universe the same run introduced. It had never surfaced
because the previous harness run was on a Saturday, with no poller session
behind it.

**Three causes, and only the third is the one that looks obvious.**

| | rows | cause |
|---|---|---|
| Different primary type from the same fired set | 23 | AEE fired `bb_lower_touch` and `stoch_oversold`. The backtest wrote one row, `signal_type = bb_lower_touch`, `signal_types_all = {both}`. The poller wrote a second under `stoch_oversold`. |
| A close-confirmed type the poller cannot emit | part of the 23 | DE: the backtest resolved to `bear_close_above_upper` (strength 3); the poller emitted `bb_upper_touch`. DESIGN 4.8 forbids the poller writing close-confirmed types, since it cannot see the close. |
| No bar at all | 8 | DLR's 2026-08-24 bar was **rejected** by `open_outside_range` -- 35 were that day, invariant 4 working. No bar, so no detection to reconcile against. |

The first two are not price disagreements. The live quote is inside the
day's range in every case; the writers simply choose a different *primary*
from the set of types that fired.

**Decision.**

`cscan nightly` deletes rows still carrying a `poll_` `run_id` for the
session it just processed, immediately after `run_events`.

By that point the authoritative pass has had its say. A poller row that
survives it is one the nightly did not reproduce under the same key, and
under ADR 140 that row was never the authority.

**Nothing is lost, and ADR 140 already says so**: *"The observation is not
lost and is not an entry price. It lives in `signal_reports.state_json ->
'live_price'`, alongside the bands and the open that produced the call, and
in that session's `reports/poller/*.csv`, which is written before the
nightly runs and never rewritten."* All 31 rows on 2026-08-24 had a
`signal_reports` row; the CSV is committed.

**Rationale.**

*Why not change the primary key.* Dropping `signal_type` from the conflict
key would make the two rows collide and supersede naturally. It also
destroys the cell grid: `core.cells.cell_key` conditions on `signal_type`,
so `bb_lower_touch` and `stoch_oversold` are measured separately by
construction, and merging them in the key merges two genuinely different
signals into one row.

*Why not make both writers agree on the primary type.* Attractive --
invariant 2 already insists on one signal implementation, and one shared
"pick the primary from the fired set" function would fix the 23. **It cannot
fix DE.** The poller's fired set is genuinely smaller, because
`bear_close_above_upper` requires a close it does not have. Different
inputs, not different logic, so a shared rule still selects a different
primary. A fix that cannot cover its own second case is not the fix.

*Why not narrow the harness instead.* Scoping
`_load_events_for_config` to backtest-written rows would make the symptom
go away in one line. Rejected: the harness would then stop looking at rows
that really are in the study population, and the same failure returns
whenever it runs mid-session. Fix the data, not the instrument.

*Why the sweep is unconditional rather than "delete only rows a backtest
row covers".* The conditional version leaves the 8 no-bar rows behind, and
those are the least defensible of the three: a signal with no bar has no
validated event at all. The uniform rule is also the one that can be stated
in a sentence, which is what makes it checkable a year from now.

**Consequences.**

- The sweep runs inside the nightly, after `run_events`, scoped to that
  session's date and to `run_id LIKE 'poll_%'`. Same-day only: an older
  poller row is a different question and is not silently swept by a job
  that says it processes today.
- **`signal_reports.event_id` is cleared by the sweep, not left dangling.**
  Measured rather than assumed: there is **no foreign key** from
  `signal_reports.event_id` to `events.id` -- only `prediction_id` has one
  -- so a delete neither cascades nor raises. It would simply leave the
  column pointing at an id that no longer exists, and **106 rows are already
  in that state** from the ADR 145/146 stale-event sweeps.

  The observation survives regardless: `ticker`, `fired_at` and `state_json`
  are all NOT NULL, so the report is self-contained. `event_id` is nullable,
  so the sweep sets it to NULL first. A report saying "no surviving event"
  is honest; one pointing at a deleted id is a reference that reads as
  meaningful and is not.
- A poller row for a name whose bar was rejected disappears at the nightly.
  That is correct and is the same reading invariant 4 already applies to
  the bar itself.
- The harness's population is unchanged in scope. It still validates every
  row for the config, which is the property that made this defect visible
  rather than silent.

---

### Consequence found in production, 2026-08-28

**Nulling `event_id` is only safe if every reader stops using it, and one
did not.** The sweep's justification is that `signal_reports` is
self-contained — `ticker`, `fired_at` and `state_json` are NOT NULL — so
clearing the link "preserves the observation". That is true of the table.
It was false of `v_screen_live`, which resolved `fired_at` through
`r.event_id = e.id` and therefore showed nothing for any swept report.

The visible symptom was that **every intraday detection disappeared from the
site after each nightly**, leaving only the opening burst, so the screener
looked like the poller only ever fired at 06:45. Of 175 reports on
2026-08-28: 90 resolved (all 06:45), 61 nulled by this sweep (06:45–12:52),
24 dangling (07:04–12:37).

Fixed in `d5e91a7c3b48` by matching on `(ticker, signal_date)` instead.

**The general rule this earns:** a cleanup that deliberately breaks a
reference must name the readers of that reference, because "the row still
holds the information" and "the query can still find it" are different
claims, and only the first was checked here. No error is raised on either
side — the view simply returns NULL, which renders as a blank cell.

## 151. `run_events` does not tag clusters

**Date:** 2026-08-24. **Status:** Pinned. Enforces Ruling C5, which already assigned the cluster columns to the backtest exclusively. Does not move `config_hash`.

**Context.**

Ruling C5 gives `cluster_id`, `seq_in_cluster`, `is_cluster_head` and
`days_since_head` to `run_backtest` **exclusively**. `run_events` was
tagging them anyway, by calling `_tag_clusters` over its own detections
before the upsert.

The columns are correctly absent from `_RUN_EVENTS_UPDATE_COLUMNS`, so an
existing backtest-tagged row was never overwritten. **Rows this job
*inserts* were another matter**: their tags came from `_tag_clusters` run
over the nightly's five-day window, which cannot see a head that fired
before it.

Found 2026-08-24 by `_check_non_overlap`, the last failing check in a
harness run where the other four passed:

```
DKNG  short  2026-08-17 -> 2026-08-24   5 trading bars
EXR   long   2026-08-17 -> 2026-08-24   5 trading bars
NTNX  short  2026-08-17 -> 2026-08-24   5 trading bars
STZ   short  2026-08-17 -> 2026-08-24   5 trading bars
```

Each pair sits exactly at `ExitParams.max_hold_days`, the boundary the check
fails on. In every case the 08-24 head was inserted by `run_events`, blind
to the 08-17 head the backtest had already tagged.

All eight rows are `in_trade = false`, unpriced ADR 122 detections. That is
why only one check caught it: `entry_sanity` and `exit_sanity` skip unpriced
rows and `return_identity` skips rows with no `gross_ret`, so
`_check_non_overlap` -- which keys only on `is_cluster_head` -- was the sole
check reaching this population.

**Decision.**

`run_events` does not tag clusters. `_tag_clusters` is no longer called from
it, and the four columns are absent from the rows it writes rather than
written NULL.

An insert therefore takes the column default (NULL), and the backtest tags
the row when it next runs. `_tag_clusters` itself stays -- the backtest is
its real caller.

**Rationale.**

*Why not widen the window instead.* `run_events` could load each ticker's
prior heads and tag correctly. It is the most faithful fix and it was
rejected as work in the nightly's hot path to maintain a column the job does
not own. Ruling C5 already says who owns it; the defect is that the ruling
was not enforced.

*Why not scope the harness check instead.* Narrowing `_check_non_overlap` to
`in_trade` would have passed the run and left the mis-tagging in place.
Rejected for the reason ADR 150 gives: fix the data, not the instrument. It
would also have been the second check narrowed in one day.

*What this does not cost.* The screener reads `v_screen_live` (ADR 119),
which filters `entry_kind = 'touch' AND in_trade` and has never looked at
`is_cluster_head` -- its own documentation notes the column is "null while
the poller has written the row and clustering has not run", so NULL is
already an expected state there. `v_screen` does filter it and is read by
nothing; it survives only in comments.

*What this does cost, and it is worth stating plainly.* `cell_stats` filters
`AND is_cluster_head`, so the cluster columns **are** load-bearing for
ADR 112 -- DESIGN 5.3's "non-overlapping, clean standard errors" depends on
them, and without the filter one market move is counted many times and the
standard errors shrink for no real reason. Nothing here weakens that: the
statistics read priced backtest rows, and the backtest still tags them.

**Consequences.**

- 2,102 existing rows had their tags cleared, scoped by writer rather than
  by the four that collided: every tag this job wrote came from the same
  too-narrow window and is unreliable for the same reason. Overlapping
  pairs afterwards: **0**.
- A fresh nightly detection carries no cluster tag until a backtest runs.
  That is honest -- the tag would have been a guess made from five days of
  context -- and invisible to every consumer.
- `_check_non_overlap` keeps its full scope, so it still reaches rows the
  other four checks skip. That property is what surfaced this.

**A related question, raised and deliberately left where it was.**

The user's original intent for `events` was that every fire is a separate
observation -- averaging down is real, bleeding out of a long is real, and
different entries against a shared exit produce genuinely different returns.
That intent was never recorded, and cluster-head filtering in `cell_stats`
quietly encodes the opposite.

Decided 2026-08-24 to keep the filter, on the grounds that it changes
nothing today and the alternative is not free. `n_eff = n / (1 + rho(c_bar -
1))` already corrects **cross-sectional** dependence, when many tickers fire
on one market move. Cluster-head filtering corrects **serial** dependence,
one ticker firing repeatedly inside a single holding window. Dropping it
without building the serial equivalent would raise `n` and narrow every
interval -- the direction that manufactures significance, and with no way
afterwards to tell a real edge from one move counted four times.

Measuring both arms is the honest way to answer it, and is left for when the
answer is wanted rather than folded in here.

---

## 152. Postgres on the Pi waits for the network rather than binding a wildcard

**Date:** 2026-08-25. **Status:** Pinned. Enforces PI_MIGRATION §2, which already forbade a wildcard listener. Does not move `config_hash`.

**Context.**

PI_MIGRATION §2 sets `listen_addresses = 'localhost,192.168.1.30'` so the
serving database is reachable from the LAN and nowhere else, and says
plainly: *"Do not use `0.0.0.0` or `0.0.0.0/0` here."*

The first unattended reboot broke that. `postgresql@17-main` starts before
wlan0 has its address, so the named address does not exist yet:

```
01:23:35 LOG:      could not bind IPv4 address "192.168.1.30": Cannot assign requested address
01:23:35 WARNING:  could not create listen socket for "192.168.1.30"
01:23:35 LOG:      listening on Unix socket "/var/run/postgresql/.s.PGSQL.5432"
```

**Postgres does not abort when one of several listen addresses fails.** It
warns, binds what it can, and starts. `systemd` therefore reports the unit
`Started`, `pg_lsclusters` reports `online`, and the cluster is up on
loopback only. Every LAN client fails with connection refused while every
local check says the database is healthy.

The immediate fix was `listen_addresses = '*'`, which works and gives up the
property §2 exists to protect. `*` binds `0.0.0.0` **and** `::`, and the Pi
holds a globally routable `2600:6c50:...` address with no NAT in front of
it. `pg_hba.conf` covers `192.168.1.0/24`, so an IPv6 connection fails
closed on authentication -- but that leaves one layer where the design
called for two, and the surviving layer is the router's inbound firewall.

**Decision.**

`listen_addresses` stays narrow. A systemd drop-in makes the unit wait for
the network instead:

```ini
# /etc/systemd/system/postgresql@17-main.service.d/override.conf
[Unit]
After=network-online.target
Wants=network-online.target
```

The stock template orders on `network.target`, which means "networking is
configured", not "an address is assigned". `network-online.target` is the
one that waits for the second.

**Rationale.**

*Why not keep `'*'` and rely on `pg_hba`.* It is one line and it works. It
also converts a two-layer control into a one-layer one, on the one interface
family that has no NAT. The failure mode is not "someone guesses the
password" but "a router firmware update changes an inbound default", and
that is exactly the class of event §2 was written against.

*Why not `ExecStartPre` polling for the address.* More robust, since it
tests the actual precondition rather than a target that approximates it. Held
in reserve: it is a retry loop in a boot path, and `network-online.target`
turned out to be sufficient here. Reach for it if this recurs on wifi.

*Why the diagnosis took four wrong turns, which is the durable lesson.* The
outage presented as "the site is down", and the site was never down -- the
web unit reached `Ready in 6.8s` at boot and the browser was holding stale
chunk hashes. Avahi was restarted by hand at the same time as the Postgres
fix and got the credit for it; its log later showed it registering
`192.168.1.30` eight seconds into boot, unprompted, on the failing run. The
proposed Avahi drop-in and an IPv6 disable were both fixes for problems that
did not exist.

What settled it was reading the writer's own log rather than the
supervisor's. `journalctl -u postgresql` shows `Finished` because Debian's
`postgresql.service` is an empty oneshot wrapper; `journalctl -u
postgresql@17-main` shows `Started` because Postgres genuinely did start.
Only `/var/log/postgresql/postgresql-17-main.log` records that it started
degraded. This repeats the pattern CLAUDE.md already records for `psql`
exiting 0 on a shared-memory failure: **a success status from the process
supervisor is not evidence about what the process accomplished.**

**Consequences.**

- Verified by reboot on 2026-08-25, not by inspection. Booted 01:57:05;
  `grep -c 'could not bind'` stayed at **1** (the pre-fix occurrence), and
  the workstation reached `192.168.1.30:5432` 29s after the machine dropped.
  Web over IP and over `capitalscan.local` both answered 200 at 39s with
  nothing restarted by hand.
- The Pi's full boot to serving is **~39 seconds**. Anything checked inside
  that window reads as an outage. Two dead ends in this session came from
  exactly that.
- `192.168.1.30` is still a DHCP address, so this breaks the day the lease
  moves -- now with a clean `could not bind` line naming the cause. The
  reservation is still open in PI_MIGRATION's closing section.
- The verification is `grep -c 'could not bind'` on the cluster log after a
  reboot. Not `systemctl is-active`, which was true throughout the failure.


---

## 153. The poller pushes to serving every tick

**Date:** 2026-08-25. **Status:** Pinned. Extends ADR 053 with a second sync path and gives ADR 150's sweep a second target. Does not move `config_hash`.

**Context.**

The serving store updated once a night. `cscan nightly` ends with
`run_sync`, and nothing else wrote to it, so the deployed site showed the
previous night's state all day while the workstation showed live signals.

That was correct while serving was Neon and the site was a public copy. It
stopped matching intent when serving moved to a Pi on the LAN (ADR 152):
the user's stated reason for the migration was *"having live updates on a
website not locally served here."*

**Three things had to be true before this was safe, and two were already.**

`sync.py` excluded `bars_live` and `quotes_live` deliberately, and said
why: a nightly copy of a five-minute table is a price frozen at the last
sync wearing a live label, *"and worse remotely because nobody there can
see the poller is not running."* That is ADR 131 and ADR 134's failure.

Re-reading both: their protections live in the **view and API layers**, not
in sync. ADR 131 polls `GET /api/live` every 45 seconds client-side and
prints when it last re-read, so a failed poll is legible -- the price holds
and the clock beside it stops. ADR 134's `market_is_open()` NULLs
`live_price` outside ET 09:30-16:00 and keeps `live_price_ts`. Both apply
on the Pi unchanged, because the Pi runs the same Next.js app against the
same views.

So the objection was to the **frequency**, not the tables. At one copy per
tick the serving store is in the position the workstation is already in.

The third thing was missing: **nothing could distinguish a quiet session
from a dead poller.** `stale_after_days = 2` is day-grained, built for a
missed nightly, and cannot see a poller that died at 07:15. Without it, an
empty screen and a stopped writer look identical -- which is the ADR 131
failure arriving by another route.

`poller_sessions` already had the shape and was written once at session
end. Moving that upsert into the tick loop makes it a heartbeat at no
schema cost.

**Decision.**

`run_live_sync` copies the poller's own footprint to serving after every
tick: `poller_sessions`, the `runs` row `events.run_id` points at,
today's `touch` events, their `signal_reports`, `bars_live` and
`quotes_live`.

`poller_sessions` ships first. `ended_at` now means "last tick", not
"finished".

**The nightly sweep gains a second target.** ADR 150 deletes this session's
unreconciled poller rows from research; those rows are now already on
serving, and `run_sync` never deletes. So `_sweep_provisional_poll_rows`
runs against the serving engine too, before `run_sync`.

**Rationale.**

*Why a separate table list rather than widening `_tables()`.* The nightly
exclusion is still right for the reason it documents. Widening it would
ship the live tables at nightly frequency as well, which is the original
bug. Two lists, two frequencies, one test holding the line.

*Why an `id` watermark on `events` and `signal_reports` and not on the
other four.* Neither has an `updated_at`, both `id` columns are bigint
sequences, and the poller never rewrites a key it has already fired
(`_already_fired`) -- so a high-water id is exact, and a tick ships what it
produced rather than the session so far.

`bars_live` takes no watermark: it is keyed `(ticker, session_date)`, so a
tick overwrites each ticker's row and the table must ship whole. A
watermark there would send each ticker once and never again, freezing the
deployed candle at the day's first tick while everything else moved. That
is worse than not shipping it, because it looks live.

`quotes_live` is keyed `(ticker, ts)` and is therefore **append-only**, so
it takes a clock watermark instead. The first version of this design
assumed it behaved like `bars_live`; `pg_constraint` said otherwise. Left
alone it would have re-shipped every quote written so far on every tick,
growing quadratically across a 78-tick session. Checking the six conflict
keys against the catalogue is what caught it, which is the habit `sync.py`'s
own docstring already recommends after three keys were guessed wrong.

*Why no `run_job` and no `_pin_config_hash` on the live path.* The poll's
own run row already accounts for the work; ~78 ticks a session would
otherwise write 78 `runs` rows describing a copy. The GUC does not change
intraday.

*Why a failed push never fails the poll.* Research is the source of truth
and is written before the push runs. A sleeping Pi, a moved DHCP lease, or
an unset `DATABASE_URL_SERVING` degrade to "the site is behind", not to
"the session lost a tick". The watermark does not advance on failure, so
the next tick re-ships what this one could not and no catch-up path is
needed.

*What was nearly built and was not.* A new one-row `live_heartbeat` table
with its own migration, before noticing `poller_sessions` already existed
and was merely written at the wrong moment. Recorded because the reflex to
add schema ahead of reading what is there cost the better part of a design
pass.

**Consequences.**

- The deployed site matches the workstation to within one sync round-trip.
  The lag the reader actually sees is the poller's own five-minute write
  interval, which is the same locally.
- Serving now holds rows ADR 140 classifies as provisional, visible before
  the nightly confirms them. That is the intended trade and the user's
  stated reading: *"not authoritative also doesn't mean not useful."* The
  sweep is what keeps it bounded to one session.
- **The serving sweep is load-bearing and silent if dropped.** Without it
  the serving store keeps every row the nightly rejected -- the no-bar
  rows, the wrong-primary-type rows -- accumulating a few a day forever,
  diverging in exactly the population research judged unreliable.
  `test_sync_live.py` asserts both sweeps exist and their order.
- A serving `runs` row carries status `'running'` mid-session; the
  nightly's full sync corrects it.
- Volume: 9 to 85 `touch` rows a session, measured over six sessions, plus
  ~450 `bars_live` rows a tick.


---

## 154. An ETF is in the trade universe unconditionally

**Date:** 2026-08-25. **Status:** Pinned. Amends ADR 149's hard `crit_mcap` requirement for funds only. Completes ADR 147 by giving the same list a membership meaning. Does not move `config_hash`.

**Context.**

ADR 001's four criteria ask a **company-shaped** question. `crit_mcap` is
shares times price, which for a fund is net assets rather than
capitalisation. `crit_rel_return` needs 757 sessions, which a fund launched
last year cannot have and cannot acquire by being right about anything.

Applied to funds, they produced an outcome decided by **data availability
rather than by the funds**. Measured 2026-08-25 after ingesting all four:

    QQQ    5,282 bars   mcap $289B   in_trade
    SPY    5,510 bars   mcap $685B   in_trade
    VOO    4,013 bars   mcap NULL    excluded
    IBIT     656 bars   mcap NULL    excluded

QQQ and SPY qualify because Yahoo happens to serve their share counts.
`cscan shares` resolves neither VOO nor IBIT, and SEC serves no
companyfacts for an ETF by design.

**VOO failed on nothing else.** `crit_above_sma200`, `crit_sma200_slope`
and `crit_rel_return` all passed. A ~$600B S&P 500 tracker was excluded
from a study *seeded from S&P 500 membership* by one missing number.

ADR 149 makes this worse rather than better: `watch_reason` requires
`crit_mcap` to be true, so a fund with no share count falls out of the
watch universe as well. Neither population, on a data gap.

**Decision.**

An ETF is in the trade universe unconditionally. `ETF_TICKERS` membership
alone admits it: polled, full history of bars and indicators, events
generated, no criteria applied.

`core.universe.is_tradeable_instrument(ticker, criteria, required)` is the
new entry point. `is_tradeable` is unchanged and stays ticker-blind.

`in_train` is now False for a fund, agreeing with ADR 147 rather than
leaving the row telling two stories.

**The `crit_*` columns are still written as measured.** VOO's row says
`in_trade = true` and `crit_mcap = NULL` together. The row records both
that the fund was admitted and that it did not pass, which is the honest
shape; a row claiming `crit_mcap` where none was computed is the quiet lie
invariant 4 exists to refuse.

**Rationale.**

*Why not find a third source for ETF units outstanding.* It fixes the
cause and was the preferred option until the criteria themselves were
examined. Net assets is not market capitalisation, and 757 sessions is not
a thing a 2024 fund can have. A better share count would admit VOO and
leave IBIT excluded for a reason that is about the calendar rather than
about IBIT. The criteria are the wrong instrument here, not the data.

*Why not exempt only `crit_mcap`.* It admits VOO and not IBIT, which is
the same availability-driven split one criterion further down. If the
criteria do not apply to funds, they do not apply.

*Why this is safe, and it rests entirely on one distinction.*
`ETF_TICKERS` and `SEC_NON_FILER_TICKERS` were deliberately separated in
ADR 147: the first answers "is this an instrument rather than a company",
the second "does SEC serve companyfacts". That first question is exactly
the one this exemption turns on, and it is answered by explicit membership
rather than inferred from a missing field -- which ADR 147 already insists
on, because an ETF has *no* sector while an equity with a blank sector has
an unpopulated one, and both render as NULL.

*Why the exemption lives in `core/` and not at the call site.* Invariant 2.
`jobs/compute.py` and `research/candidates.py` both decide membership, and
ADR 129 records what two copies of `_in_trade` cost when they drifted.

*What this does not do.* It does not admit an equity on a missing input.
`test_etf_universe_exemption.py::test_an_equity_still_has_to_pass` is the
guard, and ADR 129 is why it matters: `in_trade` failing open admitted
18,805 events on 566 tickers that had never been evaluated.

**Consequences.**

- All four ETFs are `in_trade` as of 2026Q2 and will be polled from the
  next session.
- **The study population grows, so ADR 112 wants re-measuring**, for the
  same reason ADR 148's sector backfill did. QQQ's 34,687 events were
  always in it; SPY, VOO and IBIT now join as their events are generated.
  A fund's events entering `cell_stats` is a real change to what the cell
  grid measures, and it should be measured rather than assumed neutral.
- **Only 2026Q2 is evaluated.** The other 65 quarters are unevaluated for
  all three new tickers, so they contribute no history yet. Measured at
  2m23s per quarter for a four-ticker subset, that backfill is ~2.6 hours
  and belongs in an overnight slot. Recorded in `BACKLOG.md`.
- IBIT is admitted while sitting below its SMA200 with a negative slope.
  That is the intended reading of the decision -- membership is not a view
  -- and DESIGN's entry rules still govern whether anything fires.
- `universe_watch_consistent` is satisfied without change: `watch_reason`
  returns `None` for anything `in_trade` accepts, so an admitted fund is
  never also watched.


---

## Open item (2026-08-25): ADR 113's check 5 names a baseline that cannot be built

**Not a decision made here.** Recorded with its cost so it can be made.

ADR 113's fifth promotion check reads:

> the model's out-of-sample pinball loss must beat **the unconditional
> baseline** -- the same **per-ticker-year** empirical distribution the cell
> grid measured against, fit with no features.

Those two phrases describe different objects, and the second one cannot be
constructed across ADR 019's split. Measured 2026-08-25:

    train years      2010-2021        1,649 ticker-year keys
    validate years   2022-2023          430 ticker-year keys
    overlap                              0

The split is **temporal**, so no ticker-year appears on both sides. A
per-ticker-year quantile fitted on train has nothing to look up for any
validation event; every row falls back to a global value. Measured both
ways, the twenty head losses agree to five decimal places -- which is the
symptom, not a coincidence.

The phrase is inherited from the cell grid, where per-ticker-year baselines
were computed **within** sample (`research/baselines.py`, ADR 062). That is
a coherent thing to do for a hit rate measured in the same period. It does
not transfer to an out-of-sample check.

**The reading taken for now** is the global unconditional quantile of the
training labels: strictly no features, fitted on train, scored on validate.
It is the loss-minimising constant, so a model that cannot beat it has
extracted nothing from twenty-one features that one number does not carry.
`core/pinball.py::unconditional_quantile` implements it.

**The bar it sets**, weighted by `1/|cluster|`, on 125,714 train and 26,788
validate rows:

| head | τ=0.05 | τ=0.25 | τ=0.50 | τ=0.75 | τ=0.95 |
|---|---|---|---|---|---|
| `fwd_ret_5d` | 0.00573 | 0.01457 | 0.01693 | 0.01376 | 0.00541 |
| `fwd_ret_10d` | 0.00806 | 0.02110 | 0.02437 | 0.01945 | 0.00736 |
| `peak_ret_5d` | 0.00301 | 0.01033 | 0.01529 | 0.01488 | 0.00648 |
| `peak_ret_10d` | 0.00358 | 0.01314 | 0.01985 | 0.01922 | 0.00811 |

**The alternative, and why it is not obviously wrong.** A per-ticker
baseline *without* the year -- fitted on each ticker's training history and
applied to its validation events -- is constructible, since tickers do
overlap across the split. It is a harder bar, and arguably the fairer one:
a model that only learns "AAPL moves more than KO" has learned something a
per-ticker constant already knows.

Choosing it would make check 5 stricter and make the ADR 113 kill criterion
more likely to fire. That is a reason to consider it, not a reason to avoid
it -- but it is a change to a pre-registered criterion and so belongs to
whoever set the criterion, not to the session that noticed the wording.


---

## 155. Quantile coverage fails and DESIGN §7.6 has no repair for it

**Date:** 2026-08-25. **Status:** Provisional — **a decision is required and is not made here.** Four options with their costs. Blocks the Phase 6 gate.

**Context.**

Measured on validate 2026-08-25, realised fraction at or below
$\hat{Q}_	au$:

| | τ=0.05 | τ=0.25 | τ=0.50 | τ=0.75 | τ=0.95 |
|---|---|---|---|---|---|
| **target** | 0.05 | 0.25 | 0.50 | 0.75 | 0.95 |
| `terminal_h5` | 0.087 | 0.328 | 0.553 | 0.760 | 0.935 |
| `terminal_h10` | 0.090 | 0.335 | 0.544 | 0.761 | 0.937 |
| `peak_h5` | 0.077 | 0.297 | 0.527 | 0.738 | 0.934 |
| `peak_h10` | 0.073 | 0.285 | 0.503 | 0.724 | 0.925 |

Both tails err **outward**: too much mass below $\hat{Q}_{0.05}$ and too
little below $\hat{Q}_{0.95}$. A nominal 90% band covers about 85%. The
lower half is worse — every head over-covers at τ=0.05 and τ=0.25 by 45–75%
in relative terms.

This is the failure DESIGN §7.6 names: *"The product **is** the
probability, so calibration is the metric."* Pinball loss rewards being
close on average; coverage asks whether the number means what it says.
**Eighteen of twenty heads won check 5 while failing this.**

**§7.6's repair does not apply.** It specifies isotonic regression fitted
on validation, *"applied to every binary head"*, and says quantile heads
are **"checked by coverage rather than recalibrated"**. ADR 113 retired
every binary head. So the design prescribes a repair for heads that no
longer exist and prescribes none for the heads that do — the same staleness
class as DESIGN §7.4 and the Phase 6 gate's Brier-score item.

**A likely cause, not yet tested.** DESIGN §7.5's hyperparameters are
deliberately conservative for an effective sample near 8,000:
`num_leaves=15`, `max_depth=4`, `min_child_samples=100`, `lambda_l2=5.0`.
Every one of those shrinks predictions toward the centre of the training
distribution. **Under-dispersion is the expected consequence of heavy
regularisation on a quantile fit**, and the observed error is exactly
under-dispersion. That makes "the fan is too narrow" a plausible artifact
of the fit rather than a property of the data — and it is cheap to test.

**The options.**

**A. Test the cause before patching the symptom.** Relax regularisation
(`lambda_l2` down, `num_leaves` up) and re-measure coverage. Costs one
re-run — ~5 minutes for the fan, measured. Consumes no data split, and if
it works the model is calibrated rather than corrected. Risk: relaxing
regularisation on a thin effective sample is what the conservative
parameters were chosen to prevent, so it may buy coverage at the cost of
out-of-sample loss. Both are measurable in the same run.

**B. Quantile recalibration.** Learn a monotone map $	au \mapsto 	au'$
on a calibration split such that realised coverage at $	au'$ equals
$	au$, then serve $\hat{Q}_{	au'}$. Cheap, standard, preserves the
fitted shape. **Consumes a calibration split.**

**C. Conformalised quantile regression.** Widen each interval by the
empirical quantile of the conformity score on a calibration split. Gives a
finite-sample marginal coverage guarantee, which is stronger than anything
else here. **Consumes a calibration split**, and widens uniformly, so it
repairs marginal coverage without fixing conditional coverage.

**D. Ship nothing until the fit meets coverage unaided.** Strictest and
most faithful to §7.6's stated position that quantile heads are checked
rather than recalibrated. Costs whatever A takes, with no fallback if A
fails.

**The cost B and C share, and it is the real constraint.** Both need a
calibration set that is neither train nor the thing coverage is verified
on. Validate is **26,788 rows**. Splitting it gives ~13,400 to calibrate
and ~13,400 to verify, halving the precision of both. The holdout is not
available — it is evaluated once, at the end, and spending it here would
end the study's only unbiased estimate on a calibration step.

**Recommendation, which is not a decision.** Run **A** first. It is the
only option that addresses the cause, it consumes no data, it takes
minutes, and its result narrows the choice: if coverage comes right, B and
C are unnecessary and D is satisfied. If it does not, the miss is a
property of the data and **C** is the honest repair, with the validate
split halved and that stated wherever the numbers appear.

---

### Measured 2026-08-25, after the options above were written

**Option A was tested and is ruled out.** Four regularisation settings,
coverage and validation loss measured together so a setting buying coverage
by predicting worse could not hide:

| variant | mean abs coverage error | sum loss |
|---|---|---|
| baseline (§7.5) | 0.0386 | 0.05497 |
| `lambda_l2=1.0` | 0.0384 | 0.05497 |
| `lambda_l2=0`, 31 leaves, depth 6 | 0.0394 | 0.05501 |
| `lambda_l2=0`, 63 leaves, depth 8, `min_child=20` | **0.0399** | 0.05513 |

Relaxation does not improve coverage. It degrades it slightly and degrades
loss slightly. `peak_h5` worsens monotonically, 0.0258 → 0.0285. The
regularisation hypothesis in this ADR was wrong.

**The root cause is a regime shift, and the proof is a model with no
features.** Applying the train-fitted *unconditional quantile* — a
constant, no inputs at all — to validate reproduces almost exactly the same
miss:

| τ | fitted model | constant, no features |
|---|---|---|
| 0.05 | 0.087 | 0.087 |
| 0.25 | 0.328 | 0.333 |
| 0.50 | 0.553 | 0.536 |
| 0.75 | 0.760 | 0.730 |
| 0.95 | 0.935 | 0.926 |

**So the coverage failure is not the fit, the regularisation, or the
features.** The label distribution moved between the two splits:

    fwd_ret_5d      train      validate      shift
      q05         -0.05790    -0.07603     -0.01812
      q95          0.05915     0.07049     +0.01133
      sd           0.04156     0.04713     +13%

Train is 2010–2021, validate is 2022–2023. Validate is 13% more volatile
with both tails pushed outward. A fan fitted on the calmer era is too
narrow in the more violent one, necessarily, and that accounts for the
entire observed miss.

**The uncomfortable part.** `vix_close`, `rv_pct_252d` and `bb_width_pct`
are all features, so the model *can* see contemporaneous volatility and
still under-disperses. It learned the map from those features to future
dispersion during a low-volatility decade, and the map did not transfer.
Conditioning on volatility is not the same as conditioning on the level of
volatility being unprecedented in the training data.

**Corrected 2026-08-25, later the same day.** Three further diagnostics
show "entirely a regime shift" was too strong.

Coverage measured **inside train**, on fold-validation years 2015–2021,
gives mean absolute error **0.018** against **0.039** on validate. Half the
miss exists within the training era. Split by validate year, **2023 scores
0.018 and 2022 scores 0.047**: 2023 is as well calibrated as a normal
in-train year and the failure is almost entirely 2022. And training saw VIX
from 9.1 to 82.7 while validate spans 12.1 to 36.5, so nothing here is
out-of-range volatility.

So there are **two** components: a stable fit-induced under-dispersion of
about 0.018 present every year, and a 2022-specific excess on top. The
first is consistent and therefore correctable. The second is a mid-VIX
grinding drawdown the features do not describe.

**The options are re-cast by this.**

**A is closed.** Measured, not argued.

**B and C are stronger than the first correction suggested, if fitted on
the right period.** The stable 0.018 component is consistent across 2015–
2021 and 2023, so a calibration fitted on normal years repairs it and
transfers. What must **not** happen is fitting on 2022: that absorbs a
shock into the band and leaves it too wide for a year resembling 2023,
where the model is already nearly right. The calibration period is now the
decision, not the technique.

**D means something different now.** "Wait for a fit that meets coverage
unaided" is, given this finding, waiting for a model that anticipates
volatility-regime change. That is a much larger claim than the one Phase 6
set out to test, and nothing in ADR 112's evidence suggests it is
available.

**A fifth option this measurement creates.** Report coverage **as a
property of the era** rather than repairing it: serve the fan with its
measured train-era calibration stated, and treat a coverage miss on new
data as a *regime signal* rather than a model defect. This is the only
option that does not claim something untrue, and it is the least useful to
a reader who wants a number. It should be considered precisely because it
is the honest one.

**Recommended sequencing, still not a decision.** The choice is now between
C (marginal coverage, era-bound, useful, and requiring the validate split
be halved) and E (honest, weaker, no data cost). B is dominated by C, which
offers a finite-sample guarantee for the same data cost.

---

**What must not happen.** Serving any head at its current calibration. A
reader told "5% chance of a move below X" who sees it 8.7% of the time has
been handed a wrong number, and invariant 8 exists because that claim has
to hold.

---

### Decided 2026-08-26 — **option C, calibrated on normal years, excluding 2022**

**Status: pinned.** User decision, taken against the measurements above.

Conformalised quantile regression. Each interval is widened by the
empirical quantile of the conformity score, giving a finite-sample marginal
coverage guarantee -- the only option here that guarantees anything.

**The calibration period is part of the decision, not an implementation
detail.** Fit on **2015-2021 and 2023**; **exclude 2022**.

That follows from the decomposition: a stable fit-induced under-dispersion
of ~0.018 is present in every ordinary year, and 2022 carries a further
excess on top (2022 scores 0.047, 2023 scores 0.018, in-train years score
0.018). Calibrating on the stable component repairs it and transfers.
Calibrating on 2022 absorbs one regime's shock into the band permanently,
leaving it too wide for a year resembling 2023 where the fan is already
close to right.

**What this costs and what must be said wherever the numbers appear.**

- Validate is **26,788 rows** and gets split. Both the calibration estimate
  and the coverage verification lose precision, and that halving is stated
  beside any coverage figure derived from it.
- The guarantee is **marginal, not conditional**. Coverage holds on
  average across the fan; it does not promise coverage within any
  particular cell, drawdown bucket or volatility regime.
- The calibration is **era-bound by construction**. It repairs the
  component that was stable across 2015-2021 and 2023. It does not
  anticipate the next 2022, and nothing here claims it does.

**Holdout is not touched.** It is evaluated once, at the end. Spending it
on calibration would end the study's only unbiased estimate on a
preprocessing step.

**Option E is not adopted, and its point is kept.** Reporting coverage as
an era property claims nothing untrue, and the reason to prefer C is that
Phase 6 exists to produce a calibrated number rather than a caveat. The
honest part of E survives as the disclosure requirements above: a coverage
miss on new data is still evidence of regime change, and is to be read that
way rather than as a bug.


---

## 156. ETF market cap: `netAssets` disagrees with `sharesOutstanding`

**Date:** 2026-08-26. **Status:** Provisional — **a decision is required and is not made here.** Investigated in response to VOO and IBIT carrying no market cap.

**Context.**

ADR 154 admits ETFs to the trade universe unconditionally, because
`crit_mcap` could not be judged for VOO and IBIT: `cscan shares` resolves
neither, so `mcap_usd` is NULL. The obvious repair was to read `netAssets`
instead, which Yahoo publishes for all four funds.

Measured 2026-08-26:

    SPY    sharesOutstanding 917,782,016   netAssets   $795.3B
    QQQ    sharesOutstanding 393,100,000   netAssets   $452.8B
    VOO    sharesOutstanding None          netAssets $1,686.9B
    IBIT   sharesOutstanding None          netAssets    $46.5B

**The two fields do not agree.** For a fund, price times shares *is* net
assets by construction -- that identity is what a NAV means. It does not
hold here:

    SPY    netAssets/price = 1,038,381,651   vs shares   917,782,016   ratio 1.131
    QQQ    netAssets/price =   637,101,310   vs shares   393,100,000   ratio 1.621

Thirteen percent apart for SPY and **sixty-two percent** for QQQ. One of the
two numbers is wrong, or they are measured at different times, or
`netAssets` for QQQ counts something the share class does not.

**This is what makes it a decision rather than a patch.** Switching to
`netAssets` would not merely fill two NULLs. It would move QQQ's recorded
market cap from **$289B to $453B**, a 57% change to a value that already
exists, is stored across 66 quarters, and feeds `crit_mcap`.

**What is not at stake.** Neither fund's membership changes: ADR 154 already
admits all four regardless, and `max_mcap_usd` is $6T so nothing trips the
plausibility guard. `mcap_rank` is NULL on all 51,950 universe rows, so no
ranking is affected. The change is to a recorded quantity, not to who is
in the study.

**The options.**

**A. Do nothing.** VOO and IBIT keep NULL market caps and stay admitted by
exemption. Honest, and the exemption already exists precisely because the
criteria ask a company-shaped question. Costs nothing and hides nothing.

**B. Use `netAssets` for every ETF.** Fills the two NULLs and changes SPY
and QQQ by 13% and 57%. Defensible only if `netAssets` is the more reliable
of the two fields, which this measurement does not establish -- it
establishes that they disagree.

**C. Use `netAssets` only where `sharesOutstanding` is absent.** Fills VOO
and IBIT, leaves SPY and QQQ untouched. Attractive, and wrong in a specific
way: two funds would then carry market caps computed by different methods
that demonstrably differ by up to 62%, with nothing in the row saying which
was used. That is the `events.sector`-shaped problem again -- a column whose
meaning varies by row.

**D. Find out which field is right first.** The 62% gap on QQQ is large
enough to be a data defect rather than a timing difference, and neither
number has been checked against the issuer. Invesco publishes QQQ's shares
outstanding daily; one comparison settles it.

**Resolved the same day, from data already held.** Option D turned out not
to need the issuer. `shares_outstanding` answers it:

    SPY   917,782,016   on 2020-08-28, 2020-11-26 and 2021-03-17
    QQQ   393,100,000   on 2020-08-28, 2020-11-26 and 2021-03-17
          99 and 96 rows respectively, every one ending 2021-03-17

**The number never changes.** The same value is stamped on every date, the
series stops in March 2021, and it is the identical figure Yahoo's `info`
returns today. That is not a time series; it is one stale constant repeated.

An ETF's share count moves **daily** through creation and redemption -- that
mechanism is what holds the price near NAV. A constant share count is
therefore not a disagreeing measurement, it is a wrong one.

So the 62% gap on QQQ is not ambiguity between two plausible figures. It is
a current number against one frozen five years ago, and **QQQ's recorded
$289B is computed from a 2020 share count**.

**Recommendation: B, use `netAssets` for every ETF.** Not because it fills
two NULLs, but because the alternative is provably stale for all four. The
SPY and QQQ revisions are corrections rather than changes.

**What implementing it still needs.** `mcap_usd` is `shares * close`, so
`netAssets` cannot simply be stored in `shares_outstanding.shares` without
that column meaning two different things. Either it carries the derived
share count `netAssets / close` with a `source` that says so -- the
identity is exact for a fund, which is what makes the derivation honest
rather than a fabrication -- or `universe` learns to read an ETF's market
cap from a different place. The first is smaller and keeps one code path;

---

### Decided 2026-08-26 — **option B, implemented as (i)**

**Status: pinned.** User decision.

`shares_outstanding` carries the **derived share count** `netAssets /
close`, with `source` recording that it was derived rather than reported.
For a fund the identity `price x shares = net assets` is exact by
definition of NAV, so the division is a change of units and not an
estimate. One code path; `mcap_usd = shares * close` keeps working
unchanged and every consumer of it does too.

Rejected: teaching `universe` to read an ETF's market cap from a second
place. It is more explicit about the two being different quantities, and it
adds a branch to a path that currently has none -- for a value that the
identity makes equivalent anyway.

**Consequences, and this is not a routine backfill.**

- **QQQ's stored `mcap_usd` moves $289B -> $453B across 66 quarters, and
  SPY's by 13%.** These are corrections, not revisions: the existing
  figures are computed from a share count frozen at 2021-03-17 and repeated
  identically on every one of 96-99 rows.
- **VOO and IBIT stop being NULL**, which is what prompted the
  investigation.
- **No membership changes.** ADR 154 admits ETFs unconditionally,
  `max_mcap_usd` is $6T, and `mcap_rank` is NULL on all 51,950 universe
  rows.
- **It wants its own migration and a `RESULTS.md` note**, not a ride along
  in a nightly. Rewriting a recorded quantity across ten years of history
  should be a dated, reversible act that a reader can find later.
the second is cleaner and costs a migration.

**Also worth recording:** this is why the ETF market caps stopped being
trustworthy in 2021 without anything reporting it. `run_shares` kept
succeeding, `crit_mcap` kept passing, and the number behind it had been
constant for five years.

**What must not happen.** Option C, or any variant where `mcap_usd` is
derived one way for some rows and another way for others without the row
recording which. `SharesPlausibility.source` exists on
`shares_outstanding` for exactly this reason; whatever is decided, the
provenance has to travel with the number.

---

## 157. `events.sector` is a current snapshot, and that is accepted look-ahead

**Status.** Accepted, 2026-08-28. Amends ADR 135's look-ahead note; moved
out of `BACKLOG.md` because it is a standing trade-off rather than work
waiting to be done.

`tickers.sector` holds **one current value per ticker with no history**. A
company GICS-reclassified in 2018 therefore carries its post-2018 sector on
its 2010 events, and a model conditioning on sector is reading a fact that
did not exist at signal time. That is look-ahead, of the mild kind ADR 135
names.

**Accepted, for three reasons.** Reclassifications are rare; the
alternative is dropping the only categorical feature DESIGN §7.3 asks for;
and point-in-time GICS history is a paid data source this project does not
have. Inventing one is worse than recording the limit.

**`universe.mcap_usd` does not have this problem**, and the contrast is the
useful part: that table is evaluated quarterly and every lookup is bounded
`as_of <= signal_date`, so an event gets the market cap that was on file
when it fired. Sector has no such table to read from. Any future fix looks
like giving sector the same shape -- a dated table with an `as_of` bound --
not like patching the lookup.

**`c2b91e4a7d08` made it more visible, not worse.** That migration
populated `events.sector` from the snapshot, so the value now sits on the
event row where it reads as authoritative. The caveat is recorded in the
database itself:

    COMMENT ON COLUMN events.sector IS
      'GICS sector from tickers.sector, which has NO history: a company
       reclassified in 2018 carries its post-2018 sector on its 2010
       events. Mild look-ahead, accepted (ADR 135, ADR 157).'

so a reader who finds the column populated learns the limit from `\d+
events`, not only from this file.

**Not to be confused with the other sector entry.** "123 tickers have no
sector" is a different thing: those are preferred shares and baby bonds,
which have no GICS sector to begin with. This ADR is about the tickers that
*do* have one.

**What would reopen this.** A point-in-time GICS source, or evidence that
sector materially moves a result -- ADR 112 found nothing surviving FDR, so
the feature is not currently carrying weight that would justify buying data
for it.

---

## 158. The poller should write serving directly, not research

**Status.** Proposed, 2026-08-28. Not implemented. Amends ADR 153's
direction of travel; consistent with ADR 140's "poller rows were never
authoritative".

**The problem this solves is scheduling, not performance.** `cscan poll`
runs on the workstation and writes the **research** store, so the
workstation must be awake and free of conflicting writers for the whole
session. That single fact decides when a rebuild can run, when the machine
can be shut down, and when it can be updated. It is the binding constraint
on the development day, and it exists for no reason the data requires.

**Correction, 2026-08-28, before implementation.** The paragraph below
originally read "every poller row is provisional". That is false, and the
distinction is the hard part of this ADR.

`_sweep_provisional_poll_rows` deletes only `events` rows whose `run_id`
begins `poll`, same-day. It explicitly **preserves `signal_reports`** --
nulling `event_id` while `ticker`, `fired_at` and `state_json` stay, so
"the observation survives and says plainly that no event survived it". It
does not touch `poller_sessions` at all.

Those two tables are the durable record of what the poller saw: 1,656
reports and 18 sessions spanning 2026-08-03 to 2026-08-27, and they are why
a past date still shows fired-at timestamps. They currently live in
research and are **synced research -> serving**, which is the opposite of
the direction this ADR proposes.

**So the poller has two kinds of output, not one**, and only the first can
move:

| output | nature | may be born on serving? |
|---|---|---|
| `events` (`run_id` like `poll%`) | provisional, swept nightly | yes |
| `bars_live`, `quotes_live` | per-tick, rewritten | yes |
| `signal_reports` | **permanent observation** | no -- research needs it |
| `poller_sessions` | **permanent**, ADR 084 reads `coverage_pct` | no -- research needs it |

**The fix is a reverse path, and it must be part of the change**: nightly
gains a serving -> research pull for `signal_reports` and `poller_sessions`
before its own sweep runs. Without it, moving the poller silently stops
research accumulating the record Phase 6 is meant to analyse -- and the
loss would be invisible until someone asked a question of data that was
never there.

The alternative -- have the poller write those two to research directly and
everything else to serving -- reintroduces the research dependency and
defeats the purpose, since the workstation would still need to be awake.

**Why the `events` write is not load-bearing.** Every poller *event* row is
provisional. `_sweep_provisional_poll_rows` deletes them from research on
the next nightly, which then recomputes the authoritative version from
bars. So research receives rows, uses them as the source for the serving
push, and deletes them hours later. Nothing downstream depends on their
having been in research.

`_push_live`'s own docstring states the current direction: *"the research
store is the source of truth and has already been written by the time this
runs"*, and `run_live_sync(..., source=engine)` reads research to write
serving. This ADR inverts that for live rows only.

**Feasibility, checked rather than assumed.** The poller reads four things,
and serving carries all four:

| poller reads | on serving |
|---|---|
| `universe` — membership and `watch_reason` | yes, synced |
| `indicators` — the t-1 row | yes |
| `market_days` | yes |
| `events` — existence and id checks for dedupe | yes |

It writes `poller_sessions`, `bars_live`, `events`, `quotes_live`; serving
holds all four, because `v_screen_live` already joins `bars_live` there.

**What it removes.** The live push (ADR 153) and its failure path; the
research sweep; the concurrent-writer conflict on research during market
hours; and nightly's per-tick sync overhead. The serving sweep stays and
gets simpler.

**What it costs.**

- **A staleness contract.** The poller would read `universe` and
  `indicators` from serving, which is one sync behind. During a session
  both stores hold the previous night's data, so it is equivalent *today* —
  but a missed sync would silently poll a stale universe, and that must be
  detected rather than assumed. The serving store already has a watermark;
  the poller should refuse to start if it is older than the last trading
  day.
- **`events.run_id` references `runs`.** Serving carries a narrow subset
  (ADR 053's note in `sync.py`), so the poller's own run row has to be
  written there.
- **Losing a session's live rows if the Pi dies.** Acceptable: they are
  provisional by construction and nightly recomputes them.

**What it does not buy, stated so it is not oversold.** Not CPU or heat —
the poller sleeps five minutes between sub-second bursts. Not the ability
to move the research database, which is 22 GB against 27 GB free on the
Pi's SD card. The rebuilds remain the real load and remain on the
workstation.

**Not to be confused with running `wait_and_poll.ps1` on the Pi unchanged.**
That variant still writes research over an SSH tunnel originating from the
workstation, so it frees nothing. The value is entirely in removing
research from the live write path.

### The trading-day guard, verified against a real firing 2026-08-29

The timer fires `*-*-* 00:00:00`, every day, and `cscan poll` checks only the
time of day rather than the date, so a weekend would otherwise have polled a
closed market and written signals off Friday's stale quotes into the store
the site serves.

First real weekend under ADR 158, and it held:

    00:00:05  Started capitalscan-poller.service
    00:00:05  sudo ... psql -c 'SELECT 1 FROM trading_days WHERE d = '2026-08-29''
    00:00:05  [SKIP] 2026-08-29 is not a trading day. Nothing to poll; exiting cleanly.
    00:00:08  capitalscan-poller.service: Deactivated successfully.

Three seconds, and `Deactivated successfully` rather than `Failed`, so
`Restart=on-failure` did not retry it. **Zero `poller_sessions` rows exist
for 2026-08-29**, which is the outcome that actually matters — a session row
on a non-trading day would also have polluted ADR 084's `coverage_pct`.

### Consequence discovered in production, 2026-08-28

**A second writer makes a surrogate `id` ambiguous, and nothing announces
it.** The first live session under this ADR died at 06:56, one tick after
the open:

    duplicate key value violates unique constraint "signal_reports_pkey"
    DETAIL:  Key (id)=(21) already exists.

Serving held **1,829 `signal_reports` with `signal_reports_id_seq` at 21**.
The cause is mechanical and was invisible for as long as the design had one
writer: `run_sync` copies every row with its own explicit id, and an INSERT
that supplies an id **does not advance the sequence**. Serving had been
copied into for months and never inserted into, so its sequences had never
moved. `events_id_seq` sat at 21 against a max of 39,167,955 by the same
route.

**The crash is not the damage.** Before reaching the collision the poller
wrote **18 real reports into ids 1-18** — rows whose ids say nothing about
when they were written. They committed and survived. What kept that from
destroying history was luck rather than design: research's own ids begin at
**19**, because everything below had been purged, and `pull_live_records`
upserts on `id`. One more fire before the collision would have silently
replaced a real 2026-08-03 row on the next nightly.

Three fixes, at three different distances from the cause:

1. `run_sync` now resets every sequence on the target from the catalogue
   after copying. This is the actual repair — a sequence fixed by hand
   goes stale on the next sync, because the next sync copies the ids again.
2. `poll.assert_sequences_are_ahead` refuses at startup, because (1) only
   runs at night and a store can be wrong all day. It reads the catalogue
   rather than a list of the poller's tables, so `events` was covered
   without being named.
3. The 18 rows' `event_id` values, which pointed at provisional events
   present in neither store, were set NULL and backed up to
   `reports/poller/signal_reports_id1_18_backup_20260828.csv`.

**The 977 other dangling `event_id`s on serving were left alone, and the
distinction matters.** They point at real ids in 7.6M–30M whose events were
swept by nightly or purged by generation cleanup — a normal steady state,
since `signal_reports` is durable and poller `events` are provisional.
Those ids carry information. Ids 1-18 never identified anything.

**A caution about the shape.** `pull_live_records` keying on `id` is safe
only while the two stores' id spaces stay disjoint, which fix (1) maintains
by construction: serving's sequence is set past research's max on every
sync, so new poller rows land above anything research holds. If a future
change ever lets research allocate `signal_reports` ids again, that
assumption breaks and the key must become natural rather than surrogate.

## 159. Superseded sweep generations are archived out of the database, not kept in it

**Status.** Decided 2026-08-31, applied the same day. Narrows ADR 096's
"sweep results coexist in one table" to "coexist until the question they
answered is settled, then move to cold storage". Does not move
`config_hash`.

**The problem is storage, and it had a specific size.** The research
database reached 48 GB, `capitalscan-data` 55.6 GB. `events` and `path`
were 44 GB of that across **23 `config_hash` generations**, and only about
nine were load-bearing: the live config, the earlier configs of record
each pinned by an ADR, and the prior serving generation. The rest were the
2026-08-28 to 2026-08-30 exit and holding-window sweep grid. They back the
reference tables in `RESULTS.md`, but nothing recomputes from them on any
schedule, and `cell_stats` / `rho_era` for those arms are equally static.

**The tension with ADR 034 and ADR 096.** Both require every published
number to trace to a reproducible event population. Deleting a generation
outright breaks that silently: the `RESULTS.md` row keeps its
`config_hash`, the rows it names are gone, and nothing detects the gap.

**Resolution: preserve the population, move it off Postgres.** Before any
delete, each arm's `events`, `path`, `cell_stats` and `rho_era` rows are
written to gzipped CSV under `reports/archive/sweep_arms_<date>/`, with a
`config_hashes.txt` manifest. `gzip -t` verifies each file and the line
counts are checked against the pre-delete `count(*)`. Restore is a manual
`\copy ... FROM` in FK order (`events`, then `path`, then the stats
tables) followed by `VACUUM (FULL, ANALYZE)`. ADR 096's composite key
still governs what stays resident.

**First application, 2026-08-31.** Twelve arms removed:

    753813ea1b7bb09f  a56d05a752a217ee  f2a56c9e8aa5c810  fcc6df7649798127
    6e7b11fc1c6ee599  0fdd15e962436b72  f7b31c5443d30948  0bdf21eba0ff2e34
    8dcdc265a0509005  ccfde27281981436  49fc87114751f32a  c74e355184fea7bb

16,546,848 `events` rows, 78,679,504 cascaded `path` rows, 6,144
`cell_stats`, 48 `rho_era`. Archive is 2.7 GB. Database 48 GB -> 19 GB,
volume 55.6 GB -> 24.2 GB. The same pass dropped three unused `events`
indexes (`events_feed_latest`, `events_feed_watch`, `events_cluster`) and
four stale pre-migration backup tables.

**Kept resident:**

| generation | why |
|---|---|
| `0523841076f47293` | live config (`serving_config`, arm `t5_atr20`) |
| `1835688bf7d760ba` | RESULTS Sessions 12-14, `rho_era`, ADRs 096/098/109 |
| `86e91448a65aa40b` | ADR 110, live config 2026-08-16 to 08-29 |
| `697f3ae71428d392` | ADR 111 |
| `f66729c7eda212a4` | ADR 143 |
| `bbc99a02ebdc999f` | ADR 145 |
| `a38d3ca6b58295e8` | prior serving generation, 81 runs |
| `fda16796c6e82ee4`, `185bba9a239c18f4` | full Phase 4 with benchmarks |
| `d750336f30551cab`, `f7d7bcd52ec48c22` | 2026-08-30 holding-window arms; hold until that sweep closes |

**Not touched.** `runs` keeps every job row, including the deleted arms'
`backtest_compute` / `harness` provenance. `benchmarks` had no rows for
these hashes. `universe` is keyed by `config_hash` but shared in spirit
and small; left alone.

**What it costs.**

- **A restore is an operation, not a query.** Auditing a `RESULTS.md`
  number whose generation was archived means reloading the archive first.
  The `config_hash` in the doc still identifies it; the rows are just not
  online.
- **The archive is one copy on one disk.** Until it is backed up
  elsewhere, the reproducibility guarantee for those arms is exactly as
  durable as the workstation's filesystem. `reports/archive/` is in
  `.gitignore` deliberately (2.7 GB), so git is not that backup.
- **`VACUUM FULL` took an ACCESS EXCLUSIVE lock** on `events` and `path`
  for the duration. Run only in a no-writer window: market closed, no
  nightly, no backtest, poller idle on the Pi.

**The rule going forward.** A sweep generation may be archived and deleted
once the decision it informed is recorded in `RESULTS.md` and any config
change it produced is shipped. Until then it stays resident. The live
generation and every ADR's config of record are never archived.
