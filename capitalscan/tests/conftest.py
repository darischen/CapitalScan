"""Shared pytest configuration.

Hypothesis profiles (TESTS.md §9).

`max_examples` is per test, not per suite, and there are nine property
tests. Measured: `full` across all nine runs 509 s, and `ci_fast` at 1,000
runs 57 s — the latter alone would exhaust the 60 s fast-tier budget before
unit tests, ruff, and mypy get to run.

Two things carry the cost instead. `ci_fast` is 250. And the slow tier runs
`full` only over the tests marked `exit_invariant`, which is what TESTS.md
§3.4 and the Phase 3 gate name specifically, with the rest at `ci_fast`:

    slow: pytest tests/property -m exit_invariant --hypothesis-profile=full
          pytest tests/property -m "not exit_invariant" --hypothesis-profile=ci_fast

The gate's 10,000 cases per exit invariant are preserved exactly. Measured:
fast tier 29 s against 60 s, slow tier 459 s against the 10-minute budget.

Most of the slow tier is one test. `test_stop_exits_land_at_or_beyond_the_
stop_level` runs ~193 s against ~60 s for the others, because
`assume(reason is STOP)` discards non-STOP draws and regenerates to reach
10,000 valid cases. That cost is the point: it samples the natural
distribution of stop-hitting scenarios rather than the ones someone thought
to construct. **Do not replace the generator with a targeted one.** If it
ever needs relief, the fix is additive — keep the unfiltered generator at a
lower count and add a targeted strategy alongside it.

Select with `--hypothesis-profile=<name>`. `dev` is the default so a local
`pytest` stays fast; CI names its profile explicitly.
"""

from __future__ import annotations

import os

from hypothesis import settings

settings.register_profile("ci_fast", max_examples=250, deadline=None)
settings.register_profile("full", max_examples=10000, deadline=None)
settings.register_profile("dev", max_examples=200)

# `--hypothesis-profile` overrides this when passed. CAPSCAN_HYPOTHESIS_PROFILE
# exists so the Phase 3 gate script can request `full` without threading a
# pytest flag through every invocation.
settings.load_profile(os.getenv("CAPSCAN_HYPOTHESIS_PROFILE", "dev"))


# **The hash the default `Config()` produces today.**
#
# Nine tests asserted this value as a literal, each guarding that *its own*
# change was hash-neutral -- adding an enum member, a plausibility check, a
# criterion flag. That intent is right and worth keeping: a change that
# rehashes every generation by accident is a serious bug.
#
# What was wrong was hardcoding the value nine times. On 2026-08-29 the exit
# sweep moved `target_pct` 0.04 -> 0.05 and `stop_atr_k` 1.5 -> 2.0 on
# purpose, and nine tests failed for a change none of them was written about.
# The pin now lives here, so a deliberate config change is one edit and an
# accidental one still fails every guard.
DEFAULT_CONFIG_HASH = "0523841076f47293"
