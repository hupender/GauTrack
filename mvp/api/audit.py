"""Tamper-evident audit: chain verification and audit-log reads.

The hashes are recomputed *inside Postgres* using the very same
``gt_audit_payload`` / ``gt_event_payload`` functions the triggers used to write
them.  Reimplementing the serialisation in Python would risk a false BROKEN the
first time a JSON key order or timestamp format differed.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

ZERO = "0" * 64

AUDIT_VERIFY_SQL = text(
    """
    WITH r AS (
        SELECT id, prev_hash, hash, ts, table_name, action, row_id,
               encode(sha256(convert_to(prev_hash ||
                   gt_audit_payload(id, ts, actor_user_id, ip, action, table_name,
                                    row_id, before, after), 'UTF8')), 'hex') AS calc,
               lag(hash) OVER (ORDER BY id) AS prior
          FROM audit_log
    )
    SELECT id, table_name, action, row_id, hash, calc, prev_hash, prior,
           (calc <> hash) AS content_broken,
           (prev_hash IS DISTINCT FROM coalesce(prior, repeat('0',64))) AS link_broken
      FROM r
     WHERE calc <> hash
        OR prev_hash IS DISTINCT FROM coalesce(prior, repeat('0',64))
     ORDER BY id
     LIMIT 5
    """
)

EVENT_VERIFY_SQL = text(
    """
    WITH r AS (
        SELECT seq, id, prev_hash, hash, type,
               encode(sha256(convert_to(prev_hash ||
                   gt_event_payload(id, seq, type::text, animal_id, owner_id, ulb_id,
                                    user_id, device_id, occurred_at, payload, photo_ids),
                   'UTF8')), 'hex') AS calc,
               lag(hash) OVER (ORDER BY seq) AS prior
          FROM events
    )
    SELECT seq, id, type::text AS type, hash, calc, prev_hash, prior,
           (calc <> hash) AS content_broken,
           (prev_hash IS DISTINCT FROM coalesce(prior, repeat('0',64))) AS link_broken
      FROM r
     WHERE calc <> hash
        OR prev_hash IS DISTINCT FROM coalesce(prior, repeat('0',64))
     ORDER BY seq
     LIMIT 5
    """
)


def _verify(conn: Connection | Session, sql, count_sql: str, tip_sql: str, key: str) -> dict[str, Any]:
    total = int(conn.execute(text(count_sql)).scalar_one())
    bad = conn.execute(sql).mappings().all()
    tip = conn.execute(text(tip_sql)).scalar_one_or_none()
    return {
        "rows": total,
        "ok": len(bad) == 0,
        "tip_hash": tip or ZERO,
        "first_broken": (dict(bad[0]) if bad else None),
        "broken_sample": [dict(b) for b in bad],
        "key": key,
    }


def verify_audit_chain(conn: Connection | Session) -> dict[str, Any]:
    return _verify(
        conn,
        AUDIT_VERIFY_SQL,
        "SELECT count(*) FROM audit_log",
        "SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1",
        "id",
    )


def verify_event_chain(conn: Connection | Session) -> dict[str, Any]:
    return _verify(
        conn,
        EVENT_VERIFY_SQL,
        "SELECT count(*) FROM events",
        "SELECT hash FROM events ORDER BY seq DESC LIMIT 1",
        "seq",
    )


def verify_all(conn: Connection | Session) -> dict[str, Any]:
    audit = verify_audit_chain(conn)
    events = verify_event_chain(conn)
    return {"ok": audit["ok"] and events["ok"], "audit_log": audit, "events": events}


def list_audit(db: Session, *, table: str | None, row_id: str | None, limit: int = 100, offset: int = 0):
    rows = db.execute(
        text(
            """
            SELECT id, ts, actor_user_id, ip, action, table_name, row_id, before, after, hash
              FROM audit_log
             WHERE (CAST(:table AS text) IS NULL OR table_name = :table)
               AND (CAST(:row_id AS text) IS NULL OR row_id = :row_id)
             ORDER BY id DESC
             LIMIT :limit OFFSET :offset
            """
        ),
        {"table": table, "row_id": row_id, "limit": max(1, min(limit, 500)), "offset": max(0, offset)},
    ).mappings().all()
    return [dict(r) for r in rows]
