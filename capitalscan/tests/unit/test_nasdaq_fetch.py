"""The Nasdaq ticker source, and the two ways a ticker list goes wrong.

Neither failure mode here raises. A mis-normalised symbol produces a ticker
that never matches anything in `tickers`, and a floor applied to the wrong
number produces a universe that looks deliberate and is not.

No network. `fetch_listed` is stubbed with rows shaped like the real
endpoint's, including the fields it actually returns malformed.
"""

from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.jobs import ingest
from capitalscan.jobs.fetch import nasdaq


def _row(symbol: str, cap: str, **kw: object) -> dict:
    """One screener row. `marketCap` is a *string* on the real endpoint."""
    return {"symbol": symbol, "name": f"{symbol} Inc.", "marketCap": cap, **kw}


@pytest.fixture
def rows(monkeypatch):
    data = [
        _row("AAPL", "3500000000000.00"),
        _row("MSFT", "3100000000000.00"),
        _row("IBIT", "85000000000.00"),
        _row("BIGCO", "20000000000.00"),
        _row("TENB", "10000000000.00"),  # exactly at the floor
        _row("SMALL", "9999999999.00"),  # a dollar under it
        _row("TINY", "69148660.00"),
        _row("NOCAP", ""),  # listed, no cap reported
        _row("BADCAP", "n/a"),
    ]
    monkeypatch.setattr(nasdaq, "fetch_listed", lambda: pd.DataFrame(data))
    return data


class TestTheFloor:
    def test_keeps_names_at_or_above_the_floor(self, rows):
        got = nasdaq.tickers_above(10e9)
        assert "TENB" in got, "exactly at the floor is above it"
        assert "BIGCO" in got
        assert "AAPL" in got

    def test_drops_a_name_one_dollar_under(self, rows):
        assert "SMALL" not in nasdaq.tickers_above(10e9)

    def test_a_missing_cap_fails_the_floor_rather_than_passing_it(self, rows):
        """**Absent is not permissive**, the shape invariant 4 asks for.

        The endpoint returns an empty `marketCap` for some recent listings.
        Treating that as "unknown, let it through" would ingest an unbounded
        tail; treating it as zero drops it, and a name that matters will
        have a cap by the next snapshot.
        """
        got = nasdaq.tickers_above(10e9)
        assert "NOCAP" not in got
        assert "BADCAP" not in got

    def test_the_ingest_floor_sits_below_the_trade_floor(self):
        """The whole reason there are two numbers.

        `min_mcap_usd` decides tradeability from point-in-time data;
        `INGEST_MIN_MCAP_USD` decides only what is worth holding bars for.
        Equal floors would mean a name at $12B today could never contribute
        the quarters when it was above $20B, because its bars would not
        exist to measure.
        """
        from capitalscan.core.config import UniverseParams

        assert nasdaq.INGEST_MIN_MCAP_USD < UniverseParams().min_mcap_usd


class TestOnlyCommonStockAndOrdinaryADRs:
    """The screener lists every security and gives each the *issuer's* cap.

    AGNC Investment appears seven times -- the common plus six
    depositary-preferred series, each reporting the parent's $10B+. Six
    extra "companies" whose bars are a preferred share's price. 29 of 176
    new names on 2026-08-21 were this shape.
    """

    def test_drops_depositary_preferred_series(self, monkeypatch):
        monkeypatch.setattr(
            nasdaq,
            "fetch_listed",
            lambda: pd.DataFrame(
                [
                    dict(
                        symbol="AGNCP",
                        name=(
                            "AGNC Investment Corp. Depositary Shares rep 6.875% Series D Fixed-Rate"
                        ),
                        marketCap="12000000000.00",
                    )
                ]
            ),
        )
        assert nasdaq.tickers_above(1e9) == []

    def test_drops_a_tangible_equity_unit(self, monkeypatch):
        # BTSGU, whose reported cap ($38.8B) is *larger* than the common's
        # ($11.8B) -- these instruments carry nonsense caps as well as being
        # the wrong instrument.
        monkeypatch.setattr(
            nasdaq,
            "fetch_listed",
            lambda: pd.DataFrame(
                [
                    dict(
                        symbol="BTSGU",
                        name="BrightSpring Health Services Inc. Tangible Equity Unit",
                        marketCap="38866472860.00",
                    )
                ]
            ),
        )
        assert nasdaq.tickers_above(1e9) == []

    def test_keeps_an_ordinary_adr(self, monkeypatch):
        """**ADR 011 admits non-US exposure through US-listed ADRs.**

        Matching on "depositary" alone drops all 16 of them -- ARM, ARGX,
        BNTX and the rest. It did, on the first attempt. The pattern names
        what makes a security *preferred*, not what makes it depositary.
        """
        monkeypatch.setattr(
            nasdaq,
            "fetch_listed",
            lambda: pd.DataFrame(
                [
                    dict(
                        symbol="ARM",
                        name="Arm Holdings plc American Depositary Shares",
                        marketCap="140000000000.00",
                    )
                ]
            ),
        )
        assert nasdaq.tickers_above(1e9) == ["ARM"]

    def test_unit_is_word_bounded_so_unitedhealth_survives(self):
        """**The bug this test exists for.**

        The word boundaries were written through a shell heredoc that turned
        `\b` into a literal backspace byte (0x08). The pattern compiled, ran,
        and matched nothing -- `repr` showed
        `'warrant|\x08units?\x08|'`. Every other case still passed, because
        they match on multi-character alternatives that need no boundary.

        Without the boundary `unit` matches "UnitedHealth" and drops UNH, a
        Dow component. With a corrupted boundary it matches nothing and
        every unit slips through. This asserts the behaviour at both ends.
        """
        assert nasdaq._is_common({"name": "UnitedHealth Group Incorporated Common Stock"})
        assert not nasdaq._is_common({"name": "Acme Corp. Tangible Equity Unit"})
        # And the compiled pattern holds real boundaries, not control bytes.
        assert "" not in repr(nasdaq._NOT_COMMON.pattern)


class TestSymbolNormalisation:
    def test_rewrites_class_shares_the_way_wikipedia_ingest_does(self, monkeypatch):
        # `run_tickers_refresh` does `.upper().replace(".", "-")`. A symbol
        # normalised differently here would create a second row for the same
        # company and never join to the first.
        monkeypatch.setattr(
            nasdaq, "fetch_listed", lambda: pd.DataFrame([_row("BRK.B", "900000000000.00")])
        )
        assert nasdaq.tickers_above(1e9) == ["BRK-B"]

    def test_upper_cases_and_trims(self, monkeypatch):
        monkeypatch.setattr(
            nasdaq, "fetch_listed", lambda: pd.DataFrame([_row(" nvda ", "4000000000000.00")])
        )
        assert nasdaq.tickers_above(1e9) == ["NVDA"]

    def test_drops_a_blank_symbol(self, monkeypatch):
        monkeypatch.setattr(
            nasdaq, "fetch_listed", lambda: pd.DataFrame([_row("", "50000000000.00")])
        )
        assert nasdaq.tickers_above(1e9) == []

    def test_deduplicates(self, monkeypatch):
        monkeypatch.setattr(
            nasdaq,
            "fetch_listed",
            lambda: pd.DataFrame(
                [_row("AAPL", "3500000000000.00"), _row("AAPL", "3500000000000.00")]
            ),
        )
        assert nasdaq.tickers_above(1e9) == ["AAPL"]

    def test_is_sorted_so_two_runs_agree(self, rows):
        got = nasdaq.tickers_above(10e9)
        assert got == sorted(got)


class TestTheSnapshotIsCached:
    def test_fetch_listed_returns_a_frame_because_the_cache_writes_parquet(self, monkeypatch):
        """The contract the stubs above must honour.

        `@cached` calls `to_parquet` on whatever it wraps. A list survives a
        cache *hit* and raises `AttributeError` on the *write*, so the first
        real call fails and every stubbed test still passes -- which is
        exactly what happened while writing this file.
        """
        monkeypatch.setattr(nasdaq, "_fetch_rows", lambda: [_row("AAPL", "1.0")])
        assert isinstance(nasdaq.fetch_listed.__wrapped__(), pd.DataFrame)

    def test_an_empty_frame_yields_no_tickers(self, monkeypatch):
        monkeypatch.setattr(nasdaq, "fetch_listed", lambda: pd.DataFrame())
        assert nasdaq.tickers_above(1e9) == []

    def test_the_source_string_is_versioned(self):
        """A cache key must capture everything that determines the output.

        CLAUDE.md's rule, and it applies with force here: the payload is
        "what is listed today", so a re-fetch silently changes the universe.
        Bumping the source is how a new snapshot is taken deliberately.
        """
        import inspect

        src = inspect.getsource(nasdaq.fetch_listed)
        assert "nasdaq_screener" in src


class TestETFsSkipTheSecEndpoints:
    def test_the_three_etfs_are_named(self):
        """An ETF files no 10-K/10-Q/8-K, so the XBRL endpoints 404 by
        design. Named explicitly rather than caught as a blanket
        `except NotFoundError`, so a 404 on a real operating company still
        surfaces as the data problem it is."""
        assert {"QQQ", "VOO", "IBIT"} <= ingest.SEC_NON_FILER_TICKERS

    def test_the_list_holds_only_etfs(self):
        # A ticker added here stops being checked against SEC forever. That
        # is right for a fund and wrong for a company, so the list stays
        # short and its growth is a decision someone makes here.
        assert ingest.SEC_NON_FILER_TICKERS == frozenset({"QQQ", "VOO", "IBIT"})
