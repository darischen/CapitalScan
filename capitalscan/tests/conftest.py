"""Shared pytest configuration.

Hypothesis profiles (TESTS.md §9). Property tests at 10,000 examples take
roughly 105 seconds, which exceeds the fast-tier budget, so the tier split
carries the difference: fast CI runs `ci_fast`, slow CI runs `full`. The
Phase 3 gate still requires the full 10,000 — the split preserves quick
feedback without weakening the gate.

Select with `--hypothesis-profile=<name>`. `dev` is the default so a local
`pytest` stays fast; CI names its profile explicitly.
"""

from __future__ import annotations

import os

from hypothesis import settings

settings.register_profile("ci_fast", max_examples=1000, deadline=None)
settings.register_profile("full", max_examples=10000, deadline=None)
settings.register_profile("dev", max_examples=200)

# `--hypothesis-profile` overrides this when passed. CAPSCAN_HYPOTHESIS_PROFILE
# exists so the Phase 3 gate script can request `full` without threading a
# pytest flag through every invocation.
settings.load_profile(os.getenv("CAPSCAN_HYPOTHESIS_PROFILE", "dev"))
