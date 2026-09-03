"""Serialisation helpers shared by the JSON routes.

Masking happens here, once, so no individual handler can forget it.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from authz import Scope, mask_name, mask_phone
from models import Animal, Event, Owner, Shelter, Ulb, User
from schemas import AnimalOut, EventOut, OwnerOut


def ulb_codes(db: Session) -> dict[int, str]:
    return {u.id: u.code for u in db.execute(select(Ulb)).scalars()}


def owner_out(db: Session, owner: Owner, scope: Scope, *, codes: dict[int, str] | None = None) -> OwnerOut:
    codes = codes if codes is not None else ulb_codes(db)
    visible = scope.sees_pii_for_ulb(owner.ulb_id)
    counts = db.execute(
        text(
            "SELECT (SELECT count(*) FROM animals WHERE owner_id = :o) AS animals, "
            "       (SELECT count(*) FROM fines   WHERE owner_id = :o) AS offences"
        ),
        {"o": str(owner.id)},
    ).mappings().one()
    return OwnerOut(
        id=owner.id,
        ulb_id=owner.ulb_id,
        ulb_code=codes.get(owner.ulb_id),
        # council R1 §D4: name + village is enough to find a man; out-of-ULB callers get initials
        name=owner.name if visible else (mask_name(owner.name) or owner.name[:1] + "."),
        relation_name=owner.relation_name if visible else None,
        phone=mask_phone(owner.phone_norm, visible),
        phone_masked=not visible,
        address=owner.address if visible else None,
        ward_or_village=owner.ward_or_village,
        keeper_type=owner.keeper_type,
        self_declared_cattle_count=owner.self_declared_cattle_count,
        premises_area_sq_yards=owner.premises_area_sq_yards,
        lat=owner.lat,
        lng=owner.lng,
        gps_accuracy_m=owner.gps_accuracy_m,
        photo_id=owner.photo_id,
        notes=owner.notes if visible else None,
        merged_into=owner.merged_into,
        animal_count=int(counts["animals"]),
        offence_count=int(counts["offences"]),
        created_at=owner.created_at,
        updated_at=owner.updated_at,
    )


def animal_out(db: Session, animal: Animal, scope: Scope, *, codes: dict[int, str] | None = None) -> AnimalOut:
    codes = codes if codes is not None else ulb_codes(db)
    owner_name = None
    if animal.owner_id:
        owner = db.get(Owner, animal.owner_id)
        owner_name = owner.name if owner else None
    shelter_name = None
    if animal.current_shelter_id:
        shelter = db.get(Shelter, animal.current_shelter_id)
        shelter_name = shelter.name if shelter else None
    return AnimalOut(
        id=animal.id,
        ulb_id=animal.ulb_id,
        ulb_code=codes.get(animal.ulb_id),
        owner_id=animal.owner_id,
        owner_name=owner_name,
        species=animal.species,
        sex=animal.sex,
        age_class=animal.age_class,
        age_years=animal.age_years,
        breed=animal.breed,
        colour_markings=animal.colour_markings,
        # Carried on every animal response: the field app shows these on a tag
        # lookup so an officer can confirm the record matches the animal in
        # front of them when the tag is damaged or missing.
        identification_mark_1=animal.identification_mark_1,
        identification_mark_2=animal.identification_mark_2,
        tag_id=animal.tag_id,
        tag_type=animal.tag_type,
        secondary_tag_id=animal.secondary_tag_id,
        status=animal.status,
        current_shelter_id=animal.current_shelter_id,
        shelter_name=shelter_name,
        photo_id=animal.photo_id,
        muzzle_photo_id=animal.muzzle_photo_id,
        lat=animal.lat,
        lng=animal.lng,
        created_at=animal.created_at,
        updated_at=animal.updated_at,
    )


def event_out(db: Session, event: Event, *, user_names: dict[uuid.UUID, str] | None = None) -> EventOut:
    name = None
    if event.user_id:
        if user_names is not None and event.user_id in user_names:
            name = user_names[event.user_id]
        else:
            u = db.get(User, event.user_id)
            name = u.full_name if u else None
    return EventOut(
        id=event.id,
        seq=event.seq,
        type=event.type,
        animal_id=event.animal_id,
        owner_id=event.owner_id,
        ulb_id=event.ulb_id,
        user_id=event.user_id,
        user_name=name,
        lat=event.lat,
        lng=event.lng,
        occurred_at=event.occurred_at,
        received_at=event.received_at,
        payload=event.payload or {},
        photo_ids=list(event.photo_ids or []),
        hash=event.hash,
    )
