"""`clean` conflates two different questions; separate them.

DESIGN §5.8 makes `cscan validate --report` a deliberate human gate. Two very
different things can stop it:

  - the data is bad (rejects that cost bars, missing bars, Stooq disagreeing)
  - a check could not run at all (vendor outage, network block)

Both must block an unattended rerun, so `clean` stays the conjunction. But an
operator reading the report needs to tell them apart: bad data means fix the
data, an unavailable vendor means decide whether to proceed without that
safeguard. Reporting one boolean forces them to read the prose to find out
which world they are in.

Concretely, as of 2026-08-01 Stooq serves a JavaScript proof-of-work
challenge to `requests` on every endpoint (stooq.com and stooq.pl, with and
without a browser UA), so the cross-check cannot run at all while every data
check passes.
"""

from __future__ import annotations

import pandas as pd

from capitalscan.jobs.ingest import ValidationReport

EMPTY = pd.DataFrame()


def _report(
    stooq_checked: int = 20,
    stooq_errors: pd.DataFrame = EMPTY,
    stooq_disagreements: pd.DataFrame = EMPTY,
    missing_bars: pd.DataFrame = EMPTY,
    open_rejects: pd.DataFrame = EMPTY,
) -> ValidationReport:
    """An all-passing report, with one dimension varied per test."""
    return ValidationReport(
        reject_counts=pd.DataFrame(columns=["rule", "severity", "n"]),
        coverage=EMPTY,
        stooq_disagreements=stooq_disagreements,
        clean=False,
        stooq_checked=stooq_checked,
        stooq_errors=stooq_errors,
        missing_bars=missing_bars,
        open_rejects=open_rejects,
    )


def test_data_clean_is_true_when_only_the_cross_check_is_unavailable():
    """A vendor outage is not a data-quality problem."""
    report = _report(
        stooq_checked=0,
        stooq_errors=pd.DataFrame([{"ticker": "A", "error": "404"}]),
    )

    assert report.data_clean is True
    assert report.checks_complete is False


def test_data_clean_is_false_when_a_reject_still_costs_a_bar():
    report = _report(
        open_rejects=pd.DataFrame([{"ticker": "A", "d": "2024-01-02", "severity": "reject"}])
    )

    assert report.data_clean is False


def test_data_clean_is_false_when_a_bar_is_missing():
    report = _report(missing_bars=pd.DataFrame([{"ticker": "A", "missing_date": "2024-01-02"}]))

    assert report.data_clean is False


def test_data_clean_is_false_when_stooq_actually_disagrees():
    """A completed check that found a discrepancy is a data problem."""
    report = _report(stooq_disagreements=pd.DataFrame([{"ticker": "A", "frac_disagreeing": 0.5}]))

    assert report.data_clean is False
    assert report.checks_complete is True


def test_everything_passing_is_both_clean_and_complete():
    report = _report()

    assert report.data_clean is True
    assert report.checks_complete is True
