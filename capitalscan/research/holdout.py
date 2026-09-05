"""Spending the holdout. Once, deliberately, and published whatever it says.

**This module exists so that spending the holdout is an act with a name.**
`features.build_training_frame` refuses `split="holdout"` outright, and that
refusal is correct: a frame builder that could reach the holdout by omission
is one typo from spending it. The fix is not to weaken that guard but to put
the deliberate path somewhere it cannot be reached by accident, cannot be
reached by default, and reads unmistakably at the call site.

**What "spent" means.** The holdout is 2024-01-02 to 2026-08-28, 82,957
events, and it has never been read. Every number the project has published
about model quality comes from *validate* (2022-2023) -- which has been
scored many times over: five architectures, three market-feature arms, a
blend, seed sweeps. Choosing a winner on a split examined that often makes
the reported figure optimistic by an unknown amount. The holdout is the one
measurement not contaminated that way.

It is contaminated the moment it is read. After this runs, the honest
position is that the project no longer has a clean out-of-sample estimate,
and no further model may be selected against these numbers. That is the
price, and it is why ADR 019 says *once*.

**Published whatever it says.** CLAUDE.md's wording, and it binds: a result
that is worse than validate is the expected outcome, not a reason to
re-run, re-tune, or quietly not mention it.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.research import features as feat

#: The literal a caller must pass. Spelled out so that reading the call site
#: tells you what it does without opening this file, and so that no
#: plausible typo or default reaches it.
CONFIRM = "yes-spend-the-holdout"


def build_holdout_frame(
    engine: Engine,
    config_hash: str,
    confirm: str,
) -> tuple[pd.DataFrame, feat.FrameReport]:
    """The holdout, as a scored frame. Requires the literal `CONFIRM`.

    Deliberately not a `split=` argument on `build_training_frame`: a
    boolean or a string among other strings is exactly the shape that gets
    passed through from a variable one day. This takes a sentence.
    """
    if confirm != CONFIRM:
        raise ValueError(
            f"refusing to read the holdout without an explicit confirmation. "
            f"Pass confirm={CONFIRM!r}. The holdout is evaluated exactly once "
            f"(ADR 019) and every model selected afterwards is selected "
            f"against a contaminated estimate."
        )

    cols = feat._select_columns()
    with engine.connect() as conn:
        frame = pd.read_sql(
            text(feat._SQL.format(cols=", ".join(cols))),
            conn,
            params={"chash": config_hash, "split": "holdout"},
        )

    trainable, etf, missing = feat.partition_for_training(
        list(zip(frame["ticker"], frame["sector"], strict=True))
    )
    if missing:
        sample = sorted({frame["ticker"].iloc[i] for i in missing})[:10]
        raise ValueError(
            f"{len(missing)} holdout event(s) carry a blank or non-canonical "
            f"sector, e.g. {sample}. Repair before spending the holdout -- "
            "this is the one read that cannot be redone."
        )

    kept = frame.iloc[trainable].reset_index(drop=True)
    before = len(kept)
    kept = kept.dropna(subset=list(feat.LABEL_COLS)).reset_index(drop=True)
    kept = feat._coerce_boolean_features(kept)
    kept = feat._add_derived(kept)
    return kept, feat.FrameReport(
        rows=len(kept),
        dropped_etf=len(etf),
        dropped_missing_sector=len(missing),
        dropped_no_label=before - len(kept),
    )


def holdout_window(engine: Engine, config_hash: str) -> tuple[date | None, date | None, int]:
    """First date, last date and event count of the holdout, without reading
    a single label.

    Safe to call before deciding: it reads the *shape* of the split, which
    is already public in DESIGN, not the outcomes that spending it consumes.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                # **Filtered to what will actually be evaluated**, not the
                # raw split. `in_trade` and `next_open` are the same
                # predicates `build_training_frame` applies, so this count
                # is the one a reader should compare against the result --
                # the unfiltered number is 5x larger and means nothing.
                # It also keeps this inside the invariant that every
                # production read of `events` filters `in_trade`.
                "SELECT min(signal_date), max(signal_date), count(*) "
                "FROM events WHERE config_hash = :chash AND split_key = 'holdout' "
                "AND in_trade AND entry_kind = 'next_open'"
            ),
            {"chash": config_hash},
        ).fetchone()
    if row is None:
        return None, None, 0
    return row[0], row[1], int(row[2])


def summarise(evaluations: Sequence) -> pd.DataFrame:
    """One row per head, in `train.all_heads()` order.

    Reports the same columns the validate gate reports, so the two tables
    can be read side by side -- which is the only comparison that matters
    here, and the one the reader will make whether or not it is offered.
    """
    return pd.DataFrame(
        [
            {
                "head": e.head,
                "n_holdout": e.n_validate,
                "model_loss": round(e.model_loss, 6),
                "baseline": round(e.baseline_global, 6),
                "improve_pct": round(e.improvement * 100, 3),
                "beats_global": e.beats_global,
                "coverage": round(e.coverage, 4),
                "cov_err": round(e.coverage_error, 4),
                "cov_ok": e.coverage_ok,
            }
            for e in evaluations
        ]
    )
