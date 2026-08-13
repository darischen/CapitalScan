"""`SignalParams.enabled_signal_types` — the ablation switch and the reason
ADR 108's new type gets its own `config_hash`.

**The defect this closes.** ADR 108 states as a consequence that the new
signal type forces a new `config_hash`, because `signal_strength` counts
concurrent types and therefore shifts on every day it fires. That does not
happen on its own: `jobs.config.config_hash` hashes
`dataclasses.asdict(Config)`, and a `SignalType` enum member is not a
`Config` field. Adding the type alone left the hash at `1835688bf7d760ba`,
so a backtest would have overwritten the 626,977 events Sessions 12 and 13
published against — in place, with nothing raising.

Naming the enabled set in `SignalParams` fixes that at the root rather than
by convention: the signal set genuinely *is* a config dimension (ADR 060
already treats a universe threshold that way), and DESIGN §3.10 wants an
ablated criterion to be a config change rather than a code change.
"""

from __future__ import annotations

import pandas as pd
import pytest

from capitalscan.core import signals as sig
from capitalscan.core.config import Config, SignalParams
from capitalscan.core.types import Side, SignalType
from capitalscan.jobs.config import config_hash

# The hash Sessions 12 and 13 measured, and every number in RESULTS.md under
# them. Pinned so a config change that would silently invalidate those
# results fails here first.
SESSION_12_13_HASH = "1835688bf7d760ba"


def _bar(**kw):
    fields = {"open": 100.0, "high": 106.0, "low": 99.0, "close": 100.0, "ticker": "TSM"}
    fields.update(kw)
    return pd.Series(fields, name=pd.Timestamp("2026-07-29"))


def _ind(k_full=85.0):
    return pd.Series(
        {
            "bb_lower": 95.0,
            "bb_mid": 100.0,
            "bb_upper": 105.0,
            "bb_pctb": 0.5,
            "k_full": k_full,
            "d_full": 50.0,
            "k_fast": k_full,
            "atr_14": 2.0,
        }
    )


# --------------------------------------------------------------------------
# The hash
# --------------------------------------------------------------------------


def test_the_default_config_no_longer_hashes_to_the_session_12_13_value():
    """Enabling the new type is a different config, so it must not reuse the
    identity of the runs that predate it."""
    assert config_hash(Config()) != SESSION_12_13_HASH


def test_the_session_12_13_hash_is_not_reconstructible_and_that_is_expected():
    """**Stated plainly because the opposite was assumed first.**

    Adding *any* field to `Config` changes every hash it produces, because
    `asdict` emits the key regardless of its value. So ablating the new
    signal type does **not** recover `1835688bf7d760ba` — that identity
    predates the field entirely and cannot be produced by any of today's
    configs.

    That is correct behaviour, not a defect: the config schema genuinely
    changed, and a hash that collided across schema versions would be
    claiming two different things are the same config. `1835688bf7d760ba`
    remains a historical identity, preserved in `events` and in RESULTS.md,
    and the point of this field is that the new run gets its own hash rather
    than overwriting it.
    """
    ablated = SignalParams(
        enabled_signal_types=tuple(
            s.value for s in SignalType if s is not SignalType.BEAR_CLOSE_ABOVE_UPPER
        )
    )
    assert config_hash(Config(signals=ablated)) != SESSION_12_13_HASH
    # And it is distinct from the enabled default, which is what makes the
    # field an ablation switch rather than decoration.
    assert config_hash(Config(signals=ablated)) != config_hash(Config())


def test_two_different_enabled_sets_hash_differently():
    a = Config(signals=SignalParams(enabled_signal_types=("bb_upper_touch",)))
    b = Config(signals=SignalParams(enabled_signal_types=("bb_lower_touch",)))
    assert config_hash(a) != config_hash(b)


def test_the_hash_is_sensitive_to_the_order_the_set_is_written_in():
    """Recorded rather than fixed, and it follows existing precedent.

    `UniverseParams.required_criteria` is a tuple of strings with exactly
    this property and has shipped that way since ADR 014. `config_hash`
    hashes `asdict` output, where a tuple is a list and order survives, so
    two configs enabling the same signals in different orders hash
    differently.

    Normalizing would mean `config_hash` special-casing one field, which
    trades a documented convention for a hidden one. The convention is:
    **write the set in `SignalType` declaration order**, as the default
    does.
    """
    a = Config(signals=SignalParams(enabled_signal_types=("bb_upper_touch", "confluence_high")))
    b = Config(signals=SignalParams(enabled_signal_types=("confluence_high", "bb_upper_touch")))
    assert config_hash(a) != config_hash(b)


def test_the_default_is_written_in_enum_declaration_order():
    """The convention above, enforced. A reordered default would mint a new
    hash for an identical config."""
    assert SignalParams().enabled_signal_types == tuple(s.value for s in SignalType)


# --------------------------------------------------------------------------
# The behaviour
# --------------------------------------------------------------------------


def test_every_type_is_enabled_by_default():
    assert set(SignalParams().enabled_signal_types) == {s.value for s in SignalType}


def test_disabling_the_new_type_suppresses_it():
    sp = SignalParams(
        enabled_signal_types=tuple(
            s.value for s in SignalType if s is not SignalType.BEAR_CLOSE_ABOVE_UPPER
        )
    )
    hits = sig.detect(_bar(bear_close_above_upper=True), _ind(), sp)
    for hit in hits:
        assert SignalType.BEAR_CLOSE_ABOVE_UPPER not in hit.signal_types_all


def test_disabling_it_restores_the_prior_signal_type_and_strength():
    """Ablation has to reproduce the old answer exactly, not merely drop one
    entry: `signal_type` falls back to `confluence_high` and
    `signal_strength` returns to its pre-ADR-108 count."""
    enabled = SignalParams()
    ablated = SignalParams(
        enabled_signal_types=tuple(
            s.value for s in SignalType if s is not SignalType.BEAR_CLOSE_ABOVE_UPPER
        )
    )
    bar = _bar(bear_close_above_upper=True)
    with_new = [h for h in sig.detect(bar, _ind(), enabled) if h.side is Side.SHORT][0]
    without = [h for h in sig.detect(bar, _ind(), ablated) if h.side is Side.SHORT][0]

    assert with_new.signal_type is SignalType.BEAR_CLOSE_ABOVE_UPPER
    assert without.signal_type is SignalType.CONFLUENCE_HIGH
    assert without.signal_strength == with_new.signal_strength - 1


def test_disabling_a_long_type_leaves_the_short_side_alone():
    sp = SignalParams(
        enabled_signal_types=tuple(
            s.value for s in SignalType if s is not SignalType.CONFLUENCE_LOW
        )
    )
    hits = sig.detect(_bar(low=90.0), _ind(k_full=10.0), sp)
    long_hit = [h for h in hits if h.side is Side.LONG][0]
    assert SignalType.CONFLUENCE_LOW not in long_hit.signal_types_all
    assert SignalType.BB_LOWER_TOUCH in long_hit.signal_types_all


def test_an_empty_enabled_set_fires_nothing():
    sp = SignalParams(enabled_signal_types=())
    assert sig.detect(_bar(low=90.0, bear_close_above_upper=True), _ind(k_full=10.0), sp) == []


def test_breach_live_respects_the_enabled_set_too():
    """The live and backtest paths must agree on which signals exist, or the
    poller fires on types the backtest never measured (ADR 006)."""
    from capitalscan.core.types import Bands

    bands = Bands(
        bb_lower=95.0,
        bb_mid=100.0,
        bb_upper=105.0,
        k_full=85.0,
        d_full=50.0,
        k_fast=85.0,
        atr_14=2.0,
    )
    sp = SignalParams(
        enabled_signal_types=tuple(
            s.value for s in SignalType if s is not SignalType.CONFLUENCE_HIGH
        )
    )
    assert SignalType.CONFLUENCE_HIGH not in sig.breach_live(106.0, bands, sp)
    assert SignalType.BB_UPPER_TOUCH in sig.breach_live(106.0, bands, sp)


def test_an_unknown_signal_name_is_rejected():
    """A typo would otherwise silently disable a real signal and mint a new
    `config_hash` for a config nobody intended."""
    with pytest.raises(ValueError, match="unknown signal type"):
        sig.enabled_types(SignalParams(enabled_signal_types=("confluance_high",)))
