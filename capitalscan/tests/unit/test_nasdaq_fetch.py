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
    monkeypatch.setattr(nasdaq, "fetch_listed", lambda *_a, **_k: pd.DataFrame(data))
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
            lambda *_a, **_k: pd.DataFrame(
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
            lambda *_a, **_k: pd.DataFrame(
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
            lambda *_a, **_k: pd.DataFrame(
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
            nasdaq,
            "fetch_listed",
            lambda *_a, **_k: pd.DataFrame([_row("BRK.B", "900000000000.00")]),
        )
        assert nasdaq.tickers_above(1e9) == ["BRK-B"]

    def test_upper_cases_and_trims(self, monkeypatch):
        monkeypatch.setattr(
            nasdaq,
            "fetch_listed",
            lambda *_a, **_k: pd.DataFrame([_row(" nvda ", "4000000000000.00")]),
        )
        assert nasdaq.tickers_above(1e9) == ["NVDA"]

    def test_drops_a_blank_symbol(self, monkeypatch):
        monkeypatch.setattr(
            nasdaq, "fetch_listed", lambda *_a, **_k: pd.DataFrame([_row("", "50000000000.00")])
        )
        assert nasdaq.tickers_above(1e9) == []

    def test_deduplicates(self, monkeypatch):
        monkeypatch.setattr(
            nasdaq,
            "fetch_listed",
            lambda *_a, **_k: pd.DataFrame(
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
        monkeypatch.setattr(nasdaq, "_fetch_rows", lambda *_a, **_k: [_row("AAPL", "1.0")])
        assert isinstance(nasdaq.fetch_listed.__wrapped__(), pd.DataFrame)

    def test_an_empty_frame_yields_no_tickers(self, monkeypatch):
        monkeypatch.setattr(nasdaq, "fetch_listed", lambda *_a, **_k: pd.DataFrame())
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
        assert ingest.SEC_NON_FILER_TICKERS == frozenset({"QQQ", "SPY", "VOO", "IBIT"})


# ---------------------------------------------------------------------------
# The batch cache key (unrelated source, same night's bug)
# ---------------------------------------------------------------------------


class TestBatchKeyIsBounded:
    """`cscan bars --tickers <317 symbols>` exited 0 and wrote nothing on
    2026-08-21: the joined key produced a filename past the OS limit and
    `to_parquet` raised *after* the fetch had already succeeded."""

    def test_a_large_batch_key_stays_short(self):
        from datetime import date

        from capitalscan.jobs.fetch.yahoo import _batch_key

        key = _batch_key([f"TICK{i:04d}" for i in range(400)], date(2020, 1, 1), date(2021, 1, 1))
        assert len(key) < 120, key
        assert "TICK0000" not in key

    def test_a_small_batch_keeps_its_readable_key(self):
        """Existing cache entries must still answer.

        CLAUDE.md's rule is that bumping the *source* is what discards a
        cache, and this change is not one -- what the fetcher returns for
        given arguments is untouched, so re-filing old work would be pure
        waste.
        """
        from datetime import date

        from capitalscan.jobs.fetch.yahoo import _batch_key

        assert (
            _batch_key(["AAPL", "MSFT"], date(2020, 1, 1), date(2021, 1, 1))
            == "AAPL-MSFT_2020-01-01_2021-01-01"
        )

    def test_argument_order_does_not_split_the_entry(self):
        from datetime import date

        from capitalscan.jobs.fetch.yahoo import _batch_key

        many_a = [f"T{i}" for i in range(50)]
        many_b = list(reversed(many_a))
        d0, d1 = date(2020, 1, 1), date(2021, 1, 1)
        assert _batch_key(many_a, d0, d1) == _batch_key(many_b, d0, d1)

    def test_different_batches_do_not_collide(self):
        from datetime import date

        from capitalscan.jobs.fetch.yahoo import _batch_key

        d0, d1 = date(2020, 1, 1), date(2021, 1, 1)
        a = _batch_key([f"T{i}" for i in range(50)], d0, d1)
        b = _batch_key([f"T{i}" for i in range(1, 51)], d0, d1)
        assert a != b


# ---------------------------------------------------------------------------
# Exchange parameterisation (2026-08-25, for the NYSE round)
# ---------------------------------------------------------------------------


class TestTheCacheKeyIsPerExchange:
    """The trap BACKLOG named before this was written.

    `fetch_listed` is `@cached` on a key that was the bare constant
    `listed_with_mcap`. Calling it for a second exchange through the same
    key returns the **Nasdaq** snapshot, and NYSE looks like it has no
    listings at all: a wrong answer that raises nothing.
    """

    def test_nasdaq_keeps_its_original_key(self):
        """Deliberately asymmetric.

        Making the key `listed_with_mcap_{exchange}` for everything would
        change Nasdaq's key too, turning its next call into a miss and
        silently replacing the snapshot the current universe was built
        from. CLAUDE.md records that exact failure: a cache key is a
        promise, and changing what a key means is how a fix ships and never
        runs.
        """
        assert nasdaq._listed_key(nasdaq.NASDAQ) == "listed_with_mcap"
        assert nasdaq._listed_key() == "listed_with_mcap"

    def test_another_exchange_gets_its_own_key(self):
        assert nasdaq._listed_key(nasdaq.NYSE) == "listed_with_mcap_NYSE"

    def test_two_exchanges_never_share_a_key(self):
        """The property, rather than the two spellings above."""
        keys = {nasdaq._listed_key(x) for x in (nasdaq.NASDAQ, nasdaq.NYSE, "AMEX")}
        assert len(keys) == 3


class TestTheExchangeReachesTheRequest:
    def test_the_exchange_parameter_is_sent(self, monkeypatch):
        """A parameterised function that ignores its parameter and returns
        Nasdaq anyway is the same bug one layer down."""
        seen = {}

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": {"rows": []}}

        def _get(url, params=None, headers=None, timeout=None):
            seen.update(params or {})
            return _Resp()

        monkeypatch.setattr(nasdaq.requests, "get", _get)
        nasdaq._fetch_rows(nasdaq.NYSE)
        assert seen["exchange"] == "NYSE"

    def test_the_default_is_still_nasdaq(self, monkeypatch):
        """Every existing caller passes nothing and must keep getting
        Nasdaq."""
        seen = {}

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": {"rows": []}}

        monkeypatch.setattr(
            nasdaq.requests,
            "get",
            lambda url, params=None, headers=None, timeout=None: (
                seen.update(params or {}) or _Resp()
            ),
        )
        nasdaq._fetch_rows()
        assert seen["exchange"] == "NASDAQ"


class TestTheFilterIsExchangeAgnostic:
    def test_preferred_series_are_dropped_on_any_exchange(self, monkeypatch):
        """NYSE carries far more preferred series than Nasdaq, and each one
        gets the *issuer's* market cap from the screener, so it clears any
        floor on its parent's size while its bars are a different
        instrument. The filter is the same one; this pins that it still
        runs when the exchange changes.
        """
        rows = [
            _row("BAC", "300000000000.00", name="Bank of America Corporation Common Stock"),
            _row("BAC-PL", "300000000000.00", name="Bank of America Corp 7.25% Preferred Series L"),
        ]
        monkeypatch.setattr(nasdaq, "fetch_listed", lambda *_a, **_k: pd.DataFrame(rows))
        got = nasdaq.tickers_above(10e9, nasdaq.NYSE)
        assert got == ["BAC"]


class TestTheExchangeSurvivesTheWholeCallChain:
    """The integration test whose absence let a real bug through.

    `test_the_exchange_parameter_is_sent` checks `_fetch_rows` in isolation
    and passed while `fetch_listed` was dropping its argument on the floor:
    it took `exchange`, used it for the cache key, then called
    `_fetch_rows()` with no arguments. Every NYSE request fetched Nasdaq and
    filed it under the NYSE key.

    Nothing raised. The cache file appeared with a plausible size, the frame
    had 4,145 rows, and the tell was that AAPL was in it and JPM was not.

    Two unit tests, both green, either side of the one call that mattered.
    """

    def test_fetch_listed_forwards_the_exchange(self, monkeypatch):
        seen = []
        monkeypatch.setattr(nasdaq, "_fetch_rows", lambda ex=nasdaq.NASDAQ: seen.append(ex) or [])
        nasdaq.fetch_listed.__wrapped__(nasdaq.NYSE)
        assert seen == [nasdaq.NYSE]

    def test_tickers_above_forwards_the_exchange(self, monkeypatch):
        seen = []

        def _listed(ex=nasdaq.NASDAQ):
            seen.append(ex)
            return pd.DataFrame([_row("JPM", "700000000000.00")])

        monkeypatch.setattr(nasdaq, "fetch_listed", _listed)
        nasdaq.tickers_above(10e9, nasdaq.NYSE)
        assert seen == [nasdaq.NYSE]
