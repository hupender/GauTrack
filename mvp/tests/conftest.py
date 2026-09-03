"""Test harness.

Spins up a throwaway `gautrack_test` database on the development Postgres,
migrates it, and hands each test a TestClient plus a small fixture set with a
record in two different ULBs — which is what the IDOR tests need.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"

# 1. real .env (passwords, host, port) ...
load_dotenv(ROOT / ".env")
# 2. ... then the test overrides, which win because they are set in the process
os.environ["POSTGRES_DB"] = "gautrack_test"
os.environ["SEED_DEMO"] = "0"
os.environ["COOKIE_SECURE"] = "0"
os.environ["PHOTO_DIR"] = str(ROOT / "data" / "test_photos")
# The suite logs in far more often than a human would; the throttle itself is
# exercised by test_login_lockout, which uses the failure counter.
os.environ["LOGIN_MAX_PER_MIN_PER_IP"] = "10000"
os.environ["LOGIN_MAX_PER_HOUR_PER_USER"] = "10000"

sys.path.insert(0, str(API))


def _create_test_database() -> None:
    from sqlalchemy import create_engine, text

    from config import settings

    admin_url = settings.owner_database_url.rsplit("/", 1)[0] + "/postgres"
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    with engine.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS gautrack_test WITH (FORCE)"))
        conn.execute(text(f'CREATE DATABASE gautrack_test OWNER "{settings.postgres_owner_user}"'))
    engine.dispose()


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(API / "alembic.ini"))
    cfg.set_main_option("script_location", str(API / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def database():
    _create_test_database()
    cwd = os.getcwd()
    os.chdir(API)
    try:
        _migrate()
    finally:
        os.chdir(cwd)
    yield


PASSWORD = "Test-Password-2026!"


@pytest.fixture(scope="session")
def fixtures(database):
    """Two ULBs, one owner + one tagged animal in each, one user per role."""
    from sqlalchemy import select

    import auth
    from db import SessionLocal
    from ids import uuid7
    from models import Animal, AnimalStatus, Owner, Role, Species, TagType, Ulb, User

    data: dict = {}
    with SessionLocal() as db:
        ulbs = {u.code: u.id for u in db.execute(select(Ulb)).scalars()}
        data["rwr"] = ulbs["RWR"]
        data["bwl"] = ulbs["BWL"]

        def mkuser(username: str, role: Role, ulb_id: int | None) -> uuid.UUID:
            u = User(
                id=uuid7(), username=username, password_hash=auth.hash_password(PASSWORD),
                full_name=username.upper(), role=role, ulb_id=ulb_id,
            )
            db.add(u)
            db.flush()
            return u.id

        data["u_super"] = mkuser("t_super", Role.super_admin, None)
        data["u_rwr_field"] = mkuser("t_rwr_field", Role.field_officer, ulbs["RWR"])
        data["u_rwr_field2"] = mkuser("t_rwr_field2", Role.field_officer, ulbs["RWR"])
        data["u_bwl_field"] = mkuser("t_bwl_field", Role.field_officer, ulbs["BWL"])
        data["u_rwr_admin"] = mkuser("t_rwr_admin", Role.ulb_admin, ulbs["RWR"])
        data["u_viewer"] = mkuser("t_viewer", Role.viewer, None)
        data["u_auditor"] = mkuser("t_auditor", Role.auditor, None)
        data["u_locked"] = mkuser("t_locked", Role.field_officer, ulbs["RWR"])

        for key, code in (("rwr", "RWR"), ("bwl", "BWL")):
            owner = Owner(
                id=uuid7(), ulb_id=ulbs[code], name=f"Owner {code}",
                phone_norm="9876543210" if code == "RWR" else "9812345678",
                ward_or_village="Kosli" if code == "RWR" else "Bawal",
                created_by=data["u_super"],
            )
            db.add(owner)
            db.flush()
            animal = Animal(
                id=uuid7(), ulb_id=ulbs[code], owner_id=owner.id,
                species=Species.cattle, tag_id="111111111111" if code == "RWR" else "222222222222",
                tag_type=TagType.pashu_aadhaar_12, status=AnimalStatus.registered,
                created_by=data["u_super"],
            )
            db.add(animal)
            db.flush()
            data[f"owner_{key}"] = owner.id
            data[f"animal_{key}"] = animal.id
            data[f"tag_{key}"] = animal.tag_id
        db.commit()
    return data


class Client:
    """Thin wrapper that always sends the headers the API demands for writes."""

    def __init__(self, tc):
        self.tc = tc
        self.csrf = ""

    def login(self, username: str, password: str = PASSWORD):
        r = self.tc.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers={"X-Requested-With": "GauTrack"},
        )
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r

    def logout(self):
        self.tc.post("/api/auth/logout", headers={"X-Requested-With": "GauTrack"})
        self.tc.cookies.clear()
        self.csrf = ""

    def _h(self, extra=None):
        h = {"X-Requested-With": "GauTrack", "X-CSRF-Token": self.csrf}
        if extra:
            h.update(extra)
        return h

    def get(self, url, **kw):
        return self.tc.get(url, **kw)

    def post(self, url, json=None, **kw):
        headers = kw.pop("headers", None)
        return self.tc.post(url, json=json, headers=self._h(headers), **kw)

    def post_raw(self, url, **kw):
        headers = kw.pop("headers", None)
        return self.tc.post(url, headers=self._h(headers), **kw)

    def patch(self, url, json=None, **kw):
        headers = kw.pop("headers", None)
        return self.tc.patch(url, json=json, headers=self._h(headers), **kw)


@pytest.fixture
def client(fixtures):
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as tc:
        yield Client(tc)


@pytest.fixture
def owner_session(client, fixtures):
    """A separate raw DB session for tests that poke the database directly."""
    from db import owner_engine

    engine = owner_engine()
    with engine.connect() as conn:
        yield conn
    engine.dispose()


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
