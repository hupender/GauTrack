from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

import photos as photo_svc
from auth import Principal, get_principal
from config import settings
from db import get_db
from models import Photo, Role
from schemas import PhotoOut

router = APIRouter(prefix="/api/photos", tags=["photos"])


@router.post("", response_model=PhotoOut, status_code=201)
async def upload_photo(
    file: UploadFile = File(...),
    sha256: str | None = Form(default=None),
    lat: float | None = Form(default=None),
    lng: float | None = Form(default=None),
    taken_at: dt.datetime | None = Form(default=None),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    if principal.scope.role is Role.viewer:
        raise HTTPException(status_code=403, detail="role may not upload")
    # Read with a hard cap so a huge upload cannot exhaust memory.
    data = await file.read(settings.max_photo_bytes + 1)
    photo = photo_svc.store_photo(
        db,
        data,
        uploaded_by=principal.scope.user_id,
        client_sha256=sha256,
        lat=lat,
        lng=lng,
        taken_at=taken_at,
    )
    out = PhotoOut(id=photo.id, sha256=photo.sha256, bytes=photo.bytes, mime=photo.mime)
    db.commit()
    return out


@router.get("/{photo_id}")
def get_photo(photo_id: uuid.UUID, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    photo = db.get(Photo, photo_id)
    if photo is None or not photo_svc.may_read_photo(db, photo, principal.scope):
        # 404 either way: existence of a photo id is itself information.
        raise HTTPException(status_code=404, detail="not found")
    data = photo_svc.read_bytes(photo)
    db.commit()
    return Response(
        content=data,
        media_type=photo.mime,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
