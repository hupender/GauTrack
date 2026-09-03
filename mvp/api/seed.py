"""Demo dataset (SPEC §8).

Run with `make seed`.  Idempotent-ish: it refuses to run twice unless you pass
--force, which wipes the registry tables first.  Credentials are random and are
written to mvp/.seed_credentials.txt (git-ignored, mode 600).

Everything created here is clearly labelled DEMO in the UI banner.
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import secrets
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auth import hash_password  # noqa: E402
from config import ROOT_DIR, settings  # noqa: E402
from ids import uuid7  # noqa: E402
from schemas import normalize_phone  # noqa: E402
from sync import phone_hash  # noqa: E402
from models import (  # noqa: E402
    AgeClass,
    Animal,
    AnimalStatus,
    Event,
    EventType,
    Fine,
    FineStatus,
    KeeperType,
    Owner,
    Role,
    Sex,
    Shelter,
    ShelterKind,
    Species,
    TagType,
    Ulb,
    User,
)

RNG = random.Random(20260817)

FIRST_NAMES = [
    "Ram Kumar", "Suresh", "Mahesh", "Rajesh", "Dharam Singh", "Om Prakash", "Satbir",
    "Balwan", "Rohtash", "Jai Bhagwan", "Krishan", "Ranbir", "Sube Singh", "Naresh",
    "Vijay", "Anil", "Sunil", "Rakesh", "Hawa Singh", "Dalip", "Bijender", "Karan",
    "Sombir", "Pawan", "Jagdish", "Ishwar", "Mange Ram", "Ved Prakash", "Sarita Devi",
    "Kamla Devi", "Santosh Devi", "Bimla Devi",
]
IDENT_MARKS = [
    "Broken left horn", "Broken right horn", "Torn left ear", "Torn right ear",
    "White patch on forehead", "White socks on hind legs", "Black patch on flank",
    "Short/stumpy tail", "Limp in hind leg", "Hump scar", "Curved right horn",
    "White tip on tail", "Scar above left eye",
]

RELATIONS = ["S/o Ram Singh", "S/o Hari Ram", "S/o Chandgi Ram", "W/o Suresh Kumar",
             "S/o Dhanpat", "S/o Bhim Singh", "W/o Rakesh", "S/o Prem Singh"]
VILLAGES = ["Kosli", "Bawal", "Dharuhera", "Masani", "Kund", "Jatusana", "Nahar",
            "Bolni", "Khijuri", "Gurawara", "Sahibpur", "Bikaner Chowk", "Model Town",
            "Ward 12", "Ward 5", "Ward 21"]
BREEDS_CATTLE = ["Sahiwal", "HF cross", "Jersey cross", "Desi/Non-descript", "Haryana"]
BREEDS_BUFFALO = ["Murrah", "Desi/Non-descript", "Nili-Ravi cross"]
COLOURS = ["White with black patch on left flank", "Brown, white forehead star",
           "Black", "Grey, broken left horn", "White, notched right ear",
           "Brown-white mixed", "Black with white socks"]

SHELTERS = [
    ("Shri Krishna Gaushala (demo)", ShelterKind.gaushala, 400, "RWR", 28.2010, 76.6250),
    ("Bawal Nandi-shala (demo)", ShelterKind.nandishala, 150, "BWL", 28.0740, 76.5850),
    ("Dharuhera Cattle Pound (demo)", ShelterKind.cattle_pound, 80, "DHR", 28.2070, 76.7990),
    ("Kosli Gau Seva Sadan (demo)", ShelterKind.gaushala, 220, "RUR", 28.2400, 76.4400),
]

# NH-48 / NH-11 corridor around Rewari (SPEC §8)
ROAD_POINTS = [
    (28.1990, 76.6190), (28.2130, 76.6480), (28.1760, 76.5980), (28.2280, 76.6900),
    (28.1520, 76.5740), (28.2050, 76.7960), (28.0725, 76.5830), (28.2450, 76.7200),
]


def random_phone() -> str:
    return f"9{RNG.randint(100000000, 999999999)}"


def jitter(lat: float, lng: float, spread: float = 0.02) -> tuple[float, float]:
    return lat + RNG.uniform(-spread, spread), lng + RNG.uniform(-spread, spread)


def sighting_time(day: dt.date) -> dt.datetime:
    """Skewed to the two grazing peaks: 05:00-09:00 and 17:00-21:00 IST."""
    if RNG.random() < 0.55:
        hour = RNG.randint(5, 8)
    else:
        hour = RNG.randint(17, 20)
    naive = dt.datetime.combine(day, dt.time(hour, RNG.randint(0, 59), RNG.randint(0, 59)))
    # stored in UTC; IST is +05:30
    return naive.replace(tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))).astimezone(dt.timezone.utc)


def previously_recorded_passwords(path: Path) -> dict[str, str]:
    """Read the passwords out of an existing `.seed_credentials.txt`.

    When a reseed keeps the old passwords, it must keep the *record* of them
    too.  Rewriting this file with "(unchanged)" in every row would leave the
    accounts working and the operator with no idea what they are — the file is
    the only place the plaintext ever exists, by design, because the database
    holds nothing but argon2 hashes.
    """
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        # A credential row is: username, password, role, ulb.  Anything else in
        # the file (headings, rules, the prose at the bottom) fails this shape.
        if len(parts) == 4 and parts[2] in {r.value for r in Role} and not line.startswith(" "):
            username, password = parts[0], parts[1]
            if password != "(unchanged)":
                out[username] = password
    return out


def existing_password_hashes(db: Session) -> dict[str, str]:
    """Snapshot username -> password hash before a wipe.

    A reseed exists to refresh the *demo data*, and until now it also silently
    reissued every password, which breaks every phone already signed in and
    every credential anyone has written down.  Capturing the hashes here lets
    `seed()` put them back on the accounts it recreates, so a reseed stops
    being an event that logs the district out.  Hashes are moved, never
    decrypted: the plaintext is not recoverable and is not needed.
    """
    return {
        row.username: row.password_hash
        for row in db.execute(text("SELECT username, password_hash FROM users")).mappings()
    }


def wipe(db: Session) -> None:
    db.execute(text("ALTER TABLE events DISABLE TRIGGER events_append_only"))
    db.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
    db.execute(
        text(
            # export_log and lookup_log reference users(id); without them in the
            # TRUNCATE list the DELETE FROM users below fails on a machine where
            # anyone has ever taken a CSV or looked up a tag.
            "TRUNCATE fines, events, animals, owners, shelters, photos, devices, "
            "sessions, login_attempts, export_log, lookup_log, audit_log "
            "RESTART IDENTITY CASCADE"
        )
    )
    db.execute(text("DELETE FROM users"))
    db.execute(text("ALTER TABLE events ENABLE TRIGGER events_append_only"))
    db.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
    db.commit()


def seed(force: bool = False, new_passwords: bool = False) -> None:
    engine = create_engine(
        settings.owner_database_url,
        future=True,
        connect_args=settings.db_connect_args(),
    )
    with Session(engine, future=True) as db:
        existing = db.execute(text("SELECT count(*) FROM users")).scalar_one()
        if existing and not force:
            print("[seed] users already exist — nothing to do (use --force to reseed)")
            return
        creds_path = ROOT_DIR / ".seed_credentials.txt"
        kept_hashes: dict[str, str] = {}
        kept_plaintext: dict[str, str] = {}
        if existing:
            if not new_passwords:
                kept_hashes = existing_password_hashes(db)
                kept_plaintext = previously_recorded_passwords(creds_path)
            print("[seed] --force: wiping registry tables")
            wipe(db)
            if kept_hashes:
                print(f"[seed] keeping the existing password for {len(kept_hashes)} account(s) "
                      "— pass --new-passwords to reissue them instead")

        # the seed itself is an actor in the audit log
        db.execute(text("SELECT set_config('app.ip', 'seed-script', false)"))

        ulbs = {u.code: u for u in db.execute(select(Ulb)).scalars()}
        if not ulbs:
            raise SystemExit("no ULBs — run `make migrate` first")

        # ---- shelters ---------------------------------------------------
        shelters: list[Shelter] = []
        for name, kind, capacity, code, lat, lng in SHELTERS:
            s = Shelter(
                ulb_id=ulbs[code].id, name=name, kind=kind, capacity=capacity,
                current_count=0, lat=lat, lng=lng, phone=random_phone(),
            )
            db.add(s)
            shelters.append(s)
        db.flush()

        # ---- users ------------------------------------------------------
        creds: list[tuple[str, str, str, str]] = []

        def make_user(username: str, full_name: str, role: Role, ulb_code: str | None) -> User:
            """Three ways an account gets its password, in priority order:

            1. It already had one and this is a reseed — keep it, so nobody is
               logged out and no written-down credential goes stale.
            2. `SEED_PASSWORD` is set in `.env` — use it, so the credentials are
               reproducible on a pilot box that gets reseeded repeatedly.
            3. Neither — mint a random one, so a demo box is never shipped with
               a password anybody could guess.
            """
            kept = kept_hashes.get(username)
            if kept:
                # Password unchanged; show the one already on record so the file
                # stays a complete, usable list rather than a row of blanks.
                pw_hash, shown = kept, kept_plaintext.get(username, "(unchanged)")
            else:
                pw = settings.seed_password.strip() or (
                    f"{secrets.choice(['Rewari', 'Bawal', 'Kosli', 'Nahar'])}"
                    f"-{secrets.token_hex(4)}-{secrets.randbelow(9000) + 1000}"
                )
                pw_hash, shown = hash_password(pw), pw
            u = User(
                id=uuid7(), username=username, password_hash=pw_hash, full_name=full_name,
                role=role, ulb_id=ulbs[ulb_code].id if ulb_code else None, phone=random_phone(),
            )
            db.add(u)
            creds.append((username, shown, role.value, ulb_code or "-"))
            return u

        make_user("dmc", "District Municipal Commissioner (DEMO)", Role.super_admin, None)
        make_user("rwr_admin", "Rewari MC Admin (DEMO)", Role.ulb_admin, "RWR")
        make_user("bwl_admin", "Bawal MC Admin (DEMO)", Role.ulb_admin, "BWL")
        make_user("viewer", "CM Dashboard Viewer (DEMO)", Role.viewer, None)
        make_user("auditor", "District Auditor (DEMO)", Role.auditor, None)

        field_users: dict[str, list[User]] = {}
        codes = ["RWR", "RWR", "BWL", "BWL", "DHR", "DHR"]
        for i, code in enumerate(codes, start=1):
            u = make_user(f"field{i}", f"Field Officer {i} — {ulbs[code].name} (DEMO)", Role.field_officer, code)
            field_users.setdefault(code, []).append(u)
        field_users.setdefault("RUR", []).extend(field_users["RWR"])
        db.flush()

        all_codes = ["RWR", "BWL", "DHR", "RUR"]

        # ---- owners -----------------------------------------------------
        owners: list[Owner] = []
        for i in range(60):
            code = RNG.choices(all_codes, weights=[45, 20, 20, 15])[0]
            ulb = ulbs[code]
            creator = RNG.choice(field_users[code])
            phone = random_phone()
            lat, lng = jitter(ulb.lat, ulb.lng, 0.03)
            created = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RNG.randint(0, 44), hours=RNG.randint(0, 23))
            o = Owner(
                id=uuid7(),
                ulb_id=ulb.id,
                name=RNG.choice(FIRST_NAMES),
                relation_name=RNG.choice(RELATIONS),
                phone_norm=normalize_phone(phone),
                phone_hash=phone_hash(normalize_phone(phone)),
                address=f"House {RNG.randint(1, 300)}, {RNG.choice(VILLAGES)}, Rewari",
                ward_or_village=RNG.choice(VILLAGES),
                keeper_type=RNG.choices(
                    [KeeperType.household, KeeperType.dairy_tabela, KeeperType.commercial,
                     KeeperType.trader, KeeperType.other],
                    weights=[60, 22, 8, 6, 4],
                )[0],
                # Demo figures for the two survey columns.  ~15% of keepers are
                # left blank on purpose: the field app does not require them, so
                # the dashboard and the CSV must both cope with nulls.
                self_declared_cattle_count=(None if RNG.random() < 0.15 else RNG.randint(1, 9)),
                premises_area_sq_yards=(
                    None if RNG.random() < 0.15 else float(RNG.choice(
                        [80, 100, 120, 150, 180, 200, 250, 300, 400, 500, 750, 1000]
                    ))
                ),
                lat=lat, lng=lng, gps_accuracy_m=RNG.choice([4.0, 6.5, 9.0, 14.0, 22.0]),
                created_by=creator.id, created_at=created, updated_at=created,
            )
            db.add(o)
            owners.append(o)
        db.flush()

        # ---- animals ----------------------------------------------------
        animals: list[Animal] = []
        for i in range(240):
            owner = RNG.choice(owners)
            creator = RNG.choice(field_users[next(c for c, u in ulbs.items() if u.id == owner.ulb_id)])
            species = Species.cattle if RNG.random() < 0.60 else Species.buffalo
            sex = RNG.choices([Sex.female, Sex.male], weights=[78, 22])[0]
            age = RNG.choices(
                [AgeClass.calf, AgeClass.young, AgeClass.adult, AgeClass.old], weights=[14, 18, 55, 13]
            )[0]
            tagged = RNG.random() < 0.70
            tag = f"{RNG.randint(100000000000, 999999999999)}" if tagged else None
            created = owner.created_at + dt.timedelta(minutes=RNG.randint(5, 600))
            lat, lng = jitter(owner.lat, owner.lng, 0.004)
            # age_years is generated *from* age_class so the two never contradict
            # each other on screen (a "calf" aged 11 would look like a bug).
            age_years = {
                AgeClass.calf: round(RNG.uniform(0.2, 1.0), 1),
                AgeClass.young: round(RNG.uniform(1.5, 3.0), 1),
                AgeClass.adult: round(RNG.uniform(3.5, 9.0), 1),
                AgeClass.old: round(RNG.uniform(9.5, 16.0), 1),
            }[age]
            # Most animals get one mark, some get two, a quarter get none — and
            # a second mark never repeats the first.
            n_marks = RNG.choices([0, 1, 2], weights=[25, 40, 35])[0]
            picked = RNG.sample(IDENT_MARKS, n_marks)
            marks = (picked + [None, None])[:2]
            a = Animal(
                id=uuid7(),
                ulb_id=owner.ulb_id,
                owner_id=owner.id,
                species=species,
                sex=sex,
                age_class=age,
                age_years=None if RNG.random() < 0.12 else age_years,
                identification_mark_1=marks[0],
                identification_mark_2=marks[1],
                breed=RNG.choice(BREEDS_CATTLE if species is Species.cattle else BREEDS_BUFFALO),
                colour_markings=RNG.choice(COLOURS),
                tag_id=tag,
                tag_type=TagType.pashu_aadhaar_12 if tagged else TagType.none,
                status=AnimalStatus.registered,
                lat=lat, lng=lng,
                created_by=creator.id,
                created_at=min(created, dt.datetime.now(dt.timezone.utc)),
                updated_at=created,
            )
            db.add(a)
            animals.append(a)
        db.flush()

        # ---- events -----------------------------------------------------
        today = dt.date.today()
        n_events = 0

        def add_event(**kw) -> Event:
            nonlocal n_events
            n_events += 1
            e = Event(id=uuid7(), **kw)
            db.add(e)
            return e

        for a in animals:
            add_event(
                type=EventType.tagging if a.tag_id else EventType.registration,
                animal_id=a.id, owner_id=a.owner_id, ulb_id=a.ulb_id, user_id=a.created_by,
                lat=a.lat, lng=a.lng, occurred_at=a.created_at,
                payload={"kind": "animal", "tag_id": a.tag_id, "demo": True},
            )
        db.flush()

        # ~120 road sightings clustered along the highways
        sighting_animals = RNG.sample(animals, 90)
        offender_pool = RNG.sample(animals, 12)  # a few repeat offenders
        for i in range(120):
            animal = RNG.choice(sighting_animals if i % 4 else offender_pool)
            day = today - dt.timedelta(days=RNG.randint(0, 44))
            base = RNG.choice(ROAD_POINTS)
            lat, lng = jitter(base[0], base[1], 0.012)
            officer = RNG.choice(field_users[next(c for c, u in ulbs.items() if u.id == animal.ulb_id)])
            add_event(
                type=EventType.sighting_road,
                animal_id=animal.id, owner_id=animal.owner_id, ulb_id=animal.ulb_id,
                user_id=officer.id, lat=lat, lng=lng,
                gps_accuracy_m=RNG.choice([5.0, 8.0, 12.0, 20.0]),
                occurred_at=sighting_time(day),
                payload={"road": RNG.choice(["NH-48", "NH-11", "Rewari-Jhajjar Rd", "Circular Rd"]), "demo": True},
            )
        db.flush()

        # 30 impounds -> gaushala intake, some released
        impounded = RNG.sample(animals, 30)
        for animal in impounded:
            day = today - dt.timedelta(days=RNG.randint(0, 40))
            when = sighting_time(day)
            officer = RNG.choice(field_users[next(c for c, u in ulbs.items() if u.id == animal.ulb_id)])
            shelter = RNG.choice([s for s in shelters if s.ulb_id == animal.ulb_id] or shelters)
            add_event(type=EventType.impound, animal_id=animal.id, owner_id=animal.owner_id,
                      ulb_id=animal.ulb_id, user_id=officer.id, lat=animal.lat, lng=animal.lng,
                      occurred_at=when, payload={"reason": "found on road", "demo": True})
            add_event(type=EventType.gaushala_intake, animal_id=animal.id, owner_id=animal.owner_id,
                      ulb_id=animal.ulb_id, user_id=officer.id,
                      occurred_at=when + dt.timedelta(hours=2),
                      payload={"shelter_id": shelter.id, "demo": True})
            animal.status = AnimalStatus.in_gaushala
            animal.current_shelter_id = shelter.id
            shelter.current_count += 1
            if RNG.random() < 0.45:
                add_event(type=EventType.release, animal_id=animal.id, owner_id=animal.owner_id,
                          ulb_id=animal.ulb_id, user_id=officer.id,
                          occurred_at=when + dt.timedelta(days=RNG.randint(1, 5)),
                          payload={"released_to": "owner", "demo": True})
                animal.status = AnimalStatus.released
                animal.current_shelter_id = None
                shelter.current_count = max(0, shelter.current_count - 1)
        db.flush()

        # 18 fines, weighted so a handful of owners are repeat offenders
        fine_owners = [a.owner_id for a in offender_pool] + [RNG.choice(animals).owner_id for _ in range(6)]
        counts: dict[uuid.UUID, int] = {}
        schedule = {1: 5100, 2: 11000, 3: 11000}
        for i, owner_id in enumerate(fine_owners[:18]):
            animal = next(a for a in animals if a.owner_id == owner_id)
            counts[owner_id] = counts.get(owner_id, 0) + 1
            n = counts[owner_id]
            amount = schedule.get(min(n, 3), 11000)
            when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RNG.randint(0, 40))
            officer = RNG.choice(field_users[next(c for c, u in ulbs.items() if u.id == animal.ulb_id)])
            ev = add_event(type=EventType.fine_issued, animal_id=animal.id, owner_id=owner_id,
                           ulb_id=animal.ulb_id, user_id=officer.id, occurred_at=when,
                           payload={"offence_number": n, "amount": str(amount), "demo": True})
            db.flush()
            paid = RNG.random() < 0.5
            db.add(
                Fine(
                    id=uuid7(), event_id=ev.id, animal_id=animal.id, owner_id=owner_id,
                    ulb_id=animal.ulb_id, offence_number=n, amount=amount,
                    status=FineStatus.paid if paid else FineStatus.issued,
                    receipt_no=f"RWR/{RNG.randint(10000, 99999)}" if paid else None,
                    issued_at=when,
                    paid_at=when + dt.timedelta(days=RNG.randint(1, 7)) if paid else None,
                )
            )
        db.flush()

        # 6 lost tags
        for animal in RNG.sample([a for a in animals if a.tag_id], 6):
            when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RNG.randint(0, 30))
            officer = RNG.choice(field_users[next(c for c, u in ulbs.items() if u.id == animal.ulb_id)])
            add_event(type=EventType.tag_lost, animal_id=animal.id, owner_id=animal.owner_id,
                      ulb_id=animal.ulb_id, user_id=officer.id, occurred_at=when,
                      payload={"old_tag_id": animal.tag_id, "suspected": "cut", "demo": True})
            animal.status = AnimalStatus.tag_missing
        db.commit()

        # ---- credentials file -------------------------------------------
        path = creds_path
        unrecoverable = sum(1 for _, pw, _, _ in creds if pw == "(unchanged)")
        preserved = len(kept_hashes)
        lines = [
            "GauTrack DEMO seed credentials",
            f"generated {dt.datetime.now().isoformat(timespec='seconds')}",
            "THIS FILE IS GIT-IGNORED. Delete it before any real deployment.",
            "",
            f"{'username':<12} {'password':<28} {'role':<14} ulb",
            "-" * 72,
        ]
        for username, pw, role, code in creds:
            lines.append(f"{username:<12} {pw:<28} {role:<14} {code}")
        lines.append("")
        if preserved:
            lines.append(
                f"This reseed KEPT the existing password on {preserved} account(s), so nothing"
            )
            lines.append(
                "  that was working stopped working and nobody was signed out."
            )
            lines.append(
                "  To reissue every password instead:  make reseed-new-passwords"
            )
            lines.append("")
        if unrecoverable:
            lines.append(
                f'"(unchanged)" on {unrecoverable} row(s): the password still works, but this'
            )
            lines.append(
                "  file no longer holds it (it was not in the previous copy). Set a new one"
            )
            lines.append("  with:  make set-password U=<username>")
            lines.append("")
        if settings.seed_password.strip():
            lines.append(
                "SEED_PASSWORD is set in .env, so newly created accounts all share that one"
            )
            lines.append("  password and it survives every reseed.")
            lines.append("")
        lines.append("To set a password deliberately:  make set-password U=<username>")
        lines.append("")
        lines.append("Sign in at  http://localhost:8000/admin   (dashboard)")
        lines.append("            http://localhost:8000/app     (field PWA)")
        lines.append("            http://localhost:8000/cm      (viewer account)")
        path.write_text("\n".join(lines) + "\n")
        path.chmod(0o600)

        print(f"[seed] {len(owners)} owners, {len(animals)} animals, {n_events} events, "
              f"{len(shelters)} shelters, {len(creds)} users")
        print(f"[seed] credentials -> {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the GauTrack demo dataset")
    parser.add_argument("--force", action="store_true", help="wipe existing data first")
    parser.add_argument(
        "--new-passwords",
        action="store_true",
        help="with --force, also reissue every password (default: existing passwords are kept)",
    )
    args = parser.parse_args()
    seed(force=args.force, new_passwords=args.new_passwords)


if __name__ == "__main__":
    main()
