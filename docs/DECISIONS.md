# DECISIONS.md

Architecture decision record for `CapitalScan`.

Format: each entry states the decision, why, and what it costs. Status is one of Pinned, Provisional, or Superseded. Never delete an entry. Mark it Superseded and add the replacement below it.

Last updated: 2026-07-28

---

## Index

| ID | Title | Status |
|---|---|---|
| 001 | Two universes: train wide, trade narrow | Pinned |
| 002 | Ten years of history, 2015 forward | Pinned |
| 003 | Compute indicators in-house | Pinned |
| 004 | Indicator parameters pinned once | Pinned |
| 005 | Signal fires on intraday touch, not close | Pinned |
| 006 | One signal implementation, shared by backtest and live | Pinned |
| 007 | Five exit conditions, measured separately | Pinned |
| 008 | Stop scaled to ATR, fixed 3% as control | Pinned |
| 009 | Target swept over 3% to 5% | Pinned |
| 010 | Intrabar ordering resolved conservatively | Pinned |
| 011 | Continuous features, headline grid capped at 12 cells | Pinned |
| 012 | Benchmark is three arms plus DCA comparison | Pinned |
| 013 | Baseline computed per ticker-year | Pinned |
| 014 | Health filter is causal and re-evaluated quarterly | Pinned |
| 015 | Drawdown from 52-week high is a first-class dimension | Pinned |
| 016 | Short side restricted to failed-health regime | Pinned |
| 017 | Upper-band signal drives trim, not naive short | Pinned |
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
| 032 | Tax reported pre and post | Provisional |
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
| 057 | Multi-signal emits one row with strength | Pinned |
| 058 | Both sides computed, only long surfaced | Pinned |
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

---

## 001. Two universes: train wide, trade narrow

Status: Pinned

Decision. Ingest 500 tickers for statistical power. Trade and display a filtered subset of 30 to 60 mega-caps chosen by the rule in ADR 014.

Rationale. Confluence signals fire on roughly 4% of days. Ten tickers over two years yields around 200 raw events, and clustering on market-wide down days cuts the effective sample to about 38. Standard error at $n_{eff} = 38$ and $p = 0.4$ is 7.9 points, so the detectable effect is 22 points. The target effect sits between 5 and 15 points. Widening the training universe is the only way to see it.

Cost. Ingest and storage grow, both to a level with no practical impact. Roughly 1.25M daily bars, under 200MB.

Schema. `universe_train` and `universe_trade` as separate flags on the `universe` table.

---

## 002. Ten years of history, 2015 forward

Status: Pinned

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

Status: Pinned

Decision. A band breach registers when the intraday high or low crosses the band level, not when the close does.

Rationale. Matches the live rule, where a 5-minute poll compares the current price against bands fixed at the prior close. Also roughly triples event count. Close-based breach runs 4 to 6% of days. Intraday breach runs 12 to 18%, and confluence with a Stochastic extreme lands near 3 to 6%.

Cost. Entry price is uncertain within the poll window. ADR 007 handles this by measuring four entry timings.

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

Status: Pinned

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

---

## 015. Drawdown from 52-week high is a first-class dimension

Status: Pinned

Decision. Report edge separately across drawdown buckets: 0 to 10%, 10 to 20%, 20 to 35%, above 35%.

Rationale. This is the Meta-versus-Intel problem. Meta 2022 fell about 75% and recovered fully. Intel 2020 through 2024 fell about 70% and did not. Same size filter, same trend filter at entry, opposite outcomes. Cisco 2000 fell 86% and every step down was a lower-band confluence touch. A regime break and a routine dip look identical at the moment of the touch, so the separating information has to live in the feature set or the strategy carries an unmeasured left tail.

Hypothesis under test. Edge is positive and stable in the first two buckets and turns negative past 35%, because deep mega-cap drawdowns are usually thesis breaks. If that holds, the rule is: trade shallow dips, stand down on deep ones. That single cut is potentially worth more than either indicator.

---

## 016. Short side restricted to failed-health regime

Status: Pinned

Decision. Short entries require at least one of: close below the 200-day SMA, 200-day slope negative, or drawdown past 20%. Naive always-short on upper-band confluence is run only as a control.

Rationale. The short is not the mirror of the long, for three reasons.

Drift runs against the position. On a name compounding at 25%, the 5-day drift near 0.50% helps a long and hurts a short, costing roughly a point of hit rate before anything else.

Band walking is the larger issue. During strong advances, price rides the upper band for weeks while %K stays pinned above 80, printing touches nearly every day. The upper signal fires most often during exactly the periods a short bleeds worst, and most often in the names the health filter selected. Fear-driven selloffs resolve in days. Greed-driven advances grind for months. There is no lower-band equivalent.

Loss geometry. A losing short grows as a share of capital while a losing long shrinks. Mega-cap earnings gaps of 8 to 15% are routine, and a 5-day window contains earnings roughly 8% of the time.

Coherence. The long trades dips inside uptrends. The short trades rips inside downtrends. Same indicators, opposite regime, non-overlapping universes at any given time.

---

## 017. Upper-band signal drives trim, not naive short

Status: Pinned

Decision. Primary use of the upper-band and %K-above-80 signal is trimming a held core position, with redeployment on the next lower-band signal. Backtested directly against buy-and-hold on the same names.

Rationale. Carries no borrow cost, no gap risk, no wash-sale interaction, and no undefined loss. Matches the actual described behavior of selling into strength and buying back on weakness.

Expected ranking. Trim-and-redeploy beats regime-filtered short on risk-adjusted terms, and both beat naive short, which likely shows negative edge from band walking.

---

## 018. Options deferred to Phase 7

Status: Pinned

Decision. v1 trades long shares and regime-filtered short shares only. No calls, puts, or spreads.

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

Status: Provisional

Decision. Backtest reports pre-tax and after-tax results separately. Sleeve gains taxed at short-term ordinary rates. Wash-sale interaction flagged.

Rationale. The core-plus-sleeve structure trades the same tickers held long term. Selling a sleeve position at a loss triggers wash-sale disallowance if the ticker was bought within 30 days either side, including the core position and dividend reinvestment. This is a real accounting cost of the strategy, not a footnote.

Note. This is a modeling assumption for evaluation, not tax advice. Rates and rules vary by situation.

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

Status: Pinned

Decision. Multiple signals firing on the same ticker within the same trading day emit one event row.

```sql
signal_type       text,      -- most specific that fired
signal_types_all  text[],    -- all concurrent
signal_strength   int        -- count of distinct concurrent signals
```

Specificity ranking, fixed: `CONFLUENCE_LOW` > `BB_LOWER_TOUCH` > `STOCH_OVERSOLD`, mirrored for the high side. `signal_strength` becomes both a model feature and a headline grid dimension.

Rationale. The strategy operates on a days-to-months horizon, so sub-day granularity is noise. Two indicators firing hours apart is one alignment event, and the advisory action is identical either way. Emitting separate rows would double-count in pooled statistics. Whether strength 2 outperforms strength 1 becomes measurable rather than assumed.

---

## 058. Both sides computed, only long surfaced

Status: Pinned

Decision. The engine constructs short events alongside long ones from the first run, at the cost of a sign flip and borrow accounting. Only long events surface in v1's UI and notifications.

Rationale. Zero marginal cost, and it gives the statistics layer long-versus-short asymmetry data immediately. ADR 016's band-walking hypothesis becomes testable before any short is ever recommended.

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

## Open items

| Item | Options | Current lean |
|---|---|---|
| Point-in-time index membership | Scrape Wikipedia history, or accept survivorship bias and state it | Scrape, note residual error in RESULTS.md |
| Historical earnings dates | Finnhub free tier, Nasdaq scrape, or drop the feature | Finnhub, since earnings contamination is the largest 5-day confound |
| Point-in-time market cap | Shares outstanding from filings, or price-times-current-shares approximation | Filings where available, approximation flagged elsewhere |
| Polling home | Actions cron with internal loop, or persistent Modal function | Actions until the live log matters, then Modal |
| Non-US mega-caps | Add ASML, SAP, Novo, Toyota, Samsung, LVMH for lower correlation | Add if effective sample falls short after clustering adjustment |

---

## Phase gates

Phase 1. Ingest 500 tickers from 2015. Compute indicators. Apply the health filter to build the trade universe. Produce three outputs: the three-arm comparison, the drawdown slice, and the time-to-MFE distribution. Nothing downstream matters if this shows no separation.

Phase 2. Full event table with MFE/MAE and four entry timings. Target-by-stop sweep surface. Headline cell table with FDR correction on the validation split.

Phase 3. Quantile model on shares only, long and regime-filtered short. Calibration curves and pinball loss.

Phase 4. Frontend and chat layer over shares-only results.

Phase 5. Synthetic options pricing, labeled synthetic. MCP server.

Phase 6. One month of real option chains. Publish synthetic pricing error.

Holdout evaluation runs once, at the end, and gets published whatever it says.

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
