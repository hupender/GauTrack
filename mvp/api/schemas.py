"""Request/response shapes.  Pydantic is the only place raw client input is
allowed to become typed data — nothing downstream re-parses strings."""
from __future__ import annotations

import datetime as dt
import re
import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from models import AgeClass, AnimalStatus, EventType, KeeperType, Role, Sex, Species, TagType

_DIGITS = re.compile(r"\D+")


def normalize_phone(raw: str | None) -> str | None:
    """Indian mobile numbers, reduced to the bare 10 digits so that
    +91-98765 43210 and 09876543210 dedupe against each other."""
    if not raw:
        return None
    d = _DIGITS.sub("", raw)
    if d.startswith("91") and len(d) == 12:
        d = d[2:]
    if d.startswith("0") and len(d) == 11:
        d = d[1:]
    return d or None


# --------------------------------------------------------------------------- auth
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, max_length=16)


class MeOut(BaseModel):
    user_id: uuid.UUID
    username: str
    full_name: str
    role: Role
    ulb_id: int | None
    ulb_code: str | None
    ulb_name: str | None
    csrf_token: str
    demo: bool


# --------------------------------------------------------------------------- owners
class OwnerIn(BaseModel):
    id: uuid.UUID | None = None  # client-generated for offline creates
    ulb_id: int | None = None    # ignored for non-super roles: forced to the user's ULB
    name: str = Field(min_length=1, max_length=120)
    relation_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=24)
    address: str | None = Field(default=None, max_length=400)
    ward_or_village: str | None = Field(default=None, max_length=120)
    keeper_type: KeeperType = KeeperType.household
    id_type: str | None = Field(default=None, max_length=32)
    id_last4: str | None = Field(default=None, max_length=4)
    # Optional survey fields (0004).  Bounds mirror the database CHECKs, so a
    # bad value is refused at the edge with a readable message rather than as a
    # 500 from Postgres.
    self_declared_cattle_count: int | None = Field(default=None, ge=0, le=2000)
    premises_area_sq_yards: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    lat: float | None = None
    lng: float | None = None
    gps_accuracy_m: float | None = None
    photo_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("id_last4")
    @classmethod
    def _last4(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if not re.fullmatch(r"\d{4}", v):
            raise ValueError("id_last4 must be exactly 4 digits")
        return v

    @field_validator("id_type")
    @classmethod
    def _no_aadhaar_number(cls, v: str | None) -> str | None:
        # SPEC §1.10: we store a *type* and last 4 digits, never a full number.
        if v and _DIGITS.sub("", v) and len(_DIGITS.sub("", v)) > 4:
            raise ValueError("id_type must be a label, not a number")
        return v


class OwnerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    relation_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=24)
    address: str | None = Field(default=None, max_length=400)
    ward_or_village: str | None = Field(default=None, max_length=120)
    keeper_type: KeeperType | None = None
    self_declared_cattle_count: int | None = Field(default=None, ge=0, le=2000)
    premises_area_sq_yards: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    notes: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=300)


class OwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ulb_id: int
    ulb_code: str | None = None
    name: str
    relation_name: str | None
    phone: str | None  # masked for out-of-scope roles
    phone_masked: bool = False
    address: str | None
    ward_or_village: str | None
    keeper_type: KeeperType
    self_declared_cattle_count: int | None = None
    premises_area_sq_yards: Decimal | None = None
    lat: float | None
    lng: float | None
    gps_accuracy_m: float | None
    photo_id: uuid.UUID | None
    notes: str | None
    merged_into: uuid.UUID | None
    animal_count: int = 0
    offence_count: int = 0
    created_at: dt.datetime
    updated_at: dt.datetime


class OwnerCreateOut(BaseModel):
    owner: OwnerOut
    possible_duplicates: list[dict[str, Any]] = []


class MergeIn(BaseModel):
    source_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=300)


# --------------------------------------------------------------------------- animals
class AnimalIn(BaseModel):
    id: uuid.UUID | None = None
    ulb_id: int | None = None
    owner_id: uuid.UUID | None = None
    species: Species
    sex: Sex = Sex.unknown
    age_class: AgeClass = AgeClass.adult
    age_years: Decimal | None = Field(default=None, ge=0, le=40)
    breed: str | None = Field(default=None, max_length=80)
    colour_markings: str | None = Field(default=None, max_length=200)
    identification_mark_1: str | None = Field(default=None, max_length=120)
    identification_mark_2: str | None = Field(default=None, max_length=120)
    tag_id: str | None = Field(default=None, max_length=40)
    tag_type: TagType = TagType.none
    secondary_tag_id: str | None = Field(default=None, max_length=40)
    photo_id: uuid.UUID | None = None
    muzzle_photo_id: uuid.UUID | None = None
    lat: float | None = None
    lng: float | None = None
    status: AnimalStatus | None = None
    current_shelter_id: int | None = None

    @field_validator("tag_id", "secondary_tag_id")
    @classmethod
    def _tag_chars(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z0-9\-]{3,40}", v):
            raise ValueError("tag id may contain only letters, digits and hyphens")
        return v

    def validated_tag(self) -> str | None:
        """Pashu Aadhaar tags are exactly 12 digits (SPEC §4)."""
        if self.tag_id and self.tag_type is TagType.pashu_aadhaar_12:
            if not re.fullmatch(r"\d{12}", self.tag_id):
                raise ValueError("pashu_aadhaar_12 tag must be exactly 12 digits")
        return self.tag_id


class AnimalPatch(BaseModel):
    owner_id: uuid.UUID | None = None
    species: Species | None = None
    sex: Sex | None = None
    age_class: AgeClass | None = None
    age_years: Decimal | None = Field(default=None, ge=0, le=40)
    breed: str | None = Field(default=None, max_length=80)
    colour_markings: str | None = Field(default=None, max_length=200)
    identification_mark_1: str | None = Field(default=None, max_length=120)
    identification_mark_2: str | None = Field(default=None, max_length=120)
    tag_id: str | None = Field(default=None, max_length=40)
    tag_type: TagType | None = None
    secondary_tag_id: str | None = Field(default=None, max_length=40)
    status: AnimalStatus | None = None
    current_shelter_id: int | None = None
    photo_id: uuid.UUID | None = None
    muzzle_photo_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=300)


class AnimalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ulb_id: int
    ulb_code: str | None = None
    owner_id: uuid.UUID | None
    owner_name: str | None = None
    species: Species
    sex: Sex
    age_class: AgeClass
    age_years: Decimal | None = None
    breed: str | None
    colour_markings: str | None
    identification_mark_1: str | None = None
    identification_mark_2: str | None = None
    tag_id: str | None
    tag_type: TagType
    secondary_tag_id: str | None
    status: AnimalStatus
    current_shelter_id: int | None
    shelter_name: str | None = None
    photo_id: uuid.UUID | None
    muzzle_photo_id: uuid.UUID | None
    lat: float | None
    lng: float | None
    created_at: dt.datetime
    updated_at: dt.datetime


# --------------------------------------------------------------------------- events
class EventIn(BaseModel):
    id: uuid.UUID | None = None
    type: EventType
    animal_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    ulb_id: int | None = None
    device_id: uuid.UUID | None = None
    lat: float | None = None
    lng: float | None = None
    gps_accuracy_m: float | None = None
    occurred_at: dt.datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    photo_ids: list[uuid.UUID] = Field(default_factory=list)

    @field_validator("payload")
    @classmethod
    def _payload_size(cls, v: dict) -> dict:
        import json

        if len(json.dumps(v, default=str)) > 8000:
            raise ValueError("payload too large")
        return v

    @field_validator("photo_ids")
    @classmethod
    def _photo_count(cls, v: list) -> list:
        if len(v) > 8:
            raise ValueError("at most 8 photos per event")
        return v


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    seq: int
    type: EventType
    animal_id: uuid.UUID | None
    owner_id: uuid.UUID | None
    ulb_id: int
    user_id: uuid.UUID | None
    user_name: str | None = None
    lat: float | None
    lng: float | None
    occurred_at: dt.datetime
    received_at: dt.datetime
    payload: dict[str, Any]
    photo_ids: list[uuid.UUID]
    hash: str


# --------------------------------------------------------------------------- sync
class SyncItem(BaseModel):
    """One queued offline mutation."""

    kind: Literal["owner", "animal", "event"]
    id: uuid.UUID
    device_id: uuid.UUID | None = None
    occurred_at: dt.datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class SyncIn(BaseModel):
    items: list[SyncItem] = Field(default_factory=list, max_length=200)
    device_id: uuid.UUID | None = None
    device_label: str | None = Field(default=None, max_length=80)


class SyncResult(BaseModel):
    id: uuid.UUID
    kind: str
    status: Literal["created", "duplicate", "conflict", "rejected"]
    reason: str | None = None
    existing: dict[str, Any] | None = None


class SyncOut(BaseModel):
    results: list[SyncResult]
    server_time: dt.datetime


# --------------------------------------------------------------------------- lookup
class LookupOut(BaseModel):
    animal: AnimalOut
    owner: OwnerOut | None
    offence_count: int
    recent_events: list[EventOut]
    in_scope: bool


# --------------------------------------------------------------------------- photos
class PhotoOut(BaseModel):
    id: uuid.UUID
    sha256: str
    bytes: int
    mime: str


# --------------------------------------------------------------------------- users
class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9._-]+$")
    full_name: str = Field(min_length=1, max_length=120)
    role: Role
    ulb_id: int | None = None
    phone: str | None = Field(default=None, max_length=24)
    totp_enabled: bool = False


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    username: str
    full_name: str
    role: Role
    ulb_id: int | None
    phone: str | None
    is_active: bool
    totp_enabled: bool
    locked_until: dt.datetime | None
    created_at: dt.datetime


class UserCreateOut(BaseModel):
    user: UserOut
    temp_password: str
    totp_provisioning_uri: str | None = None


# --------------------------------------------------------------------------- public
class PublicReportIn(BaseModel):
    """No session.  Rate-limited, honeypot-protected."""

    lat: float | None = None
    lng: float | None = None
    gps_accuracy_m: float | None = None
    tag_digits: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=500)
    photo_id: uuid.UUID | None = None
    ulb_id: int | None = None
    # honeypot: real humans never see or fill this
    website: str | None = None
