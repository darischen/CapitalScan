"""Which rows are eligible to *train* a model. Pure functions, no IO.

Distinct from `core/universe.py`, which decides what is **tradeable**. A
ticker can be fully tradeable — firing signals, carrying cell statistics,
appearing on the screener — and still be ineligible to train, because
training asks a question trading does not: does this row carry the features
the model conditions on?

ADR 147 is the decision this module implements.
"""

from __future__ import annotations

from collections.abc import Sequence

from capitalscan.core.sectors import is_canonical

# Exchange-traded products in the tracked universe. QQQ is the only one with
# a `tickers` row today; VOO and IBIT appear in
# `jobs.ingest.SEC_NON_FILER_TICKERS` but have never been ingested.
#
# **Deliberately a separate list from `SEC_NON_FILER_TICKERS`**, which it
# currently mirrors. That set answers "does SEC serve companyfacts for this?"
# and this one answers "is this an instrument rather than a company?". They
# agree today and need not: a foreign private issuer can file nothing useful
# and still be a company, and an ETF sponsor could in principle file. Sharing
# one list would silently couple a training decision to an ingest detail.
ETF_TICKERS: frozenset[str] = frozenset({"QQQ", "VOO", "IBIT"})


def is_etf(ticker: str | None) -> bool:
    """True when this listing is a fund or trust rather than an operating company.

    Explicit membership, never inferred from a missing field. ADR 147 turns
    on the difference: an ETF has **no** sector (the attribute does not
    apply), while an equity with a blank sector has an unpopulated one. Both
    render as NULL, and only a list can tell them apart.
    """
    if not ticker:
        return False
    return ticker.upper() in ETF_TICKERS


def training_exclusion_reason(ticker: str | None, sector: str | None) -> str | None:
    """`None` when the row may train; otherwise why it may not.

    Two rejections, and they are **not** the same rejection wearing
    different labels:

    - `etf` is a decision. ADR 068 makes `sector` the granularity that
      stands in for ticker identity, and DESIGN §7.3 excludes `ticker`
      because "60 names over 40k events permits memorizing individual
      histories". One fund in a category of its own is that identity
      restored through a feature the design includes. It is excluded from
      training and stays fully tradeable.

    - `missing_sector` is a **defect**. Every such ticker is an operating
      company with a real sector that `tickers.sector` does not hold, because
      `run_tickers_refresh` populates that column solely from Wikipedia's
      *current* S&P 500 table. Removed constituents (kept on purpose by ADR
      035) and the Nasdaq additions (ADR 143) therefore arrive blank.

    **Why the distinction is load-bearing.** Measured 2026-08-22, 32 tickers
    reach the training population with a NULL sector; 31 are equities and
    only QQQ is a fund. Collapsing the two into one `sector IS NULL` filter
    would silently drop ASML, TSM, ILMN, VFC, M, ETSY, AAL and 24 others —
    overwhelmingly *removed* S&P members, which is exactly the survivorship
    bias ADR 035 exists to prevent. The model would train only on names that
    were still in the index when the table was last scraped.

    So `missing_sector` is returned as its own reason for the caller to
    **raise on**, not to filter on. The frame builder must fail loudly and
    the sector must be backfilled from a real source. Nothing here imputes
    one: a guessed sector is a fabricated one (invariant 4).
    """
    if is_etf(ticker):
        return "etf"
    if sector is None or not str(sector).strip():
        return "missing_sector"
    # ADR 148. A sector in some other vocabulary is as unusable as a blank
    # one: `tickers.sector` held both GICS and Nasdaq names, so
    # `Information Technology` (53,031 training events) and `Technology`
    # (4,513) were two levels for one sector. ADR 147's first gate checked
    # only for NULL and would have passed that frame while it carried a
    # split category, which is the defect this module exists to prevent.
    if not is_canonical(sector):
        return "non_canonical_sector"
    return None


def may_train(ticker: str | None, sector: str | None) -> bool:
    """True when this row is eligible to train. Convenience over the reason."""
    return training_exclusion_reason(ticker, sector) is None


def partition_for_training(
    rows: Sequence[tuple[str | None, str | None]],
) -> tuple[list[int], list[int], list[int]]:
    """Split row positions into (trainable, etf, missing_sector).

    Positions rather than rows so the caller keeps whatever row type it has,
    and so a frame builder can report exactly which events it dropped and
    which it must refuse to proceed on.
    """
    trainable: list[int] = []
    etf: list[int] = []
    missing: list[int] = []
    for i, (ticker, sector) in enumerate(rows):
        reason = training_exclusion_reason(ticker, sector)
        if reason is None:
            trainable.append(i)
        elif reason == "etf":
            etf.append(i)
        else:
            # `missing_sector` and `non_canonical_sector` share a bucket:
            # both are data defects the caller must raise on, and both are
            # repaired by re-resolving from the source of record (ADR 148).
            missing.append(i)
    return trainable, etf, missing
