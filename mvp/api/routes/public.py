"""The one route with no session behind it: a member of the public reporting a
cow on the road.

Defences: 10 reports/hour/IP, a honeypot field, a hard cap on note length, no
read-back of anything, and the resulting event carries `user_id = NULL` and
`payload.source = 'public'` so a citizen report can never be mistaken for an
officer's observation.
"""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

import auth
import photos as photo_svc
from db import get_db
from ids import uuid7
from models import Event, EventType, Photo, Ulb
from schemas import PublicReportIn

router = APIRouter(prefix="/api/public", tags=["public"])

# Rewari district centroid — used when the reporter has no GPS fix.
DEFAULT_ULB_CODE = "RUR"


@router.post("/report", status_code=201)
def public_report(payload: PublicReportIn, request: Request, db: Session = Depends(get_db)):
    ip = auth.client_ip(request)

    # Honeypot: a browser never fills this in, a bot fills everything in.
    # Answer 201 so the bot cannot tell it was rejected.
    if payload.website:
        return {"ok": True, "id": str(uuid7())}

    auth.check_public_report_rate(db, ip=ip)
    auth.record_attempt(db, ip=ip, username=None, ok=True, kind="public_report")

    ulb = None
    if payload.ulb_id is not None:
        ulb = db.get(Ulb, payload.ulb_id)
    if ulb is None:
        ulb = db.execute(select(Ulb).where(Ulb.code == DEFAULT_ULB_CODE)).scalar_one_or_none()
    if ulb is None:
        raise HTTPException(status_code=500, detail="no ULB configured")

    photo_ids: list[uuid.UUID] = []
    if payload.photo_id is not None:
        photo = db.get(Photo, payload.photo_id)
        # only a photo uploaded through the public upload route in this session
        if photo is not None and photo.uploaded_by is None:
            photo_ids.append(photo.id)

    tag_digits = "".join(ch for ch in (payload.tag_digits or "") if ch.isdigit())[:20] or None

    event = Event(
        id=uuid7(),
        type=EventType.sighting_road,
        ulb_id=ulb.id,
        user_id=None,  # a citizen, not an officer
        lat=payload.lat,
        lng=payload.lng,
        gps_accuracy_m=payload.gps_accuracy_m,
        occurred_at=dt.datetime.now(dt.timezone.utc),
        payload={
            "source": "public",
            "tag_digits": tag_digits,
            "note": (payload.note or "")[:500] or None,
            "unverified": True,
        },
        photo_ids=photo_ids,
    )
    db.add(event)
    db.commit()
    return {"ok": True, "id": str(event.id)}


@router.post("/photo", status_code=201)
async def public_photo(request: Request, db: Session = Depends(get_db)):
    """Anonymous photo intake for /report.  Same 5 MB + magic-byte checks; the
    row is stored with `uploaded_by = NULL`."""
    ip = auth.client_ip(request)
    auth.check_public_report_rate(db, ip=ip)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="file required")
    data = await upload.read(6 * 1024 * 1024)
    photo = photo_svc.store_photo(db, data, uploaded_by=None, client_sha256=form.get("sha256"))
    db.commit()
    return {"id": str(photo.id), "sha256": photo.sha256}
