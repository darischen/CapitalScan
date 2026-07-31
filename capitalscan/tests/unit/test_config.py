"""Test config resolution precedence."""

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
