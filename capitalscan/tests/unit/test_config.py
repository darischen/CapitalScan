"""Test config resolution precedence."""

import json

from capitalscan.core.config import (
    Config,
    resolve_config,
)


def test_config_default():
    """Verify default config loads."""
    cfg = resolve_config()
    assert isinstance(cfg, Config)
    assert cfg.indicators.bb_window == 20
    assert cfg.exits.max_hold_days == 5


def test_config_cli_overrides():
    """CLI flags override defaults."""
    overrides = {"indicators": {"bb_window": 25}}
    cfg = resolve_config(cli_overrides=overrides)
    assert cfg.indicators.bb_window == 25


def test_config_precedence():
    """Verify precedence: CLI > env > file > default."""
    cfg = resolve_config(
        cli_overrides={"indicators": {"bb_window": 30}}
    )
    assert cfg.indicators.bb_window == 30


def test_config_env_var_overrides_default(monkeypatch):
    """CAPSCAN_INDICATORS as JSON overrides the dataclass default."""
    monkeypatch.setenv("CAPSCAN_INDICATORS", json.dumps({"bb_window": 22}))
    cfg = resolve_config()
    assert cfg.indicators.bb_window == 22


def test_config_cli_overrides_env_var(monkeypatch):
    """CLI flag beats an env var for the same field (precedence order)."""
    monkeypatch.setenv("CAPSCAN_INDICATORS", json.dumps({"bb_window": 22}))
    cfg = resolve_config(cli_overrides={"indicators": {"bb_window": 30}})
    assert cfg.indicators.bb_window == 30


def test_config_malformed_env_var_is_ignored(monkeypatch):
    """A non-JSON env var value is skipped rather than crashing resolution."""
    monkeypatch.setenv("CAPSCAN_INDICATORS", "not json")
    cfg = resolve_config()
    assert cfg.indicators.bb_window == 20


def test_config_file_values_used_when_present(tmp_path, monkeypatch):
    """config.toml values load when no CLI or env override exists."""
    config_file = tmp_path / "config.toml"
    config_file.write_text('[indicators]\nbb_window = 25\n')
    monkeypatch.chdir(tmp_path)
    cfg = resolve_config(config_file="config.toml")
    assert cfg.indicators.bb_window == 25
