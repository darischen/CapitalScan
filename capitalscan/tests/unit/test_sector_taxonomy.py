"""ADR 148: `tickers.sector` speaks one vocabulary, and it is GICS.

The column held two. Measured 2026-08-22 over the training population:

    Information Technology  53,031 events   Technology            4,513
    Financials              61,846          Finance               1,429
    Communication Services  15,468          Telecommunications      791

Those are not three pairs of related sectors. They are three sectors written
down twice, so a categorical feature saw six levels where there are three,
with the smaller half of each pair holding a handful of tickers. ADR 068
makes `sector` the granularity that replaces ticker identity, and a level of
four tickers is most of the way back to identity.

The subtle part, and the reason this file exists rather than a dict literal:
**the two vocabularies disagree about membership, not just spelling.**
"""

from __future__ import annotations

import pytest

from capitalscan.core.sectors import (
    GICS_SECTORS,
    is_canonical,
    needs_resolution,
    normalize_yahoo_sector,
)
from capitalscan.core.training import training_exclusion_reason


class TestTheCanonicalSet:
    def test_there_are_exactly_eleven(self):
        assert len(GICS_SECTORS) == 11

    @pytest.mark.parametrize(
        "sector",
        [
            "Information Technology",
            "Financials",
            "Communication Services",
            "Materials",
            "Health Care",
        ],
    )
    def test_the_wikipedia_spellings_are_the_canonical_ones(self, sector):
        """`run_tickers_refresh` has always written these, and most of the
        population already carries them. Canon follows the incumbent."""
        assert is_canonical(sector)

    @pytest.mark.parametrize(
        "sector", ["Technology", "Finance", "Telecommunications", "Basic Materials"]
    )
    def test_the_nasdaq_spellings_are_not_canonical(self, sector):
        assert not is_canonical(sector)
        assert needs_resolution(sector)

    def test_null_needs_resolution_too(self):
        """One predicate for both defects: a blank and a foreign vocabulary
        are equally unusable as a level, and both are repaired the same way."""
        assert needs_resolution(None)
        assert needs_resolution("")


class TestTheYahooCrosswalk:
    @pytest.mark.parametrize(
        ("yahoo", "gics"),
        [
            ("Technology", "Information Technology"),
            ("Financial Services", "Financials"),
            ("Healthcare", "Health Care"),
            ("Consumer Cyclical", "Consumer Discretionary"),
            ("Consumer Defensive", "Consumer Staples"),
            ("Basic Materials", "Materials"),
            ("Communication Services", "Communication Services"),
            ("Industrials", "Industrials"),
            ("Energy", "Energy"),
            ("Real Estate", "Real Estate"),
            ("Utilities", "Utilities"),
        ],
    )
    def test_every_yahoo_sector_maps_to_a_gics_sector(self, yahoo, gics):
        assert normalize_yahoo_sector(yahoo) == gics
        assert is_canonical(normalize_yahoo_sector(yahoo))

    def test_the_map_is_total_over_gics(self):
        """All eleven are reachable, so no GICS sector is unreachable through
        the source of record."""
        reachable = {
            normalize_yahoo_sector(y)
            for y in (
                "Technology",
                "Financial Services",
                "Healthcare",
                "Consumer Cyclical",
                "Consumer Defensive",
                "Basic Materials",
                "Communication Services",
                "Industrials",
                "Energy",
                "Real Estate",
                "Utilities",
            )
        }
        assert reachable == set(GICS_SECTORS)

    def test_an_unknown_name_is_none_not_a_guess(self):
        """Invariant 4. Nasdaq's `Miscellaneous` has no GICS equivalent, and
        the answer is that the row stays blank."""
        assert normalize_yahoo_sector("Miscellaneous") is None
        assert normalize_yahoo_sector("Conglomerates") is None
        assert normalize_yahoo_sector(None) is None
        assert normalize_yahoo_sector("   ") is None

    def test_whitespace_is_tolerated_but_novelty_is_not(self):
        assert normalize_yahoo_sector("  Financial   Services ") == "Financials"
        assert normalize_yahoo_sector("Financial-Services") is None


class TestWhyStoredValuesAreReResolvedRatherThanMapped:
    """The finding that shaped ADR 148, pinned as a test.

    `"Technology"` is emitted by **both** Nasdaq and Yahoo, and they do not
    mean the same set of companies. Nasdaq files NTES (Electronic Gaming &
    Multimedia) and BILI (Internet Content & Information) under it; GICS and
    Yahoo both call those Communication Services.

    So a general `normalize(stored_value)` cannot exist: given the string
    `"Technology"` it cannot know whether the row came from Nasdaq, where the
    correct answer for NTES is Communication Services, or from Yahoo, where
    it is Information Technology. Mapping the stored label would reclassify
    473 NTES events and 89 BILI events into the wrong sector on the strength
    of a matching string.
    """

    def test_the_function_is_named_for_its_source(self):
        """A rename to `normalize_sector` would be the bug, so the name is
        part of the contract."""
        from capitalscan.core import sectors

        assert hasattr(sectors, "normalize_yahoo_sector")
        assert not hasattr(sectors, "normalize_sector")

    def test_yahoo_places_the_discriminating_cases_in_communication_services(self):
        """Live-verified 2026-08-23: Yahoo returns Communication Services for
        NTES and BILI, which is where GICS puts them and where Nasdaq does
        not. This is why Yahoo is the source of record."""
        assert normalize_yahoo_sector("Communication Services") == "Communication Services"

    def test_a_nasdaq_label_is_not_silently_accepted(self):
        """`Finance` and `Telecommunications` are Nasdaq-only spellings and
        must fall through to `None` rather than to a plausible GICS name."""
        assert normalize_yahoo_sector("Finance") is None
        assert normalize_yahoo_sector("Telecommunications") is None


class TestTheTrainingGateTightens:
    """ADR 147's gate checked only for NULL, so a split category passed it."""

    def test_a_nasdaq_sector_now_blocks_the_row(self):
        assert training_exclusion_reason("SHOP", "Technology") == "non_canonical_sector"
        assert training_exclusion_reason("TW", "Finance") == "non_canonical_sector"
        assert training_exclusion_reason("ROKU", "Telecommunications") == ("non_canonical_sector")

    def test_a_canonical_sector_still_trains(self):
        assert training_exclusion_reason("AAPL", "Information Technology") is None

    def test_a_blank_is_still_reported_as_missing_not_as_non_canonical(self):
        """The two reasons stay distinguishable. `missing_sector` carries the
        survivorship argument from ADR 147 and should not be absorbed."""
        assert training_exclusion_reason("ASML", None) == "missing_sector"

    def test_the_etf_decision_still_wins_over_the_sector_check(self):
        """Order matters: an ETF is excluded by decision, not by data defect,
        and must report `etf` whatever its sector says."""
        assert training_exclusion_reason("QQQ", "Technology") == "etf"
        assert training_exclusion_reason("QQQ", None) == "etf"
