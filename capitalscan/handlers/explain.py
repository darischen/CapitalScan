"""`explain_signal` — why one bar fired, and what cell it lands in.

Features and the cell. No attribution.

**No SHAP field.** DESIGN §10.1 lists a top-5 feature attribution, and it is
absent here rather than present and empty. A SHAP value is an attribution
*of a model*, and no model exists (ADR 093 Provisional, ADR 113). An empty
list would read as "nothing contributed", which is a claim; a missing field
reads as "no model", which is the fact. Phase 6 adds it.

The features are the stored event columns - the state the detector saw, not
a re-derivation. Recomputing them here would be a second implementation of
`core/signals.py`'s inputs and would drift the first time an indicator
window moved.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Engine

from capitalscan.core.config import ExitParams, StatsParams
from capitalscan.handlers import _db, enums
from capitalscan.handlers.errors import HandlerError, InvalidEnum
from capitalscan.handlers.stats import get_stats
from capitalscan.handlers.types import CellStats, Explanation, Suppressed
from capitalscan.handlers.validate import validated

# The state the detector conditioned on, in the order a reader wants it:
# what the bands said, what the stochastic said, then the context that
# selects the cell, then the environment.
FEATURE_COLUMNS: tuple[str, ...] = (
    "touch_level",
    "bb_pctb",
    "bb_width_pct",
    "k_full",
    "k_fast",
    "d_full",
    "k_cross_up",
    "k_cross_down",
    "dd_52w",
    "dd_bucket",
    "above_sma200",
    "sma200_slope_60",
    "atr_14",
    "rv_pct_252d",
    "vol_z_20d",
    "bw_regime",
    "cofire_count",
    "days_to_earnings",
    "vix_close",
    "spx_ret_1d",
    "sector",
    "era",
    "split_key",
)

_SELECT = f"""
SELECT id, ticker, signal_date, signal_type, signal_types_all, signal_strength,
       side, entry_kind, {", ".join(FEATURE_COLUMNS)}
FROM events
WHERE config_hash = :config_hash
  AND ticker = :ticker
  AND signal_date = :signal_date
  AND entry_kind = :entry_kind
ORDER BY signal_type
"""


class SignalNotFound(HandlerError):
    """No event for that ticker on that date, under the live config.

    A raise rather than an empty `Explanation`. "Explain what fired" when
    nothing fired has no answer, and an `Explanation` with empty features
    would render as a page describing a signal that did not happen.
    """


def _numeric(value: Any) -> Any:
    # `numeric` arrives as `Decimal`, which is not JSON-serializable and
    # compares unequal to the float a test writes. Session 16 serializes
    # this dict straight to the wire, so the conversion belongs here rather
    # than in each consumer.
    from decimal import Decimal

    return float(value) if isinstance(value, Decimal) else value


def explain_signal(
    ticker: str,
    date_: date,
    entry_kind: str = "next_open",
    split: str | None = None,
    target_pct: float | None = None,
    engine: Engine | None = None,
    sp: StatsParams | None = None,
    ep: ExitParams | None = None,
) -> Explanation:
    """The most specific signal on `ticker` at `date_`, its features, its cell.

    When a bar fires several types, the one reported is the most specific by
    ADR 057's ranking - the same one `events.signal_type` stores, and the
    same one that selects the cell. `signal_types_all` carries the rest, so
    nothing is hidden; the choice is only about which single type names the
    row.

    `target_pct` and `split` select which cell to attach. Both default to
    None, in which case no cell is looked up and `cell` is None. That is
    deliberate: "up 3% within 5 sessions on the validate split" is a
    question, and a handler that picked one for the caller would put a
    statistical choice in a default argument.
    """
    sp = sp or StatsParams()
    ep = ep or ExitParams()
    entry_kind = enums.parse_entry_kind(entry_kind)
    # Validated here rather than at the `get_stats` call below. That call is
    # conditional, so a `split='holdout'` arriving without a `target_pct`
    # would have been accepted and quietly dropped - a refusal that depends
    # on another argument is not a refusal. Found by
    # `test_handlers_contract.py::test_holdout_raises_on_every_handler_that_
    # takes_a_split`, which is the reason it iterates the signature rather
    # than naming the handlers it expects to check.
    if split is not None:
        enums.parse_split(split)
    if (split is None) != (target_pct is None):
        raise InvalidEnum(
            "split and target_pct select a cell together. Pass both to "
            "attach one, or neither to get features alone. Passing one is "
            "a half-stated question and there is no sensible default for "
            "the other half (invariant 9)."
        )

    engine = _db.engine_or_default(engine)
    config_hash = _db.resolve_config_hash(engine)
    first_bar, last_bar = _db.bar_window(engine)
    enums.check_date_window(date_, first_bar, last_bar)

    found = _db.rows(
        engine,
        _SELECT,
        {
            "config_hash": config_hash,
            "ticker": ticker.upper(),
            "signal_date": date_,
            "entry_kind": entry_kind,
        },
    )
    if not found:
        raise SignalNotFound(
            f"No event for {ticker.upper()} on {date_} under config "
            f"{config_hash} at entry_kind={entry_kind}. Nothing fired, so "
            "there is nothing to explain."
        )

    # More than one row means the bar fired on both sides, which
    # `core.signals.detect` does emit - one hit per side. Ranking by
    # `signal_strength` picks the confluence over the bare touch, and the
    # ticker/type ordering below it keeps the choice deterministic.
    row = max(found, key=lambda r: (int(r.get("signal_strength") or 0), r["signal_type"]))

    cell: CellStats | Suppressed | None = None
    cell_id: str | None = None
    if split is not None and target_pct is not None:
        cell = get_stats(
            signal_type=row["signal_type"],
            target_pct=target_pct,
            dd_bucket=row["dd_bucket"],
            split=split,
            entry_kind=entry_kind,
            engine=engine,
            sp=sp,
            ep=ep,
        )
        cell_id = cell.cell_id

    return validated(
        Explanation(
            ticker=row["ticker"],
            signal_date=row["signal_date"],
            signal_type=row["signal_type"],
            signal_types_all=tuple(row.get("signal_types_all") or ()),
            side=row.get("side"),
            features={name: _numeric(row.get(name)) for name in FEATURE_COLUMNS},
            cell_id=cell_id,
            cell=cell,
            meta=_db.build_meta(engine, config_hash=config_hash, as_of=last_bar, split=split),
        ),
        sp,
    )
