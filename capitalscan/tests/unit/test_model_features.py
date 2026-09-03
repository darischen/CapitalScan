"""The model's training matrix (Session 22, DESIGN §7.3, ADR 113).

The sharp test here is the leak check. `events` carries each signal's
*outcome* in the same row as its state — `gross_ret`, `mfe`,
`touched_5pct`, `entry_price` — so a feature list assembled by "select the
numeric columns" would train a model to predict a number it was handed.
It would score beautifully and mean nothing, and nothing else in the
pipeline would object.

This is the model-layer analogue of invariant 3, and it fails the same way:
silently, with a result that looks like success.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from capitalscan.research import features as feat


def _output_name(expr: str) -> str:
    """The column name pandas will see, from a qualified SELECT item.

    `e.bb_pctb` -> `bb_pctb`; `t.sector AS sector` -> `sector`.
    """
    return expr.split(" AS ")[-1].split(".")[-1].strip()


# ---------------------------------------------------------------------------
# Leakage
# ---------------------------------------------------------------------------


def test_no_feature_is_an_outcome_column():
    """The one that matters.

    Every name in `FORBIDDEN_COLS` is known only *after* the signal
    resolved. Any of them as a feature is a perfect leak.
    """
    leaked = set(feat.FEATURE_COLS) & feat.FORBIDDEN_COLS
    assert not leaked, (
        f"{sorted(leaked)} are outcome columns being used as features. A model "
        "trained on these predicts a number it was given (invariant 3's "
        "failure mode, one layer up)."
    )


def test_the_labels_are_forbidden_as_features():
    """Not a contradiction: the four labels are selected *as labels* and
    must never appear in `FEATURE_COLS`.

    `fwd_ret_5d` as a feature for `fwd_ret_5d` is the degenerate case, and
    `fwd_ret_5d` as a feature for `fwd_ret_10d` is the subtle one — still a
    leak, since day 5 is unknown when the signal fires.
    """
    assert set(feat.LABEL_COLS) <= feat.FORBIDDEN_COLS
    assert not set(feat.LABEL_COLS) & set(feat.FEATURE_COLS)


def test_meta_columns_are_not_features():
    """`id`, `split_key` and `cluster_id` are plumbing. `id` especially:
    it is a monotonic sequence, so it encodes time, and a tree splitting on
    it memorises the calendar."""
    assert not set(feat.META_COLS) & set(feat.FEATURE_COLS)


def test_era_is_excluded():
    """DESIGN §7.3 excludes it explicitly: it invites memorising regime and
    is unavailable for a future prediction anyway."""
    assert "era" not in feat.FEATURE_COLS
    assert "era" in feat.FORBIDDEN_COLS


def test_ticker_identity_is_not_a_feature():
    """DESIGN §7.3: 60 names over 40k events permits memorising individual
    histories. Sector is the right granularity, and it is the one carried."""
    assert "ticker" not in feat.FEATURE_COLS
    assert "sector" in feat.FEATURE_COLS


# ---------------------------------------------------------------------------
# The holdout
# ---------------------------------------------------------------------------


def test_the_holdout_cannot_be_built():
    """Evaluated exactly once, at the end, and published whatever it says.

    A builder that reaches it by a typo is one keystroke from spending the
    only unbiased estimate the project has.
    """
    with pytest.raises(ValueError, match="holdout"):
        feat.build_training_frame(engine=None, config_hash="x", split="holdout")


def test_the_default_split_is_train():
    """Not holdout, and not an empty string that would silently match
    nothing and return a frame of zero rows that looks merely unlucky."""
    import inspect

    assert inspect.signature(feat.build_training_frame).parameters["split"].default == "train"


# ---------------------------------------------------------------------------
# Derived columns
# ---------------------------------------------------------------------------


def _frame(**over) -> pd.DataFrame:
    # The four breach-depth inputs and `side` are always present on a real
    # frame -- `build_training_frame` joins them from `bars` and
    # `indicators` -- so the fixture carries them rather than
    # `_breach_depth` tolerating their absence. A silently-NaN column would
    # hide the SQL breaking.
    base = {
        "k_full": [80.0],
        "d_full": [75.0],
        "mcap_usd": [1e9],
        "side": ["long"],
        "bar_low": [98.0],
        "bar_high": [105.0],
        "band_lower": [100.0],
        "band_upper": [110.0],
    }
    base.update({k: [v] for k, v in over.items()})
    return pd.DataFrame(base)


def test_k_minus_d_is_the_spread():
    out = feat._add_derived(_frame())
    assert out["k_minus_d"].iloc[0] == pytest.approx(5.0)


def test_mcap_log_is_the_natural_log():
    out = feat._add_derived(_frame(mcap_usd=1e9))
    assert out["mcap_log"].iloc[0] == pytest.approx(math.log(1e9))


@pytest.mark.parametrize("bad", [0.0, -1.0, None])
def test_a_non_positive_market_cap_yields_null_not_negative_infinity(bad):
    """`log(0)` is `-inf`, which a tree splits on as though it meant
    "very small". A market cap of zero is absent or corrupted, not small.

    Invariant 4: absent stays absent.
    """
    out = feat._add_derived(_frame(mcap_usd=bad))
    value = out["mcap_log"].iloc[0]
    assert pd.isna(value), f"expected NaN, got {value}"
    assert not np.isneginf(value)


def test_the_input_frame_is_not_mutated():
    """Project convention: never mutate in place, always return a new
    object. A builder that mutates makes a caller's retry return a
    different frame than its first call."""
    original = _frame()
    before = original.copy(deep=True)
    feat._add_derived(original)
    pd.testing.assert_frame_equal(original, before)


def test_derived_columns_are_deterministic():
    """Identical input, identical output — the model-layer form of the
    determinism test that carries the correctness load elsewhere."""
    a = feat._add_derived(_frame())
    b = feat._add_derived(_frame())
    pd.testing.assert_frame_equal(a, b)


# ---------------------------------------------------------------------------
# The column list
# ---------------------------------------------------------------------------


def test_nineteen_raw_plus_three_derived():
    """DESIGN §7.3 claims twenty-two are on the event row. Three are not —
    `bb_mid`-based distance, `atr_14/close`, and `vix_pct_252d` — and the
    module docstring records why each is blocked rather than dropping them
    silently. If that count ever changes, the docstring and BACKLOG entry
    change with it.
    """
    assert len(feat.RAW_FEATURE_COLS) == 19
    # Three as of 2026-09-02: `breach_depth` joined `k_minus_d` and
    # `mcap_log`. ADR 069's deferred feature, and the half `bb_pctb` does
    # not carry -- the signal is a touch, so the low's depth past the band
    # is a different quantity from the close's (they correlate -0.054).
    # Measured and kept despite being worth only +0.087% on the directional
    # heads: it is genuinely new information, and RESULTS 2026-09-02 says
    # what it bought.
    assert len(feat.DERIVED_FEATURE_COLS) == 3
    assert len(feat.FEATURE_COLS) == 22


def test_no_duplicate_features():
    assert len(set(feat.FEATURE_COLS)) == len(feat.FEATURE_COLS)


def test_selected_columns_cover_everything_the_build_needs():
    """`mcap_usd` is read but is not itself a feature; `d_full` is both a
    feature and the second half of `k_minus_d`. A missing selection shows up
    as a KeyError deep in `_add_derived`, which is a worse error than this
    assertion."""
    selected = {_output_name(c) for c in feat._select_columns()}
    assert set(feat.RAW_FEATURE_COLS) <= selected
    assert set(feat.LABEL_COLS) <= selected
    assert set(feat.META_COLS) <= selected
    assert "mcap_usd" in selected


def test_selected_columns_are_unique():
    """They are interpolated into a SELECT list; a duplicate would produce
    two identically-named columns and pandas would silently keep one."""
    names = [_output_name(c) for c in feat._select_columns()]
    assert len(set(names)) == len(names)


def test_sector_and_mcap_are_read_from_their_populated_source():
    """Both columns exist on `events` and are NULL on all 227,543 rows.

    An unqualified `sector` or `mcap_usd` resolves to the `events` copy: a
    column that exists, is spelled correctly, and is empty. Both were one
    unprefixed name away from shipping as all-NULL features that no test
    would have caught, because "the column is there" and "the column has
    values" are different claims.
    """
    selected = feat._select_columns()
    assert "t.sector AS sector" in selected, "sector must come from tickers"
    assert "u.mcap_usd AS mcap_usd" in selected, "mcap must come from the universe lateral"
    assert "e.sector" not in selected
    assert "e.mcap_usd" not in selected


def test_the_market_cap_lateral_is_causal():
    """`as_of <= signal_date`, most recent first.

    A join without the bound would hand a 2010 event its 2026 market cap.
    ADR 135 is the rule and this is the same lateral `v_watchlist` uses,
    rather than a third reading of "which universe row applies here".
    """
    assert "u.as_of <= e.signal_date" in feat._SQL
    assert "ORDER BY u.as_of DESC" in feat._SQL
    assert "LIMIT 1" in feat._SQL


def test_the_query_scopes_config_split_grain_and_population():
    """Four predicates, each load-bearing:

    `config_hash` — 22 hashes live in research and mixing them mixes
    incomparable runs. `entry_kind` — `touch` rows are the poller's live
    grain, unpriced and unresolved. `in_trade` — the study population
    (`test_events_in_trade_filter.py` exists because losing this looks
    normal). `split_key` — ADR 019.
    """
    for fragment in (
        "e.config_hash = :chash",
        "e.entry_kind = 'next_open'",
        "e.in_trade",
        "e.split_key = :split",
    ):
        assert fragment in feat._SQL
