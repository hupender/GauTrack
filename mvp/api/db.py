"""Database engine + session plumbing.

Two engines exist on purpose:

* ``engine``        — the *app* role.  No UPDATE/DELETE on ``events``, only
                      SELECT on ``audit_log``.  This is what the web process uses.
* ``owner_engine``  — the schema owner.  Migrations, seeding and the chain
                      verifier only.  Never reachable from a request handler.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid as _uuid
from decimal import Decimal
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from config import settings


def _json_default(value):
    """Widen what may be written into a JSONB column.

    Event payloads carry before/after snapshots of table rows, so whatever type
    a column has ends up here.  ``json.dumps`` refuses Decimal, UUID and
    datetime out of the box, which would turn a perfectly ordinary correction
    (``age_years: 4.5``) into a 500.  Decimals become floats because a JSON
    document has no decimal type and these are survey figures, not money —
    ``fines.amount`` is Numeric in its own column and never round-trips here.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def _json_serializer(obj) -> str:
    return json.dumps(obj, default=_json_default)


_connect = settings.db_connect_args()

engine = create_engine(
    settings.app_database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
    json_serializer=_json_serializer,
    connect_args=_connect,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def owner_engine():
    """Lazily-built engine for the schema owner (admin tooling only)."""
    return create_engine(
        settings.owner_database_url,
        pool_pre_ping=True,
        future=True,
        json_serializer=_json_serializer,
        connect_args=settings.db_connect_args(),
    )


def set_db_actor(db: Session, user_id, ip: str | None) -> None:
    """Publish the acting user to Postgres for the audit triggers.

    ``set_config(..., true)`` is the parameterised form of ``SET LOCAL`` — it is
    scoped to the current transaction, so a pooled connection can never leak one
    request's identity into the next.  Parameterised, so a spoofed value cannot
    inject SQL.
    """
    db.execute(
        text("SELECT set_config('app.user_id', :u, true), set_config('app.ip', :i, true)"),
        {"u": str(user_id) if user_id else "", "i": (ip or "")[:64]},
    )


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one Session (and one transaction) per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
