"""`scan_candidates` hands `detect` only the indicator fields it reads.

**Why this exists.** Profiled 2026-08-27: `ind_group.iloc[prior_pos]` was
called once per bar and its cost scales with the frame's **column count**,
not its row count. On AAPL, 5,252 bars:

    iloc on the full 28-column frame   1.89s
    iloc on a 4-column frame           1.02s   -> 1.85x

`detect` reads exactly four fields from `ind` — `bb_lower`, `bb_upper`
(via `_types_fired` and `level_field`), `bb_pctb` and `k_full` — so the
other 24 columns were materialised per bar and never read.

**Narrowing also tightens the guarantee it must not weaken.** `detect`'s
signature is the look-ahead contract (TESTS.md §3.1b): one indicator row,
never a frame, so bar `t`'s indicators are out of scope. Handing it four
columns instead of twenty-eight means strictly fewer values are reachable.

The risk is the reverse direction: if `detect` later reads a fifth field
and this list is not updated, it would silently receive NaN instead of the
value — a wrong answer, not a crash. So the list is asserted against the
source rather than maintained by hand, and `_REQUIRED_INDICATOR_FIELDS`
must remain a subset of it or the null-check would pass on a field that
was never carried.
"""

from __future__ import annotations

import inspect
import re

from capitalscan.core import signals as core_signals
from capitalscan.research.candidates import (
    _DETECT_INDICATOR_FIELDS,
    _REQUIRED_INDICATOR_FIELDS,
)


def _fields_read_from_ind() -> set[str]:
    """Every literal field name `core.signals` reads off the `ind` row."""
    src = inspect.getsource(core_signals)
    return set(re.findall(r'_get\(\s*ind\s*,\s*"([a-z_0-9]+)"\s*\)', src))


def test_every_field_detect_reads_is_carried():
    """The failure this guards is silent: a field read but not carried
    arrives as NaN, and the signal simply does not fire."""
    missing = _fields_read_from_ind() - set(_DETECT_INDICATOR_FIELDS)
    assert not missing, (
        f"core.signals reads {sorted(missing)} off the indicator row, but "
        f"scan_candidates does not carry it. Add it to "
        f"_DETECT_INDICATOR_FIELDS or detect() will silently receive NaN."
    )


def test_the_indirect_level_field_reads_are_covered():
    """`touch_level=_get(ind, level_field)` is a variable, not a literal,
    so the regex above cannot see it. Both values it can take are pinned
    here instead."""
    assert "bb_lower" in _DETECT_INDICATOR_FIELDS
    assert "bb_upper" in _DETECT_INDICATOR_FIELDS


def test_required_fields_are_a_subset_of_what_is_carried():
    """The null check runs against the narrowed row. A required field that
    is not carried would read NaN and reject every bar."""
    assert set(_REQUIRED_INDICATOR_FIELDS) <= set(_DETECT_INDICATOR_FIELDS)


def test_the_list_is_not_silently_empty():
    assert len(_DETECT_INDICATOR_FIELDS) >= 4
