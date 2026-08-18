# Config thresholds — Session 9 (2026-08-02)

HEAD before: `cc7e9fe`. Commit: `a1dacec`.

## Change 1 — `SharesPlausibility.max_shares`: 32B -> 320B

**Reasoning.** The 32B ceiling sat only ~10% above Citigroup's own live
2026 share count (29.2B); TSM (25.9B) and NVDA (24.5B, post its 2024 10:1
split) are close behind. A single genuine 2:1 split on any of the three
produces a filing above 32B, which the old guard rejected permanently —
`run_shares` never retries a rejected accession, so the ticker freezes at
its last-accepted count with no error, forever. Rejecting good data is
worse than admitting bad data here: a bad share count surfaces downstream
as an absurd market cap (how the original defect was found); a rejected
genuine filing is invisible.

**Number chosen: 320,000,000,000** (10x the old ceiling). Chosen so a 10:1
split on today's largest issuer (Citigroup, ~29.2B -> 292B) still clears
with room, not just a 2:1. `min_shares` untouched — every real value on
file sits orders of magnitude above it, every bad value far below.

**Documented tradeoff (in the docstring, `core/config.py`), quantified
against the reject log.** Raising the ceiling widens the
undetectable-corruption band: a x1,000 filer error on a company whose real
share count is in the tens of *millions* (result: tens of billions) now
lands inside `[min_shares, max_shares]` undetected, whereas the old 32B
ceiling only missed errors on companies up to ~32M real shares. Alaska
Air's confirmed-bad 2011 filing is one instance, and is pinned explicitly
in `test_alk_1e3_scaled_filing_is_no_longer_caught_by_raised_ceiling`
rather than silently dropped — but it is not the only one.

The controller backed up every row this guard has ever rejected to
`shares_outstanding_rejected_20260802` before deleting them. Querying that
table (verified independently, not copied from the review):

```sql
SELECT count(*) FILTER (WHERE shares BETWEEN 32000000000 AND 320000000000) AS in_new_band,
       count(*) FILTER (WHERE shares > 320000000000) AS still_above
FROM shares_outstanding_rejected_20260802;
--  in_new_band | still_above
--  26          | 33
```

**26 filings across 12 tickers** now fall inside the widened band and would
be silently accepted: AAP (4), GRMN (5), PKG (3), ALK (3), FTNT (2), SWKS
(2), MAA (2), AIZ (1), CNX (1), EOG (1), PNR (1), REG (1) — every one a
real company with real shares in the tens-to-hundreds of millions,
x1,000-corrupted.

The ceiling is not vestigial: **33 rows across 23 tickers** (ORCL, AMD,
EXC, TFC, AEP, and 18 others) still exceed 320B and are still caught —
real x10^5/x10^6-class corruption, distinct from the x1,000-class gap
above. Re-run the query above against `shares_outstanding_rejected_20260802`
to reproduce either count.

## Change 2 — `UniverseParams.min_mcap_usd`: 200e9 -> 100e9

User's decision. CPI deflation was considered and rejected as the
mechanism — deflation factors span only 0.677 (2010) to 1.000 (2025), a 47%
move, far short of a 2x change. The real point is candidate-pool size: in
2010, 23 tickers already cleared $100B and 6 cleared $200B, yet `in_trade`
was zero regardless, because `is_tradeable` requires all four ADR 014
criteria and the SMA-200 / slope / relative-return conditions were binding,
not market cap. This widens the pool; it does not by itself fix that
historical emptiness.

**ADR 014 conflict — found and resolved.** ADR 014 states the criterion as
"Market cap above $200B" (a fixed number, not a parameter). I added a dated
note under ADR 014 in `docs/DECISIONS.md` (2026-08-02) recording the change
and the rejected-deflation reasoning, following the convention used
elsewhere in that file (e.g. ADR 069's "Note." under ADR 066, ADR 081's
inline "Revised:" note under ADR 070's status line). I did not mark ADR 014
Superseded — the rest of the ADR (causal, quarterly re-evaluation, the
other three criteria) is unchanged; only the number moved.

**New `config_hash(Config())`: `3e598c59e7d71eae`** (was
`22df3117b890793b`). `UniverseParams` is a field of `Config`, so this is
expected per ADR 060. **The Postgres GUC must be updated to this value.**

## TDD evidence

Change 1 — RED:
```
uv run pytest capitalscan/tests/unit/test_shares_plausibility.py -k "post_2_for_1" -v
FAILED ... AssertionError: assert [{'ticker': 'C', ... 'rule': 'shares_above_plausible_ceiling', ...}] == []
```
GREEN after raising `max_shares` to 320e9: 9/9 passed in
`test_shares_plausibility.py`.

Change 2 — RED:
```
uv run pytest capitalscan/tests/unit/test_backtest_config.py -k "min_mcap" -v
FAILED ... AssertionError: assert 200000000000.0 == 100000000000.0
```
GREEN after changing the default: 1/1 passed. New hash confirmed via
`config_hash(Config())` -> `3e598c59e7d71eae`.

Full safe suite, once, after all edits:
```
uv run pytest capitalscan/tests/unit capitalscan/tests/property
735 passed in 26.35s
```

## Tests changed and why

- `test_shares_plausibility.py`: added
  `test_a_post_2_for_1_split_citigroup_scale_count_is_accepted` (new,
  Change 1's acceptance criterion). Updated
  `test_default_bounds_bracket_the_confirmed_genuine_and_bad_extremes` to
  assert the new headroom and flip the ALK assertion (35.8B is now inside
  the band, not outside). Renamed and rewrote
  `test_a_1e3_scaled_filing_is_rejected_and_logged` ->
  `test_alk_1e3_scaled_filing_is_no_longer_caught_by_raised_ceiling`,
  asserting the row is now accepted, with a docstring explaining why this
  is the accepted tradeoff, not a regression to fix.
- `test_backtest_config.py`: added `TestUniverseMinMcapThreshold` pinning
  `min_mcap_usd == 100e9`.
- `test_cli_config_resolution.py` and `test_events_backtest_config_agreement.py`:
  updated the three hardcoded `22df3117b890793b` literals to
  `3e598c59e7d71eae`, with comments explaining the move.
- `test_universe.py`: three tests (`test_mcap_criterion_uses_the_configured_floor`,
  `test_is_tradeable_false_when_any_criterion_fails`,
  `test_is_tradeable_with_a_single_required_criterion`) used mcap probe
  values (199e9, 100e9) that were below the old $200B floor but are now
  above the new $100B floor, so they broke on the real behavior change.
  Adjusted probe values (99e9/101e9, 50e9) to straddle the new floor
  instead of weakening any assertion.

`test_shares_yahoo_fallback.py`'s docstring still narrates "clears by 5x"
under the old 200e9 threshold as a historical fact about when that test was
written — left as-is; it is prose, not an assertion, and no longer affects
correctness.

## Concerns

- x1,000-class corruption on tens-of-millions-share companies is now
  genuinely invisible to `max_shares` alone — not a one-off (Alaska Air),
  but 26 confirmed-bad filings across 12 tickers per the reject-log query
  above. Nothing else in the pipeline currently cross-checks share counts
  against an independent source for tickers already accepted. If that gap
  matters in practice, the real fix is a second, orthogonal check (e.g. a
  relative sanity check *per source*, not per-ticker-history, which was
  ruled out for the reason PSKY illustrates) — out of scope here.
- Widening `min_mcap_usd` to $100B roughly doubles the nominal candidate
  pool but, per ADR 014's own 2010 numbers, may still yield an empty
  `in_trade` set in early years since the SMA/slope/relative-return
  criteria remain binding. This was flagged as expected, not a defect.
