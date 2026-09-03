from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

import sync as svc
from auth import Principal, get_principal
from authz import apply_ulb_scope, not_found, require_entity_read, require_write
from db import get_db
from ids import uuid7
from models import Animal, Event, EventType, Owner
from routes._common import animal_out, owner_out, ulb_codes
from schemas import AnimalOut, MergeIn, OwnerCreateOut, OwnerIn, OwnerOut, OwnerPatch, normalize_phone

router = APIRouter(prefix="/api/owners", tags=["owners"])


def _get_scoped_owner(db: Session, owner_id: uuid.UUID, principal: Principal) -> Owner:
    # scoped query — prevents IDOR: the ULB predicate is part of the SELECT, so
    # an id from another ULB simply returns no row (404, never 403).
    stmt = apply_ulb_scope(select(Owner).where(Owner.id == owner_id), Owner.ulb_id, principal.scope)
    owner = db.execute(stmt).scalar_one_or_none()
    if owner is None:
        raise not_found()
    return owner


@router.get("", response_model=dict)
def list_owners(
    q: str | None = Query(default=None, max_length=80),
    ulb: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    require_entity_read(principal.scope)
    stmt = select(Owner).where(Owner.merged_into.is_(None))
    stmt = apply_ulb_scope(stmt, Owner.ulb_id, principal.scope)
    if ulb is not None:
        stmt = stmt.where(Owner.ulb_id == ulb)
    if q:
        phone = normalize_phone(q)
        needle = f"%{q.strip()}%"
        conds = [Owner.name.ilike(needle), Owner.ward_or_village.ilike(needle)]
        if phone:
            conds.append(Owner.phone_norm == phone)
        stmt = stmt.where(or_(*conds))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Owner.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    codes = ulb_codes(db)
    db.commit()
    return {
        "total": int(total),
        "items": [owner_out(db, o, principal.scope, codes=codes).model_dump(mode="json") for o in rows],
    }


@router.post("", response_model=OwnerCreateOut, status_code=201)
def create_owner(
    payload: OwnerIn,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    require_write(principal.scope)
    try:
        owner, dups = svc.create_owner(db, principal.scope, payload)
    except svc.Conflict as exc:
        raise HTTPException(status_code=409, detail={"reason": exc.reason, "existing": exc.existing}) from exc
    except svc.Rejected as exc:
        raise HTTPException(status_code=400, detail=exc.reason) from exc

    db.add(
        Event(
            id=uuid7(),
            type=EventType.registration,
            owner_id=owner.id,
            ulb_id=owner.ulb_id,
            user_id=principal.scope.user_id,
            lat=owner.lat,
            lng=owner.lng,
            gps_accuracy_m=owner.gps_accuracy_m,
            occurred_at=svc.utcnow(),
            payload={"kind": "owner"},
        )
    )
    out = OwnerCreateOut(owner=owner_out(db, owner, principal.scope), possible_duplicates=dups)
    db.commit()
    return out


@router.get("/{owner_id}", response_model=OwnerOut)
def get_owner(owner_id: uuid.UUID, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    require_entity_read(principal.scope)
    owner = _get_scoped_owner(db, owner_id, principal)
    out = owner_out(db, owner, principal.scope)
    db.commit()
    return out


@router.patch("/{owner_id}", response_model=OwnerOut)
def patch_owner(
    owner_id: uuid.UUID,
    payload: OwnerPatch,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    require_write(principal.scope)
    owner = _get_scoped_owner(db, owner_id, principal)

    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    reason = changes.pop("reason", None)
    before = {}
    for field, value in changes.items():
        if field == "phone":
            before["phone_norm"] = owner.phone_norm
            owner.phone_norm = normalize_phone(value)
            owner.phone_hash = svc.phone_hash(owner.phone_norm)
        else:
            current = getattr(owner, field)
            before[field] = current.value if hasattr(current, "value") else current
            setattr(owner, field, value)
    owner.updated_at = svc.utcnow()

    # A correction is recorded as an event as well as an audit row (SPEC §1.9).
    db.add(
        Event(
            id=uuid7(),
            type=EventType.correction,
            owner_id=owner.id,
            ulb_id=owner.ulb_id,
            user_id=principal.scope.user_id,
            occurred_at=svc.utcnow(),
            payload={"kind": "owner", "before": before, "after": changes, "reason": reason},
        )
    )
    out = owner_out(db, owner, principal.scope)
    db.commit()
    return out


@router.get("/{owner_id}/animals", response_model=list[AnimalOut])
def owner_animals(owner_id: uuid.UUID, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    require_entity_read(principal.scope)
    owner = _get_scoped_owner(db, owner_id, principal)
    rows = db.execute(
        select(Animal).where(Animal.owner_id == owner.id).order_by(Animal.created_at.desc())
    ).scalars().all()
    codes = ulb_codes(db)
    out = [animal_out(db, a, principal.scope, codes=codes) for a in rows]
    db.commit()
    return out


@router.post("/{owner_id}/merge", response_model=OwnerOut)
def merge_owner(
    owner_id: uuid.UUID,
    payload: MergeIn,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """Merge `source_id` INTO `owner_id`.  super_admin only (SPEC §1.9)."""
    if not principal.scope.can_merge_owners:
        raise HTTPException(status_code=403, detail="only super_admin may merge owners")

    target = db.get(Owner, owner_id)
    source = db.get(Owner, payload.source_id)
    if target is None or source is None:
        raise not_found()
    if target.id == source.id:
        raise HTTPException(status_code=400, detail="cannot merge an owner into itself")
    if source.merged_into is not None:
        raise HTTPException(status_code=409, detail="source owner is already merged")

    moved_animals = db.execute(select(Animal).where(Animal.owner_id == source.id)).scalars().all()
    for animal in moved_animals:
        animal.owner_id = target.id

    # Past events are NOT rewritten to point at the target: `events` is
    # append-only, and the merge event below is what ties the two histories
    # together. Readers follow `owners.merged_into` forward.
    source.merged_into = target.id
    source.updated_at = svc.utcnow()
    target.updated_at = svc.utcnow()

    db.add(
        Event(
            id=uuid7(),
            type=EventType.owner_merge,
            owner_id=target.id,
            ulb_id=target.ulb_id,
            user_id=principal.scope.user_id,
            occurred_at=svc.utcnow(),
            payload={
                "merged_owner_id": str(source.id),
                "merged_owner_name": source.name,
                "animals_moved": len(moved_animals),
                "reason": payload.reason,
            },
        )
    )
    out = owner_out(db, target, principal.scope)
    db.commit()
    return out
