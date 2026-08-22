"""Depositary listings are priced from the count that matches the price.

Measured 2026-08-22. `NTES` carried a peak `mcap_usd` of **$1,666.9B**
against a real NetEase peak near $100B, because its SEC filing reports
3,349,335,066 **ordinary** shares while the bar price is per **ADR**:

    ticker   sec (ordinary)      yahoo (ADR)    ratio
    NTES      3,192,111,251      640,250,196     4.99
    PDD       5,693,585,848    1,423,396,462     4.00
    HTHT      3,071,525,690      307,523,819     9.99
    SIMO        134,244,840       33,907,750     3.96
    VOD      26,676,624,411    2,311,033,297    11.54   <- real ratio is 10
    LI        2,027,667,098      807,696,399     2.51
    ONC       1,478,124,405      104,271,757    14.18

ADR 014 solved this with `adr_ordinary_per_adr`, a hand-maintained map. The
map has one entry (`TSM: 5.0`) and the expansion added 22 depositary
listings.

**The ratio is not derivable and must not be inferred.** VOD measures 11.54
against a real 10:1, and LI and ONC land nowhere near an integer, because
SEC's latest filing and Yahoo's current count are months apart and the share
count drifts between them. `jobs.ingest._implausible_shares_reason` names
this failure directly: "dividing by an inferred scale factor would be
guessing at the factor from the data's own shape, which is exactly how a
wrong guess turns into a plausible-looking wrong number."

**So use the count that is already in the right unit.** Yahoo reports
`sharesOutstanding` per ADR, so no ratio is computed, nothing is rounded,
and the three tickers no map could safely cover are fixed along with the
rest.
"""

from __future__ import annotations

import pytest

from capitalscan.core.universe import is_depositary_listing


class TestDetection:
    """Name-based, because nothing else in the schema records it.

    There is no `is_adr` column and SEC does not expose one; the listing
    name is the only signal available at ingest.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "NetEase Inc. American Depositary Shares",
            "Vodafone Group Plc American Depositary Shares",
            "PDD Holdings Inc. American Depositary Shares",
            "argenx SE American Depositary Shares",
        ],
    )
    def test_depositary_names_match(self, name):
        assert is_depositary_listing(name) is True

    @pytest.mark.parametrize(
        "name",
        ["Apple Inc.", "Microsoft", "Packaging Corporation of America", "Garmin"],
    )
    def test_ordinary_names_do_not(self, name):
        assert is_depositary_listing(name) is False

    def test_broadridge_is_not_an_adr(self):
        """**The substring trap, found by hitting it.**

        A first pass matched `%ADR%` and flagged *Bro**adr**idge* — a US
        company with ordinary shares. Matching the phrase rather than the
        letters is the whole point: `ADR` and `ADS` appear inside ordinary
        English words, `American Depositary` does not.
        """
        assert is_depositary_listing("Broadridge Financial Solutions") is False

    def test_a_missing_name_is_not_a_match(self):
        """317 Nasdaq tickers arrived with a NULL name. "Unknown" must not
        become "ADR" — that would switch a correct SEC count for a Yahoo one
        on hundreds of ordinary listings."""
        assert is_depositary_listing(None) is False
        assert is_depositary_listing("") is False

    def test_case_and_spacing_do_not_matter(self):
        assert is_depositary_listing("BILIBILI INC AMERICAN DEPOSITARY SHARES") is True
        assert is_depositary_listing("Grifols SA - American  Depositary Shares") is True


class TestNoRatioIsInferred:
    """The rule this fix exists to obey.

    Nothing in the implementation may compute or round a ratio. Yahoo's
    count is used as-is because it is already per-ADR.
    """

    def test_the_source_computes_no_ratio(self):
        """Checked against the code body, with the docstring stripped.

        The docstring necessarily explains why no ratio is inferred, so a
        substring search over the raw source matches its own explanation
        and can never pass — the same trap as the `searchsorted` test.
        """
        import ast
        import inspect

        from capitalscan.core import universe

        tree = ast.parse(inspect.getsource(universe.is_depositary_listing).lstrip())
        fn = tree.body[0]
        assert isinstance(fn, ast.FunctionDef)
        body = fn.body[1:] if ast.get_docstring(fn) else fn.body
        code = "\n".join(ast.unparse(node) for node in body)

        for smell in ("round(", "ratio", "/"):
            assert smell not in code, f"{smell!r} suggests a ratio is being inferred"
