"""Arm 3: the relative-return criterion can drop its median test and keep
its history gate.

**ADR 014 defines `crit_rel_return` as two independent things** — "trailing
3-year total return above the sector median". That is a **history
requirement** (757 daily bars) *and* a **relative-performance test**.
`crit_rel_return_history` is the first half alone, evaluated and stored
alongside `crit_rel_return` on every row. Arm 3 swaps which of the two
`required_criteria` names.

**Why a second criterion rather than a config flag.** `config_hash` is
`sha256(asdict(Config))`, so adding a config *field* moves the hash even at
its default -- measured, `a38d3ca6b58295e8` became `be4e4702241ce90c`,
orphaning a fully built generation. Changing the *value* of the existing
`required_criteria` tuple moves the hash for the arm and for nobody else.

**Why only the median test is worth dropping.** It compares against the
sector median, so roughly half of every sector fails it by construction
(measured 440 pass, 448 fail), and it is a momentum filter inside a
mean-reversion study: three-year outperformance selects recent winners
while the signal hunts dips. That tension is arm 3's experiment.

**Why the history gate stays.** It is not a judgement about a company, it
is a statement that there is not enough data to judge one. GE Vernova was
spun out of GE in April 2024 with 603 bars — its three-year return is
undefined, not bad.

**`None`, never `False`, when the history is missing. This is the whole
risk.** `watch_reason` admits a ticker by ADR 149's `history` route on
exactly that distinction (`universe.py`: `rel is None and bars <
min_bars_for_rel_return`). Written as a plain `bars >= 757` boolean, a new
ticker returns `False`, the route stops firing, and the watch universe
silently loses that half of its purpose. `REBUILD_ARMS.md` warned about
this variant; the flag below is the spelling that avoids it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from capitalscan.core.config import UniverseParams
from capitalscan.core.universe import evaluate_criteria


def _crit(rel_return, median):
    """Both criteria for one indicator row."""
    ind = pd.Series(
        {
            "close": 100.0,
            "sma_200": 90.0,
            "sma200_slope_60": 0.01,
            "rel_return_756d": rel_return,
        }
    )
    return evaluate_criteria(
        ind,
        mcap=500e9,
        sector_median_return=median,
        rev_growth_positive=True,
        up=UniverseParams(),
    )


class TestTheMedianCriterionIsUnchanged:
    """Arms 1 and 2 must be completely unaffected."""

    def test_below_median_fails(self):
        assert _crit(0.10, 0.20)["crit_rel_return"] is False

    def test_above_median_passes(self):
        assert _crit(0.30, 0.20)["crit_rel_return"] is True

    def test_the_default_required_criteria_still_names_it(self):
        assert "crit_rel_return" in UniverseParams().required_criteria
        assert "crit_rel_return_history" not in UniverseParams().required_criteria


class TestTheHistoryCriterionDropsOnlyTheMedianTest:
    def test_a_laggard_with_history_now_passes(self):
        """The point of arm 3: no longer excluded merely for lagging."""
        got = _crit(0.10, 0.20)
        assert got["crit_rel_return"] is False
        assert got["crit_rel_return_history"] is True

    def test_a_leader_passes_both(self):
        got = _crit(0.30, 0.20)
        assert got["crit_rel_return"] is True
        assert got["crit_rel_return_history"] is True

    def test_a_missing_sector_median_no_longer_matters(self):
        """With the median gone, only the ticker's own history is judged."""
        got = _crit(0.10, np.nan)
        assert got["crit_rel_return"] is None
        assert got["crit_rel_return_history"] is True


class TestTheHistoryGateSurvives:
    """The half of ADR 014 arm 3 keeps, and the exact regression
    `REBUILD_ARMS.md` warns about."""

    def test_a_missing_return_is_none_on_both(self):
        got = _crit(np.nan, 0.20)
        assert got["crit_rel_return"] is None
        assert got["crit_rel_return_history"] is None

    def test_a_none_return_is_none_on_both(self):
        got = _crit(None, 0.20)
        assert got["crit_rel_return_history"] is None

    def test_the_history_criterion_is_never_false(self):
        """`False` for a new ticker would stop ADR 149's `history` watch
        route firing. It must be `None` or `True`, never `False`."""
        for rel in (np.nan, None, 0.0, -0.5, 0.9):
            assert _crit(rel, 0.20)["crit_rel_return_history"] is not False


class TestTheHashIsUnmoved:
    def test_the_default_config_hash_did_not_move(self):
        """Adding the criterion must not rehash every existing generation.
        It is not a config field, so it cannot."""
        from capitalscan.core.config import Config
        from capitalscan.jobs.config import config_hash

        assert config_hash(Config()) == "a38d3ca6b58295e8"

    def test_naming_it_in_required_criteria_does_move_the_hash(self):
        """ADR 060: changing what membership means must change the hash."""
        from capitalscan.core.config import Config
        from capitalscan.jobs.config import config_hash

        arm3 = Config(
            universe=UniverseParams(
                required_criteria=(
                    "crit_mcap",
                    "crit_above_sma200",
                    "crit_sma200_slope",
                    "crit_rel_return_history",
                )
            )
        )
        assert config_hash(arm3) != config_hash(Config())
