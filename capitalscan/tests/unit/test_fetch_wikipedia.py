"""Unit tests for the Wikipedia S&P 500 fetcher (BUILD §5.2).

The regression these lock down: `pandas.read_html` no longer accepts a
literal HTML string (removed in pandas 3.0), so passing `resp.text`
directly makes lxml treat the whole page as a *filename*. The failure
mode is an `OSError` whose message contains the entire document, which
is what reaches the terminal.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
import requests

from capitalscan.jobs.fetch import wikipedia

TWO_TABLE_PAGE = """
<!DOCTYPE html>
<html><body>
<table id="constituents">
  <tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>
  <tr><td>NVDA</td><td>Nvidia</td><td>Information Technology</td></tr>
  <tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td></tr>
</table>
<table id="changes">
  <tr>
    <th rowspan="2">Effective Date</th><th colspan="2">Added</th>
    <th colspan="2">Removed</th><th rowspan="2">Reason</th>
  </tr>
  <tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
  <tr><td>2026-01-02</td><td>AAA</td><td>Alpha</td><td>ZZZ</td><td>Zeta</td><td>Mkt cap.</td></tr>
</table>
</body></html>
"""


@pytest.fixture
def stub_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve `TWO_TABLE_PAGE` over a stubbed `requests.get`, with no sleeping."""

    def fake_get(url: str, **kwargs: Any) -> requests.Response:
        response = requests.Response()
        response.status_code = 200
        response._content = TWO_TABLE_PAGE.encode("utf-8")
        response.encoding = "utf-8"
        return response

    monkeypatch.setattr(requests, "get", fake_get)
    # `base.rate_limited` resolves `time.sleep` at call time, so patching the
    # stdlib module here is enough to keep the 1 req/s courtesy delay out of tests.
    monkeypatch.setattr(time, "sleep", lambda _s: None)


class TestGetTables:
    def test_parses_in_memory_html_without_touching_the_filesystem(self, stub_page: None):
        """The core regression: the response body is content, never a path."""
        tables = wikipedia._get_tables(wikipedia.SP500_URL)

        assert len(tables) == 2
        assert tables[0].shape == (2, 3)

    def test_does_not_leak_the_document_into_an_exception(self, stub_page: None):
        """A parse failure must not echo the page back to the caller.

        Guards the reported symptom directly: whatever goes wrong, the
        error text must stay short enough to read in a terminal.
        """
        try:
            wikipedia._get_tables(wikipedia.SP500_URL)
        except Exception as exc:  # pragma: no cover - only on regression
            assert len(str(exc)) < 500, "exception message contains the raw document"
            raise


class TestFlattenHeader:
    def test_drops_unnamed_colspan_fillers(self):
        assert wikipedia._flatten_header(("Added", "Ticker")) == "Added_Ticker"
        assert wikipedia._flatten_header(("Symbol", "Unnamed: 0_level_1")) == "Symbol"

    def test_collapses_a_rowspan_repeat(self):
        assert wikipedia._flatten_header(("Effective Date", "Effective Date")) == "Effective Date"

    def test_keeps_genuinely_distinct_levels(self):
        assert wikipedia._flatten_header(("Removed", "Security")) == "Removed_Security"


class TestFetchers:
    def test_current_constituents_renames_symbol_to_ticker(
        self, stub_page: None, monkeypatch: pytest.MonkeyPatch, tmp_path
    ):
        monkeypatch.setattr("capitalscan.jobs.fetch.base.CACHE_ROOT", tmp_path)
        df = wikipedia.fetch_current_constituents()

        assert "ticker" in df.columns
        assert "gics_sector" in df.columns
        assert list(df["ticker"]) == ["NVDA", "BRK.B"]
