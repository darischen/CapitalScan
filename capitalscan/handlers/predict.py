"""`predict` — a calibrated `p_touch`, or `NotFound` when none was written.

**This handler returned `NotFound` for every input from Phase 5 until
2026-09-05, and that was correct for as long as it lasted.** ADR 093 was
Provisional, ADR 113 opened Phase 6 without promising it would find
anything, and a handler that invented a fan would have been forgotten and
then trusted. The docstring that lived here argued the refusal should end
only as a deliberate edit to a test that says why. ADR 174 is that edit.

**What changed is narrower than "Phase 6 shipped".** No directional call is
published. ADR 172 retired the directional heads because `terminal_h5_q50`
is negative out of sample, and it stays retired: `q05`..`q95` are returned
because DESIGN §7.4 defines the fields and they cost nothing to read off
the same CDF, but no surface displays them and no caller should treat the
midpoint as a forecast.

What ships is `p_touch_2/3/5/10` — the probability that price reaches a
favourable excursion within the horizon, in the direction the signal
already assigned. On validate that is monotone across all ten reliability
deciles with a Brier skill of +5.54% at 3% and an AUC of 0.771 at 10%.

**The interval is measured, not modelled** (ADR 174). `ci_low`/`ci_high`
come from the Wilson interval on what actually happened to past predictions
in the same reliability bucket, sized on that bucket's Kish `n_eff` — not
from the ensemble's seed spread, which would describe the optimiser rather
than the world. `n_eff` is therefore the *calibration* bucket's effective
sample, which is what invariant 8 needs a reader to be able to check.

**A ticker that fired twice in one day is ambiguous without a side.**
`predictions` keys on `event_id` (migration `e7b4c92f1a08`) because one
name can produce a long and a short on the same date, and `p_touch` is
directional -- it is the probability of a favourable excursion *for the
side the signal assigned*. Pass `side` to choose (2026-09-25); it filters
through the linked event, since `predictions` stores no side of its own.
Without it the newest row by `(as_of DESC, id DESC)` wins, and the result
now carries `side` so the pick is visible rather than silent. A prediction
whose event is unresolved (ADR 191, `event_id` NULL) returns `side=None`
and never matches a side filter.

**`NotFound` still happens and still means something.** No row exists for a
ticker with no recent event, for a date before the first `cscan predict`
run, or for a config generation that has not been scored. That is a
different statement from "no model exists" and the reason string says so.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Engine

from capitalscan.core.calibration import MODEL_CAVEAT
from capitalscan.core.config import StatsParams
from capitalscan.handlers import _db, enums
from capitalscan.handlers.types import NotFound, Prediction
from capitalscan.handlers.validate import validated

#: Why a specific lookup found nothing. It names the job that would fix it,
#: because "no prediction" is now an operational state rather than a
#: permanent one and the reader deserves to know which.
NO_ROW_REASON = (
    "No prediction has been written for this ticker and date. Predictions "
    "cover recent events under the live config generation and are written "
    "by `cscan predict`; a ticker with no recent signal, a date before the "
    "first prediction run, or an unscored config generation all land here. "
    "Historical frequencies are available through get_stats."
)

_SQL = """
SELECT p.ticker, p.as_of, p.model_version, p.cell_id,
       p.q05, p.q25, p.q50, p.q75, p.q95,
       p.p_touch_2, p.p_touch_3, p.p_touch_5, p.p_touch_10,
       p.p_adverse_3, p.p_adverse_5,
       p.calib_bucket, p.calib_n_eff, p.ci_low, p.ci_high,
       e.side
  FROM predictions p
  LEFT JOIN events e ON e.id = p.event_id
 WHERE p.ticker = :ticker
   AND p.config_hash = :chash
   {date_filter}
   {side_filter}
 ORDER BY p.as_of DESC, p.id DESC
 LIMIT 1
"""


def _as_float(value: object) -> float | None:
    """`numeric` arrives as `Decimal`; the wire contract is `float | None`."""
    return None if value is None else float(value)  # type: ignore[arg-type]


def predict(
    ticker: str,
    as_of: date | None = None,
    side: str | None = None,
    engine: Engine | None = None,
    sp: StatsParams | None = None,
) -> Prediction | NotFound:
    """The most recent calibrated prediction for `ticker`, at or before `as_of`.

    `as_of=None` returns the newest available row rather than today's,
    which is the honest default: events are written by a nightly job, so
    "today" frequently has no row and refusing would report an operational
    gap as a missing model.

    The returned `n_eff` is the calibration bucket's effective sample size
    and the interval is that bucket's Wilson interval on realised outcomes
    (ADR 174). Both describe how well probabilities of this magnitude have
    historically behaved. Neither is a statement about this ticker.
    """
    sp = sp or StatsParams()
    if side is not None:
        side = enums.parse_side(side)
    engine = _db.engine_or_default(engine)
    config_hash = _db.resolve_config_hash(engine)
    _, last_bar = _db.bar_window(engine)
    meta = _db.build_meta(engine, config_hash=config_hash, as_of=last_bar)

    params: dict[str, object] = {"ticker": ticker.upper(), "chash": config_hash}
    date_filter = ""
    if as_of is not None:
        date_filter = "AND p.as_of <= :as_of"
        params["as_of"] = as_of
    side_filter = ""
    if side is not None:
        side_filter = "AND e.side = :side"
        params["side"] = side

    found = _db.rows(engine, _SQL.format(date_filter=date_filter, side_filter=side_filter), params)
    if not found:
        return validated(
            NotFound(
                what=f"prediction for {ticker.upper()}"
                + (f" ({side})" if side is not None else "")
                + (f" as of {as_of}" if as_of is not None else ""),
                reason=NO_ROW_REASON,
                meta=meta,
            ),
            sp,
        )

    row = found[0]
    n_eff = row["calib_n_eff"]
    return validated(
        Prediction(
            ticker=str(row["ticker"]),
            as_of=row["as_of"],
            side=row.get("side"),
            model_version=str(row["model_version"]),
            # The reliability bucket, not an ADR 093 conditioning cell. The
            # two are different objects and the column names keep them apart;
            # this one is what the interval was computed over.
            cell_id=row["calib_bucket"],
            q05=_as_float(row["q05"]),
            q25=_as_float(row["q25"]),
            q50=_as_float(row["q50"]),
            q75=_as_float(row["q75"]),
            q95=_as_float(row["q95"]),
            p_touch_2=_as_float(row["p_touch_2"]),
            p_touch_3=_as_float(row["p_touch_3"]),
            p_touch_5=_as_float(row["p_touch_5"]),
            p_touch_10=_as_float(row["p_touch_10"]),
            p_adverse_3=_as_float(row["p_adverse_3"]),
            p_adverse_5=_as_float(row["p_adverse_5"]),
            n_eff=None if n_eff is None else int(float(n_eff)),
            ci_low=_as_float(row["ci_low"]),
            ci_high=_as_float(row["ci_high"]),
            # `q_value` is Phase 4's multiple-testing correction over a
            # family of cell hypotheses. A single calibrated probability is
            # not a hypothesis test and has no q-value; None is the honest
            # answer, and inventing one would imply a family that does not
            # exist here.
            q_value=None,
            meta=meta,
        ),
        sp,
    )


#: Re-exported so a caller rendering a probability can render the caveat
#: beside it. Sourced from `core`, never from `research`: nothing in the
#: serving path may depend on the fitting stack.
CAVEAT = MODEL_CAVEAT
