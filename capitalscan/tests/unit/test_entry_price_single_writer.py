"""`entry_price` means one thing: a fill the backtest priced.

Until 2026-08-22 it meant two. `run_events` wrote the raw touch level
(ADR 049: "its same-bar entry price, cheap: no forward bars needed") while
`research.enrich.resolve_entries` wrote the same level *with slippage baked
in*:

    raw_price = entry_price_for(...)
    price = raw_price + leg_slip        # adverse to side

Both wrote the same column. Nothing recorded which convention a given row
carried, and two consumers had quietly keyed off the difference.

**How it surfaced.** `cscan backtest --phase harness` failed
`entry_sanity` on 32 events. `_pre_slippage_price` divides the 3bps back
out to recover the price that traded inside the bar, and on a row that
never had slippage added that lands *below* the bar's low — 47.3246
against an ALKS low of 47.3300. The engine was right and the check was
right; the rows were never the backtest's to validate.

**The worse one.** `test_events_in_trade_filter.py` exempts
`path_backfill`, `path_labels`, `path_queries` and `path_reconcile` from
the `in_trade` predicate, on the reasoning that they are "keyed on
`entry_price IS NOT NULL`, which only the backtest writes … the wider
detections have no entry price". That was true when written and stopped
being true when ADR 122 made `run_events` record out-of-trade bars instead
of skipping them. Measured: **755 out-of-trade events carrying an
`entry_price`, and 1,324 `path` rows built from them.**

Fixing the writer rather than the four readers is what makes that
exemption's stated reason true again, instead of leaving it stale behind a
predicate patched in four places.
"""

from __future__ import annotations

import ast
import inspect

from capitalscan.jobs import compute


def _event_row_source() -> str:
    return inspect.getsource(compute._build_event_row)


class TestRunEventsDoesNotPrice:
    def test_the_row_carries_no_entry_price(self):
        """`_build_event_row` must not compute a fill.

        Pricing belongs to `research/enrich.py`, which owns cost
        application. A second place that sets this column is a second
        convention, whatever value it writes.
        """
        tree = ast.parse(_event_row_source().lstrip())
        fn = tree.body[0]
        assert isinstance(fn, ast.FunctionDef)
        code = ast.unparse(fn)
        assert "entry_price_for" not in code, (
            "run_events is pricing entries again; the backtest owns entry_price"
        )

    def test_entry_price_is_neither_written_nor_owned(self):
        """Omitted entirely, and **this reverses an earlier decision here.**

        The first version of this test required `"entry_price": None`,
        reasoning that the column sits in `_RUN_EVENTS_UPDATE_COLUMNS` so an
        omitted key would raise at upsert. Both halves were true and the
        conclusion was wrong: owning the column meant every nightly *erased*
        a price the backtest had computed, while `gross_ret` -- which this
        job does not own -- survived. The row became a return with no entry,
        which `_check_return_identity` fails on, correctly. SNA 2026-08-19
        and 2026-08-20 reached that state before it was caught.

        Ruling C4 settles it: a writer names only the columns it computes.
        `run_events` computes no fill, so it neither writes nor owns one.
        Dropping the key *and* the ownership together is what makes an
        insert still land NULL (the column's default) while an existing
        backtest price is left alone.
        """
        src = _event_row_source()
        assert '"entry_price"' not in src, (
            "run_events writes entry_price again; owning this column makes "
            "the nightly null the backtest's fill and strand gross_ret"
        )
        assert "entry_price" not in compute._RUN_EVENTS_UPDATE_COLUMNS
        assert "entry_date" not in compute._RUN_EVENTS_UPDATE_COLUMNS

    def test_the_column_is_still_reachable_by_its_real_writer(self):
        """Dropping ownership must not orphan the column: the backtest
        still writes and updates it."""
        from capitalscan.research import backtest

        assert "entry_price" in backtest._RUN_BACKTEST_UPDATE_COLUMNS
        assert "entry_price" in backtest._EVENT_COLUMNS

    def test_touch_level_is_still_recorded(self):
        """The detection fact survives.

        `touch_level` is the band the signal fired against and is what the
        ticker page should show for a detection nobody could trade. Dropping
        the fill price must not drop the level with it.
        """
        assert '"touch_level": hit.touch_level' in _event_row_source()


class TestTheProxyHoldsAgain:
    """`entry_price IS NOT NULL` means "the backtest priced this".

    Four modules depend on that sentence being true — see the
    `KNOWN_UNFILTERED` allowlist in `test_events_in_trade_filter.py`. It is
    a property of *who writes the column*, so it belongs here rather than
    being re-asserted at each reader.
    """

    def test_only_enrich_applies_slippage_to_an_entry(self):
        from capitalscan.research import enrich

        src = inspect.getsource(enrich.resolve_entries)
        assert "leg_slip" in src

    def test_the_allowlist_reason_is_still_accurate(self):
        """The exemption's wording is load-bearing and must match reality.

        `threshold_lint.KNOWN_EXCEPTIONS` learned this the hard way: an
        exemption kept past its reason stops documenting a known problem and
        starts hiding a working one. This one went stale silently because
        the test only checked that the entry still *matched a file*, never
        that its stated justification still held.
        """
        from capitalscan.tests.unit import test_events_in_trade_filter as gate

        reason = gate.KNOWN_UNFILTERED["research/path_backfill.py"]
        assert "only the backtest writes" in reason
