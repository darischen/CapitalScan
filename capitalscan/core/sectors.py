"""One sector taxonomy: GICS. Pure functions, no IO (ADR 148).

`sector` is a categorical model feature (DESIGN §7.3) and ADR 068 pins it as
the granularity that stands in for ticker identity. A feature carrying two
vocabularies for one concept splits a real category into two smaller ones,
which is the same defect ADR 147 addresses from the other direction.
"""

from __future__ import annotations

# The eleven GICS sectors, and the only values `tickers.sector` may hold.
# Spellings are Wikipedia's S&P 500 constituent table verbatim, because that
# is what `run_tickers_refresh` has always written and what the majority of
# the population already carries.
GICS_SECTORS: frozenset[str] = frozenset(
    {
        "Energy",
        "Materials",
        "Industrials",
        "Consumer Discretionary",
        "Consumer Staples",
        "Health Care",
        "Financials",
        "Information Technology",
        "Communication Services",
        "Utilities",
        "Real Estate",
    }
)

# Yahoo's sector vocabulary to GICS. A **naming** difference: Yahoo's scheme
# has the same eleven top-level sectors and assigns membership the way GICS
# does, so each line is a rename rather than a reclassification.
#
# Verified on the cases that discriminate: Yahoo puts NTES (Electronic Gaming
# & Multimedia) and BILI (Internet Content & Information) in Communication
# Services, which is where GICS puts them.
_YAHOO_TO_GICS: dict[str, str] = {
    "Energy": "Energy",
    "Basic Materials": "Materials",
    "Industrials": "Industrials",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Healthcare": "Health Care",
    "Financial Services": "Financials",
    "Technology": "Information Technology",
    "Communication Services": "Communication Services",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
}


def is_canonical(sector: str | None) -> bool:
    """True when this value is one of the eleven GICS sectors."""
    return sector is not None and sector in GICS_SECTORS


def normalize_yahoo_sector(raw: str | None) -> str | None:
    """Yahoo's sector name as GICS, or `None` when it does not resolve.

    **Named for its source on purpose, and not usable on a stored value.**
    Nasdaq's screener and Yahoo both emit the string `"Technology"`, and they
    do not mean the same set of companies: Nasdaq files NTES and BILI under
    it, while GICS and Yahoo both call those Communication Services. A
    general `normalize(raw)` could not tell the two apart, so it would
    reclassify a gaming company as Information Technology on the strength of
    a matching label.

    That is why ADR 148 re-resolves every non-canonical row from Yahoo rather
    than crosswalking what is already stored. A crosswalk is only safe where
    the two vocabularies agree on membership, and Nasdaq's does not.

    `None` in, `None` out, and an unrecognised name is `None` rather than a
    guess (invariant 4).
    """
    if raw is None:
        return None
    cleaned = " ".join(str(raw).split())
    if not cleaned:
        return None
    return _YAHOO_TO_GICS.get(cleaned)


def needs_resolution(sector: str | None) -> bool:
    """True when this row must be re-resolved from the source of record.

    Covers both defects ADR 148 fixes with one predicate: a NULL sector and a
    sector expressed in some other vocabulary are equally unusable as a
    categorical level, and both are repaired the same way.
    """
    return not is_canonical(sector)
