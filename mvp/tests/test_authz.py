"""SPEC §7: prove IDOR is impossible, and that role boundaries hold.

The key assertion throughout is 404 rather than 403: a 403 would confirm that a
record with that id exists, which is itself a leak.
"""
from __future__ import annotations


def test_field_officer_cannot_read_other_ulb_owner(client, fixtures):
    client.login("t_rwr_field")
    r = client.get(f"/api/owners/{fixtures['owner_bwl']}")
    assert r.status_code == 404, "must not leak existence of a Bawal owner"


def test_field_officer_cannot_read_other_ulb_animal(client, fixtures):
    client.login("t_rwr_field")
    r = client.get(f"/api/animals/{fixtures['animal_bwl']}")
    assert r.status_code == 404


def test_field_officer_cannot_patch_other_ulb_owner(client, fixtures):
    client.login("t_rwr_field")
    r = client.patch(f"/api/owners/{fixtures['owner_bwl']}", json={"name": "hijacked"})
    assert r.status_code == 404


def test_field_officer_cannot_patch_other_ulb_animal(client, fixtures):
    client.login("t_rwr_field")
    r = client.patch(f"/api/animals/{fixtures['animal_bwl']}", json={"breed": "hijacked"})
    assert r.status_code == 404


def test_field_officer_can_read_own_ulb_owner(client, fixtures):
    client.login("t_rwr_field")
    r = client.get(f"/api/owners/{fixtures['owner_rwr']}")
    assert r.status_code == 200
    body = r.json()
    assert body["phone"] == "9876543210", "in-ULB phone is not masked"
    assert body["phone_masked"] is False


def test_owner_list_never_contains_other_ulb_rows(client, fixtures):
    client.login("t_rwr_field")
    ids = [o["id"] for o in client.get("/api/owners").json()["items"]]
    assert str(fixtures["owner_bwl"]) not in ids
    assert str(fixtures["owner_rwr"]) in ids


def test_tag_lookup_is_district_wide_with_masked_phone(client, fixtures):
    """SPEC §1.5: a Bawal cow WILL be found in Rewari — deliberately."""
    client.login("t_rwr_field")
    r = client.get(f"/api/lookup/tag/{fixtures['tag_bwl']}")
    assert r.status_code == 200
    body = r.json()
    assert body["in_scope"] is False
    assert body["animal"]["id"] == str(fixtures["animal_bwl"])
    assert body["owner"]["phone_masked"] is True
    assert body["owner"]["phone"] != "9812345678"
    assert body["owner"]["phone"].startswith("98") and body["owner"]["phone"].endswith("78")
    assert body["owner"]["name"] == "O. B.", "council R1: out-of-ULB callers see initials only"
    assert body["owner"]["address"] is None, "address is withheld out of ULB"


def test_tag_lookup_is_audited_and_rate_limited(client, fixtures):
    """Council R1 §D4: every lookup (hit or miss) lands in lookup_log; per-user hourly cap."""
    import pytest as _pt
    from sqlalchemy import text as _t
    from sqlalchemy.exc import DBAPIError

    from db import SessionLocal

    client.login("t_rwr_field")
    client.get(f"/api/lookup/tag/{fixtures['tag_bwl']}")
    r = client.get("/api/lookup/tag/999999999999")
    assert r.status_code == 404
    with SessionLocal() as db:
        n = db.execute(_t("SELECT count(*) FROM lookup_log WHERE tag_id IN (:a, '999999999999')"),
                       {"a": fixtures["tag_bwl"]}).scalar_one()
        assert n >= 2, "hit and miss are both logged"
        # append-only: cannot be deleted (statement-level trigger)
        with _pt.raises(DBAPIError):
            db.execute(_t("DELETE FROM lookup_log"))
        db.rollback()
    # rate limit: 120/hour/user — use a throwaway user so other tests keep their quota
    import auth
    from ids import uuid7
    from models import Role, User
    with SessionLocal() as db:
        db.add(User(id=uuid7(), username="t_ratelimit", password_hash=auth.hash_password(
            "Test-Password-2026!"), full_name="RATE", role=Role.field_officer,
            ulb_id=fixtures["rwr"], is_active=True))
        db.commit()
    client.login("t_ratelimit")
    for _ in range(125):
        r = client.get(f"/api/lookup/tag/{fixtures['tag_rwr']}")
        if r.status_code == 429:
            break
    assert r.status_code == 429


def test_tag_lookup_in_own_ulb_is_unmasked(client, fixtures):
    client.login("t_rwr_field")
    body = client.get(f"/api/lookup/tag/{fixtures['tag_rwr']}").json()
    assert body["in_scope"] is True
    assert body["owner"]["phone"] == "9876543210"


def test_super_admin_sees_every_ulb(client, fixtures):
    client.login("t_super")
    assert client.get(f"/api/owners/{fixtures['owner_bwl']}").status_code == 200
    assert client.get(f"/api/owners/{fixtures['owner_rwr']}").status_code == 200


# --------------------------------------------------------------------- viewer
def test_viewer_cannot_read_entities(client, fixtures):
    client.login("t_viewer")
    assert client.get("/api/owners").status_code == 403
    assert client.get("/api/animals").status_code == 403
    assert client.get(f"/api/owners/{fixtures['owner_rwr']}").status_code == 403
    assert client.get(f"/api/animals/{fixtures['animal_rwr']}").status_code == 403


def test_viewer_cannot_read_photos(client, fixtures):
    import uuid

    client.login("t_viewer")
    assert client.get(f"/api/photos/{uuid.uuid4()}").status_code == 404


def test_viewer_can_read_stats(client, fixtures):
    client.login("t_viewer")
    for url in ("/api/stats/summary", "/api/stats/by_ulb", "/api/stats/timeseries",
                "/api/stats/shelters", "/api/stats/sightings_geo", "/api/stats/repeat_offenders"):
        r = client.get(url)
        assert r.status_code == 200, url


def test_viewer_sees_real_district_wide_numbers(client, fixtures):
    """Regression: `viewer` has an empty *entity* scope, which must not leak into
    the aggregate queries and turn the CM dashboard into a wall of zeros."""
    client.login("t_super")
    expected = client.get("/api/stats/summary").json()
    client.logout()

    client.login("t_viewer")
    got = client.get("/api/stats/summary").json()
    assert got["animals"] == expected["animals"] > 0
    assert got["owners"] == expected["owners"] > 0
    assert len(client.get("/api/stats/by_ulb").json()) == len(
        [r for r in client.get("/api/stats/by_ulb").json()]
    )
    assert client.get("/api/stats/by_ulb").json(), "viewer must see every ULB"


def test_viewer_stats_carry_no_owner_identity(client, fixtures):
    client.login("t_viewer")
    for row in client.get("/api/stats/repeat_offenders").json():
        assert row["owner_id"] is None
        assert "***" in row["name"]


def test_viewer_cannot_write(client, fixtures):
    client.login("t_viewer")
    assert client.post("/api/owners", json={"name": "nope"}).status_code == 403


# -------------------------------------------------------------------- auditor
def test_auditor_reads_audit_log_others_cannot(client, fixtures):
    client.login("t_auditor")
    assert client.get("/api/audit").status_code == 200
    client.logout()

    client.login("t_rwr_field")
    assert client.get("/api/audit").status_code == 403
    assert client.get("/api/audit/verify").status_code == 403


def test_only_super_admin_manages_users(client, fixtures):
    client.login("t_rwr_admin")
    assert client.get("/api/users").status_code == 403
    assert client.post(
        "/api/users", json={"username": "sneaky_admin", "full_name": "X", "role": "viewer"}
    ).status_code == 403
    client.logout()

    client.login("t_super")
    assert client.get("/api/users").status_code == 200


def test_only_super_admin_merges_owners(client, fixtures):
    client.login("t_rwr_admin")
    r = client.post(
        f"/api/owners/{fixtures['owner_rwr']}/merge",
        json={"source_id": str(fixtures["owner_bwl"]), "reason": "test merge"},
    )
    assert r.status_code == 403


def test_field_officer_cannot_write_into_another_ulb(client, fixtures):
    """The client does not get to choose its jurisdiction."""
    client.login("t_rwr_field")
    r = client.post("/api/owners", json={"name": "Smuggled", "ulb_id": fixtures["bwl"]})
    assert r.status_code == 400
