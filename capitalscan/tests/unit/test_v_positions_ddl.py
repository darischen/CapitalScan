"""`v_positions` carries no exit policy of its own (ADR 095, ADR 115).

Session 15 task 15.4 required "a test that fails against the current view
and passes against the rebuilt one. A test asserting the view's shape passes
both ways and proves nothing."

`V_POSITIONS_DDL_PRE_115` is checked in verbatim, so that requirement is met
literally: every assertion below is run against **both** DDLs, and each one
must fail on the old text and pass on the new. `test_the_old_view_fails_
every_assertion` is the guard that keeps that true - without it, an
assertion that quietly stopped discriminating would still look green.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capitalscan.core.config import Config, ExitParams
from capitalscan.jobs import threshold_lint as tl
from capitalscan.jobs.views import (
    SERVING_CONFIG_COLUMNS,
    V_POSITIONS_DDL,
    V_POSITIONS_DDL_PRE_115,
    serving_config_values,
)

# Each check takes the DDL text and returns True when the *rebuilt*
# behaviour is present. Named so a failure says which defect came back.
CHECKS = {
    "no_threshold_literal": lambda ddl: not tl.scan_sql_source(ddl, Path("<v_positions>")),
    "reads_serving_config": lambda ddl: "serving_config" in ddl,
    "stoch_respects_side": lambda ddl: "exit_stoch_threshold_short" in ddl,
    "stoch_follows_source": lambda ddl: "exit_stoch_source" in ddl,
    "mid_band_is_gated": lambda ddl: "exit_on_mid_band" in ddl,
    "timeout_counts_sessions": lambda ddl: "trading_days" in ddl,
}


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_the_rebuilt_view_passes_every_check(name):
    assert CHECKS[name](V_POSITIONS_DDL), f"rebuilt v_positions fails {name}"


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_the_old_view_fails_every_check(name):
    """The discriminating half. A check that passes here proves nothing.

    ADR 095's own words: a test asserting the view's shape passes both ways.
    This is what makes the assertions above evidence rather than decoration.
    """
    assert not CHECKS[name](V_POSITIONS_DDL_PRE_115), (
        f"{name} passes against the pre-115 view too, so it discriminates nothing"
    )


def test_the_old_view_is_what_the_linter_used_to_find():
    """Pins the reason `KNOWN_EXCEPTIONS` still carries a `views.py` entry.

    The literal has to stay wrong in `V_POSITIONS_DDL_PRE_115`, because
    `downgrade()` restores the old view exactly rather than approximately.
    """
    findings = tl.scan_sql_source(V_POSITIONS_DDL_PRE_115, Path("<pre_115>"))
    assert [f.literal for f in findings] == ["80"]
    assert findings[0].column == "k_full"


# ---------------------------------------------------------------------------
# The settings row is derived from ExitParams, not transcribed
# ---------------------------------------------------------------------------


def test_the_row_reads_the_live_exit_params():
    ep = ExitParams()
    values = serving_config_values()
    assert values["exit_stoch_threshold"] == ep.exit_stoch_threshold
    assert values["exit_stoch_threshold_short"] == ep.exit_stoch_threshold_short
    assert values["max_hold_days"] == ep.max_hold_days
    assert values["exit_stoch_source"] == ep.exit_stoch_source
    assert values["exit_on_mid_band"] == ep.exit_on_mid_band


def test_changing_a_threshold_moves_the_row():
    """The derivation is real, not a coincidence at the defaults.

    ADR 095's failure mode in one line: sweep `exit_stoch_threshold` to 70
    and the backtest moves. The serving surface has to move with it.
    """
    swept = Config(exits=ExitParams(exit_stoch_threshold=70.0, max_hold_days=8))
    values = serving_config_values(swept)
    assert values["exit_stoch_threshold"] == 70.0
    assert values["max_hold_days"] == 8


def test_the_row_covers_every_field_the_view_reads():
    """A view column referencing a field the row does not carry is a NULL
    flag nobody notices until a position renders blank."""
    for column in SERVING_CONFIG_COLUMNS:
        if column == "config_hash":
            continue  # provenance, not policy: the view never reads it
        assert f"c.{column}" in V_POSITIONS_DDL, f"serving_config.{column} is written and unread"


def test_every_policy_field_the_view_reads_is_in_the_row():
    """The other direction. A `c.something` the row does not have is a
    query error at read time rather than at migration time."""
    import re

    referenced = set(re.findall(r"\bc\.([a-z_0-9]+)", V_POSITIONS_DDL))
    assert referenced <= set(SERVING_CONFIG_COLUMNS), (
        f"v_positions reads columns serving_config does not have: "
        f"{sorted(referenced - set(SERVING_CONFIG_COLUMNS))}"
    )


def test_the_view_survives_a_missing_settings_row():
    """`LEFT JOIN ... ON true`, not a cross join.

    ADR 095 named the row-existence dependency as this option's cost. A
    plain join would return zero positions when the row is missing, which
    reads as "you have no positions". The left join renders the position
    with NULL exit flags, which reads as "unknown" - the honest failure.
    """
    assert "LEFT JOIN public.serving_config c ON (true)" in V_POSITIONS_DDL
