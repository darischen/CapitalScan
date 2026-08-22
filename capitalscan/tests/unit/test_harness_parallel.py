"""The parallel harness, written before it exists.

`run_harness` is single-threaded and takes ~6 hours on 865,984 events
(measured 2026-08-21). The plan in `BACKLOG.md` chunks tickers across a
`ProcessPoolExecutor`, which is safe because every check is per-ticker or
per-event and the module is pure.

Two things about that split would produce a wrong answer that still looks
entirely reasonable, and they are the reason these tests are written first:

1. **Jaccard aggregates counts, not ratios.** Averaging per-chunk Jaccards
   is not the Jaccard of the union.
2. **The control shuffle must stay global.** `_shuffled_control` mixes
   indicator values *across* tickers deliberately; shuffling per-chunk is a
   weaker control and moves `jc` toward its 0.15 floor for reasons that have
   nothing to do with the detection engine.

The equivalence tests below skip until `run_harness` grows `max_workers`,
and activate on their own the moment it does.
"""

from __future__ import annotations

import inspect
from fractions import Fraction

import pytest

from capitalscan.research.harness import _jaccard, run_harness

_PARALLEL_READY = "max_workers" in inspect.signature(run_harness).parameters
_needs_parallel = pytest.mark.skipif(
    not _PARALLEL_READY,
    reason="parallel harness not implemented yet — see BACKLOG.md",
)


# ---------------------------------------------------------------------------
# The aggregation rule, which holds independently of any API
# ---------------------------------------------------------------------------


class TestJaccardAggregatesOverCounts:
    """Tickers are disjoint, so the sets partition cleanly:

        |A ∩ B| = Σ |Aᵢ ∩ Bᵢ|      |A ∪ B| = Σ |Aᵢ ∪ Bᵢ|

    which makes the whole-universe Jaccard reconstructable from per-chunk
    *counts*. It is not reconstructable from per-chunk Jaccards.
    """

    def test_counts_reconstruct_the_whole_universe_value(self):
        # Two chunks, disjoint tickers. Chunk sets built explicitly so the
        # arithmetic is checkable by eye.
        a1, b1 = {("AAA", 1), ("AAA", 2)}, {("AAA", 1)}
        a2, b2 = {("BBB", 1)}, {("BBB", 1), ("BBB", 2), ("BBB", 3)}

        whole = _jaccard(a1 | a2, b1 | b2)

        inter = len(a1 & b1) + len(a2 & b2)
        union = len(a1 | b1) + len(a2 | b2)
        assert inter / union == pytest.approx(whole)

    def test_averaging_per_chunk_jaccards_gives_a_different_answer(self):
        """**The point of the whole test file.**

        Without this, an implementation that averages ratios passes every
        other test here: it returns a number in [0, 1] that moves in the
        right direction and sits in a plausible range. This pins the two
        apart on a concrete case so the wrong one cannot survive.
        """
        a1, b1 = {("AAA", 1), ("AAA", 2)}, {("AAA", 1)}
        a2, b2 = {("BBB", 1)}, {("BBB", 1), ("BBB", 2), ("BBB", 3)}

        correct = Fraction(len(a1 & b1) + len(a2 & b2), len(a1 | b1) + len(a2 | b2))
        averaged = (Fraction(len(a1 & b1), len(a1 | b1)) + Fraction(len(a2 & b2), len(a2 | b2))) / 2

        assert correct == Fraction(2, 5)
        assert averaged == Fraction(5, 12)
        assert correct != averaged

    def test_chunking_does_not_change_the_value_however_it_is_cut(self):
        """Any partition of the tickers must give the same answer.

        A merge that depended on chunk *count* or chunk *boundaries* would
        make the gate's verdict a function of `--workers`, which is the
        determinism failure ADR 060 exists to prevent.
        """
        per_ticker = {
            "AAA": ({("AAA", 1), ("AAA", 2)}, {("AAA", 1)}),
            "BBB": ({("BBB", 1)}, {("BBB", 1), ("BBB", 2)}),
            "CCC": ({("CCC", 9)}, {("CCC", 9)}),
            "DDD": (set(), {("DDD", 4)}),
        }
        names = sorted(per_ticker)
        whole_a = set().union(*(per_ticker[t][0] for t in names))
        whole_b = set().union(*(per_ticker[t][1] for t in names))
        expected = _jaccard(whole_a, whole_b)

        for cut in ([1, 1, 1, 1], [2, 2], [3, 1], [4]):
            chunks, i = [], 0
            for size in cut:
                chunks.append(names[i : i + size])
                i += size
            inter = sum(len(per_ticker[t][0] & per_ticker[t][1]) for c in chunks for t in c)
            union = sum(len(per_ticker[t][0] | per_ticker[t][1]) for c in chunks for t in c)
            assert inter / union == pytest.approx(expected), f"cut {cut} disagreed"

    def test_an_empty_chunk_contributes_nothing_rather_than_agreement(self):
        """**The sharpest reason counts beat ratios, and it fails toward a pass.**

        `_jaccard(∅, ∅)` is 1.0 by design — "both empty: no measurable
        disagreement, vacuously identical". That is right for one call and
        poison for an average: a chunk whose tickers produced no events
        contributes a *perfect agreement score* it never measured, pulling
        the mean toward 1.0.

        1.0 is the direction that makes a look-ahead failure look like a
        pass. `j[1] < 0.80` and `j[5] < 0.50` are ceilings, so an inflated
        Jaccard is what a broken engine would produce. Summed counts give
        such a chunk `(0, 0)`, which adds nothing to either total.
        """
        assert _jaccard(set(), set()) == 1.0

        live_a, live_b = {("AAA", 1), ("AAA", 2)}, {("AAA", 1)}
        empty_a, empty_b = set(), set()

        counted = Fraction(
            len(live_a & live_b) + len(empty_a & empty_b),
            len(live_a | live_b) + len(empty_a | empty_b),
        )
        averaged = (Fraction(1, 2) + Fraction(1, 1)) / 2

        assert counted == Fraction(1, 2)  # the empty chunk changed nothing
        assert averaged == Fraction(3, 4)  # ... and here it invented agreement
        assert averaged > counted


# ---------------------------------------------------------------------------
# Equivalence — activates when `max_workers` lands
# ---------------------------------------------------------------------------


@_needs_parallel
class TestParallelMatchesSerial:
    """The entire correctness claim: workers change speed, never verdict."""

    def test_reports_are_identical(self, harness_fixture):
        serial = run_harness(*harness_fixture, max_workers=1)
        parallel = run_harness(*harness_fixture, max_workers=4)
        for name in (
            "no_lookahead",
            "entry_sanity",
            "exit_sanity",
            "return_identity",
            "non_overlap",
        ):
            s, p = getattr(serial, name), getattr(parallel, name)
            assert s.passed == p.passed, name
            assert s.violations == p.violations, name
            assert s.detail == p.detail, name

    def test_jaccard_values_match_to_full_precision(self, harness_fixture):
        # `detail` carries the measured Jaccards. A merge that recomputed
        # them approximately would pass `passed ==` while quietly moving the
        # numbers a human reads off the report.
        serial = run_harness(*harness_fixture, max_workers=1).no_lookahead.detail
        parallel = run_harness(*harness_fixture, max_workers=4).no_lookahead.detail
        assert serial == parallel

    def test_worker_count_does_not_change_the_verdict(self, harness_fixture):
        verdicts = {run_harness(*harness_fixture, max_workers=n).all_passed for n in (1, 2, 3, 8)}
        assert len(verdicts) == 1

    def test_default_is_serial(self):
        """Every existing caller must keep its current behaviour untouched."""
        assert inspect.signature(run_harness).parameters["max_workers"].default == 1


@_needs_parallel
class TestTheControlIsShuffledGlobally:
    """`_shuffled_control` mixes indicator values across tickers on purpose.

    Shuffling inside a worker only mixes that worker's tickers, which leaves
    more of the bar-to-indicator relationship intact. The control would then
    agree more with the base set, `jc` would rise toward the 0.15 floor, and
    the gate could fail for a reason unrelated to look-ahead.
    """

    def test_control_jaccard_is_independent_of_worker_count(self, harness_fixture):
        values = {
            run_harness(*harness_fixture, max_workers=n).no_lookahead.detail.get("jc")
            for n in (1, 4)
        }
        assert len(values) == 1


# ---------------------------------------------------------------------------
# The fixture the equivalence tests run against
# ---------------------------------------------------------------------------


@pytest.fixture
def harness_fixture():
    """A small synthetic universe: `(events, bars_by_ticker, config)`.

    **What this does and does not cover.** The equivalence tests assert that
    serial and parallel agree, not that the checks pass — so synthetic data
    that *produces* violations is useful rather than a problem, because it
    exercises the violation merging as well as the counts.

    The lookahead merge is the part that can be wrong while looking right
    (see `LookaheadCounts`), and it is driven entirely by `bars_by_ticker`,
    which is real in shape here: four tickers, 400 bars each, indicators
    from `compute_all` rather than hand-written.

    `events` is thin. The four per-event checks need columns only the
    backtest writes (`entry_price`, `exit_date`, `gross_ret`, ...), and
    fabricating a realistic priced population belongs in an integration
    test. What is asserted here is that whatever those checks conclude,
    they conclude it identically under both paths.
    """
    import numpy as np
    import pandas as pd

    from capitalscan.core.config import Config, IndicatorParams
    from capitalscan.core.indicators import compute_all

    rng = np.random.default_rng(7)
    bars_by_ticker: dict[str, pd.DataFrame] = {}
    for i, ticker in enumerate(("AAA", "BBB", "CCC", "DDD")):
        n = 400
        idx = pd.date_range("2023-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(rng.normal(0, 1.5, n)) + i
        frame = pd.DataFrame(
            {
                "open": close + 0.05,
                "high": close + 1.2,
                "low": close - 1.2,
                "close": close,
                "adj_close": close,
                "volume": rng.integers(1_000_000, 5_000_000, n),
            },
            index=idx,
        )
        out = compute_all(frame, IndicatorParams())
        out = out.reset_index(names="ts")
        out["ticker"] = ticker
        bars_by_ticker[ticker] = out

    events = pd.DataFrame(
        {
            "ticker": pd.Series(dtype="object"),
            "signal_date": pd.Series(dtype="object"),
            "side": pd.Series(dtype="object"),
            "entry_kind": pd.Series(dtype="object"),
            "entry_date": pd.Series(dtype="object"),
            "entry_price": pd.Series(dtype="float64"),
            "exit_date": pd.Series(dtype="object"),
            "exit_price": pd.Series(dtype="float64"),
            "exit_reason": pd.Series(dtype="object"),
            "gross_ret": pd.Series(dtype="float64"),
            "touch_level": pd.Series(dtype="float64"),
            "is_cluster_head": pd.Series(dtype="bool"),
        }
    )
    return events, bars_by_ticker, Config()
