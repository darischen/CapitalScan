"""Plausibility guard on `shares_outstanding.shares` at ingestion (Session 9
shares-guard task; see `.superpowers/sdd/2026-08-01-session-9-backtest/
mcap-outlier-diagnosis.md`).

Background: a single SEC XBRL filing can report `EntityCommonStockShares
Outstanding` scaled by an extra x1,000 or x1,000,000 (a real, occasionally
seen filer tagging defect, confirmed directly against the cached raw
companyfacts parquet for ORCL). `run_shares` used to write `int(row["value"])`
straight through with no check, so one bad accession corrupted `universe.
mcap_usd` and, downstream, `crit_mcap`/`in_trade`.

The fix rejects (never corrects — invariant 4, and guessing the scale factor
from the data's own shape is the exact failure mode this codebase keeps
hitting) any filing whose `shares` falls outside `core.config.
SharesPlausibility`'s absolute `[min_shares, max_shares]` band, and logs the
rejection to `bar_rejects` (reused as a generic append-only reject log; see
`db_io.append`'s docstring — `bar_rejects` and `runs` are the two append-only
tables) with a `rule` and a `payload` carrying the rejected value.

`min_shares`/`max_shares` are absolute, not relative to the ticker's own
history: a median- or neighbor-relative test fails on PSKY (median 1,000,
because two of its three known filings are themselves bad, so the one
genuine filing — 1,071,666,977 — is the one that looks like the outlier).
See `SharesPlausibility`'s docstring in `core/config.py` for the full
reasoning and the bounds' provenance.

No database here — the engine and both fetchers are stubbed, per this
session's constraints (never touch a real `shares_outstanding` row).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd
import pytest

from capitalscan.core.config import SharesPlausibility
from capitalscan.jobs import ingest


class _FakeConn:
    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return None


class _NoNamesResult:
    """`run_shares` looks up listing names to find depositary tickers.

    These suites test SEC/Yahoo selection, not ADR handling, so the lookup
    answers "no names on file" — which makes `_depositary_tickers` empty and
    leaves every assertion below about ordinary listings unchanged.
    """

    def fetchall(self):  # noqa: ANN201
        return []


class _NameLookupConn:
    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return _NoNamesResult()

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc):  # noqa: ANN002, ANN204
        return False


class _FakeEngine:
    def connect(self):  # noqa: ANN201
        return _NameLookupConn()

    @contextmanager
    def begin(self):  # noqa: ANN201
        yield _FakeConn()


@pytest.fixture()
def upserted(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    def fake_upsert(engine, table_name, data, conflict_cols):  # noqa: ANN001, ANN202
        if table_name == "shares_outstanding":
            seen.extend(data)
        return len(data)

    monkeypatch.setattr(ingest.db_io, "upsert", fake_upsert)
    return seen


@pytest.fixture()
def rejected(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    def fake_append(engine, table_name, data):  # noqa: ANN001, ANN202
        if table_name == "bar_rejects":
            seen.extend(data)
        return len(data)

    monkeypatch.setattr(ingest.db_io, "append", fake_append)
    return seen


def _stub(monkeypatch, cik_lookup: pd.DataFrame, facts_by_cik: dict[int, pd.DataFrame]) -> None:
    monkeypatch.setattr(ingest.sec, "fetch_cik_lookup", lambda: cik_lookup)
    monkeypatch.setattr(
        ingest.sec, "fetch_company_facts", lambda cik: facts_by_cik.get(cik, pd.DataFrame())
    )


# ============ bounds sanity (mirrors core.config.SharesPlausibility docstring) ============


def test_default_bounds_bracket_the_confirmed_genuine_and_bad_extremes():
    bounds = SharesPlausibility()
    # Citigroup's highest confirmed-genuine filing on file.
    assert bounds.min_shares < 29_206_440_560 < bounds.max_shares
    # PSKY's one confirmed-genuine filing.
    assert bounds.min_shares < 1_071_666_977 < bounds.max_shares
    # A 10:1 split on today's largest issuer (Citigroup, ~29.2B) still
    # clears the ceiling (Change 1, Session 9 config-thresholds task): the
    # old 32B ceiling sat only ~10% above a *live* count and would reject a
    # single genuine 2:1 split on Citigroup, TSM, or NVDA. 320B leaves room
    # above even a 10:1 split (292B).
    assert 292_000_000_000 < bounds.max_shares
    # Confirmed-bad: ORCL x1e6 is still caught at the new ceiling.
    assert 4_819_056_000_000_000 > bounds.max_shares
    # Confirmed-bad: Alaska Air x1e3 (~35.8B) is NO LONGER caught by
    # max_shares alone at the raised ceiling — see
    # test_alk_1e3_scaled_filing_is_no_longer_caught_by_raised_ceiling and
    # SharesPlausibility's docstring ("Known limit, stated plainly") for
    # why this is an accepted, documented cost of Change 1.
    assert 35_828_450_000 < bounds.max_shares
    # Confirmed-bad: PSKY's two placeholder-shaped rows.
    assert 1_000 < bounds.min_shares


# ============ 1. a x1,000,000 filing is rejected and logged ============


def test_a_1e6_scaled_filing_is_rejected_and_logged(monkeypatch, upserted, rejected):
    """ORCL's real 2012-09-24 shape: 4,819,056,000,000,000 instead of ~4.8B."""
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["ORCL"], "cik": [1341439]}),
        {
            1341439: pd.DataFrame(
                {
                    "filed_on": ["2012-09-24"],
                    "end": ["2012-08-31"],
                    "value": [4819056000000000],
                    "accn": ["0001193125-12-401697"],
                }
            )
        },
    )

    report = ingest.run_shares(["ORCL"], engine=_FakeEngine())

    assert upserted == []
    assert len(rejected) == 1
    assert rejected[0]["ticker"] == "ORCL"
    assert rejected[0]["severity"] == "reject"
    assert rejected[0]["rule"] == "shares_above_plausible_ceiling"
    assert rejected[0]["payload"]["shares"] == 4819056000000000
    assert report.rows_rejected == 1


# ============ 2. a x1,000 filing is no longer rejected (Change 1 tradeoff) ============


def test_alk_1e3_scaled_filing_is_no_longer_caught_by_raised_ceiling(
    monkeypatch, upserted, rejected
):
    """Alaska Air's real 2011 shape: ~35.8M shares reported as ~35.8B.

    Before Change 1 (Session 9 config-thresholds task) this was rejected by
    the 32B ceiling. Change 1 raised the ceiling to 320B so a genuine 2:1 or
    even 10:1 split on Citigroup/TSM/NVDA is never permanently rejected
    (see `SharesPlausibility`'s docstring). That widened ceiling has a
    documented cost: a x1,000 filer error on a company whose real share
    count is in the tens of millions, exactly ALK's shape, now lands inside
    `[min_shares, max_shares]` and is accepted rather than rejected. This
    test pins that known, accepted tradeoff rather than silently losing
    coverage of it — do not "fix" this test by shrinking max_shares back
    down; that reintroduces the frozen-ticker defect Change 1 exists to fix.

    **The bounds still do not catch it, and ADR 146 does.** What changed on
    2026-08-22 is a second, series-level pass, not the band — see
    `test_the_series_level_pass_catches_what_the_bounds_cannot` below. It
    stays silent here because ALK is stubbed with **one** filing and a
    window needs neighbours to have an opinion, which is the same gate that
    protects PSKY. So this test is unaffected by ADR 146 by construction,
    and remains the correct statement about `max_shares` alone.
    """
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["ALK"], "cik": [766421]}),
        {
            766421: pd.DataFrame(
                {
                    "filed_on": ["2011-02-23"],
                    "end": ["2010-12-31"],
                    "value": [35831543000],
                    "accn": ["0000766421-11-000005"],
                }
            )
        },
    )

    report = ingest.run_shares(["ALK"], engine=_FakeEngine())

    assert rejected == []
    assert len(upserted) == 1
    assert upserted[0]["shares"] == 35831543000
    assert report.rows_rejected == 0


# ============ 5. the series-level pass, where the bounds cannot reach ============


def test_the_series_level_pass_catches_what_the_bounds_cannot(monkeypatch, upserted, rejected):
    """ALK's real filing series, not one row of it (ADR 146).

    The test above is right that `[min_shares, max_shares]` cannot see this
    class. A ticker's own series can: three filings sit ~1,000x their
    neighbours and drop straight back, which no split does. Values verbatim
    from `shares_outstanding`.

    This is the ingest wiring, so it also pins where the pass runs. It must
    come **after** the `(ticker, filed_on)` dedup: one filing reports facts
    for several periods, and on the raw rows the same value repeats and
    drags the local median toward whichever filing carried the most
    periods.
    """
    filed = [
        "2010-08-05",
        "2011-02-23",
        "2011-05-04",
        "2011-08-04",
        "2011-11-03",
        "2012-02-21",
        "2012-05-03",
        "2012-08-08",
        "2012-11-07",
        "2013-02-14",
        "2013-05-08",
        "2013-08-07",
    ]
    values = [
        35_817_109,
        35_831_543_000,  # bad
        35_828_450_000,  # bad
        36_006_026_000,  # bad
        35_514_972,
        35_453_202,
        71_097_685,
        70_494_808,
        70_314_136,
        70_341_799,
        70_313_337,
        69_869_025,
    ]
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["ALK"], "cik": [766421]}),
        {
            766421: pd.DataFrame(
                {
                    "filed_on": filed,
                    "end": filed,
                    "value": values,
                    "accn": [f"0000766421-11-{i:06d}" for i in range(len(filed))],
                }
            )
        },
    )

    report = ingest.run_shares(["ALK"], engine=_FakeEngine())

    assert {r["payload"]["shares"] for r in rejected} == {
        35_831_543_000,
        35_828_450_000,
        36_006_026_000,
    }
    assert {r["rule"] for r in rejected} == {"shares_scale_error_x1000"}
    assert report.rows_rejected == 3

    # The 2012 doubling is a real 2:1 split and must survive intact.
    kept = sorted(row["shares"] for row in upserted)
    assert kept == sorted(v for v in values if v < 1e9)
    assert 71_097_685 in kept


def test_nothing_is_divided_only_dropped(monkeypatch, upserted, rejected):
    """The rejected value is never written back at 1/1000 (invariant 4).

    Naming the factor is a *precondition* for rejection, not a repair.
    A corrected-looking 35,831,543 appearing in the upsert would mean the
    guard had started guessing at scale factors, which is the failure mode
    `_implausible_shares_reason` exists to avoid.
    """
    filed = [f"20{y:02d}-05-01" for y in range(10, 22)]
    values = [50_000_000 + i for i in range(12)]
    values[5] = 50_000_005_000  # x1000 of its own neighbourhood
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["TEST"], "cik": [999]}),
        {
            999: pd.DataFrame(
                {
                    "filed_on": filed,
                    "end": filed,
                    "value": values,
                    "accn": [f"0000000999-00-{i:06d}" for i in range(12)],
                }
            )
        },
    )

    ingest.run_shares(["TEST"], engine=_FakeEngine())

    written = {row["shares"] for row in upserted}
    assert 50_000_005_000 not in written
    assert 50_000_005 not in written
    assert len(rejected) == 1


# ============ 3. an implausibly small filing is rejected (the PSKY shape) ============


def test_an_implausibly_small_filing_is_rejected(monkeypatch, upserted, rejected):
    """PSKY's own bad shape: a placeholder-looking 1,000 shares."""
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["PSKY"], "cik": [1]}),
        {
            1: pd.DataFrame(
                {
                    "filed_on": ["2025-05-14"],
                    "end": ["2025-03-31"],
                    "value": [1000],
                    "accn": ["0000000001-25-000001"],
                }
            )
        },
    )

    report = ingest.run_shares(["PSKY"], engine=_FakeEngine())

    assert upserted == []
    assert len(rejected) == 1
    assert rejected[0]["rule"] == "shares_below_plausible_floor"
    assert rejected[0]["payload"]["shares"] == 1000
    assert report.rows_rejected == 1


def test_psky_good_filing_is_accepted_even_though_its_own_median_is_bad(
    monkeypatch, upserted, rejected
):
    """The counterexample that rules out a median-relative test: two bad
    1,000-share filings plus one genuine 1,071,666,977-share filing. A
    median-based test would flag the genuine row as the outlier and reject
    it. The absolute-bounds guard must keep it.
    """
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["PSKY"], "cik": [1]}),
        {
            1: pd.DataFrame(
                {
                    "filed_on": ["2025-05-14", "2025-08-01", "2025-11-10"],
                    "end": ["2025-03-31", "2025-06-30", "2025-09-30"],
                    "value": [1000, 1000, 1071666977],
                    "accn": [
                        "0000000001-25-000001",
                        "0000000001-25-000002",
                        "0000000001-25-000003",
                    ],
                }
            )
        },
    )

    ingest.run_shares(["PSKY"], engine=_FakeEngine())

    assert len(upserted) == 1
    assert upserted[0]["shares"] == 1071666977
    assert len(rejected) == 2
    assert {r["rule"] for r in rejected} == {"shares_below_plausible_floor"}


# ============ 4. a legitimate 10:1-split-driven jump is accepted ============


def test_a_legitimate_10_for_1_split_jump_is_accepted(monkeypatch, upserted, rejected):
    """NVDA's real 2024 shape: ~2.5B shares pre-split, ~24.5B post-split —
    a genuine 10x jump between consecutive filings, both individually
    plausible. Neither filing may be rejected: the guard evaluates each
    filing's absolute plausibility, never a ticker's own delta, precisely
    so a real split is never mistaken for a scale-factor defect.
    """
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["NVDA"], "cik": [1045810]}),
        {
            1045810: pd.DataFrame(
                {
                    "filed_on": ["2024-05-29", "2024-08-28"],
                    "end": ["2024-04-28", "2024-07-28"],
                    "value": [2494000000, 24530000000],
                    "accn": [
                        "0001045810-24-000100",
                        "0001045810-24-000150",
                    ],
                }
            )
        },
    )

    report = ingest.run_shares(["NVDA"], engine=_FakeEngine())

    assert rejected == []
    assert sorted(r["shares"] for r in upserted) == [2494000000, 24530000000]
    assert report.rows_rejected == 0


# ============ 4b. a post-2:1-split Citigroup-scale count is accepted ============


def test_a_post_2_for_1_split_citigroup_scale_count_is_accepted(monkeypatch, upserted, rejected):
    """Review finding (Change 1): the ceiling used to sit at 32B, only ~10%
    above Citigroup's real 29.2B pre-2011-reverse-split count. A single 2:1
    split on Citigroup, TSM, or NVDA today produces a genuine filing above
    32B that the old ceiling rejected permanently, with no fallback and no
    error. This filing (~58.4B, Citigroup's 29,206,440,560 doubled by a
    hypothetical forward split) must clear the raised ceiling.
    """
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["C"], "cik": [831001]}),
        {
            831001: pd.DataFrame(
                {
                    "filed_on": ["2026-06-01"],
                    "end": ["2026-03-31"],
                    "value": [58412881120],
                    "accn": ["0000831001-26-000001"],
                }
            )
        },
    )

    report = ingest.run_shares(["C"], engine=_FakeEngine())

    assert rejected == []
    assert len(upserted) == 1
    assert upserted[0]["shares"] == 58412881120
    assert report.rows_rejected == 0


# ============ 5. a normal filing is accepted ============


def test_a_normal_filing_is_accepted(monkeypatch, upserted, rejected):
    _stub(
        monkeypatch,
        pd.DataFrame({"ticker": ["WMT"], "cik": [104169]}),
        {
            104169: pd.DataFrame(
                {
                    "filed_on": ["2020-01-01"],
                    "end": ["2019-12-31"],
                    "value": [2850000000],
                    "accn": ["0000104169-20-000001"],
                }
            )
        },
    )

    report = ingest.run_shares(["WMT"], engine=_FakeEngine())

    assert len(upserted) == 1
    assert upserted[0]["shares"] == 2850000000
    assert rejected == []
    assert report.rows_rejected == 0


# ============ 6. the yahoo fallback path is guarded identically ============


def test_a_bad_yahoo_fallback_row_is_rejected_and_logged(monkeypatch, upserted, rejected):
    """The fallback source is not exempt: a corrupted Yahoo estimate must be
    caught the same way an SEC filing is.
    """
    _stub(monkeypatch, pd.DataFrame({"ticker": ["ZZZ"], "cik": [9]}), {9: pd.DataFrame()})
    monkeypatch.setattr(
        ingest.yahoo,
        "fetch_shares_full",
        lambda ticker, start, end: pd.DataFrame(
            {
                "ticker": [ticker],
                "filed_on": [date(2026, 5, 5)],
                "shares": [500],
            }
        ),
    )

    report = ingest.run_shares(["ZZZ"], engine=_FakeEngine())

    assert upserted == []
    assert len(rejected) == 1
    assert rejected[0]["rule"] == "shares_below_plausible_floor"
    assert rejected[0]["payload"]["source"] == ingest.SHARES_YAHOO_SOURCE
    assert report.rows_rejected == 1
