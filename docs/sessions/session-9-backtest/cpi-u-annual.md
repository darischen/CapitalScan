# CPI-U annual average index, 1982-84 = 100

Fetched 2026-08-02 for the market-cap threshold deflator (see the
`min_mcap_usd` discussion in `progress.md`).

Source: usinflationcalculator.com's reproduction of the BLS CPI-U series.
The BLS site itself (`bls.gov/news.release/cpi.t01.htm`) and the Minneapolis
Fed table both return HTTP 403 to automated requests, so this is a secondary
source. **The values below should be spot-checked against BLS before anything
published depends on them.**

| Year | CPI-U annual avg |
|------|------------------|
| 2009 | 214.537 |
| 2010 | 218.056 |
| 2011 | 224.939 |
| 2012 | 229.594 |
| 2013 | 232.957 |
| 2014 | 236.736 |
| 2015 | 237.017 |
| 2016 | 240.007 |
| 2017 | 245.120 |
| 2018 | 251.107 |
| 2019 | 255.657 |
| 2020 | 258.811 |
| 2021 | 270.970 |
| 2022 | 292.655 |
| 2023 | 304.702 |
| 2024 | 313.689 |
| 2025 | 321.943 |

## Caveats that matter for how this gets used

- **2025 is a partial-year average** at the time of fetch, not a settled
  annual figure. It will be revised.
- **2026 has no annual average yet** — the year is not over. Any "2026
  dollars" anchor is therefore an extrapolation, not a measurement.
- CPI-U is revised and rebased over time. A deflator table pinned in code is
  a snapshot, exactly like `UniverseParams.adr_ordinary_per_adr` — and the
  same rule applies: if a value is ever revised, the honest fix is a dated
  mapping, not editing a number in place, because every historical market cap
  computed from it silently rescales.

## Deflation factors relative to 2025 (the most recent full-ish year)

`factor(y) = CPI(y) / CPI(2025)`. A threshold of $X in 2025 dollars becomes
`X * factor(y)` in year `y`'s nominal dollars.

| Year | factor | $100B 2025-dollar threshold, nominal |
|------|--------|--------------------------------------|
| 2010 | 0.677  | $67.7B |
| 2012 | 0.713  | $71.3B |
| 2014 | 0.735  | $73.5B |
| 2016 | 0.746  | $74.6B |
| 2018 | 0.780  | $78.0B |
| 2020 | 0.804  | $80.4B |
| 2022 | 0.909  | $90.9B |
| 2024 | 0.974  | $97.4B |
| 2025 | 1.000  | $100.0B |

Note the range is narrow: roughly 0.68 to 1.00 across sixteen years. **CPI
deflation alone does NOT explain the historical emptiness of the trade
universe.** In 2010, 23 tickers cleared $100B nominal and 6 cleared $200B,
yet `in_trade` was 0 — because `is_tradeable` requires all four criteria and
the SMA/slope/relative-return conditions were what zeroed it, not the market
cap. Deflating the threshold widens the candidate pool from 6 to ~30 in 2010,
which helps, but it is not the whole story.

Equity market capitalization grew far faster than CPI over this period, so an
index-relative or rank-based threshold would move much more than 1.5x across
the same span. That is a separate design question from inflation.
