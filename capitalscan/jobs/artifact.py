"""Persist a fitted predictor, and refuse to load a stale one.

**Why this exists.** A refit is 24 model fits and about eleven minutes; the
forward pass is milliseconds. Scoring a signal the moment it fires means
separating the two, which means the fit has to survive the process that
made it.

**What ADR 174/175 were protecting, and how this keeps it.** The rule was
"refit, never load a pickle", so a fit could never outlive the feature code
that built it. The failure it guards against is real: features are
reordered or redefined, the artifact still loads, and every number it
produces is quietly wrong against the new design.

That failure is not caused by persisting. It is caused by *loading without
checking*. So this module persists, and refuses to load whenever
`config_hash` or `git_sha` has moved -- the same guarantee, enforced at
load rather than by never writing. A refusal is loud and recoverable; a
silent mismatch is neither.

**`.npz` and JSON, not pickle.** A pickle executes on load and encodes
Python classes, so it breaks on a refactor that renames a dataclass and
runs arbitrary code from disk. Arrays and JSON have neither property, and
the Pi can read them with numpy alone -- which is the whole point, since
the alternative is a 2GB torch wheel on an ARM board.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from capitalscan.core import calibration as calib
from capitalscan.core import inference as cinf

#: Bumped whenever the on-disk layout changes in a way an older reader
#: would misread. A reader that finds a version it does not know refuses
#: rather than guessing, for the same reason the hash guard exists.
ARTIFACT_VERSION = 1

#: Where nightly writes it. Deliberately not under `data/cache/`: that
#: directory is keyed on fetch arguments and safe to delete at any time,
#: and this is neither.
DEFAULT_PATH = Path("data/model/predictor.npz")


class StaleArtifact(RuntimeError):
    """The artifact does not match the code or config asking for it.

    Its own type, not a bare `RuntimeError`, so a caller can choose to
    refit instead of failing -- which is what `nightly` should do and what
    a per-fire scorer must not.
    """


@dataclass(frozen=True)
class Artifact:
    """A fitted predictor, loaded and verified."""

    members: tuple[cinf.NetworkWeights, ...]
    grids: np.ndarray
    #: The `DesignMatrix`, rebuilt from arrays rather than unpickled.
    columns: tuple[str, ...]
    mean: np.ndarray
    std: np.ndarray
    categorical_levels: tuple[tuple[str, tuple[str, ...]], ...]
    tables: dict[str, calib.ReliabilityTable]
    model_version: str
    config_hash: str
    git_sha: str
    fitted_at: str
    n_train: int
    n_calibrate: int
    trained_signal_types: tuple[str, ...]


def save(predictor: Any, config_hash: str, git_sha: str, path: Path = DEFAULT_PATH) -> Path:
    """Write a `research.predict.FittedPredictor` to `path`.

    Takes `Any` rather than the real type on purpose: importing
    `research.predict` here would pull torch into every process that reads
    an artifact, which is exactly the dependency this module exists to
    avoid on the Pi.
    """
    from capitalscan.research import neural

    path.parent.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, np.ndarray] = {}

    exported = [neural.export_weights(m) for m in predictor.ensemble.members]
    for i, weights in enumerate(exported):
        for j, layer in enumerate(weights.trunk):
            arrays[f"m{i}_trunk{j}_w"] = layer.weight
            arrays[f"m{i}_trunk{j}_b"] = layer.bias
        for j, layer in enumerate(weights.heads):
            arrays[f"m{i}_head{j}_w"] = layer.weight
            arrays[f"m{i}_head{j}_b"] = layer.bias

    design = predictor.ensemble.members[0].design
    arrays["grids"] = np.asarray(predictor.ensemble.grids, dtype=np.float64)
    arrays["mean"] = design.mean.to_numpy(dtype=np.float64)
    arrays["std"] = design.std.to_numpy(dtype=np.float64)

    meta = {
        "artifact_version": ARTIFACT_VERSION,
        "n_members": len(exported),
        "n_trunk": len(exported[0].trunk),
        "n_heads": len(exported[0].heads),
        "columns": list(design.columns),
        "categorical_levels": [
            [col, [str(v) for v in levels]] for col, levels in design.categorical_levels
        ],
        "tables": {k: v.to_dict() for k, v in predictor.tables.items()},
        "model_version": predictor.model_version,
        # **The guard.** Both must match at load. `config_hash` catches a
        # threshold sweep that moves the population; `git_sha` catches a
        # feature reordered or redefined in code, which no hash of the
        # config would notice.
        "config_hash": config_hash,
        "git_sha": git_sha,
        "fitted_at": datetime.now(UTC).isoformat(),
        "n_train": predictor.n_train,
        "n_calibrate": predictor.n_calibrate,
        "trained_signal_types": list(predictor.trained_signal_types),
    }
    # `meta` goes in as an array like everything else, so the file stays
    # readable with `allow_pickle=False` -- which is not a formality. It is
    # what makes the artifact safe to copy to the Pi and read there: an
    # `.npz` that needs `allow_pickle=True` can execute code on load, and
    # this file travels between machines.
    #
    # `type: ignore` because numpy's stub types `savez_compressed`'s third
    # positional as `bool`, so `**arrays` reads as that argument. The call
    # is correct; the stub is too narrow for the kwargs form.
    np.savez_compressed(path, meta=np.array(json.dumps(meta)), **arrays)  # type: ignore[arg-type]
    return path


def load(config_hash: str, git_sha: str, path: Path = DEFAULT_PATH) -> Artifact:
    """Read and verify. Raises `StaleArtifact` rather than returning junk.

    **Refuses on any mismatch, including a version it does not know.**
    Loading a model whose features have moved produces numbers, not errors,
    and those numbers reach a surface. There is no partial-trust mode here
    on purpose.
    """
    if not path.exists():
        raise StaleArtifact(f"no artifact at {path}: run `cscan predict` to write one")

    with np.load(path, allow_pickle=False) as payload:
        meta = json.loads(str(payload["meta"]))
        version = int(meta.get("artifact_version", 0))
        if version != ARTIFACT_VERSION:
            raise StaleArtifact(
                f"artifact version {version} != {ARTIFACT_VERSION}; refit rather than guess"
            )
        if meta["config_hash"] != config_hash:
            raise StaleArtifact(
                f"artifact config_hash {meta['config_hash'][:8]} != {config_hash[:8]}: "
                "the config moved, so the population did too"
            )
        if meta["git_sha"] != git_sha:
            raise StaleArtifact(
                f"artifact git_sha {meta['git_sha'][:7]} != {git_sha[:7]}: "
                "the feature code moved, so the design matrix may have"
            )

        members = []
        for i in range(int(meta["n_members"])):
            trunk = tuple(
                cinf.LinearLayer(payload[f"m{i}_trunk{j}_w"], payload[f"m{i}_trunk{j}_b"])
                for j in range(int(meta["n_trunk"]))
            )
            heads = tuple(
                cinf.LinearLayer(payload[f"m{i}_head{j}_w"], payload[f"m{i}_head{j}_b"])
                for j in range(int(meta["n_heads"]))
            )
            members.append(cinf.NetworkWeights(trunk=trunk, heads=heads))

        return Artifact(
            members=tuple(members),
            grids=payload["grids"],
            columns=tuple(meta["columns"]),
            mean=payload["mean"],
            std=payload["std"],
            categorical_levels=tuple((c, tuple(lv)) for c, lv in meta["categorical_levels"]),
            tables={k: calib.ReliabilityTable.from_dict(v) for k, v in meta["tables"].items()},
            model_version=str(meta["model_version"]),
            config_hash=str(meta["config_hash"]),
            git_sha=str(meta["git_sha"]),
            fitted_at=str(meta["fitted_at"]),
            n_train=int(meta["n_train"]),
            n_calibrate=int(meta["n_calibrate"]),
            trained_signal_types=tuple(meta["trained_signal_types"]),
        )
