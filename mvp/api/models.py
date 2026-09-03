"""SQLAlchemy 2.x models.  The authoritative DDL lives in the Alembic migration
(api/alembic/versions/0001_init.py) because the triggers, hash-chain functions
and role grants cannot be expressed as ORM metadata.  These classes must stay in
step with it."""
from __future__ import annotations

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- enums
class Role(str, enum.Enum):
    super_admin = "super_admin"
    ulb_admin = "ulb_admin"
    field_officer = "field_officer"
    viewer = "viewer"
    auditor = "auditor"


class KeeperType(str, enum.Enum):
    household = "household"
    dairy_tabela = "dairy_tabela"
    commercial = "commercial"
    gaushala = "gaushala"
    trader = "trader"
    other = "other"


class Species(str, enum.Enum):
    cattle = "cattle"
    buffalo = "buffalo"


class Sex(str, enum.Enum):
    male = "male"
    female = "female"
    unknown = "unknown"


class AgeClass(str, enum.Enum):
    calf = "calf"
    young = "young"
    adult = "adult"
    old = "old"


class TagType(str, enum.Enum):
    pashu_aadhaar_12 = "pashu_aadhaar_12"
    rfid_lf = "rfid_lf"
    rfid_uhf = "rfid_uhf"
    visual = "visual"
    microchip = "microchip"
    none = "none"


class AnimalStatus(str, enum.Enum):
    registered = "registered"
    on_road_reported = "on_road_reported"
    impounded = "impounded"
    in_gaushala = "in_gaushala"
    released = "released"
    transferred = "transferred"
    deceased = "deceased"
    tag_missing = "tag_missing"


class ShelterKind(str, enum.Enum):
    gaushala = "gaushala"
    nandishala = "nandishala"
    cattle_pound = "cattle_pound"


class EventType(str, enum.Enum):
    registration = "registration"
    tagging = "tagging"
    tag_lost = "tag_lost"
    tag_replaced = "tag_replaced"
    sighting_road = "sighting_road"
    impound = "impound"
    release = "release"
    fine_issued = "fine_issued"
    fine_paid = "fine_paid"
    gaushala_intake = "gaushala_intake"
    transfer_owner = "transfer_owner"
    death = "death"
    correction = "correction"
    owner_merge = "owner_merge"
    note = "note"


class FineStatus(str, enum.Enum):
    issued = "issued"
    paid = "paid"
    waived = "waived"
    contested = "contested"


def _pg_enum(py_enum, name):
    return Enum(
        py_enum,
        name=name,
        create_type=False,
        native_enum=True,
        values_callable=lambda e: [m.value for m in e],
    )


_TS = DateTime(timezone=True)
_NOW = text("now()")


# --------------------------------------------------------------------------- tables
class Ulb(Base):
    __tablename__ = "ulbs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    district: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Rewari'"))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[Role] = mapped_column(_pg_enum(Role, "role_enum"), nullable=False)
    ulb_id: Mapped[int | None] = mapped_column(ForeignKey("ulbs.id"))
    phone: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    totp_secret: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    locked_until: Mapped[dt.datetime | None] = mapped_column(_TS)
    created_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)

    ulb: Mapped[Ulb | None] = relationship(lazy="joined")


class SessionRow(Base):
    """``id`` is sha256(cookie token), not the token itself: a stolen database
    dump therefore does not hand the attacker live sessions."""

    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    csrf_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)
    expires_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)
    ip: Mapped[str | None] = mapped_column(Text)
    ua: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(_TS)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(Text)
    registered_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    last_ip: Mapped[str | None] = mapped_column(Text)


class Photo(Base):
    __tablename__ = "photos"
    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    taken_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)


class Shelter(Base):
    __tablename__ = "shelters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ulb_id: Mapped[int] = mapped_column(ForeignKey("ulbs.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[ShelterKind] = mapped_column(_pg_enum(ShelterKind, "shelter_kind_enum"), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    current_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    phone: Mapped[str | None] = mapped_column(Text)


class Owner(Base):
    __tablename__ = "owners"
    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    ulb_id: Mapped[int] = mapped_column(ForeignKey("ulbs.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    relation_name: Mapped[str | None] = mapped_column(Text)
    phone_norm: Mapped[str | None] = mapped_column(Text)
    phone_hash: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    ward_or_village: Mapped[str | None] = mapped_column(Text)
    keeper_type: Mapped[KeeperType] = mapped_column(
        _pg_enum(KeeperType, "keeper_type_enum"), nullable=False, server_default=text("'household'")
    )
    id_type: Mapped[str | None] = mapped_column(Text)
    id_last4: Mapped[str | None] = mapped_column(String(4))
    # What the keeper *says* at the door, before anything is counted.  The gap
    # against the animals actually registered is the audit signal (0004).
    self_declared_cattle_count: Mapped[int | None] = mapped_column(Integer)
    # Square yards, the unit used locally — not square metres.
    premises_area_sq_yards: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    gps_accuracy_m: Mapped[float | None] = mapped_column(Float)
    photo_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("photos.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    merged_into: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("owners.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)
    updated_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)


class Animal(Base):
    __tablename__ = "animals"
    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    ulb_id: Mapped[int] = mapped_column(ForeignKey("ulbs.id"), nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("owners.id"))
    species: Mapped[Species] = mapped_column(_pg_enum(Species, "species_enum"), nullable=False)
    sex: Mapped[Sex] = mapped_column(
        _pg_enum(Sex, "sex_enum"), nullable=False, server_default=text("'unknown'")
    )
    age_class: Mapped[AgeClass] = mapped_column(
        _pg_enum(AgeClass, "age_class_enum"), nullable=False, server_default=text("'adult'")
    )
    # Keeper-stated age in years; `age_class` above stays the bucket the
    # dashboards aggregate on.  Bounded 0-40 by a CHECK constraint (0004).
    age_years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    breed: Mapped[str | None] = mapped_column(Text)
    colour_markings: Mapped[str | None] = mapped_column(Text)
    # Natural marks — the fallback identity when a tag is cut off.  Optional by
    # design: no officer is blocked because an animal has no distinguishing mark.
    identification_mark_1: Mapped[str | None] = mapped_column(Text)
    identification_mark_2: Mapped[str | None] = mapped_column(Text)
    tag_id: Mapped[str | None] = mapped_column(Text)
    tag_type: Mapped[TagType] = mapped_column(
        _pg_enum(TagType, "tag_type_enum"), nullable=False, server_default=text("'none'")
    )
    secondary_tag_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AnimalStatus] = mapped_column(
        _pg_enum(AnimalStatus, "animal_status_enum"), nullable=False, server_default=text("'registered'")
    )
    current_shelter_id: Mapped[int | None] = mapped_column(ForeignKey("shelters.id"))
    photo_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("photos.id"))
    muzzle_photo_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("photos.id"))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)
    updated_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)


class Event(Base):
    """Append-only.  UPDATE/DELETE are refused by a trigger *and* not granted to
    the application's database role."""

    __tablename__ = "events"
    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    # `seq` is not in SPEC §2; a hash chain needs a total order that survives
    # clock skew and out-of-order offline uploads, and `id`/`received_at` do not
    # give one.  Recorded in DEVIATIONS.md.
    seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True,
        server_default=text("nextval('events_seq_seq'::regclass)"),
    )
    type: Mapped[EventType] = mapped_column(_pg_enum(EventType, "event_type_enum"), nullable=False)
    animal_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("animals.id"))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("owners.id"))
    ulb_id: Mapped[int] = mapped_column(ForeignKey("ulbs.id"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    gps_accuracy_m: Mapped[float | None] = mapped_column(Float)
    occurred_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)
    received_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    photo_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), nullable=False, server_default=text("'{}'::uuid[]")
    )
    prev_hash: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    hash: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))


class Fine(Base):
    __tablename__ = "fines"
    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("events.id"))
    animal_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("animals.id"))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("owners.id"))
    ulb_id: Mapped[int] = mapped_column(ForeignKey("ulbs.id"), nullable=False)
    offence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[FineStatus] = mapped_column(
        _pg_enum(FineStatus, "fine_status_enum"), nullable=False, server_default=text("'issued'")
    )
    receipt_no: Mapped[str | None] = mapped_column(Text)
    issued_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)
    paid_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    # legal instrument the amount rests on, copied from fine_schedule at issue time (council R1 §D1)
    authority_ref: Mapped[str | None] = mapped_column(Text)


class FineSchedule(Base):
    __tablename__ = "fine_schedule"
    offence_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    catching_charge: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    fir_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    note: Mapped[str | None] = mapped_column(Text)
    authority_ref: Mapped[str | None] = mapped_column(Text)
    legal_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'administrative_practice'"))
    effective_from: Mapped[dt.date | None] = mapped_column(Date)


class LookupLog(Base):
    """Every district-wide tag lookup (who asked about which tag). Append-only, audit-chained."""
    __tablename__ = "lookup_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    tag_id: Mapped[str] = mapped_column(Text, nullable=False)
    animal_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    in_scope: Mapped[bool | None] = mapped_column(Boolean)
    ip: Mapped[str | None] = mapped_column(Text)


class ExportLog(Base):
    """Every bulk CSV download (who took which dataset, with which filters).

    Append-only and audit-chained, like `lookup_log`: a registry where a copy of
    every phone number can be taken silently is not auditable (routes/export_routes.py).
    """
    __tablename__ = "export_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    dataset: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    ip: Mapped[str | None] = mapped_column(Text)


class LoginAttempt(Base):
    """Backs the login rate limiter.  Kept in the database rather than in
    process memory so the limit survives a restart and holds across workers."""

    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=_NOW)
    ip: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'login'"))
