from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

import sync as svc
from auth import Principal, client_ip, get_principal
from authz import apply_ulb_scope, not_found, require_entity_read, require_write
from db import get_db
from ids import uuid7
from models import Animal, AnimalStatus, Event, EventType, Fine, LookupLog, Owner
from routes._common import animal_out, event_out, owner_out, ulb_codes
from schemas import AnimalIn, AnimalOut, AnimalPatch, EventOut, LookupOut

router = APIRouter(prefix="/api/animals", tags=["animals"])
lookup_router = APIRouter(prefix="/api/lookup", tags=["lookup"])


def _get_scoped_animal(db: Session, animal_id: uuid.UUID, principal: Principal) -> Animal:
    # scoped query — prevents IDOR
    stmt = apply_ulb_scope(select(Animal).where(Animal.id == animal_id), Animal.ulb_id, principal.scope)
    animal = db.execute(stmt).scalar_one_or_none()
    if animal is None:
        raise not_found()
    return animal


@router.get("", response_model=dict)
def list_animals(
    q: str | None = Query(default=None, max_length=60),
    ulb: int | None = None,
    status: AnimalStatus | None = None,
    tagged: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    require_entity_read(principal.scope)
    stmt = apply_ulb_scope(select(Animal), Animal.ulb_id, principal.scope)
    if ulb is not None:
        stmt = stmt.where(Animal.ulb_id == ulb)
    if status is not None:
        stmt = stmt.where(Animal.status == status)
    if tagged is True:
        stmt = stmt.where(Animal.tag_id.is_not(None))
    elif tagged is False:
        stmt = stmt.where(Animal.tag_id.is_(None))
    if q:
        stmt = stmt.where(Animal.tag_id.ilike(f"%{q.strip()}%"))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Animal.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    codes = ulb_codes(db)
    out = {
        "total": int(total),
        "items": [animal_out(db, a, principal.scope, codes=codes).model_dump(mode="json") for a in rows],
    }
    db.commit()
    return out


@router.post("", response_model=AnimalOut, status_code=201)
def create_animal(payload: AnimalIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    require_write(principal.scope)
    try:
        animal = svc.create_animal(db, principal.scope, payload)
    except svc.Conflict as exc:
        raise HTTPException(status_code=409, detail={"reason": exc.reason, "existing": exc.existing}) from exc
    except svc.Rejected as exc:
        raise HTTPException(status_code=400, detail=exc.reason) from exc

    db.add(
        Event(
            id=uuid7(),
            type=EventType.tagging if animal.tag_id else EventType.registration,
            animal_id=animal.id,
            owner_id=animal.owner_id,
            ulb_id=animal.ulb_id,
            user_id=principal.scope.user_id,
            lat=animal.lat,
            lng=animal.lng,
            occurred_at=svc.utcnow(),
            payload={"kind": "animal", "tag_id": animal.tag_id},
            photo_ids=[p for p in [animal.photo_id, animal.muzzle_photo_id] if p],
        )
    )
    out = animal_out(db, animal, principal.scope)
    db.commit()
    return out


@router.get("/{animal_id}", response_model=AnimalOut)
def get_animal(animal_id: uuid.UUID, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    require_entity_read(principal.scope)
    animal = _get_scoped_animal(db, animal_id, principal)
    out = animal_out(db, animal, principal.scope)
    db.commit()
    return out


@router.patch("/{animal_id}", response_model=AnimalOut)
def patch_animal(
    animal_id: uuid.UUID,
    payload: AnimalPatch,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    require_write(principal.scope)
    animal = _get_scoped_animal(db, animal_id, principal)

    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    reason = changes.pop("reason", None)

    if "tag_id" in changes and changes["tag_id"] != animal.tag_id:
        clash = db.execute(select(Animal).where(Animal.tag_id == changes["tag_id"])).scalar_one_or_none()
        if clash is not None and clash.id != animal.id:
            raise HTTPException(
                status_code=409,
                detail={"reason": "tag already registered", "existing": svc.animal_summary(clash, db)},
            )
    if "owner_id" in changes:
        # scoped query — prevents IDOR: cannot reassign to an owner you can't see
        stmt = apply_ulb_scope(
            select(Owner).where(Owner.id == changes["owner_id"]), Owner.ulb_id, principal.scope
        )
        if db.execute(stmt).scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail="owner not found in your scope")

    before = {}
    for field, value in changes.items():
        current = getattr(animal, field)
        before[field] = current.value if hasattr(current, "value") else str(current) if current is not None else None
        setattr(animal, field, value)
    animal.updated_at = svc.utcnow()

    db.add(
        Event(
            id=uuid7(),
            type=EventType.correction,
            animal_id=animal.id,
            owner_id=animal.owner_id,
            ulb_id=animal.ulb_id,
            user_id=principal.scope.user_id,
            occurred_at=svc.utcnow(),
            payload={
                "kind": "animal",
                "before": before,
                "after": {k: (v.value if hasattr(v, "value") else str(v)) for k, v in changes.items()},
                "reason": reason,
            },
        )
    )
    out = animal_out(db, animal, principal.scope)
    db.commit()
    return out


@router.get("/{animal_id}/events", response_model=list[EventOut])
def animal_events(
    animal_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    require_entity_read(principal.scope)
    animal = _get_scoped_animal(db, animal_id, principal)
    rows = db.execute(
        select(Event).where(Event.animal_id == animal.id).order_by(Event.occurred_at.desc()).limit(limit)
    ).scalars().all()
    out = [event_out(db, e) for e in rows]
    db.commit()
    return out


# --------------------------------------------------------------------------- lookup
LOOKUPS_PER_HOUR = 120  # council R1 §D4: the district-wide oracle must not be scrapable


@lookup_router.get("/tag/{tag_id}", response_model=LookupOut)
def lookup_tag(
    tag_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """District-wide on purpose (SPEC §1.5): a Bawal cow will be found in Rewari.

    The animal record is returned regardless of ULB; the owner's phone is masked and the
    owner's name reduced to initials unless the caller's scope covers that ULB.  Every
    lookup (hit or miss) is written to `lookup_log`, which is append-only and hash-chained,
    and each user is limited to LOOKUPS_PER_HOUR.
    """
    require_entity_read(principal.scope)
    tag = (tag_id or "").strip().upper()
    if len(tag) < 3:
        raise not_found()

    recent_n = db.execute(
        text("SELECT count(*) FROM lookup_log WHERE user_id = :u AND ts > now() - interval '1 hour'"),
        {"u": str(principal.user.id)},
    ).scalar_one()
    if int(recent_n) >= LOOKUPS_PER_HOUR:
        raise HTTPException(status_code=429, detail="lookup rate limit reached; try again later")

    animal = db.execute(select(Animal).where(Animal.tag_id == tag)).scalar_one_or_none()
    scope = principal.scope
    in_scope = bool(animal) and (scope.ulb_ids is None or animal.ulb_id in scope.ulb_ids)
    db.add(LookupLog(
        user_id=principal.user.id, tag_id=tag,
        animal_id=animal.id if animal else None, in_scope=in_scope if animal else None,
        ip=client_ip(request),
    ))
    if animal is None:
        db.commit()  # the miss is logged too
        raise not_found()
    owner = db.get(Owner, animal.owner_id) if animal.owner_id else None
    offences = db.execute(
        select(func.count()).select_from(Fine).where(Fine.owner_id == animal.owner_id)
    ).scalar_one() if animal.owner_id else 0
    recent = db.execute(
        select(Event).where(Event.animal_id == animal.id).order_by(Event.occurred_at.desc()).limit(5)
    ).scalars().all()

    out = LookupOut(
        animal=animal_out(db, animal, scope),
        owner=owner_out(db, owner, scope) if owner else None,
        offence_count=int(offences),
        recent_events=[event_out(db, e) for e in recent],
        in_scope=in_scope,
    )
    db.commit()
    return out
