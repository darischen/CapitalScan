"""Config resolution. Owns the IO that `core/` may not perform (ADR 091).

Precedence, highest first: CLI flag, environment variable, `config.toml`,
dataclass default (BUILD §0.5). Returns populated frozen dataclasses from
`core.config`, which holds the values and nothing else.

**Malformed input raises. It never falls back to a default.** This is
stricter than ordinary input validation, and the reason is provenance:
`config_hash` is computed from the resolved config and stamped on every
event row, every `cell_stats` row, and every `RESULTS.md` entry (ADR 034).
A silently-defaulted config yields a hash asserting parameters that never
ran, and the record is internally consistent, so no later check can catch
it. Three rules, all raising:

1. Parse failure — `CAPSCAN_INDICATORS='{bad json'` names the variable.
2. Unknown keys — `{"bb_windwo": 20}` is valid JSON that applies nothing.
3. Type coercion failure — `{"bb_window": "twenty"}` must not land a string
   in a field typed `int`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import Any

from capitalscan.core.config import (
    Config,
    CostParams,
    ExitParams,
    IndicatorParams,
    SignalParams,
    SplitParams,
    StatsParams,
    UniverseParams,
)

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 only
    import tomli as tomllib  # type: ignore[no-redef]

ENV_PREFIX = "CAPSCAN_"

SECTIONS: dict[str, type] = {
    "indicators": IndicatorParams,
    "signals": SignalParams,
    "exits": ExitParams,
    "costs": CostParams,
    "universe": UniverseParams,
    "stats": StatsParams,
    "splits": SplitParams,
}


class ConfigError(Exception):
    """Configuration could not be resolved as written.

    Raised rather than defaulting, because a defaulted config produces a
    `config_hash` that describes parameters which never ran.
    """


def _check_section_names(overrides: dict, origin: str) -> None:
    unknown = set(overrides) - set(SECTIONS)
    if unknown:
        raise ConfigError(
            f"{origin}: unknown config section(s) {sorted(unknown)}. "
            f"Valid sections: {sorted(SECTIONS)}"
        )


def _coerce(value: Any, expected: type, section: str, name: str, origin: str) -> Any:
    """Return `value` as `expected`, or raise.

    `bool` is checked before `int` because `bool` subclasses `int` in Python,
    so `True` would otherwise pass as a valid `bb_window`. Widening `int` to
    `float` is allowed — TOML writes `2` for 2.0 routinely — but narrowing
    `float` to `int` is not, since it would silently truncate.
    """
    where = f"{origin}: {section}.{name}"
    if expected is bool:
        if isinstance(value, bool):
            return value
        raise ConfigError(f"{where} expects a boolean, got {value!r}")
    if expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{where} expects an int, got {value!r}")
        return value
    if expected is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where} expects a float, got {value!r}")
        return float(value)
    if expected is str:
        if not isinstance(value, str):
            raise ConfigError(f"{where} expects a string, got {value!r}")
        return value
    if expected is tuple:
        if not isinstance(value, (list, tuple)):
            raise ConfigError(f"{where} expects a sequence, got {value!r}")
        return tuple(value)
    return value


def _build(section: str, overrides: dict, origin_by_key: dict[str, str]):
    """Instantiate one section's dataclass, validating names and types."""
    cls = SECTIONS[section]
    assert is_dataclass(cls)
    valid = {f.name: f.type for f in fields(cls)}

    unknown = set(overrides) - set(valid)
    if unknown:
        origin = origin_by_key.get(section, "config")
        raise ConfigError(
            f"{origin}: unknown field(s) {sorted(unknown)} in section '{section}'. "
            f"Valid fields: {sorted(valid)}"
        )

    kwargs = {}
    for name, value in overrides.items():
        declared = valid[name]
        # Dataclass field types resolve to strings under
        # `from __future__ import annotations`; map the handful used here.
        expected = {
            "int": int,
            "float": float,
            "bool": bool,
            "str": str,
            "tuple": tuple,
        }.get(declared if isinstance(declared, str) else declared.__name__, object)
        kwargs[name] = _coerce(value, expected, section, name, origin_by_key.get(section, "config"))
    try:
        return cls(**kwargs)
    except TypeError as e:  # pragma: no cover - name check above catches these
        raise ConfigError(f"{section}: {e}") from e


def _read_toml(config_file: str | None) -> dict:
    if config_file is None:
        return {}
    path = Path(config_file)
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from e


def _read_env() -> dict:
    out: dict = {}
    for section in SECTIONS:
        var = f"{ENV_PREFIX}{section.upper()}"
        raw = os.getenv(var)
        if raw is None or raw == "":
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"{var} is not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise ConfigError(
                f"{var} must be a JSON object mapping field names to values, got {parsed!r}"
            )
        out[section] = parsed
    return out


def resolve_config(
    cli_overrides: dict | None = None,
    env_file: str | None = None,
    config_file: str | None = "config.toml",
) -> Config:
    """Resolve the full config. CLI > env > file > dataclass default.

    `config_file` is a path, not a flag: pass it explicitly in tests so the
    result does not depend on the working directory. A path that does not
    exist is not an error — that is the "no config file" case. A path that
    exists but does not parse *is* an error.
    """
    layers: list[tuple[str, dict]] = []

    file_cfg = _read_toml(config_file)
    if file_cfg:
        _check_section_names(file_cfg, str(config_file))
        layers.append((str(config_file), file_cfg))

    if env_file:
        import dotenv

        dotenv.load_dotenv(env_file)

    env_cfg = _read_env()
    if env_cfg:
        layers.append((ENV_PREFIX + "*", env_cfg))

    if cli_overrides:
        _check_section_names(cli_overrides, "cli")
        layers.append(("cli", cli_overrides))

    # Merge per section, later layers winning per field. `origin_by_key`
    # records which layer last supplied a section so an error names it.
    merged: dict[str, dict] = {}
    origin_by_key: dict[str, str] = {}
    for origin, layer in layers:
        for section, values in layer.items():
            if not isinstance(values, dict):
                raise ConfigError(
                    f"{origin}: section '{section}' must be a mapping, got {values!r}"
                )
            merged.setdefault(section, {}).update(values)
            origin_by_key[section] = origin

    return Config(
        **{section: _build(section, merged.get(section, {}), origin_by_key) for section in SECTIONS}
    )


def config_hash(config: Config) -> str:
    """Stable identity for a resolved config, stamped on every generated row.

    `hashlib`, not `dataclasses`, is why this lives in `jobs/` rather than
    `core/config.py` (invariant 10). `sort_keys=True` makes field order
    irrelevant; `asdict` recurses into every section so a change anywhere
    in the config changes the hash. 16 hex chars (64 bits) is plenty of
    collision resistance for a provenance tag, and stays short in a
    filename or a URL.
    """
    payload = json.dumps(asdict(config), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
