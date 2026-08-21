"""Config resolution — `jobs/config.py` (ADR 091).

Resolution lives in `jobs/` because it does IO: it reads `config.toml`,
`CAPSCAN_*` environment variables, and optionally a dotenv file. `core/`
performs none of that (invariant 1), and `core/config.py` is now frozen
dataclasses only.

**Malformed input raises, never defaults.** `config_hash` is computed from
the resolved config and stamped on every event row, every `cell_stats` row,
and every `RESULTS.md` entry (ADR 034). A silently-defaulted config produces
a hash asserting parameters that never ran, and the record is internally
consistent, so nothing downstream can detect it.

Every test pins the environment explicitly. A resolver reading ambient CWD
and `CAPSCAN_*` makes the suite depend on the machine it runs on — during
the session 1-3 audit, a stray `CAPSCAN_INDICATORS` failed two tests.
"""

from __future__ import annotations

import json

import pytest

from capitalscan.core.config import Config
from capitalscan.jobs.config import ConfigError, resolve_config

SECTIONS = ["indicators", "signals", "exits", "costs", "universe", "stats", "splits"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """No ambient CAPSCAN_* and no ambient config.toml. Green by
    construction, not by luck."""
    for section in SECTIONS:
        monkeypatch.delenv(f"CAPSCAN_{section.upper()}", raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def missing(tmp_path):
    """A config path that does not exist, passed explicitly."""
    return str(tmp_path / "absent.toml")


def _toml(tmp_path, body: str) -> str:
    path = tmp_path / "config.toml"
    path.write_text(body)
    return str(path)


# ---------------------------------------------------------------------------
# Precedence: CLI > env > file > dataclass default (BUILD §0.5)
# ---------------------------------------------------------------------------


def test_defaults_when_nothing_overrides(missing):
    cfg = resolve_config(config_file=missing)
    assert isinstance(cfg, Config)
    assert cfg.indicators.bb_window == 20
    assert cfg.exits.max_hold_days == 5


def test_file_beats_default(tmp_path):
    cfg = resolve_config(config_file=_toml(tmp_path, "[indicators]\nbb_window = 25\n"))
    assert cfg.indicators.bb_window == 25


def test_env_beats_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSCAN_INDICATORS", json.dumps({"bb_window": 22}))
    cfg = resolve_config(config_file=_toml(tmp_path, "[indicators]\nbb_window = 25\n"))
    assert cfg.indicators.bb_window == 22


def test_cli_beats_env(missing, monkeypatch):
    monkeypatch.setenv("CAPSCAN_INDICATORS", json.dumps({"bb_window": 22}))
    cfg = resolve_config(cli_overrides={"indicators": {"bb_window": 30}}, config_file=missing)
    assert cfg.indicators.bb_window == 30


def test_cli_beats_file(tmp_path):
    cfg = resolve_config(
        cli_overrides={"indicators": {"bb_window": 30}},
        config_file=_toml(tmp_path, "[indicators]\nbb_window = 25\n"),
    )
    assert cfg.indicators.bb_window == 30


def test_full_precedence_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSCAN_INDICATORS", json.dumps({"bb_window": 22, "atr_window": 21}))
    cfg = resolve_config(
        cli_overrides={"indicators": {"bb_window": 30}},
        config_file=_toml(tmp_path, "[indicators]\nbb_window = 25\nsma_long = 150\n"),
    )
    assert cfg.indicators.bb_window == 30  # CLI wins
    assert cfg.indicators.atr_window == 21  # env, unopposed
    assert cfg.indicators.sma_long == 150  # file, unopposed
    assert cfg.indicators.rv_window == 20  # untouched default


def test_a_partial_override_leaves_other_fields_at_defaults(missing):
    cfg = resolve_config(cli_overrides={"exits": {"target_pct": 0.05}}, config_file=missing)
    assert cfg.exits.target_pct == 0.05
    assert cfg.exits.stop_atr_k == 1.5
    assert cfg.indicators.bb_window == 20


def test_every_section_is_resolvable(missing):
    cfg = resolve_config(
        cli_overrides={
            "indicators": {"bb_window": 21},
            "signals": {"stoch_oversold": 25.0},
            "exits": {"max_hold_days": 7},
            "costs": {"slippage_bps": 5.0},
            "universe": {"min_mcap_usd": 2.0},
            "stats": {"min_n_eff": 40},
            "splits": {"train_end": "2020-12-31"},
        },
        config_file=missing,
    )
    assert cfg.indicators.bb_window == 21
    assert cfg.signals.stoch_oversold == 25.0
    assert cfg.exits.max_hold_days == 7
    assert cfg.costs.slippage_bps == 5.0
    assert cfg.universe.min_mcap_usd == 2.0
    assert cfg.stats.min_n_eff == 40
    assert cfg.splits.train_end == "2020-12-31"


# ---------------------------------------------------------------------------
# Rule 1 — parse failure raises (ADR 091)
# ---------------------------------------------------------------------------


def test_malformed_env_json_raises(missing, monkeypatch):
    monkeypatch.setenv("CAPSCAN_INDICATORS", "{bad json")
    with pytest.raises(ConfigError):
        resolve_config(config_file=missing)


def test_the_parse_error_names_the_variable(missing, monkeypatch):
    monkeypatch.setenv("CAPSCAN_INDICATORS", "not json")
    with pytest.raises(ConfigError, match="CAPSCAN_INDICATORS"):
        resolve_config(config_file=missing)


def test_the_parse_error_preserves_the_underlying_cause(missing, monkeypatch):
    monkeypatch.setenv("CAPSCAN_EXITS", "{{{")
    with pytest.raises(ConfigError) as exc:
        resolve_config(config_file=missing)
    assert isinstance(exc.value.__cause__, json.JSONDecodeError)


def test_a_json_scalar_is_not_a_section(missing, monkeypatch):
    # Valid JSON, wrong shape: a section must be a mapping of fields.
    monkeypatch.setenv("CAPSCAN_INDICATORS", "20")
    with pytest.raises(ConfigError):
        resolve_config(config_file=missing)


def test_malformed_toml_raises(tmp_path):
    with pytest.raises(ConfigError):
        resolve_config(config_file=_toml(tmp_path, "[indicators\nbb_window = 25\n"))


# ---------------------------------------------------------------------------
# Rule 2 — unknown keys raise (ADR 091)
# ---------------------------------------------------------------------------


def test_a_misspelled_field_raises_rather_than_applying_nothing(missing):
    # {"bb_windwo": 20} is valid JSON that silently changes nothing.
    with pytest.raises(ConfigError, match="bb_windwo"):
        resolve_config(cli_overrides={"indicators": {"bb_windwo": 20}}, config_file=missing)


def test_a_misspelled_field_from_env_raises(missing, monkeypatch):
    monkeypatch.setenv("CAPSCAN_INDICATORS", json.dumps({"bb_windwo": 20}))
    with pytest.raises(ConfigError, match="bb_windwo"):
        resolve_config(config_file=missing)


def test_a_misspelled_field_from_the_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="bb_windwo"):
        resolve_config(config_file=_toml(tmp_path, "[indicators]\nbb_windwo = 25\n"))


def test_an_unknown_section_raises(missing):
    with pytest.raises(ConfigError, match="indicatorz"):
        resolve_config(cli_overrides={"indicatorz": {"bb_window": 20}}, config_file=missing)


def test_the_unknown_key_error_names_the_section(missing):
    with pytest.raises(ConfigError, match="indicators"):
        resolve_config(cli_overrides={"indicators": {"nope": 1}}, config_file=missing)


# ---------------------------------------------------------------------------
# Rule 3 — type coercion failure raises (ADR 091)
# ---------------------------------------------------------------------------


def test_a_string_in_an_int_field_raises(missing):
    with pytest.raises(ConfigError, match="bb_window"):
        resolve_config(cli_overrides={"indicators": {"bb_window": "twenty"}}, config_file=missing)


def test_a_string_in_a_float_field_raises(missing):
    with pytest.raises(ConfigError, match="target_pct"):
        resolve_config(cli_overrides={"exits": {"target_pct": "high"}}, config_file=missing)


def test_a_float_in_an_int_field_raises(missing):
    # 20.5 cannot be bb_window without silently truncating.
    with pytest.raises(ConfigError, match="bb_window"):
        resolve_config(cli_overrides={"indicators": {"bb_window": 20.5}}, config_file=missing)


def test_an_int_is_accepted_for_a_float_field(missing):
    # Widening is safe and TOML writes `2` for 2.0 routinely.
    cfg = resolve_config(cli_overrides={"indicators": {"bb_std": 3}}, config_file=missing)
    assert cfg.indicators.bb_std == 3.0
    assert isinstance(cfg.indicators.bb_std, float)


def test_a_bool_in_an_int_field_raises(missing):
    # bool is a subclass of int in Python; it must not sneak through.
    with pytest.raises(ConfigError, match="bb_window"):
        resolve_config(cli_overrides={"indicators": {"bb_window": True}}, config_file=missing)


def test_a_non_bool_in_a_bool_field_raises(missing):
    with pytest.raises(ConfigError, match="exit_on_mid_band"):
        resolve_config(cli_overrides={"exits": {"exit_on_mid_band": 1}}, config_file=missing)


def test_a_valid_bool_is_accepted(missing):
    cfg = resolve_config(cli_overrides={"exits": {"exit_on_mid_band": True}}, config_file=missing)
    assert cfg.exits.exit_on_mid_band is True


def test_a_string_field_accepts_a_string(missing):
    cfg = resolve_config(cli_overrides={"exits": {"stop_mode": "fixed"}}, config_file=missing)
    assert cfg.exits.stop_mode == "fixed"


def test_a_number_in_a_string_field_raises(missing):
    with pytest.raises(ConfigError, match="stop_mode"):
        resolve_config(cli_overrides={"exits": {"stop_mode": 3}}, config_file=missing)


# ---------------------------------------------------------------------------
# Isolation — the resolver reads only what it is told to
# ---------------------------------------------------------------------------


def test_an_absent_config_file_is_not_an_error(missing):
    assert resolve_config(config_file=missing).indicators.bb_window == 20


def test_resolution_returns_frozen_dataclasses(missing):
    cfg = resolve_config(config_file=missing)
    with pytest.raises(Exception):
        cfg.indicators.bb_window = 99


def test_the_result_is_deterministic(missing):
    assert resolve_config(config_file=missing) == resolve_config(config_file=missing)
