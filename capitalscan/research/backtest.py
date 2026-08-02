"""The backtest contract: config in, enriched event table out (BUILD session 9).

This module is the first piece of `research/backtest.py`. It owns nothing
new — it names the config type the rest of session 9 builds against and
re-exports the two labelling functions every later step (entry/exit
resolution, cost application, split-scoped stats) needs, so nothing in
`research/` reaches into `jobs/` directly for them.

**Why `BacktestConfig` is an alias, not a new dataclass.** The session 9
plan named a six-field `BacktestConfig` (`indicators`, `signals`, `exits`,
`costs`, `splits`, `universe`). `core/config.py` already has `Config`, a
frozen dataclass with those six fields plus `stats: StatsParams` — and
`stats` is not optional scope creep: Task 7 reads `StatsParams.reach_targets`
and Task 8 reads `dd_buckets` and `era_bounds`. A second, narrower
`BacktestConfig` would need translating to/from `Config` at every boundary,
and CLAUDE.md invariant 2 ("one signal implementation... never write a
second comparison anywhere") extends to config: two config types that are
supposed to describe the same run is exactly the drift that a second
`config_hash` implementation would cause, just one level up. So
`BacktestConfig` is `Config` under another name, kept for the plan's
readability where a name says "the config a backtest run took," not a
type pytest or Postgres ever have to distinguish.

**Why `config_hash` and `split_key_for` are re-exported, not reimplemented.**
`jobs/config.py:config_hash` is already the exact algorithm this module's
brief specified. `jobs/config.py:split_key_for` is already the exact
train/validate/holdout rule. Both are pure functions of their arguments —
no IO — so importing them here does not violate invariant 1; only calling
IO-performing code from `core/` would. `research/` and `jobs/` sharing one
`config_hash` and one `split_key_for` is the whole point: two hashes or two
split-labelling rules that drift would produce rows whose provenance lies.
"""

from __future__ import annotations

from capitalscan.core.config import Config
from capitalscan.jobs.config import config_hash, split_key_for

# See module docstring: an alias, not a second dataclass (Ruling C1).
BacktestConfig = Config

__all__ = ["BacktestConfig", "config_hash", "split_key_for"]
