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

The gate's 10,000 cases per exit invariant are preserved exactly.

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
