"""Registry writes: owners, animals, events, and the idempotent offline batch.

Everything a field device can create funnels through here, so the conflict rules
(SPEC §1.9) and the event side-effects (SPEC §3) exist in exactly one place.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from authz import Scope
from config import settings
from ids import uuid7
from models import (
    Animal,
    AnimalStatus,
    Device,
    Event,
    EventType,
    Fine,
    FineSchedule,
    FineStatus,
    Owner,
    Shelter,
    TagType,
)
from schemas import AnimalIn, EventIn, OwnerIn, SyncItem, SyncResult, normalize_phone

# Event types a field officer may record against an animal belonging to another
# ULB.  Operational reality: a Bawal cow gets impounded in Rewari.  Anything that
# *rewrites* the record (corrections, ownership changes) stays ULB-scoped.
CROSS_ULB_EVENT_TYPES = {
    EventType.sighting_road,
    EventType.impound,
    EventType.gaushala_intake,
    EventType.release,
    EventType.fine_issued,
    EventType.fine_paid,
    EventType.tag_lost,
    EventType.tag_replaced,
    EventType.death,
    EventType.note,
}


class Rejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Conflict(Exception):
    def __init__(self, reason: str, existing: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.existing = existing


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def phone_hash(phone_norm: str | None) -> str | None:
    """Peppered hash so the same number can be matched across records without
    keeping a second plaintext copy lying around in an index."""
    if not phone_norm:
        return None
    return hashlib.sha256((settings.secret_key + "|" + phone_norm).encode()).hexdigest()


def resolve_ulb(scope: Scope, requested: int | None) -> int:
    """Non-district-wide roles are pinned to their own ULB regardless of what the
    client sends — the client does not get to choose its jurisdiction."""
    if scope.ulb_ids is None:
        if requested is None:
            raise Rejected("ulb_id is required for district-wide roles")
        return requested
    if not scope.ulb_ids:
        raise Rejected("this role has no ULB scope")
    own = scope.ulb_ids[0]
    if requested is not None and requested != own:
        raise Rejected("cannot write outside your ULB")
    return own


# --------------------------------------------------------------------------- owners
def find_possible_duplicates(db: Session, scope: Scope, data: OwnerIn, ulb_id: int) -> list[dict[str, Any]]:
    """SPEC §1.9: warn, never auto-merge."""
    probe = f"{data.name} {data.ward_or_village or ''}".strip()
    rows = db.execute(
        text(
            """
            SELECT id, name, ward_or_village, phone_norm, ulb_id,
                   similarity(name || ' ' || coalesce(ward_or_village,''), :probe) AS sim
              FROM owners
             WHERE merged_into IS NULL
               AND (:all_ulbs OR ulb_id = ANY(:ulbs))
               AND (
                     (CAST(:phone AS text) IS NOT NULL AND phone_norm = :phone)
                  OR similarity(name || ' ' || coalesce(ward_or_village,''), :probe) > 0.6
                   )
             ORDER BY sim DESC
             LIMIT 5
            """
        ),
        {
            "probe": probe,
            "phone": normalize_phone(data.phone),
            "all_ulbs": scope.ulb_ids is None,
            "ulbs": list(scope.ulb_ids or [ulb_id]),
        },
    ).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "ward_or_village": r["ward_or_village"],
            "ulb_id": r["ulb_id"],
            "match": "phone" if r["phone_norm"] and r["phone_norm"] == normalize_phone(data.phone) else "name",
            "similarity": round(float(r["sim"] or 0), 3),
        }
        for r in rows
    ]


def create_owner(db: Session, scope: Scope, data: OwnerIn) -> tuple[Owner, list[dict[str, Any]]]:
    ulb_id = resolve_ulb(scope, data.ulb_id)
    if not scope.may_write_ulb(ulb_id):
        raise Rejected("not permitted to create owners in this ULB")

    oid = data.id or uuid7()
    if db.get(Owner, oid) is not None:
        raise Conflict("duplicate id", {"id": str(oid)})

    dups = find_possible_duplicates(db, scope, data, ulb_id)
    pn = normalize_phone(data.phone)
    owner = Owner(
        id=oid,
        ulb_id=ulb_id,
        name=data.name.strip(),
        relation_name=(data.relation_name or "").strip() or None,
        phone_norm=pn,
        phone_hash=phone_hash(pn),
        address=data.address,
        ward_or_village=data.ward_or_village,
        keeper_type=data.keeper_type,
        id_type=data.id_type,
        id_last4=data.id_last4,
        self_declared_cattle_count=data.self_declared_cattle_count,
        premises_area_sq_yards=data.premises_area_sq_yards,
        lat=data.lat,
        lng=data.lng,
        gps_accuracy_m=data.gps_accuracy_m,
        photo_id=data.photo_id,
        notes=data.notes,
        created_by=scope.user_id,
    )
    db.add(owner)
    db.flush()
    return owner, dups


# --------------------------------------------------------------------------- animals
def animal_summary(a: Animal, db: Session) -> dict[str, Any]:
    owner = db.get(Owner, a.owner_id) if a.owner_id else None
    return {
        "id": str(a.id),
        "tag_id": a.tag_id,
        "species": a.species.value,
        "sex": a.sex.value,
        "age_class": a.age_class.value,
        "status": a.status.value,
        "ulb_id": a.ulb_id,
        "owner_id": str(a.owner_id) if a.owner_id else None,
        "owner_name": owner.name if owner else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def create_animal(db: Session, scope: Scope, data: AnimalIn) -> Animal:
    ulb_id = resolve_ulb(scope, data.ulb_id)
    if not scope.may_write_ulb(ulb_id):
        raise Rejected("not permitted to create animals in this ULB")

    try:
        tag = data.validated_tag()
    except ValueError as exc:
        raise Rejected(str(exc)) from exc

    aid = data.id or uuid7()
    if db.get(Animal, aid) is not None:
        raise Conflict("duplicate id", {"id": str(aid)})

    if data.owner_id is not None:
        # scoped query — prevents IDOR: you may only attach an animal to an owner
        # you are allowed to see.
        owner_stmt = select(Owner).where(Owner.id == data.owner_id)
        if scope.ulb_ids is not None:
            owner_stmt = owner_stmt.where(Owner.ulb_id.in_(scope.ulb_ids or [-1]))
        if db.execute(owner_stmt).scalar_one_or_none() is None:
            raise Rejected("owner not found in your scope")

    if tag:
        existing = db.execute(select(Animal).where(Animal.tag_id == tag)).scalar_one_or_none()
        if existing is not None:
            # SPEC §1.9: second registration of a live tag is a conflict, and the
            # client is told what the tag is already attached to.
            raise Conflict("tag already registered", animal_summary(existing, db))

    animal = Animal(
        id=aid,
        ulb_id=ulb_id,
        owner_id=data.owner_id,
        species=data.species,
        sex=data.sex,
        age_class=data.age_class,
        age_years=data.age_years,
        breed=data.breed,
        colour_markings=data.colour_markings,
        identification_mark_1=data.identification_mark_1,
        identification_mark_2=data.identification_mark_2,
        tag_id=tag,
        tag_type=data.tag_type,
        secondary_tag_id=data.secondary_tag_id,
        status=data.status or AnimalStatus.registered,
        current_shelter_id=data.current_shelter_id,
        photo_id=data.photo_id,
        muzzle_photo_id=data.muzzle_photo_id,
        lat=data.lat,
        lng=data.lng,
        created_by=scope.user_id,
    )
    db.add(animal)
    try:
        db.flush()
    except IntegrityError as exc:
        # Loser of a race on the partial unique index. The caller's SAVEPOINT (or
        # request transaction) does the rollback — never roll back here, or a
        # sibling item in the same sync batch would be discarded too.
        raise Conflict("tag already registered") from exc
    return animal


# --------------------------------------------------------------------------- events
def _fine_amount(db: Session, offence_number: int) -> tuple[Decimal, Decimal, bool, str | None]:
    """Amount, catching charge, FIR flag and the legal instrument (authority_ref) for this offence.

    Council R1 §D1: a fine must always carry the reference of the instrument it rests on, so
    an RTI on any fine row shows exactly what schedule (and legal status) applied that day.
    """
    row = db.execute(
        select(FineSchedule).order_by(FineSchedule.offence_number.desc()).where(
            FineSchedule.offence_number <= offence_number
        )
    ).scalars().first()
    if row is None:
        raise Rejected("no fine schedule row configured - refuse to issue an unreferenced fine")
    ref = f"[{row.legal_status}] {row.authority_ref or 'NO AUTHORITY REFERENCE RECORDED'}"
    return row.amount, row.catching_charge, row.fir_flag, ref


def _load_animal_for_event(db: Session, scope: Scope, animal_id: uuid.UUID, ev_type: EventType) -> Animal:
    animal = db.get(Animal, animal_id)
    if animal is None:
        raise Rejected("animal not found")
    if scope.ulb_ids is None or animal.ulb_id in scope.ulb_ids:
        return animal
    # District-wide reach for field work only; record-rewriting types stay scoped.
    if ev_type in CROSS_ULB_EVENT_TYPES:
        return animal
    raise Rejected("animal not found")


def apply_event(db: Session, scope: Scope, data: EventIn, *, user_id: uuid.UUID | None) -> Event:
    """Insert the event and apply its documented side effect, in one transaction."""
    if data.type in {EventType.owner_merge} and not scope.can_merge_owners:
        raise Rejected("owner_merge is issued by the merge tool only")

    ulb_id = resolve_ulb(scope, data.ulb_id) if user_id is not None else (data.ulb_id or 0)
    if user_id is not None and not scope.may_write_ulb(ulb_id):
        raise Rejected("not permitted to record events in this ULB")

    eid = data.id or uuid7()
    if db.get(Event, eid) is not None:
        raise Conflict("duplicate id", {"id": str(eid)})

    animal = None
    if data.animal_id is not None:
        animal = _load_animal_for_event(db, scope, data.animal_id, data.type)

    owner_id = data.owner_id or (animal.owner_id if animal is not None else None)
    payload = dict(data.payload or {})

    # council R1 §D7: phone clocks are settable; refuse timestamps from the future so that
    # offence windows / "last 7 days" counts cannot be gamed by a device clock.
    if data.occurred_at is not None:
        occ = data.occurred_at if data.occurred_at.tzinfo else data.occurred_at.replace(tzinfo=dt.timezone.utc)
        if occ > utcnow() + dt.timedelta(hours=6):
            raise Rejected("occurred_at is more than 6h in the future - check the device clock")
    event = Event(
        id=eid,
        type=data.type,
        animal_id=animal.id if animal is not None else None,
        owner_id=owner_id,
        ulb_id=ulb_id,
        user_id=user_id,
        device_id=data.device_id,
        lat=data.lat,
        lng=data.lng,
        gps_accuracy_m=data.gps_accuracy_m,
        occurred_at=data.occurred_at or utcnow(),
        payload=payload,
        photo_ids=list(data.photo_ids or []),
    )
    db.add(event)
    db.flush()

    _apply_side_effects(db, scope, event, animal, payload)
    return event


def _apply_side_effects(db: Session, scope: Scope, event: Event, animal: Animal | None, payload: dict) -> None:
    t = event.type

    if animal is not None:
        if t is EventType.impound:
            animal.status = AnimalStatus.impounded
        elif t is EventType.gaushala_intake:
            animal.status = AnimalStatus.in_gaushala
            shelter_id = payload.get("shelter_id")
            if shelter_id is not None:
                shelter = db.get(Shelter, int(shelter_id))
                if shelter is None:
                    raise Rejected("shelter not found")
                if animal.current_shelter_id != shelter.id:
                    shelter.current_count = shelter.current_count + 1
                    animal.current_shelter_id = shelter.id
        elif t is EventType.release:
            if animal.current_shelter_id is not None:
                shelter = db.get(Shelter, animal.current_shelter_id)
                if shelter is not None and shelter.current_count > 0:
                    shelter.current_count = shelter.current_count - 1
                animal.current_shelter_id = None
            animal.status = AnimalStatus.released
        elif t is EventType.tag_lost:
            payload.setdefault("old_tag_id", animal.tag_id)
            animal.status = AnimalStatus.tag_missing
        elif t is EventType.tag_replaced:
            new_tag = (payload.get("new_tag_id") or "").strip().upper() or None
            if not new_tag:
                raise Rejected("tag_replaced requires payload.new_tag_id")
            clash = db.execute(select(Animal).where(Animal.tag_id == new_tag)).scalar_one_or_none()
            if clash is not None and clash.id != animal.id:
                raise Conflict("tag already registered", animal_summary(clash, db))
            payload["old_tag_id"] = animal.tag_id  # old tag preserved (SPEC §3)
            animal.tag_id = new_tag
            if payload.get("new_tag_type"):
                try:
                    animal.tag_type = TagType(payload["new_tag_type"])
                except ValueError as exc:
                    raise Rejected("unknown tag type") from exc
            animal.status = AnimalStatus.registered
        elif t is EventType.death:
            animal.status = AnimalStatus.deceased
        elif t is EventType.transfer_owner:
            new_owner_id = payload.get("new_owner_id")
            if not new_owner_id:
                raise Rejected("transfer_owner requires payload.new_owner_id")
            stmt = select(Owner).where(Owner.id == uuid.UUID(str(new_owner_id)))
            if scope.ulb_ids is not None:
                stmt = stmt.where(Owner.ulb_id.in_(scope.ulb_ids or [-1]))
            new_owner = db.execute(stmt).scalar_one_or_none()  # scoped query — prevents IDOR
            if new_owner is None:
                raise Rejected("new owner not found in your scope")
            payload["old_owner_id"] = str(animal.owner_id) if animal.owner_id else None
            animal.owner_id = new_owner.id
            event.owner_id = new_owner.id
        elif t is EventType.correction:
            _apply_correction(animal, payload)
        elif t is EventType.sighting_road:
            # Only nudge the working status; never overwrite a terminal state.
            if animal.status in {AnimalStatus.registered, AnimalStatus.released, AnimalStatus.tag_missing}:
                animal.status = AnimalStatus.on_road_reported
        if t is not EventType.correction:
            animal.updated_at = utcnow()

    if t is EventType.fine_issued:
        _issue_fine(db, event, payload)
    elif t is EventType.fine_paid:
        fine_id = payload.get("fine_id")
        if fine_id:
            fine = db.get(Fine, uuid.UUID(str(fine_id)))
            if fine is not None and (scope.ulb_ids is None or fine.ulb_id in scope.ulb_ids):
                fine.status = FineStatus.paid
                fine.paid_at = utcnow()
                fine.receipt_no = payload.get("receipt_no") or fine.receipt_no

    # payload may have been enriched (old tag, offence number...) — persist it
    event.payload = dict(payload)


_CORRECTABLE = {
    "breed", "colour_markings", "sex", "age_class", "age_years", "species",
    "secondary_tag_id", "identification_mark_1", "identification_mark_2",
}


def _apply_correction(animal: Animal, payload: dict) -> None:
    """SPEC §1.9: a correction never silently overwrites — the event carries the
    before/after and the audit trigger records the row change."""
    fields = payload.get("fields") or {}
    if not isinstance(fields, dict) or not fields:
        raise Rejected("correction requires payload.fields")
    before = {}
    for key, value in fields.items():
        if key not in _CORRECTABLE:
            raise Rejected(f"field '{key}' is not correctable")
        before[key] = getattr(animal, key)
        before[key] = before[key].value if hasattr(before[key], "value") else before[key]
        setattr(animal, key, value)
    payload["before"] = before
    animal.updated_at = utcnow()


def _issue_fine(db: Session, event: Event, payload: dict) -> None:
    if event.owner_id is None:
        raise Rejected("fine_issued requires an owner")
    prior = db.execute(
        text("SELECT count(*) FROM fines WHERE owner_id = :o"), {"o": str(event.owner_id)}
    ).scalar_one()
    offence_number = int(prior) + 1
    amount, catching, fir, authority_ref = _fine_amount(db, offence_number)
    fine = Fine(
        id=uuid7(),
        event_id=event.id,
        animal_id=event.animal_id,
        owner_id=event.owner_id,
        ulb_id=event.ulb_id,
        offence_number=offence_number,
        amount=amount,
        receipt_no=payload.get("receipt_no"),
        authority_ref=authority_ref,
    )
    db.add(fine)
    db.flush()
    payload["fine_id"] = str(fine.id)
    payload["offence_number"] = offence_number
    payload["amount"] = str(amount)
    payload["catching_charge"] = str(catching)
    payload["fir_recommended"] = fir


# --------------------------------------------------------------------------- batch
def register_device(db: Session, scope: Scope, device_id: uuid.UUID | None, label: str | None, ip: str) -> None:
    if device_id is None:
        return
    device = db.get(Device, device_id)
    if device is None:
        db.add(Device(id=device_id, user_id=scope.user_id, label=label, last_seen_at=utcnow(), last_ip=ip))
    elif device.user_id == scope.user_id:
        device.last_seen_at = utcnow()
        device.last_ip = ip
    db.flush()


def process_batch(db: Session, scope: Scope, items: list[SyncItem], *, user_id: uuid.UUID) -> list[SyncResult]:
    """Idempotent: replaying the same batch produces `duplicate` for every item
    and changes nothing.  Each item gets its own SAVEPOINT so one bad row cannot
    roll back the good ones."""
    results: list[SyncResult] = []
    for item in items:
        sp = db.begin_nested()
        try:
            if item.kind == "owner":
                payload = dict(item.data)
                payload["id"] = str(item.id)
                create_owner(db, scope, OwnerIn.model_validate(payload))
            elif item.kind == "animal":
                payload = dict(item.data)
                payload["id"] = str(item.id)
                create_animal(db, scope, AnimalIn.model_validate(payload))
            else:
                payload = dict(item.data)
                payload["id"] = str(item.id)
                if item.occurred_at and not payload.get("occurred_at"):
                    payload["occurred_at"] = item.occurred_at.isoformat()
                if item.device_id and not payload.get("device_id"):
                    payload["device_id"] = str(item.device_id)
                apply_event(db, scope, EventIn.model_validate(payload), user_id=user_id)
            sp.commit()
            results.append(SyncResult(id=item.id, kind=item.kind, status="created"))
        except Conflict as exc:
            sp.rollback()
            if exc.reason == "duplicate id":
                results.append(SyncResult(id=item.id, kind=item.kind, status="duplicate"))
            else:
                results.append(
                    SyncResult(id=item.id, kind=item.kind, status="conflict", reason=exc.reason, existing=exc.existing)
                )
        except Rejected as exc:
            sp.rollback()
            results.append(SyncResult(id=item.id, kind=item.kind, status="rejected", reason=exc.reason))
        except IntegrityError as exc:
            sp.rollback()
            msg = str(exc.orig).split("\n")[0]
            status = "duplicate" if "pkey" in msg else "conflict"
            results.append(SyncResult(id=item.id, kind=item.kind, status=status, reason=msg[:200]))
        except Exception as exc:  # noqa: BLE001 - one bad item must not kill the batch
            sp.rollback()
            results.append(SyncResult(id=item.id, kind=item.kind, status="rejected", reason=str(exc)[:200]))
    return results
