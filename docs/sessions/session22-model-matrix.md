# Session 22 — the model's training matrix

**Date:** 2026-08-25. **Phase 6 opens** (ADR 113). No model is fitted here.

## What this session is

The first buildable increment of Phase 6: a frame you can inspect before
anything fits on it. `capitalscan/models/` was empty and `predictions` held
0 rows.

Deliberately excluded: no LightGBM call, no purged CV, no calibration, no
promotion gate. ADR 113 fixes a kill criterion in advance — if the model
cannot beat the unconditional baseline in pinball loss, Phase 6 closes and
that is published. A training frame with a look-ahead leak produces a model
that passes and should not, so the matrix being provably clean is the
foundation the falsifiability rests on.

## What it turned out to be

Much smaller than planned, because two thirds of the work already existed.

**Labels were already materialised.** `research/peak_labels.py` writes
`peak_ret_{h}d` and the backtest writes `fwd_ret_{h}d`, so ADR 113's four
labels — $R_5$, $R_{10}$, $M_5$, $M_{10}$ — are columns on `events`, not
something to derive from `path`:

| split | R₅ | R₁₀ | M₅ | M₁₀ | total |
|---|---|---|---|---|---|
| train | 135,348 | 135,348 | 126,455 | 126,455 | 135,348 |
| validate | 28,185 | 28,185 | 26,855 | 26,855 | 28,185 |
| holdout | 63,426 | 62,952 | 59,279 | 58,823 | 64,010 |

So the session reduced to feature assembly plus the leak test.

## The built frame

`research/features.py::build_training_frame`, measured against
`f66729c7eda212a4`:

```
train      125,714 rows   401 tickers   dropped: 741 ETF, 8,893 no label
validate    26,788 rows   305 tickers   dropped:  67 ETF, 1,330 no label
sector levels: 11        missing_sector: 0
```

Label means are the sanity check that the two families are what they claim:
`peak_ret_5d` 0.0272 against `fwd_ret_5d` 0.0028, and $M_h \ge R_h$ by
construction.

## Three things found by building it rather than reading

**1. DESIGN §7.4 was stale.** It still specified ADR 064's eleven heads —
five terminal quantiles, four reachability, two adverse — superseded by
ADR 093 and then by ADR 113's twenty. Anyone reading §7.4 would have built
retired heads. Rewritten, which ADR 093 explicitly authorised.

**2. `events.sector` and `events.mcap_usd` are NULL on all 227,543 rows.**
Both columns exist. The values live in `tickers.sector` (11 GICS levels
after ADR 148) and `universe.mcap_usd` (47,181 values, quarterly from
2010-03-31).

The hazard is the shape rather than the outcome. An unqualified `sector` in
a query over `events JOIN tickers` resolves to the `events` copy — a column
that exists, is spelled correctly, and is empty. Both features were one
unprefixed name from shipping as all-NULL, and no test would have objected,
because *"the column is there"* and *"the column has values"* are different
claims. Every selected column is now qualified and a test pins the sources.

**3. Three of DESIGN §7.3's twenty-two features are not on the event row.**
`bb_mid`-based distance, `atr_14/close`, and `vix_pct_252d`. Each is in
`indicators` at t−1, so the fix is a join or three new columns — and both
are decisions, since a join reintroduces the sourcing question the
look-ahead argument rests on closing, and `entry_price` cannot supply the
`close` denominator because it is priced at *t*. Nineteen raw plus two
derived are built and clean.

## Where the features come from, and why it matters

Every feature is read off the event row, which is written at signal time
from indicators read at t−1 (invariant 3). That is a structural look-ahead
argument rather than a checked one, and it survives only while the rule
holds.

Two documented exceptions:

- **`sector`** from `tickers`. DESIGN §7.3 classifies it as *Static* —
  instrument metadata, not market state. The caveat is real and recorded:
  `tickers.sector` has no history, so a 2018 GICS reclassification carries
  backward onto 2010 events. Accepted, in BACKLOG.
- **`mcap_usd`** from `universe` via a lateral bounded by
  `as_of <= signal_date`. This needs no caveat: it is the same causal
  reading `core.universe.in_trade` and `v_watchlist` already use.

## Tests

`test_model_features.py`, 21 tests. The load-bearing one is
`test_no_feature_is_an_outcome_column`: `events` carries each signal's
outcome in the same row as its state, so a feature list assembled by
"select the numeric columns" would train a model to predict a number it was
handed. It would score beautifully and mean nothing. That is invariant 3's
failure mode one layer up, and it fails the same way — silently, looking
like success.

`FORBIDDEN_COLS` is enumerated rather than prefix-matched, because
`bb_pctb` and `bb_width_pct` share no prefix with anything and a prefix
rule grows until it admits one of them.

The holdout cannot be built at all: `build_training_frame` raises on
`split="holdout"`. It is evaluated once, at the end, and published whatever
it says.

## Next

Session 23 is the fit: purged walk-forward CV per DESIGN §7.5, cluster
weights `1/|cluster|`, twenty heads at ADR 113's hyperparameters. The frame
already carries `cluster_id`, `is_cluster_head` and `signal_date`, which is
what the folds and weights need.
