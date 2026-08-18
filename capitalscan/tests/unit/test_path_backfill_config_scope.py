"""`path backfill` and `path capture` are scoped to one `config_hash`.

**The defect.** Both selected events with no `config_hash` filter:

    SELECT id, entry_price, side, signal_date FROM events
    WHERE ticker = :ticker AND entry_price IS NOT NULL

so a full backfill rebuilt forward paths for **every generation that had
ever been backtested**. Measured 2026-08-17: 23 generations, 67.5M `path`
rows, 5,568,263 events processed on the last unscoped run, 2h56m. Only
four generations had any consumer in `cell_stats`, `benchmarks`, or
`rho_era`.

**Why it is self-undoing, not merely slow.** Superseded events are kept
deliberately (ADR 096) so published numbers stay auditable and two
populations can be compared at the event level — that comparison is what
proved the ADR 110 flip left the band conditions untouched. So an unscoped
backfill regenerates every `path` row a manual prune removes, and the
prune buys nothing durable.

`cscan path peak-labels` was already scoped this way. This makes all three
`path` commands agree.

These tests target `_events_query_for_ticker`, which exists precisely so
the query can be checked without a database or a worker round trip.
"""

from __future__ import annotations

import pytest

from capitalscan.research.path_backfill import _events_query_for_ticker

HASH = "86e91448a65aa40b"


def test_the_query_filters_on_config_hash_when_given_one():
    query, params = _events_query_for_ticker("AAPL", 11, False, HASH)

    assert "config_hash = :config_hash" in query
    assert params["config_hash"] == HASH


def test_omitting_the_hash_preserves_the_cross_config_read():
    """`None` is not "no filter by accident" — it is the documented escape
    hatch for a caller that genuinely wants every generation."""
    query, params = _events_query_for_ticker("AAPL", 11, False, None)

    assert "config_hash" not in query
    assert "config_hash" not in params


def test_the_default_is_unscoped_so_the_scope_is_always_deliberate():
    """A default of `None` means every *caller* must decide. Defaulting to
    some hash instead would silently scope a caller that never asked.
    """
    query, _ = _events_query_for_ticker("AAPL", 11, False)
    assert "config_hash" not in query


def test_scoping_composes_with_the_incomplete_window_filter():
    """`path capture` needs both at once: one generation, incomplete
    windows only. An implementation that replaced rather than added a
    clause would pass the two single-filter tests above."""
    query, params = _events_query_for_ticker("AAPL", 11, True, HASH)

    assert "config_hash = :config_hash" in query
    assert "fwd_window_days IS NULL OR fwd_window_days < :window_days" in query
    assert params["config_hash"] == HASH
    assert params["window_days"] == 11


def test_the_entry_price_guard_survives_scoping():
    """An unfilled entry has no anchor price to build a return series from
    (module docstring, invariant 4). Losing this while adding the scope
    would feed NULLs into `path_for_event`."""
    query, _ = _events_query_for_ticker("AAPL", 11, False, HASH)
    assert "entry_price IS NOT NULL" in query


@pytest.mark.parametrize("incomplete_only", [False, True])
def test_ordering_is_stable_whatever_the_filters(incomplete_only):
    """`ORDER BY id` must stay last. Determinism across reruns depends on
    it, and it is the clause most easily lost when appending others."""
    query, _ = _events_query_for_ticker("AAPL", 11, incomplete_only, HASH)
    assert query.rstrip().endswith("ORDER BY id")


def test_two_hashes_produce_different_params_not_different_sql():
    """The hash is bound, never interpolated. A formatted-in value would
    work in tests and be an injection surface in production."""
    q1, p1 = _events_query_for_ticker("AAPL", 11, False, HASH)
    q2, p2 = _events_query_for_ticker("AAPL", 11, False, "1b97abf7e458d537")

    assert q1 == q2
    assert p1["config_hash"] != p2["config_hash"]
    assert HASH not in q1
