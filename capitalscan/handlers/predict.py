"""`predict` — the contract, and `NotFound` for every input.

**This handler returns nothing, on purpose, and the test that pins that is
the point of the module.**

No model exists. ADR 093 is Provisional; ADR 113 opened Phase 6 on
2026-08-17 in a smaller form than ADR 093 specified, gated harder, and
explicitly *not* on the expectation that it will find something. Until it
ships, `predict` has an answer and the answer is "no prediction exists for
this input."

Three things a stub could have done instead, all worse:

- **Return a plausible fan.** It would be forgotten and then trusted. A
  quantile spread that nobody computed is indistinguishable on screen from
  one that somebody did.
- **Raise `NotImplementedError`.** That is a statement about the code, not
  about the database. A caller cannot tell it apart from a crash, and a
  chat layer would surface it as a failure rather than as an answer.
- **Not exist.** Then session 16 registers six tools, session 18's chat
  learns six, and Phase 6 changes the wire contract for all three consumers
  at once instead of changing one return value.

`test_handlers_predict.py::test_predict_returns_not_found_for_every_input`
fails the moment Phase 6 changes this, which is the intent: the change
should be a deliberate edit to a test that says why, not a silent
substitution.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Engine

from capitalscan.core.config import StatsParams
from capitalscan.handlers import _db
from capitalscan.handlers.types import NotFound, Prediction
from capitalscan.handlers.validate import validated

# The one reason string, so the wire message and the UI copy cannot drift.
NO_MODEL_REASON = (
    "No model exists. ADR 093 is Provisional and ADR 113 opened Phase 6 "
    "conditionally on ADR 112's negative result; until a model passes ADR "
    "113's five gates, no prediction is available for any ticker or date. "
    "Historical frequencies are available through get_stats."
)


def predict(
    ticker: str,
    as_of: date | None = None,
    engine: Engine | None = None,
    sp: StatsParams | None = None,
) -> Prediction | NotFound:
    """`NotFound`, for every input, in Phase 5.

    The signature and the return union are the deliverable. `Prediction` is
    the shape Phase 6 must fill, including the four invariant-8 companions
    drawn from the cell the model conditioned on - so a model that cannot
    say how much data stands behind its fan cannot ship through this layer.

    Arguments are still validated and `meta` is still built from the live
    database. A `NotFound` whose `meta` said nothing would be a different
    kind of lie: the caller would not learn which config was queried or how
    stale it is, and those are true and knowable regardless of whether a
    prediction exists.
    """
    sp = sp or StatsParams()
    engine = _db.engine_or_default(engine)
    config_hash = _db.resolve_config_hash(engine)
    _, last_bar = _db.bar_window(engine)

    return validated(
        NotFound(
            what=f"prediction for {ticker.upper()}"
            + (f" as of {as_of}" if as_of is not None else ""),
            reason=NO_MODEL_REASON,
            meta=_db.build_meta(engine, config_hash=config_hash, as_of=last_bar),
        ),
        sp,
    )
