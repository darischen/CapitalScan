"""Live call overlay (ADR 050): always labeled as live-priced and never
backtested, and a missing/illiquid chain returns `None` rather than a
fabricated row (DESIGN §3.11 applied to a recommendation).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from capitalscan.jobs import call_overlay
from capitalscan.jobs.fetch import yahoo

AS_OF = date(2026, 7, 31)
REACH_TARGETS = (0.02, 0.03, 0.05, 0.10)


def _chain(strikes_and_asks: list[tuple[float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {"strike": [s for s, _ in strikes_and_asks], "ask": [a for _, a in strikes_and_asks]}
    )


def test_none_when_no_expirations_listed(monkeypatch):
    monkeypatch.setattr(yahoo, "fetch_option_expirations", lambda ticker: [])
    result = call_overlay.build_overlay("TSM", 100.0, REACH_TARGETS, as_of=AS_OF)
    assert result is None


def test_none_when_no_expiration_far_enough_out(monkeypatch):
    # Only an expiration inside the 7-day minimum hold window exists.
    monkeypatch.setattr(
        yahoo, "fetch_option_expirations", lambda ticker: [str(AS_OF + pd.Timedelta(days=2))]
    )
    result = call_overlay.build_overlay("TSM", 100.0, REACH_TARGETS, as_of=AS_OF)
    assert result is None


def test_none_when_chain_is_empty(monkeypatch):
    exp = str(AS_OF + pd.Timedelta(days=10))
    monkeypatch.setattr(yahoo, "fetch_option_expirations", lambda ticker: [exp])
    monkeypatch.setattr(yahoo, "fetch_chain", lambda ticker, expiration=None: pd.DataFrame())
    result = call_overlay.build_overlay("TSM", 100.0, REACH_TARGETS, as_of=AS_OF)
    assert result is None


def test_overlay_always_carries_the_adr_050_boundary_labels(monkeypatch):
    exp = str(AS_OF + pd.Timedelta(days=10))
    monkeypatch.setattr(yahoo, "fetch_option_expirations", lambda ticker: [exp])
    monkeypatch.setattr(
        yahoo,
        "fetch_chain",
        lambda ticker, expiration=None: _chain([(100.0, 3.0), (102.5, 2.0), (105.0, 1.2)]),
    )
    result = call_overlay.build_overlay("TSM", 100.0, REACH_TARGETS, as_of=AS_OF)
    assert result is not None
    assert result["priced_from"] == "live_quotes"
    assert result["backtested"] is False
    assert result["expiration"] == exp
    assert len(result["strikes"]) == 3


def test_payoff_and_breakeven_use_the_real_ask(monkeypatch):
    exp = str(AS_OF + pd.Timedelta(days=10))
    monkeypatch.setattr(yahoo, "fetch_option_expirations", lambda ticker: [exp])
    monkeypatch.setattr(
        yahoo, "fetch_chain", lambda ticker, expiration=None: _chain([(100.0, 3.0)])
    )
    result = call_overlay.build_overlay("TSM", 100.0, (0.05,), as_of=AS_OF)
    strike_row = result["strikes"][0]
    assert strike_row["strike"] == 100.0
    assert strike_row["cost"] == 3.0
    assert strike_row["breakeven"] == 103.0
    assert strike_row["max_loss"] == 3.0
    # target = 100 * 1.05 = 105; payoff = max(0, 105-100) - 3 = 2.0
    assert strike_row["payoff_at_reach"]["reach_5pct"] == pytest.approx(2.0)


def test_zero_ask_strikes_are_excluded(monkeypatch):
    exp = str(AS_OF + pd.Timedelta(days=10))
    monkeypatch.setattr(yahoo, "fetch_option_expirations", lambda ticker: [exp])
    monkeypatch.setattr(
        yahoo, "fetch_chain", lambda ticker, expiration=None: _chain([(100.0, 0.0)])
    )
    result = call_overlay.build_overlay("TSM", 100.0, REACH_TARGETS, as_of=AS_OF)
    assert result is None
