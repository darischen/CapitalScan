"""`cscan predict --publish`: a manual refit can ship its artifact.

Found 2026-09-10 during the bull-reversal cutover. `artifact.publish` ran
only inside `weekly`, so a standalone refit across a `config_hash` change
left serving's `model_artifact` naming the previous generation, and the
Pi's `predict --serving` refused on a config mismatch for a refit that had
happened. The flag defaults off, so the scheduled path does not change.

No real database: the serving engine, `run_predict` and the config are all
patched.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import typer

from capitalscan.jobs import artifact, cli
from capitalscan.jobs import predict as predict_mod
from capitalscan.jobs import sync as sync_mod

CHASH = "f183b0f5209a4677"


def _write_artifact(path: Path, chash: str) -> Path:
    meta = {
        "config_hash": chash,
        "git_sha": "abc1234",
        "model_version": "v-test",
        "fitted_at": "2026-09-17T00:00:00+00:00",
        "n_train": 10,
        "n_calibrate": 5,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, meta=np.array(json.dumps(meta)))
    return path


class _Engine:
    def __init__(self) -> None:
        self.params: list[dict] = []

    def begin(self):  # noqa: ANN201
        engine = self

        class _Ctx:
            def __enter__(self):  # noqa: ANN204
                return SimpleNamespace(execute=lambda stmt, p: engine.params.append(p))

            def __exit__(self, *exc):  # noqa: ANN002, ANN204
                return False

        return _Ctx()


# --- artifact.publish refuses a file from another generation ---------------


def test_publish_writes_the_row_when_the_hash_matches(tmp_path: Path) -> None:
    path = _write_artifact(tmp_path / "predictor.npz", CHASH)
    engine = _Engine()
    size = artifact.publish(engine, path, expected_config_hash=CHASH)
    assert size > 0
    assert engine.params[0]["chash"] == CHASH


def test_publish_refuses_an_artifact_fitted_for_another_config(tmp_path: Path) -> None:
    """The local file is whatever the last fit wrote. If that fit was for an
    arm or an older generation, shipping it under this run's name would put
    the wrong model on the Pi."""
    path = _write_artifact(tmp_path / "predictor.npz", "0523841076f47293")
    engine = _Engine()
    with pytest.raises(artifact.StaleArtifact, match="0523841076f47293"):
        artifact.publish(engine, path, expected_config_hash=CHASH)
    assert engine.params == []


# --- the CLI flag -----------------------------------------------------------


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):  # noqa: ANN201
    calls: dict = {"run_predict": [], "publish": []}
    monkeypatch.setattr(cli, "_resolve_config_or_exit", lambda: object())
    monkeypatch.setattr("capitalscan.jobs.config.config_hash", lambda cfg: CHASH)

    def _run_predict(**kwargs):  # noqa: ANN003, ANN202
        calls["run_predict"].append(kwargs)
        return predict_mod.PredictReport(rows_written=3, artifact_path=str(tmp_path / "p.npz"))

    monkeypatch.setattr(predict_mod, "run_predict", _run_predict)
    monkeypatch.setattr(sync_mod, "serving_engine", lambda: "serving-engine")

    def _publish(engine, path=artifact.DEFAULT_PATH, expected_config_hash=None):  # noqa: ANN001, ANN202
        calls["publish"].append((engine, expected_config_hash))
        return 1234

    monkeypatch.setattr(artifact, "publish", _publish)
    return calls


def _predict(**overrides):  # noqa: ANN003, ANN202
    args = {
        "since": "2026-09-01",
        "lookback": 45,
        "clear": False,
        "from_artifact": False,
        "serving": False,
        "universe": "trade",
        "publish": False,
    }
    args.update(overrides)
    return cli.predict(**args)


def test_default_does_not_publish(wired) -> None:  # noqa: ANN001
    _predict()
    assert len(wired["run_predict"]) == 1
    assert wired["publish"] == []


def test_publish_ships_the_refit_to_serving_under_this_hash(wired) -> None:  # noqa: ANN001
    _predict(publish=True)
    assert wired["publish"] == [("serving-engine", CHASH)]


@pytest.mark.parametrize("flag", ["from_artifact", "serving"])
def test_publish_without_a_refit_is_refused_before_any_work(wired, flag: str) -> None:  # noqa: ANN001
    """Nothing new exists to ship when no fit runs, and re-publishing a
    loaded file hides which run made it."""
    with pytest.raises(typer.Exit) as exc:
        _predict(publish=True, **{flag: True})
    assert exc.value.exit_code == 2
    assert wired["run_predict"] == []
    assert wired["publish"] == []


def test_publish_skipped_when_the_fit_was_not_saved(wired, monkeypatch) -> None:  # noqa: ANN001
    """`run_predict` survives a failed save by design. Publishing then would
    ship whatever older file sits at the default path."""
    monkeypatch.setattr(
        predict_mod,
        "run_predict",
        lambda **kw: predict_mod.PredictReport(artifact_path="not written: disk full"),
    )
    with pytest.raises(typer.Exit) as exc:
        _predict(publish=True)
    assert exc.value.exit_code == 1
    assert wired["publish"] == []


def test_a_failed_publish_fails_the_command(wired, monkeypatch) -> None:  # noqa: ANN001
    """Asked for explicitly, so unlike `weekly` a failure is not swallowed:
    exit 0 here would leave the Pi refusing with nothing saying why."""

    def _boom(engine, path=artifact.DEFAULT_PATH, expected_config_hash=None):  # noqa: ANN001, ANN202
        raise artifact.StaleArtifact("fitted for another config")

    monkeypatch.setattr(artifact, "publish", _boom)
    with pytest.raises(typer.Exit) as exc:
        _predict(publish=True)
    assert exc.value.exit_code == 1
