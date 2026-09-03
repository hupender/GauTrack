from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

import sync as svc
from auth import Principal, get_principal
from authz import apply_ulb_scope, require_entity_read, require_write
from config import settings
from db import get_db
from models import Event, EventType
from routes._common import event_out
from schemas import EventIn, EventOut, SyncIn, SyncOut

router = APIRouter(prefix="/api", tags=["events"])


@router.post("/events", response_model=EventOut, status_code=201)
def create_event(payload: EventIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    require_write(principal.scope)
    try:
        event = svc.apply_event(db, principal.scope, payload, user_id=principal.scope.user_id)
    except svc.Conflict as exc:
        raise HTTPException(status_code=409, detail={"reason": exc.reason, "existing": exc.existing}) from exc
    except svc.Rejected as exc:
        raise HTTPException(status_code=400, detail=exc.reason) from exc
    out = event_out(db, event)
    db.commit()
    return out


@router.post("/sync", response_model=SyncOut)
def sync_batch(payload: SyncIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Idempotent batch upload from the field PWA.

    Re-sending a batch is safe: every item is keyed by the client-generated id,
    so a device that never saw the ack can retry forever without duplicating.
    """
    require_write(principal.scope)
    if len(payload.items) > settings.max_sync_items:
        raise HTTPException(status_code=413, detail=f"at most {settings.max_sync_items} items per batch")

    svc.register_device(db, principal.scope, payload.device_id, payload.device_label, principal.ip)
    results = svc.process_batch(db, principal.scope, payload.items, user_id=principal.scope.user_id)
    db.commit()
    return SyncOut(results=results, server_time=dt.datetime.now(dt.timezone.utc))


@router.get("/events", response_model=dict)
def list_events(
    type: EventType | None = None,
    ulb: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    require_entity_read(principal.scope)
    stmt = apply_ulb_scope(select(Event), Event.ulb_id, principal.scope)
    if type is not None:
        stmt = stmt.where(Event.type == type)
    if ulb is not None:
        stmt = stmt.where(Event.ulb_id == ulb)
    rows = db.execute(stmt.order_by(Event.seq.desc()).limit(limit).offset(offset)).scalars().all()
    out = {"items": [event_out(db, e).model_dump(mode="json") for e in rows]}
    db.commit()
    return out
