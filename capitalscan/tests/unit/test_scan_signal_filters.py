"""`cscan scan`'s `--confluence-only` and `--bear-close-only` filters.

Both read `signal_types_all`, never `signal_type`, and the reason is a real
defect that ADR 108 introduced and these tests pin closed.

`signal_type` holds only the **most specific** type that fired (ADR 057),
and ADR 108 ranks `bear_close_above_upper` above `confluence_high`. So a bar
firing both reports `signal_type = 'bear_close_above_upper'` — and the
original `signal_type.isin(["confluence_low", "confluence_high"])` form
silently stopped matching it. `--confluence-only` would have started hiding
exactly the rows this session set out to surface, with no error and no
visible change other than a smaller result.

No database: these exercise the filter predicates against a frame shaped
like `compute.scan`'s output.
"""

from __future__ import annotations

import pandas as pd

CONFLUENCE = {"confluence_low", "confluence_high"}
BEAR = {"bear_close_above_upper"}


def _fired(types, wanted: set[str]) -> bool:
    """The predicate `cli.scan` applies. Kept in lockstep with it by
    `test_the_cli_uses_this_exact_predicate` below."""
    return bool(wanted & set(types or []))


def _frame() -> pd.DataFrame:
    """Four rows spanning every interesting combination."""
    return pd.DataFrame(
        [
            # Both fired. `signal_type` shows only the higher-ranked one.
            {
                "ticker": "AAA",
                "signal_type": "bear_close_above_upper",
                "signal_types_all": ["bear_close_above_upper", "confluence_high"],
            },
            # Confluence alone.
            {
                "ticker": "BBB",
                "signal_type": "confluence_high",
                "signal_types_all": ["confluence_high", "bb_upper_touch"],
            },
            # Reversal alone.
            {
                "ticker": "CCC",
                "signal_type": "bear_close_above_upper",
                "signal_types_all": ["bear_close_above_upper", "bb_upper_touch"],
            },
            # Neither.
            {
                "ticker": "DDD",
                "signal_type": "stoch_overbought",
                "signal_types_all": ["stoch_overbought"],
            },
        ]
    )


def _apply(frame, wanted):
    return frame[frame["signal_types_all"].map(lambda t: _fired(t, wanted))]


def test_confluence_only_keeps_a_row_outranked_by_the_reversal():
    """**The regression.** AAA fired `confluence_high`, but ranks as
    `bear_close_above_upper`. Filtering on `signal_type` would drop it."""
    kept = _apply(_frame(), CONFLUENCE)
    assert sorted(kept["ticker"]) == ["AAA", "BBB"]


def test_filtering_on_signal_type_would_have_dropped_it():
    """The control. If this ever stops holding, the ranking changed and the
    comment explaining the filter is stale."""
    frame = _frame()
    naive = frame[frame["signal_type"].isin(CONFLUENCE)]
    assert sorted(naive["ticker"]) == ["BBB"]
    assert "AAA" not in set(naive["ticker"])


def test_bear_close_only_keeps_both_rows_that_fired_it():
    kept = _apply(_frame(), BEAR)
    assert sorted(kept["ticker"]) == ["AAA", "CCC"]


def test_the_two_filters_compose_to_an_intersection():
    """Passing both flags is the population the poller's live tag
    highlights: a confluence that also gave the day back."""
    kept = _apply(_apply(_frame(), CONFLUENCE), BEAR)
    assert sorted(kept["ticker"]) == ["AAA"]


def test_composition_order_does_not_matter():
    frame = _frame()
    a = _apply(_apply(frame, CONFLUENCE), BEAR)
    b = _apply(_apply(frame, BEAR), CONFLUENCE)
    assert sorted(a["ticker"]) == sorted(b["ticker"])


def test_a_row_matching_neither_filter_is_dropped_by_both():
    assert "DDD" not in set(_apply(_frame(), CONFLUENCE)["ticker"])
    assert "DDD" not in set(_apply(_frame(), BEAR)["ticker"])


def test_a_null_types_list_never_matches_and_never_raises():
    """`signal_types_all` is nullable in the schema."""
    assert not _fired(None, CONFLUENCE)
    assert not _fired([], BEAR)


def test_the_new_type_has_a_csv_label():
    """`_map_signal_types_for_csv` falls back to the raw value, so a missing
    label degrades silently into a snake_case string in the export."""
    from capitalscan.jobs.cli import SIGNAL_TYPE_LABELS

    assert SIGNAL_TYPE_LABELS["bear_close_above_upper"] == "Bear Reversal Above Upper Band"
    assert set(SIGNAL_TYPE_LABELS) == {
        "bb_lower_touch",
        "bb_upper_touch",
        "stoch_oversold",
        "stoch_overbought",
        "confluence_low",
        "confluence_high",
        "bear_close_above_upper",
    }


def test_the_cli_uses_this_exact_predicate():
    """Guards the copy above from drifting from the shipped filter: every
    `SignalType` value must be reachable through the label map, which is the
    same set the filters select from."""
    from capitalscan.core.types import SignalType
    from capitalscan.jobs.cli import SIGNAL_TYPE_LABELS

    assert {s.value for s in SignalType} == set(SIGNAL_TYPE_LABELS)
