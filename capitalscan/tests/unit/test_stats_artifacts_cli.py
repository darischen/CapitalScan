"""`cscan stats artifacts` — the entry point Session 14 never built.

**Why this file exists.** `research/curves.py` and `research/chart_arms.py`
shipped with no entry point of any kind. `curves.py`'s own docstring refers
to "14.6's CLI hook", which was never written, so the eight artifacts under
`reports/phase4/` were produced once by hand and could not be regenerated.

That is the same defect `cscan stats cells` was added to close, and the
reason it recurs is that an unreachable function still passes its unit
tests. `research/drawdown_slice.py` had a `__main__` and was the only third
of the set anyone could re-run.

So these tests target the *wiring*, not the rendering: that the command
exists, resolves a hash, refuses holdout, and calls the three exporters in
an order where the chart reads a CSV that already exists.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import typer

from capitalscan.jobs import cli


class _Curves:
    """Stand-in for `benchmarks.ArmCurves`; the exporters are stubbed, so
    only identity matters here."""


@pytest.fixture
def stub(monkeypatch, tmp_path):
    calls: list[tuple[str, str]] = []

    class _Report:
        curves = _Curves()

    def fake_run_benchmarks(engine, chash, split, **kw):
        calls.append(("benchmarks", split))
        assert kw["collect_curves"] is True, "curves are the whole point of this command"
        assert kw["write"] is False, "the benchmarks rows already exist; this is a render"
        return pd.DataFrame(), _Report()

    def fake_export_curves(curves, chash, split, output_dir):
        calls.append(("curves", split))
        return pd.DataFrame(), Path(output_dir) / f"equity_curves_{chash}_{split}.csv"

    def fake_export_chart(engine, chash, split, output_dir=None, **kw):
        calls.append(("chart", split))
        return Path(output_dir) / f"three_arms_{chash}_{split}.svg"

    def fake_export_dd(engine, chash, split, output_dir=None, **kw):
        calls.append(("drawdown", split))
        base = Path(output_dir)
        return base / f"dd_{split}.svg", base / f"dd_{split}.csv"

    monkeypatch.setattr("capitalscan.research.benchmarks.run_benchmarks", fake_run_benchmarks)
    monkeypatch.setattr("capitalscan.research.curves.export_curves", fake_export_curves)
    monkeypatch.setattr("capitalscan.research.chart_arms.export_three_arm_chart", fake_export_chart)
    monkeypatch.setattr("capitalscan.research.drawdown_slice.export_drawdown_slice", fake_export_dd)
    monkeypatch.setattr(
        cli,
        "_resolve_config_or_exit",
        lambda *a, **k: __import__("capitalscan.core.config", fromlist=["Config"]).Config(),
    )

    class _Job:
        run_id = "artifacts_test"
        rows_written = 0

    from contextlib import contextmanager

    @contextmanager
    def fake_run_job(engine, job, params):
        yield _Job()

    monkeypatch.setattr("capitalscan.jobs.ingest.run_job", fake_run_job)
    monkeypatch.setattr("capitalscan.jobs.db_io.get_engine", lambda *a, **k: "fake-engine")
    return calls


def _run(**kw):
    args = dict(config_hash=None, splits="train,validate", output_dir=Path("reports/phase4"))
    args.update(kw)
    return cli.stats_artifacts_cmd(**args)


def test_holdout_is_refused(stub):
    """CLAUDE.md reserves holdout for exactly one evaluation at the end of
    the project. A routine artifact refresh must not spend it."""
    with pytest.raises(typer.Exit) as exc:
        _run(splits="holdout")
    assert exc.value.exit_code == 2


def test_holdout_is_refused_even_alongside_valid_splits(stub):
    """The dangerous form: it looks like an ordinary refresh."""
    with pytest.raises(typer.Exit) as exc:
        _run(splits="train,holdout")
    assert exc.value.exit_code == 2


def test_nothing_runs_when_holdout_is_refused(stub):
    with pytest.raises(typer.Exit):
        _run(splits="train,holdout")
    assert stub == [], "the guard must fire before any split is rendered"


def test_all_three_exporters_run_for_each_split(stub, tmp_path):
    _run(splits="train,validate", output_dir=tmp_path)

    for split in ("train", "validate"):
        for step in ("benchmarks", "curves", "chart", "drawdown"):
            assert (step, split) in stub


def test_the_curve_csv_is_written_before_the_chart_that_reads_it(stub, tmp_path):
    """`export_three_arm_chart` reads the curve CSV off disk unless handed a
    frame. Rendering the chart first would read a stale file, or none — and
    the chart would silently disagree with the CSV beside it."""
    _run(splits="train", output_dir=tmp_path)

    order = [step for step, _ in stub]
    assert order.index("curves") < order.index("chart")


def test_splits_are_parsed_and_whitespace_tolerated(stub, tmp_path):
    _run(splits=" train , validate ", output_dir=tmp_path)
    rendered = {split for step, split in stub if step == "curves"}
    assert rendered == {"train", "validate"}
