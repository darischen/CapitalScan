-- Run once before run_arms.sh, against the WORKSTATION research copy only.
-- `universe` is keyed by config_hash and the backtest reads eligibility from
-- it, so an arm with no universe rows writes 0 events and reports success
-- (hit 2026-09-30: 93 chunks "ok", 0 rows). The arms change ExitParams only,
-- and universe membership reads UniverseParams only, so the live
-- generation's rows are exactly what `cscan universe --quarter` x66 would
-- recompute. Copied rather than recomputed to save ~20 min per arm.
INSERT INTO universe (ticker, as_of, in_train, in_trade, mcap_usd, mcap_rank, adv_20d_usd,
       crit_mcap, crit_above_sma200, crit_sma200_slope, crit_rel_return, crit_rev_growth,
       in_watch, watch_reason, config_hash, crit_rel_return_history)
SELECT ticker, as_of, in_train, in_trade, mcap_usd, mcap_rank, adv_20d_usd,
       crit_mcap, crit_above_sma200, crit_sma200_slope, crit_rel_return, crit_rev_growth,
       in_watch, watch_reason, arm.h, crit_rel_return_history
  FROM universe, (VALUES ('a90d4561f883c4a5'), ('47beeecbd6d41696')) AS arm(h)
 WHERE config_hash = 'f183b0f5209a4677'
ON CONFLICT DO NOTHING;
