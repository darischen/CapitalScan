"""ADR 176's ranking gate: when `p_touch` may be used to choose between names.

The gate exists because `p_touch` is calibrated in every regime measured
and only *ranks* in some. The tests below pin the three things that would
quietly break that distinction.
"""

from __future__ import annotations

import pytest

from capitalscan.core import breadth as br
from capitalscan.core.config import Config, ServingParams
from capitalscan.tests.unit._probe import code_of


class TestTheGateItself:
    def test_below_the_floor_is_open(self) -> None:
        assert br.ranking_gate_open(0.60, 0.68) is True
        assert br.ranking_gate_open(0.679, 0.68) is True

    def test_at_or_above_the_floor_is_closed(self) -> None:
        """At the floor, not just above it. The measured cell that fails is
        `breadth >= 0.68`, so the boundary belongs to the closed side."""
        assert br.ranking_gate_open(0.68, 0.68) is False
        assert br.ranking_gate_open(0.95, 0.68) is False

    def test_a_missing_reading_is_neither_open_nor_closed(self) -> None:
        """Defaulting open would let a failed breadth job silently restore
        ranking; defaulting closed would hide a working model whenever
        market data lagged. Neither is code's decision to make."""
        assert br.ranking_gate_open(None, 0.68) is None

    def test_the_floor_is_a_parameter_and_not_a_literal(self) -> None:
        """0.68 is where the drop sits on one split, not a law (invariant 9)."""
        assert br.ranking_gate_open(0.70, 0.75) is True
        assert br.ranking_gate_open(0.70, 0.65) is False
        assert "0.68" not in code_of(br.ranking_gate_open)


class TestTheFloorIsOutsideTheHashedConfig:
    """It was put in `StatsParams` first and that was wrong.

    `jobs.config.config_hash` hashes `asdict(config)`, so any field on a
    `Config` section moves the hash -- which would orphan every `events`,
    `predictions` and `cell_stats` row keyed on `0523841076f47293`. The
    gate changes what a surface does with a probability and changes no
    probability, label or backtest result, so it belongs with the other
    deployment constants.
    """

    def test_serving_params_carries_it(self) -> None:
        assert ServingParams().breadth_rank_floor == pytest.approx(0.68)

    def test_it_is_sweepable_like_every_other_threshold(self) -> None:
        assert ServingParams(breadth_rank_floor=0.60).breadth_rank_floor == pytest.approx(0.60)

    def test_it_is_not_reachable_from_the_hashed_config(self) -> None:
        """The regression guard. If this ever appears on a `Config`
        section, the hash moves and the database detaches from its own
        history."""
        import dataclasses

        for field in dataclasses.fields(Config):
            section = getattr(Config(), field.name)
            if dataclasses.is_dataclass(section):
                names = {f.name for f in dataclasses.fields(section)}
                assert "breadth_rank_floor" not in names, (
                    f"breadth_rank_floor is on Config.{field.name}; that moves config_hash"
                )


class TestTheCopySaysTheRightThing:
    def test_the_closed_message_does_not_tell_a_reader_to_ignore_the_number(self) -> None:
        """The probability stays calibrated when the gate is closed. Copy
        that said 'unreliable' full stop would discard something correct."""
        detail = br.gate_detail(False).lower()
        assert "calibrated" in detail
        assert "should not be used to choose" in detail

    def test_the_closed_message_warns_about_the_inverted_low_band(self) -> None:
        """Above the floor, predictions under 0.40 resolved at 0.458 while
        0.40-0.55 resolved at 0.417. A low value is not evidence against a
        name, and that is the specific mistake a reader would make."""
        assert "not evidence against" in br.gate_detail(False).lower()

    def test_the_open_message_does_not_oversell(self) -> None:
        """AUC 0.626 was found by searching a split examined many times."""
        detail = br.gate_detail(True).lower()
        assert "lead" in detail or "confirming" in detail

    def test_the_unknown_state_has_its_own_copy(self) -> None:
        detail = br.gate_detail(None).lower()
        assert "unknown" in detail
        assert "do not rank" in detail

    def test_every_state_has_a_label_and_a_detail(self) -> None:
        for state in (True, False, None):
            assert br.gate_label(state)
            assert len(br.gate_detail(state)) > 40


class TestCoreStaysPure:
    def test_no_io_and_no_clock(self) -> None:
        """Substring probes need care here: the module's own
        `ranking_gate_open(` contains "open(", so a naive banned-list
        matches the function this file exists to test. Import names and
        call sites are checked instead."""
        src = code_of(br)
        for banned in (
            "import sqlalchemy",
            "import requests",
            "import datetime",
            "from datetime",
            "datetime.now",
            "time.time",
            "= open(",
        ):
            assert banned not in src, f"invariant 1: {banned} in core/breadth.py"

    def test_it_imports_nothing_but_annotations(self) -> None:
        """The strongest form of invariant 1 for a module this small."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(br))
        imported = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {getattr(n, "module", None) for n in imported}
        assert names <= {"__future__"}, f"core/breadth.py imports {names}"
