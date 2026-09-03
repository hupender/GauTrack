"""Photo intake and access control.

Photos never sit in a public directory.  They are written to PHOTO_DIR under a
server-chosen path and can only be read back through GET /api/photos/{id},
which re-derives the caller's right to see them from the entity that references
the photo.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from authz import Scope
from config import settings
from ids import uuid7
from models import Photo, Role

# Magic bytes, not the Content-Type header and not the filename: the client does
# not get to tell us what kind of file this is.
_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
)


def sniff(data: bytes) -> tuple[str, str] | None:
    for prefix, mime, ext in _MAGIC:
        if data.startswith(prefix):
            return mime, ext
    return None


def photo_root() -> Path:
    p = Path(settings.photo_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def store_photo(
    db: Session,
    data: bytes,
    *,
    uploaded_by: uuid.UUID | None,
    client_sha256: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    taken_at: dt.datetime | None = None,
) -> Photo:
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > settings.max_photo_bytes:
        raise HTTPException(status_code=400, detail="photo exceeds 5MB limit")

    sniffed = sniff(data)
    if sniffed is None:
        raise HTTPException(status_code=400, detail="not a JPEG or PNG image")
    mime, ext = sniffed

    digest = hashlib.sha256(data).hexdigest()
    if client_sha256:
        # SPEC §1.7: the device sends its own hash; a mismatch means the bytes
        # changed in transit (or someone is substituting evidence) -> reject.
        if client_sha256.strip().lower() != digest:
            raise HTTPException(status_code=400, detail="photo hash mismatch")

    root = photo_root()
    rel = Path(digest[:2]) / digest[2:4] / f"{digest}{ext}"
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dest)

    photo = Photo(
        id=uuid7(),
        sha256=digest,
        mime=mime,
        bytes=len(data),
        path=str(rel),
        taken_at=taken_at,
        lat=lat,
        lng=lng,
        uploaded_by=uploaded_by,
    )
    db.add(photo)
    db.flush()
    return photo


_ACCESS_SQL = text(
    """
    SELECT 1 WHERE EXISTS (
        SELECT 1 FROM owners o
          WHERE o.photo_id = :pid
            AND (:all_ulbs OR o.ulb_id = ANY(:ulbs))
        UNION ALL
        SELECT 1 FROM animals a
          WHERE (a.photo_id = :pid OR a.muzzle_photo_id = :pid)
            AND (:all_ulbs OR a.ulb_id = ANY(:ulbs))
        UNION ALL
        SELECT 1 FROM events e
          WHERE :pid = ANY(e.photo_ids)
            AND (:all_ulbs OR e.ulb_id = ANY(:ulbs))
    )
    """
)


def may_read_photo(db: Session, photo: Photo, scope: Scope) -> bool:
    """A photo is visible only through an in-scope entity that references it."""
    if scope.role is Role.viewer:
        return False  # SPEC §3: viewer role gets aggregates, never imagery
    if photo.uploaded_by is not None and photo.uploaded_by == scope.user_id:
        return True
    # scoped query — prevents IDOR on the photo id itself
    row = db.execute(
        _ACCESS_SQL,
        {
            "pid": str(photo.id),
            "all_ulbs": scope.ulb_ids is None,
            "ulbs": list(scope.ulb_ids or []),
        },
    ).first()
    return row is not None


def read_bytes(photo: Photo) -> bytes:
    # `photo.path` is server-generated (a hex digest), never user input, so it
    # cannot traverse out of PHOTO_DIR; assert it anyway.
    root = photo_root().resolve()
    full = (root / photo.path).resolve()
    if not str(full).startswith(str(root) + os.sep):
        raise HTTPException(status_code=500, detail="bad photo path")
    if not full.exists():
        raise HTTPException(status_code=404, detail="photo file missing")
    return full.read_bytes()
