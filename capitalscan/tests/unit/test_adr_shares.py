"""ADR share counts need dividing by the ADR ratio before pricing.

An ADR's SEC filing (Form 20-F) reports the issuer's **ordinary** share
count, but the bar price is per **ADR**. Multiplying them directly
overstates market cap by the ADR ratio.

Measured 2026-08-01 against yfinance's ADR-equivalent counts:

    TSM   25,932,524,521 ordinary  /  5,186,474,013 ADR  =  5.00
    ASML     385,417,665           /    384,100,000      =  1.00
    SAP    1,228,504,232           /  1,154,204,232      =  1.06
    NVO    4,421,895,520           /  3,347,023,520      =  1.32

Only TSM is a genuine ratio (exactly 5:1, and TSMC's ADR ratio is 1:5).
SAP's 6% is filing-date drift. NVO's 1.32 is Novo's A+B share classes, and
yfinance's own `marketCap` agrees with the A+B total, so our number is
already right there — the ADR itself is 1:1.

Left uncorrected, TSM priced to a $10.5T market cap against an actual
~$2.1T. `crit_mcap` happened to be unaffected — TSM clears the $200B
threshold either way — but `mcap_usd` and `mcap_rank` are stored on every
event as context tags, so anything conditioning on size inherited the error.
"""

from __future__ import annotations

import pytest

from capitalscan.core.config import UniverseParams
from capitalscan.core.universe import adr_adjusted_shares

UP = UniverseParams()


def test_a_five_to_one_adr_divides_the_ordinary_count():
    """TSM: 25.93B ordinary shares back 5.19B ADRs."""
    assert adr_adjusted_shares("TSM", 25_932_524_521, UP) == pytest.approx(5_186_504_904.2)


def test_an_ordinary_us_listing_is_untouched():
    """The overwhelming majority of tickers have no ADR ratio at all."""
    assert adr_adjusted_shares("AAPL", 14_594_180_000, UP) == 14_594_180_000


def test_a_one_to_one_adr_is_untouched():
    """ASML, SAP and NVO are 1:1 — being an ADR is not itself a correction."""
    for ticker in ("ASML", "SAP", "NVO"):
        assert adr_adjusted_shares(ticker, 1_000_000.0, UP) == 1_000_000.0


def test_the_ratio_lookup_is_case_insensitive():
    assert adr_adjusted_shares("tsm", 25_932_524_521, UP) == pytest.approx(5_186_504_904.2)


def test_a_none_share_count_stays_none():
    """A ticker with no filing yet must not become 0.0 (invariant 4)."""
    assert adr_adjusted_shares("TSM", None, UP) is None


def test_the_ratio_is_configurable_not_hardcoded():
    """Invariant 9: the number lives in `core/config.py`, not in the logic."""
    custom = UniverseParams(adr_ordinary_per_adr=(("FOO", 4.0),))

    assert adr_adjusted_shares("FOO", 400.0, custom) == 100.0
    # TSM is not in this override, so it falls back to 1:1 rather than
    # silently keeping the default map's 5.0.
    assert adr_adjusted_shares("TSM", 400.0, custom) == 400.0
