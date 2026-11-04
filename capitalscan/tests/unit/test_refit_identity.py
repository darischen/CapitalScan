"""Every weekly refit must reach the Pi, under a name of its own.

**Found 2026-11-04.** `artifact.fetch` skipped the download when the Pi's
local file carried the published row's `git_sha`, and `model_version` was
built from `config_hash` and `git_sha` alone. A refit at an unchanged
commit therefore published new weights under the old name, and the Pi kept
scoring with the old ones. It happened on two of the five October refits
(2026-10-10 and 2026-10-24): 1,286 live predictions came from the previous
week's model and carry the current one's name. ADR 029 and ADR 193 both
say a refit is a new `model_version`; ADR 185 says the fetch is a no-op
only "when the local copy already matches". Bytes are what match.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from capitalscan.jobs import artifact as art
from capitalscan.research import predict as rp

CHASH = "f183b0f5209a4677"
SHA = "a9fbb0d5e0e82c5952698396b09a6a8e1a891b33"
FIT = datetime(2026, 10, 31, 20, 51, 7, tzinfo=UTC)


def _npz_bytes(tmp_path: Path, name: str, fitted_at: str, weight: float) -> bytes:
    """A small artifact's bytes. Same commit every time, by design."""
    path = tmp_path / name
    meta = {"git_sha": SHA, "config_hash": CHASH, "fitted_at": fitted_at}
    np.savez_compressed(path, meta=np.array(json.dumps(meta)), w=np.full((2, 2), weight))
    return path.read_bytes()


class _Engine:
    """The one query `fetch` makes, answered with a fixed row."""

    def __init__(self, payload: bytes | None) -> None:
        self.row = None if payload is None else SimpleNamespace(git_sha=SHA, payload=payload)

    @contextmanager
    def connect(self) -> Iterator[Any]:
        row = self.row
        yield SimpleNamespace(execute=lambda *a, **k: SimpleNamespace(fetchone=lambda: row))


class TestFetch:
    def test_a_refit_at_the_same_commit_replaces_the_local_copy(self, tmp_path: Path) -> None:
        """The 2026-10-10 case: one commit, two fits, two sets of weights."""
        old = _npz_bytes(tmp_path, "old.npz", "2026-10-03T20:40:00+00:00", 0.1)
        new = _npz_bytes(tmp_path, "new.npz", "2026-10-10T20:59:00+00:00", 0.2)
        local = tmp_path / "predictor.npz"
        local.write_bytes(old)

        assert art.fetch(_Engine(new), CHASH, local) == local
        assert local.read_bytes() == new

    def test_identical_bytes_are_not_rewritten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The poller asks every 20 seconds; a match must cost no write."""
        same = _npz_bytes(tmp_path, "same.npz", "2026-10-31T20:51:07+00:00", 0.3)
        local = tmp_path / "predictor.npz"
        local.write_bytes(same)

        def _no_write(self: Path, data: bytes) -> int:
            raise AssertionError(f"rewrote {self} although its bytes matched")

        monkeypatch.setattr(Path, "write_bytes", _no_write)
        assert art.fetch(_Engine(same), CHASH, local) == local

    def test_a_missing_local_copy_is_written(self, tmp_path: Path) -> None:
        new = _npz_bytes(tmp_path, "new.npz", "2026-10-31T20:51:07+00:00", 0.4)
        local = tmp_path / "model" / "predictor.npz"

        assert art.fetch(_Engine(new), CHASH, local) == local
        assert local.read_bytes() == new

    def test_a_truncated_local_copy_is_replaced(self, tmp_path: Path) -> None:
        new = _npz_bytes(tmp_path, "new.npz", "2026-10-31T20:51:07+00:00", 0.5)
        local = tmp_path / "predictor.npz"
        local.write_bytes(new[:100])

        assert art.fetch(_Engine(new), CHASH, local) == local
        assert local.read_bytes() == new

    def test_no_published_row_is_none(self, tmp_path: Path) -> None:
        assert art.fetch(_Engine(None), CHASH, tmp_path / "predictor.npz") is None


class TestModelVersion:
    def test_two_refits_at_one_commit_get_two_versions(self) -> None:
        a_week_later = FIT + timedelta(days=7)
        assert rp.model_version(CHASH, SHA, FIT) != rp.model_version(CHASH, SHA, a_week_later)

    def test_it_names_the_generation_the_commit_and_the_fit(self) -> None:
        assert rp.model_version(CHASH, SHA, FIT) == "adr175-f183b0f5-a9fbb0d-20261031T2051Z"

    def test_the_fit_time_is_written_in_utc(self) -> None:
        pacific = FIT.astimezone(timezone(timedelta(hours=-7)))
        assert rp.model_version(CHASH, SHA, pacific) == rp.model_version(CHASH, SHA, FIT)


def test_save_records_the_fits_own_instant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`fitted_at` and the version name one instant, not two clock reads."""
    from capitalscan.research import neural

    layer = SimpleNamespace(weight=np.eye(2), bias=np.zeros(2))
    monkeypatch.setattr(
        neural, "export_weights", lambda m: SimpleNamespace(trunk=[layer], heads=[layer])
    )
    design = SimpleNamespace(
        columns=["a", "b"],
        categorical_levels=[("sector", ("Tech",))],
        mean=pd.Series([0.0, 0.0]),
        std=pd.Series([1.0, 1.0]),
    )
    predictor = SimpleNamespace(
        ensemble=SimpleNamespace(members=[SimpleNamespace(design=design)], grids=np.zeros((1, 3))),
        tables={},
        model_version=rp.model_version(CHASH, SHA, FIT),
        fitted_at=FIT.isoformat(),
        n_train=1,
        n_calibrate=1,
        trained_signal_types=("bb_lower_touch",),
    )

    path = art.save(predictor, CHASH, SHA, tmp_path / "predictor.npz")
    with np.load(path, allow_pickle=False) as payload:
        meta = json.loads(str(payload["meta"]))
    assert meta["fitted_at"] == FIT.isoformat()
    assert meta["model_version"].endswith("-20261031T2051Z")
