"""Personal trade log and order intents (ADR 048, ADR 073, BUILD §8.4-8.5).

`positions` is the user-declared trade log; `order_intents` is the
execution-ready seam ADR 048 requires: a structured recommendation with an
idempotency key, rendered as a notification today and nothing more. **No
broker client, no credentials, no code path capable of placing an order
exists anywhere in this module or the repo** — that absence is the safety
property (invariant 7), not a disabled flag.
"""

from __future__ import annotations

import hashlib
from datetime import date

import pandas as pd
from sqlalchemy import Engine, text

from capitalscan.core.types import Side
from capitalscan.jobs import db_io


def open_position(
    engine: Engine,
    ticker: str,
    side: str,
    entry_date: date,
    entry_price: float,
    quantity: float | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Insert one `positions` row, `source='user_declared'` (ADR 048 point 2).

    `idempotency_key` is optional here (unlike `order_intents`, where it's
    required) — a user declaring a trade by hand has no natural retry to
    dedupe against. When given, an upsert on it makes a repeated call safe.
    """
    row = {
        "ticker": ticker,
        "side": side,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "quantity": quantity,
        "source": "user_declared",
        "status": "open",
        "idempotency_key": idempotency_key,
    }
    if idempotency_key:
        db_io.upsert(engine, "positions", [row], ["idempotency_key"])
    else:
        db_io.append(engine, "positions", [row])
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT * FROM positions WHERE ticker = :ticker AND entry_date = :entry_date "
                "AND entry_price = :entry_price ORDER BY id DESC LIMIT 1"
            ),
            {"ticker": ticker, "entry_date": entry_date, "entry_price": entry_price},
        ).mappings().one()
    return dict(result)


def close_position(
    engine: Engine,
    position_id: int,
    exit_date: date,
    exit_price: float,
    exit_reason: str,
) -> dict:
    """Close an open position and compute `realized_ret`.

    Long: `exit/entry - 1`. Short: `1 - exit/entry` (a short profits when
    price falls, so the sign flips relative to the long case).
    """
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM positions WHERE id = :id"), {"id": position_id}
        ).mappings().one_or_none()
    if row is None:
        raise ValueError(f"no position with id {position_id}")

    entry_price = float(row["entry_price"])
    if row["side"] == Side.SHORT.value:
        realized_ret = 1.0 - (exit_price / entry_price)
    else:
        realized_ret = (exit_price / entry_price) - 1.0

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE positions SET status = 'closed', exit_date = :exit_date, "
                "exit_price = :exit_price, exit_reason = :exit_reason, "
                "realized_ret = :realized_ret WHERE id = :id"
            ),
            {
                "exit_date": exit_date,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "realized_ret": realized_ret,
                "id": position_id,
            },
        )
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT * FROM positions WHERE id = :id"), {"id": position_id}
        ).mappings().one()
    return dict(result)


def list_positions(engine: Engine, status: str | None = None) -> pd.DataFrame:
    query = "SELECT * FROM positions"
    params: dict = {}
    if status:
        query += " WHERE status = :status"
        params["status"] = status
    query += " ORDER BY entry_date DESC"
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


def _order_intent_key(ticker: str, signal_date: date, signal_type: str, side: str) -> str:
    """Deterministic hash of (ticker, date, signal_type, side) — ADR 048
    point 3, "prevents duplicate orders on retry". No broker exists to
    retry against yet, but the poller re-running the same tick must not
    duplicate the intent row either.
    """
    digest = hashlib.sha256(f"{ticker}|{signal_date}|{signal_type}|{side}".encode()).hexdigest()
    return digest[:16]


def emit_order_intent(
    engine: Engine,
    event_id: int | None,
    ticker: str,
    signal_date: date,
    signal_type: str,
    side: str,
    limit_level: float | None,
    stop_level: float | None,
    quantity_basis: str = "user_defined",
    time_in_force: str = "day",
    run_id: str | None = None,
    git_sha: str | None = None,
) -> dict:
    """Renders as a notification, serializes to a broker adapter later
    (ADR 048) — today it only ever reaches `order_intents`.
    """
    key = _order_intent_key(ticker, signal_date, signal_type, side)
    row = {
        "event_id": event_id,
        "ticker": ticker,
        "side": side,
        "quantity_basis": quantity_basis,
        "limit_level": limit_level,
        "stop_level": stop_level,
        "time_in_force": time_in_force,
        "idempotency_key": key,
        "run_id": run_id,
        "git_sha": git_sha,
    }
    db_io.upsert(engine, "order_intents", [row], ["idempotency_key"])
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT * FROM order_intents WHERE idempotency_key = :key"), {"key": key}
        ).mappings().one()
    return dict(result)
