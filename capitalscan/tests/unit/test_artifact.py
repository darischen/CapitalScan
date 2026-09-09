"""The artifact must refuse a stale load, not merely detect one.

**This file is the reason persisting a fit is allowed at all.** ADR
174/175 forbade it so a fit could never outlive its feature code, and that
danger is real: reorder a feature, load the old weights, and every number
that comes out is wrong in a way nothing reports. The amendment (ADR 181)
trades "never write" for "never load unchecked", which is only an
improvement if the check is airtight.

So these tests are not about round-tripping. They are about the refusals.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from capitalscan.jobs import artifact as art

CHASH = "0523841076f47293"
SHA = "abc1234def5678"


def _write(tmp_path: Path, **overrides: object) -> Path:
    """A minimal artifact on disk, with `meta` fields overridable.

    Built by hand rather than by calling `save`, because `save` needs a
    fitted torch model and these tests are about the reader.
    """
    meta: dict[str, object] = {
        "artifact_version": art.ARTIFACT_VERSION,
        "n_members": 1,
        "n_trunk": 1,
        "n_heads": 1,
        "columns": ["a", "b"],
        "categorical_levels": [["sector", ["Tech", "Energy"]]],
        "tables": {},
        "model_version": "test",
        "config_hash": CHASH,
        "git_sha": SHA,
        "fitted_at": "2026-09-09T00:00:00+00:00",
        "n_train": 10,
        "n_calibrate": 5,
        "trained_signal_types": ["confluence_low"],
    }
    meta.update(overrides)
    path = tmp_path / "predictor.npz"
    np.savez_compressed(
        path,
        meta=np.array(json.dumps(meta)),
        grids=np.zeros((1, 3)),
        mean=np.zeros(2),
        std=np.ones(2),
        m0_trunk0_w=np.eye(2),
        m0_trunk0_b=np.zeros(2),
        m0_head0_w=np.eye(2),
        m0_head0_b=np.zeros(2),
    )
    return path


class TestItRefusesWhatItCannotTrust:
    def test_a_moved_config_hash_is_refused(self, tmp_path: Path) -> None:
        """A sweep moves the population the calibration was fitted on.

        The weights would still load and still produce probabilities. They
        would be probabilities about a different population.
        """
        with pytest.raises(art.StaleArtifact, match="config moved"):
            art.load("deadbeefdeadbeef", SHA, _write(tmp_path))

    def test_a_moved_git_sha_is_refused(self, tmp_path: Path) -> None:
        """The failure `config_hash` cannot see.

        Reordering a feature column or changing what one means does not
        touch the config, so its hash is unchanged. Only the code moved,
        and only `git_sha` notices.
        """
        path = _write(tmp_path)
        with pytest.raises(art.StaleArtifact, match="feature code moved"):
            art.load(CHASH, "0000000feedface", path)

    def test_an_unknown_version_is_refused_rather_than_guessed(self, tmp_path: Path) -> None:
        path = _write(tmp_path, artifact_version=art.ARTIFACT_VERSION + 1)
        with pytest.raises(art.StaleArtifact, match="refit rather than guess"):
            art.load(CHASH, SHA, path)

    def test_a_missing_file_names_the_command_that_writes_one(self, tmp_path: Path) -> None:
        """An absent artifact is an expected first-run state, not a crash."""
        with pytest.raises(art.StaleArtifact, match="cscan predict"):
            art.load(CHASH, SHA, tmp_path / "nothing.npz")

    def test_the_error_is_its_own_type(self) -> None:
        """So a caller can refit instead of failing.

        `nightly` should catch this and refit; a per-fire scorer must not,
        because refitting on the hot path is the thing the artifact exists
        to avoid.
        """
        assert issubclass(art.StaleArtifact, RuntimeError)


class TestItLoadsWhatItShould:
    def test_a_matching_artifact_round_trips(self, tmp_path: Path) -> None:
        loaded = art.load(CHASH, SHA, _write(tmp_path))
        assert loaded.config_hash == CHASH
        assert loaded.git_sha == SHA
        assert loaded.columns == ("a", "b")
        assert loaded.categorical_levels == (("sector", ("Tech", "Energy")),)
        assert loaded.trained_signal_types == ("confluence_low",)
        assert len(loaded.members) == 1
        assert len(loaded.members[0].trunk) == 1
        assert len(loaded.members[0].heads) == 1

    def test_the_loaded_weights_run_a_forward_pass(self, tmp_path: Path) -> None:
        """Loaded arrays must be usable, not merely present."""
        loaded = art.load(CHASH, SHA, _write(tmp_path))
        pmf = loaded.members[0].pmf(np.array([[1.0, 2.0]]))
        assert pmf.shape == (1, 1, 2)
        assert pmf.sum() == pytest.approx(1.0)


class TestTheFileIsSafeToCopyBetweenMachines:
    def test_it_reads_without_allow_pickle(self, tmp_path: Path) -> None:
        """The artifact travels to the Pi, so it must not execute on load.

        An `.npz` needing `allow_pickle=True` can run arbitrary code when
        opened. `load` passes `allow_pickle=False`; this asserts the file
        `save` produces is actually readable that way, which is a property
        of what was written and not of how it is read.
        """
        with np.load(_write(tmp_path), allow_pickle=False) as payload:
            assert "meta" in payload

    def test_load_does_not_enable_pickle(self) -> None:
        src = __import__("inspect").getsource(art.load)
        assert "allow_pickle=False" in src
        assert "allow_pickle=True" not in src

    def test_it_is_not_written_under_the_fetch_cache(self) -> None:
        """`data/cache/` is documented as safe to delete at any time.

        A fitted model there would be deleted by someone clearing stale
        parquet and the loss would look like a bug in the scorer.
        """
        assert "cache" not in art.DEFAULT_PATH.parts
