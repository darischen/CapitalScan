"""Structural t-1 enforcement — TESTS.md §3.1b.

The shift ladder in `tests/property/test_lookahead.py` catches a detector
that ignores its indicators. It **cannot** catch a detector reading the
wrong bar: if `detect` wrongly read bar t's indicators, shifting forward one
bar would make it read t-1, producing the same magnitude of change and
roughly the same Jaccard. That limitation is inherent to the shift design.

The real guarantee is structural. `detect(bar, ind, sp)` receives **one**
indicator row as an argument and reads only `low`, `high`, `ts`, and
`ticker` from the bar. Bar t's indicators are not in scope, so no code path
can reach them.

That is fragile to a future refactor passing the full frame "for
convenience", or adding `bar["close"]` to a band comparison. Both are
asserted here rather than left to review. `TrackingSeries` is the
load-bearing part: it records every field access and the test fails the
moment a fifth field is touched.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from capitalscan.core import signals as sig
from capitalscan.core.config import SignalParams
from capitalscan.core.signals import detect

SP = SignalParams()

# Every indicator column `detect` could plausibly be tempted to read off the
# bar instead of the t-1 row. None of these may ever appear in `accessed`.
FORBIDDEN_ON_BAR = (
    "bb_lower",
    "bb_mid",
    "bb_upper",
    "bb_pctb",
    "k_full",
    "d_full",
    "k_fast",
    "atr_14",
)

# ADR 108 added exactly one field, and ADR 144 its mirror: precomputed
# booleans, not prices. The negative assertions below are what carry the
# guarantee — a test asserting only that these fields are permitted would
# pass on a probe with no restrictions at all.
#
# **Widening this set is the most consequential edit in the repository**, so
# the standard for joining it is stated rather than left to judgement. A
# field belongs here only if it is a boolean whose entire causality was
# resolved in `core/indicators.py` before `detect` saw the bar. That is what
# keeps the probe's real purpose intact: an *intraday* condition written
# against the close must remain impossible to express in `detect`, and a
# boolean naming its own derivation cannot be repurposed into one. A raw
# price could be, which is why `FORBIDDEN_PRICES_ON_BAR` below never
# shrinks — it is the half of this pair that actually does the work.
PERMITTED_ON_BAR = {
    "low",
    "high",
    "ts",
    "ticker",
    "bear_close_above_upper",
    "bull_close_below_lower",
}

# Raw prices `detect` must never read. `open` and `close` are the pair ADR
# 108's flag is derived from, and they stay forbidden precisely because the
# flag exists: the point of computing it in `core/indicators.py` is that an
# *intraday* condition written against the close stays impossible to
# express here.
FORBIDDEN_PRICES_ON_BAR = ("open", "close", "adj_close", "volume")


class TrackingSeries(pd.Series):
    """A `pd.Series` recording every field read from it.

    Subclasses `pd.Series` so `detect` cannot tell the difference: the probe
    has to survive `bar["low"]`, `bar.name`, and `"ts" in bar.index` exactly
    as a real bar would.

    `_metadata` propagates `accessed` across pandas' internal constructor
    calls, which otherwise return a plain Series and silently drop it.
    """

    _metadata = ["accessed"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "accessed", set())

    @property
    def _constructor(self):
        return TrackingSeries

    def __getitem__(self, key):
        if isinstance(key, str):
            self.accessed.add(key)
        return super().__getitem__(key)


def _bar_fields() -> dict:
    """A bar carrying every OHLC field plus every indicator column.

    The indicator columns are present precisely so that reading one would
    succeed rather than raise — the probe must catch a silent read, not rely
    on a KeyError to surface it.
    """
    fields = {
        "open": 100.0,
        "high": 101.0,
        "low": 94.0,
        "close": 100.0,
        "volume": 1_000_000,
        "ticker": "TSM",
    }
    # Plausible values, so a wrong read produces a plausible wrong answer.
    fields.update(
        {
            "bb_lower": 95.0,
            "bb_mid": 100.0,
            "bb_upper": 105.0,
            "bb_pctb": 0.5,
            "k_full": 15.0,
            "d_full": 15.0,
            "k_fast": 15.0,
            "atr_14": 2.0,
        }
    )
    return fields


def _probe() -> TrackingSeries:
    return TrackingSeries(_bar_fields(), name=pd.Timestamp("2026-07-29"))


def _ind_row(bb_lower=95.0, k_full=15.0) -> pd.Series:
    return pd.Series(
        {
            "bb_lower": bb_lower,
            "bb_mid": 100.0,
            "bb_upper": 105.0,
            "bb_pctb": 0.5,
            "k_full": k_full,
            "d_full": 50.0,
            "k_fast": k_full,
            "atr_14": 2.0,
        }
    )


def _ind_frame() -> pd.DataFrame:
    return pd.DataFrame([_ind_row(), _ind_row()], index=pd.date_range("2026-07-28", periods=2))


# ---------------------------------------------------------------------------
# The signature itself
# ---------------------------------------------------------------------------


def test_detect_signature_is_exactly_bar_ind_sp():
    """DESIGN §3.6 pins three parameters. A fourth is where a frame, a
    lookup table, or a full history would enter."""
    assert list(inspect.signature(sig.detect).parameters) == ["bar", "ind", "sp"]


def test_breach_live_signature_is_exactly_price_bands_sp():
    assert list(inspect.signature(sig.breach_live).parameters) == ["price", "bands", "sp"]


# ---------------------------------------------------------------------------
# `ind` must be a row, never a frame
# ---------------------------------------------------------------------------


def test_detect_rejects_a_frame_in_the_ind_position():
    """A frame would put every bar's indicators in scope at once."""
    with pytest.raises((TypeError, ValueError, AttributeError)):
        detect(_probe(), _ind_frame(), SP)


def test_the_frame_rejection_names_the_reason():
    with pytest.raises(TypeError, match="single indicator row"):
        detect(_probe(), _ind_frame(), SP)


def test_a_single_row_frame_is_still_rejected():
    """The dangerous refactor passes `ind.loc[[ts]]`, which is one row wide
    and would otherwise slip through a length check."""
    with pytest.raises(TypeError):
        detect(_probe(), _ind_frame().iloc[[0]], SP)


# ---------------------------------------------------------------------------
# The probe — only four fields may be read from the bar
# ---------------------------------------------------------------------------


def test_detect_reads_only_permitted_fields_from_the_bar():
    probe = _probe()
    detect(probe, _ind_row(), SP)
    assert probe.accessed <= PERMITTED_ON_BAR


def test_detect_reads_no_indicator_field_from_the_bar():
    """The specific failure this guards: a band comparison against the bar's
    own indicator column instead of the t-1 row."""
    probe = _probe()
    detect(probe, _ind_row(), SP)
    assert probe.accessed.isdisjoint(FORBIDDEN_ON_BAR)


def test_detect_never_reads_close_from_the_bar():
    """%K derives from the close. Reading `close` for a band decision is the
    look-ahead trap in DESIGN §3.6, stated exactly."""
    probe = _probe()
    detect(probe, _ind_row(), SP)
    assert "close" not in probe.accessed


def test_the_probe_still_produces_the_right_signal():
    """A probe that silently broke detection would pass every access
    assertion while proving nothing."""
    hits = detect(_probe(), _ind_row(bb_lower=95.0, k_full=15.0), SP)
    assert len(hits) == 1
    assert hits[0].ticker == "TSM"
    assert hits[0].signal_type.value == "confluence_low"


def test_access_is_tracked_on_the_no_signal_path_too():
    """A quiet bar must not read more than a firing one."""
    probe = _probe()
    detect(probe, _ind_row(bb_lower=1.0, k_full=50.0), SP)
    assert probe.accessed <= PERMITTED_ON_BAR


def test_the_probe_records_something():
    """Guard on the guard. An `accessed` set that stayed empty — because
    pandas bypassed `__getitem__` — would make every bound above vacuous."""
    probe = _probe()
    detect(probe, _ind_row(), SP)
    assert {"low", "high"} <= probe.accessed


def test_the_probe_catches_a_detector_reading_the_bars_indicators():
    """Guard on the guard, the other direction: a deliberate violation must
    be caught. Without this the assertions could be inert."""
    probe = _probe()

    def leaky_detect(bar, ind, sp):
        # The realistic bug: reading the band off the bar's own row.
        return bar["bb_lower"] if bar["low"] <= bar["bb_lower"] else None

    leaky_detect(probe, _ind_row(), SP)
    assert not probe.accessed <= PERMITTED_ON_BAR
    assert not probe.accessed.isdisjoint(FORBIDDEN_ON_BAR)


# ---------------------------------------------------------------------------
# `core/` purity — no IO, no clock (DESIGN §3.1)
# ---------------------------------------------------------------------------


def test_detect_does_not_read_the_clock():
    """`core/` takes data in and returns data out. A clock read would make
    the backtest non-deterministic (ADR 060) and is banned outright."""
    source = inspect.getsource(sig)
    for banned in ("datetime.now", "date.today", "time.time", "pd.Timestamp.now"):
        assert banned not in source


def test_signals_module_imports_no_io():
    source = inspect.getsource(sig)
    for banned in ("import requests", "import psycopg", "import sqlalchemy", "open("):
        assert banned not in source


def test_detect_does_not_mutate_the_bar_it_is_given():
    """DESIGN §3.2: never mutate in place."""
    bar = pd.Series(_bar_fields(), name=pd.Timestamp("2026-07-29"))
    before = bar.copy()
    detect(bar, _ind_row(), SP)
    pd.testing.assert_series_equal(bar, before)


def test_detect_does_not_mutate_the_indicator_row_it_is_given():
    ind = _ind_row()
    before = ind.copy()
    detect(pd.Series(_bar_fields(), name=pd.Timestamp("2026-07-29")), ind, SP)
    pd.testing.assert_series_equal(ind, before)


def test_detect_is_deterministic_across_repeated_calls():
    bar = pd.Series(_bar_fields(), name=pd.Timestamp("2026-07-29"))
    ind = _ind_row()
    assert detect(bar, ind, SP) == detect(bar, ind, SP)


def test_nan_indicator_row_still_reads_only_permitted_bar_fields():
    """The null path must not quietly fall back to the bar for a value."""
    probe = _probe()
    detect(probe, _ind_row(bb_lower=np.nan, k_full=np.nan), SP)
    assert probe.accessed <= PERMITTED_ON_BAR


# ---------------------------------------------------------------------------
# core/ purity, module-wide (ADR 091, invariant 1)
# ---------------------------------------------------------------------------

CORE_DIR = Path(__file__).resolve().parents[2] / "core"

# Third-party compute libraries are fine; these are the IO doors.
_IO_MODULES = {
    "os",
    "io",
    "pathlib",
    "json",
    "tomllib",
    "tomli",
    "dotenv",
    "requests",
    "httpx",
    "urllib",
    "sqlalchemy",
    "psycopg",
    "socket",
    "subprocess",
    "time",
    "random",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize(
    "module", sorted(p.name for p in CORE_DIR.glob("*.py") if p.name != "__init__.py")
)
def test_no_core_module_imports_an_io_library(module):
    offenders = _imports(CORE_DIR / module) & _IO_MODULES
    assert not offenders, f"core/{module} imports {sorted(offenders)}; jobs/ owns all IO"


def test_core_config_imports_only_dataclasses():
    """ADR 091 pins this exactly: config.py is the module every other module
    imports, so one environment read there breaks purity library-wide."""
    assert _imports(CORE_DIR / "config.py") == {"dataclasses"}


def test_core_config_defines_no_resolver():
    # Scan code, not prose: the module docstring names these terms while
    # explaining why they are banned there.
    tree = ast.parse((CORE_DIR / "config.py").read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for banned in ("open", "getenv", "load_dotenv", "exists", "read_text"):
        assert banned not in called, f"core/config.py calls {banned}(); belongs in jobs/"

    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "resolve_config" not in defined, "resolution belongs in jobs/config.py (ADR 091)"


def test_the_import_scan_would_catch_a_violation(tmp_path):
    """Guard on the guard: a synthetic offending module must be detected."""
    bad = tmp_path / "bad.py"
    bad.write_text("import os\nfrom pathlib import Path\n")
    assert _imports(bad) & _IO_MODULES == {"os", "pathlib"}


# --------------------------------------------------------------------------
# ADR 108: the one permitted addition, and the wall that stayed up
# --------------------------------------------------------------------------


def test_detect_never_reads_a_raw_price_beyond_low_and_high():
    """The load-bearing negative. ADR 108 lets `detect` learn *that* the
    close confirmed a pattern; it must never get the close itself, or an
    intraday condition written against it becomes expressible again."""
    probe = _probe()
    probe["bear_close_above_upper"] = True
    detect(probe, _ind_row(), SP)
    assert probe.accessed.isdisjoint(FORBIDDEN_PRICES_ON_BAR)


def test_the_permitted_set_did_not_quietly_widen():
    """Pins the exact membership, in a second place on purpose.

    A future edit adding `close` "for convenience" changes the set at its
    definition, and this fails -- before the look-ahead ships rather than
    after. The duplication is the mechanism: widening requires editing two
    files that both say why, so it cannot happen as a side effect.

    **Widened once, 2026-08-21, for ADR 144's `bull_close_below_lower`.**
    The first widening was ADR 108's `bear_close_above_upper`. Both are
    booleans resolved in `core/indicators.py` before `detect` sees the bar,
    and both are structurally forced rather than convenient: the flag
    describes bar *t*'s own close against bar *t*'s own band, while `ind`
    carries t-1, so there is no route for it except the bar. A raw price has
    no such justification and never will -- `FORBIDDEN_PRICES_ON_BAR` is the
    half of this pair that does the real work, and it has never changed.
    """
    assert PERMITTED_ON_BAR == {
        "low",
        "high",
        "ts",
        "ticker",
        "bear_close_above_upper",
        "bull_close_below_lower",
    }
    # The guarantee that matters, restated here so this test cannot pass by
    # someone simply appending to the set above.
    assert PERMITTED_ON_BAR.isdisjoint(FORBIDDEN_PRICES_ON_BAR)
    assert all(
        f.endswith(("_upper", "_lower")) or f in {"low", "high", "ts", "ticker"}
        for f in PERMITTED_ON_BAR
    )


def test_the_flag_firing_does_not_widen_what_is_read():
    """The close-confirmed path must not reach for anything extra on its
    way to producing a hit."""
    probe = _probe()
    probe["bear_close_above_upper"] = True
    detect(probe, _ind_row(bb_lower=1.0, k_full=50.0), SP)
    assert probe.accessed <= PERMITTED_ON_BAR


def test_the_flag_actually_fires_through_the_probe():
    """A probe that silently swallowed the field would pass every access
    assertion above while proving nothing about the new type."""
    probe = _probe()
    probe["bear_close_above_upper"] = True
    hits = detect(probe, _ind_row(bb_lower=1.0, k_full=50.0), SP)
    assert [h.signal_type.value for h in hits] == ["bear_close_above_upper"]


def test_a_bar_without_the_field_still_detects_normally():
    """Every bar predating the indicator column lacks it. Detection on the
    existing corpus must be unaffected, not merely non-crashing."""
    probe = _probe()
    assert "bear_close_above_upper" not in probe.index
    hits = detect(probe, _ind_row(bb_lower=95.0, k_full=15.0), SP)
    assert [h.signal_type.value for h in hits] == ["confluence_low"]
