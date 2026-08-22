"""The x1,000 filer-error class, caught by local shape instead of by bounds.

`SharesPlausibility`'s absolute band cannot see this class and says so:
*"a x1,000 error on a company with real shares in the tens of millions
(tens of billions after corruption) now lands inside `[min_shares,
max_shares]` and is accepted undetected"*. It names the exact casualties —
26 filings across 12 tickers. Every one reaches `universe.mcap_usd`
unchallenged, and six of them passed `crit_mcap` on a number wrong by three
orders of magnitude.

The same docstring rejects the obvious fix, correctly: a test relative to
*the ticker's own global median* fails on PSKY, whose median **is** the
corruption (two of its three filings are a placeholder-shaped `1,000`), so
the one genuine filing is what a median test flags.

**A local window survives that, and a global one does not.** Measured
against the live table on 2026-08-22, both counterexamples fall out for
structural reasons rather than by tuning:

- **PSKY** has 3 filings. A window needs neighbours to have an opinion, so
  the minimum-filings gate excludes it outright rather than out-voting it.
- **WULF** is the one a global test gets backwards. TeraWulf diluted from a
  tiny base, so its *global* median sits 247x below its recent filings and
  a global rule would reject 16 consecutive **genuine** rows. Locally each
  filing is ~1.0-1.3x its neighbours, so no window ever flags it.

The real errors do not look like either: they jump ~1,000x against their
time-neighbours and jump straight back, three to four filings later.
"""

from __future__ import annotations

from capitalscan.core.config import DEFAULT_SHARES_PLAUSIBILITY as BOUNDS
from capitalscan.core.universe import scale_error_indices


class TestTheMeasuredErrors:
    """Series taken from `shares_outstanding`, values verbatim."""

    def test_a_single_bad_filing_between_clean_ones(self):
        """SWKS 2011-11-28, 999.6x its local median."""
        series = [
            176_793_291,
            177_700_396,
            183_287_033,
            185_435_623,
            186_187_121,
            186_277_145,
            187_889_808_000,  # bad
            188_415_515_000,  # bad
            189_651_416,
            190_841_047,
            194_321_490,
            191_983_618,
        ]
        assert scale_error_indices(series, BOUNDS) == [6, 7]

    def test_a_run_of_four_does_not_out_vote_its_window(self):
        """AAP's worst stretch: four consecutive bad filings.

        The sharp case for window size. With four neighbours a side the
        window holds eight values, four of them corrupt — exactly half.
        The median still lands clean because the corrupt half sits three
        orders of magnitude away on one side, so it cannot drag the
        midpoint the way an ordinary outlier would.
        """
        series = [
            87_418_084,
            84_265_967,
            84_052_980,
            80_060_288,
            76_637_258,
            76_637_258_000,  # bad
            72_443_000_000,  # bad
            72_924_659_000,  # bad
            73_509_714_000,  # bad
            73_327_586,
            73_364_071,
            73_655_224,
            72_959_064,
            72_837_141,
        ]
        assert scale_error_indices(series, BOUNDS) == [5, 6, 7, 8]

    def test_the_clean_bookends_of_a_bad_run_are_untouched(self):
        """AAP 2011-06-01 and 2012-08-20 are correct and must survive.

        They sit at ~0.001x their local median, because their neighbours
        are the corrupt ones. An unsigned "is this far from local median"
        rule would reject them; the rule is deliberately one-sided.
        """
        series = [
            87_418_084,
            84_265_967,
            84_052_980,
            80_060_288,
            76_637_258,  # clean, surrounded
            76_637_258_000,
            72_443_000_000,
            72_924_659_000,
            73_509_714_000,
            73_327_586,  # clean, surrounded
            73_364_071,
            73_655_224,
            72_959_064,
            72_837_141,
        ]
        flagged = scale_error_indices(series, BOUNDS)
        assert 4 not in flagged
        assert 9 not in flagged


class TestTheCounterexamplesThatMustSurvive:
    def test_wulf_dilution_is_never_flagged(self):
        """16 genuine filings a global-median rule rejects.

        `x_global` runs 105x to 247x and rises monotonically — the
        signature of real dilution, not of a scale error, which spikes and
        returns. This is the test that stops a global bound from being
        substituted back in later.
        """
        series = [
            2_016_000,
            5_000_000,
            12_000_000,
            98_000_000,
            212_032_468,
            221_132_914,
            238_203_308,
            238_203_308,
            302_235_299,
            333_182_028,
            382_597_605,
            385_907_681,
            383_137_722,
            384_584_010,
            391_926_373,
            418_681_881,
            424_068_125,
            495_532_645,
            498_968_677,
        ]
        assert scale_error_indices(series, BOUNDS) == []

    def test_psky_is_excluded_for_want_of_neighbours(self):
        """Its median is the corruption. Too few filings to judge, so don't.

        Silence here is the correct answer, not a missed catch: the guard
        declines to rule rather than ruling from two data points.
        """
        series = [1_000, 1_000, 1_071_666_977]
        assert scale_error_indices(series, BOUNDS) == []

    def test_a_ten_to_one_split_is_not_a_scale_error(self):
        """NVDA 2024. The largest real jump in the tracked universe.

        `SharesPlausibility` already argues a real split "never approaches
        the 1,000x+ jump these bounds are built to catch". A 10x step that
        *persists* must stay clear of the anomaly threshold with margin.
        """
        series = [
            2_460_000_000,
            2_470_000_000,
            2_480_000_000,
            2_490_000_000,
            2_500_000_000,
            24_600_000_000,  # 10:1, and it stays
            24_650_000_000,
            24_700_000_000,
            24_750_000_000,
            24_800_000_000,
        ]
        assert scale_error_indices(series, BOUNDS) == []


class TestTheRecoveryConditionIsLoadBearing:
    def test_an_anomaly_that_is_not_a_scale_error_is_left_alone(self):
        """Flagging requires that dividing by 1,000 actually explains it.

        A 100x spike is anomalous but is not this defect, and /1000 leaves
        it 10x *below* its neighbours rather than on top of them. Rejecting
        it would be guessing at a factor from the data's own shape — the
        thing `_implausible_shares_reason` refuses to do. The guard stays
        narrow: it only removes rows whose corruption it can name.

        The measured errors sit at 510x-1271x, so the accepted band has
        real margin on both sides of every row it is built to catch.
        """
        series = [
            50_000_000,
            51_000_000,
            52_000_000,
            53_000_000,
            5_350_000_000,  # ~100x, unexplained by a x1000 scale
            54_000_000,
            55_000_000,
            56_000_000,
            57_000_000,
            58_000_000,
        ]
        assert scale_error_indices(series, BOUNDS) == []


class TestPurity:
    def test_the_input_is_not_mutated(self):
        """Convention: never mutate in place, always return a new object."""
        series = [
            176_793_291,
            177_700_396,
            183_287_033,
            185_435_623,
            186_187_121,
            186_277_145,
            187_889_808_000,
            188_415_515_000,
            189_651_416,
            190_841_047,
            194_321_490,
            191_983_618,
        ]
        before = list(series)
        scale_error_indices(series, BOUNDS)
        assert series == before

    def test_an_empty_series_is_answerable(self):
        assert scale_error_indices([], BOUNDS) == []
