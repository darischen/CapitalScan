"""ADR 147: who may train, and why a NULL sector is two different problems.

`sector` is a categorical feature (DESIGN §7.3) and ADR 068 pins it as the
granularity that stands in for ticker identity. LightGBM gives a NULL its own
level, so a category with one member is ticker identity restored through a
feature the design includes — the precise thing §7.3 excludes `ticker` to
prevent.

**The measurement that shaped the decision.** 32 tickers reach the training
population with a NULL sector. Exactly one, QQQ, is a fund. The other 31 are
operating companies whose sector is merely unpopulated, because
`run_tickers_refresh` writes that column only from Wikipedia's *current*
S&P 500 constituent table.

So a single `sector IS NULL` filter would drop 31 equities — mostly *removed*
index members — and reintroduce the survivorship bias ADR 035 exists to
prevent. The two cases must stay separable, which is why exclusion is keyed
on an explicit ETF list and a missing sector raises instead.
"""

from __future__ import annotations

import pytest

from capitalscan.core.training import (
    ETF_TICKERS,
    is_etf,
    may_train,
    partition_for_training,
    training_exclusion_reason,
)

# Verbatim from `events` joined to `tickers` on 2026-08-22: every ticker in
# the training population carrying a NULL sector, with its event count.
# Thirty-one equities and one fund.
NULL_SECTOR_IN_TRAINING = {
    "ASML": 2088,
    "QQQ": 1910,
    "TSM": 919,
    "NVO": 871,
    "ILMN": 780,
    "VFC": 642,
    "SAP": 561,
    "MTCH": 458,
    "ENPH": 414,
    "M": 338,
    "EPAM": 305,
    "BBWI": 268,
    "ETSY": 235,
    "DXC": 224,
    "AAL": 189,
    "KMX": 173,
    "PAYC": 167,
    "CAG": 163,
    "NOV": 154,
    "FTI": 132,
    "CZR": 122,
    "MOH": 118,
    "BIO": 99,
    "QRVO": 97,
    "ATI": 62,
    "CPB": 58,
    "MHK": 53,
    "PRGO": 50,
    "POOL": 49,
    "NWL": 44,
    "MKTX": 43,
    "LUMN": 40,
}


class TestTheEtfDecision:
    def test_the_only_ingested_etf_is_excluded(self):
        assert training_exclusion_reason("QQQ", None) == "etf"
        assert not may_train("QQQ", None)

    def test_exclusion_does_not_depend_on_the_sector_being_null(self):
        """The constraint ADR 147 is most likely to be violated by later.

        If someone backfills `tickers.sector` for QQQ — plausibly to
        `'ETF'`, which is Option A — the fund must still be excluded. Keying
        on the ticker rather than on the blank field is what makes that
        true, and this test is what stops a future `sector IS NULL` filter
        from quietly becoming the implementation.
        """
        assert training_exclusion_reason("QQQ", "ETF") == "etf"
        assert training_exclusion_reason("QQQ", "Technology") == "etf"

    def test_case_is_not_a_way_around_it(self):
        assert is_etf("qqq")
        assert is_etf(" ") is False

    def test_an_equity_is_never_an_etf(self):
        for ticker in ("AAPL", "ASML", "TSM", "ILMN"):
            assert not is_etf(ticker)


class TestAMissingSectorIsADefectNotAFilter:
    def test_an_equity_with_no_sector_is_reported_not_silently_dropped(self):
        """A distinct reason, so the caller can raise rather than filter."""
        assert training_exclusion_reason("ASML", None) == "missing_sector"
        assert training_exclusion_reason("ILMN", "") == "missing_sector"
        assert training_exclusion_reason("VFC", "   ") == "missing_sector"

    def test_the_two_reasons_are_distinguishable(self):
        """The whole point. If these collapse, 31 equities vanish silently."""
        assert training_exclusion_reason("QQQ", None) != training_exclusion_reason("ASML", None)

    def test_the_measured_population_splits_31_to_1(self):
        """Pins the survivorship argument to the real numbers.

        If a later change makes ETF exclusion subsume the missing-sector
        case, this fails with a count rather than with an opinion.
        """
        rows = [(t, None) for t in NULL_SECTOR_IN_TRAINING]
        _, etf, missing = partition_for_training(rows)
        assert len(etf) == 1
        assert len(missing) == 31

    def test_the_equities_dropped_by_a_naive_filter_are_named(self):
        """`sector IS NULL` would take 9,916 events off removed S&P members.

        Stated as a number because "some survivorship bias" is easy to wave
        through and 84% of the affected events is not.
        """
        etf_events = NULL_SECTOR_IN_TRAINING["QQQ"]
        total = sum(NULL_SECTOR_IN_TRAINING.values())
        assert total == 11826
        assert total - etf_events == 9916
        assert (total - etf_events) / total > 0.83


class TestTheTrainingFrameGate:
    """The criterion that fails whichever option was chosen if it was not built.

    A frame is only admissible when no surviving row carries a NULL sector
    **and** no surviving row is a fund.
    """

    def _build(self, rows):
        """Model of the Session 22 frame builder: filter funds, raise on gaps."""
        trainable, _, missing = partition_for_training(rows)
        if missing:
            raise ValueError(
                f"sector missing on {len(missing)} row(s): {sorted(rows[i][0] for i in missing)}"
            )
        return [rows[i] for i in trainable]

    def test_the_built_frame_carries_no_null_sector(self):
        """**The gate.** Fails whichever option was chosen if it was not built."""
        rows = [("AAPL", "Information Technology"), ("XOM", "Energy")]
        frame = self._build(rows)
        assert frame
        assert all(sector for _, sector in frame)

    def test_the_etf_does_not_survive_into_the_built_frame(self):
        """Filtered, not rejected. QQQ stays tradeable; it just does not train."""
        rows = [("AAPL", "Information Technology"), ("QQQ", None)]
        frame = self._build(rows)
        assert [t for t, _ in frame] == ["AAPL"]
        assert all(not is_etf(t) for t, _ in frame)

    def test_a_null_sector_equity_stops_the_build_rather_than_vanishing(self):
        """The 31. Raising is the decision; filtering would be the bug.

        Silently dropping these is what reintroduces survivorship bias, so
        the builder must refuse to produce a frame at all.
        """
        rows = [("AAPL", "Information Technology"), ("ASML", None)]
        with pytest.raises(ValueError, match="ASML"):
            self._build(rows)

    def test_giving_the_etf_a_sector_does_not_rescue_a_broken_frame(self):
        """Option A, implemented alone, still fails this gate.

        This is what makes the gate real rather than ceremonial: it fails on
        the 31 equities regardless of which ETF option was chosen, which is
        how the too-narrow framing was caught in the first place.
        """
        with pytest.raises(ValueError, match="ASML"):
            self._build([("QQQ", "ETF"), ("ASML", None)])


class TestPurity:
    def test_partition_covers_every_row_exactly_once(self):
        rows = [
            ("AAPL", "Information Technology"),
            ("QQQ", None),
            ("ASML", None),
            ("XOM", "Energy"),
        ]
        trainable, etf, missing = partition_for_training(rows)
        assert sorted(trainable + etf + missing) == list(range(len(rows)))
        assert trainable == [0, 3]
        assert etf == [1]
        assert missing == [2]

    def test_empty_input(self):
        assert partition_for_training([]) == ([], [], [])

    @pytest.mark.parametrize("ticker", sorted(ETF_TICKERS))
    def test_every_declared_etf_is_excluded(self, ticker):
        assert training_exclusion_reason(ticker, "anything") == "etf"
