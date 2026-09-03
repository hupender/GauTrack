"""SPEC §7: unauthenticated access, and lockout after 10 failures."""
from __future__ import annotations

import pytest

PROTECTED_GET = [
    "/api/me",
    "/api/owners",
    "/api/animals",
    "/api/events",
    "/api/stats/summary",
    "/api/stats/timeseries",
    "/api/stats/by_ulb",
    "/api/stats/repeat_offenders",
    "/api/stats/shelters",
    "/api/stats/sightings_geo",
    "/api/audit",
    "/api/audit/verify",
    "/api/users",
    "/api/lookup/tag/111111111111",
]

PROTECTED_POST = [
    ("/api/owners", {"name": "x"}),
    ("/api/animals", {"species": "cattle"}),
    ("/api/events", {"type": "note"}),
    ("/api/sync", {"items": []}),
    ("/api/users", {"username": "abc", "full_name": "x", "role": "viewer"}),
]


@pytest.mark.parametrize("url", PROTECTED_GET)
def test_unauthenticated_get_is_401(client, url):
    assert client.get(url).status_code == 401, url


@pytest.mark.parametrize("url,body", PROTECTED_POST)
def test_unauthenticated_post_is_401(client, url, body):
    assert client.post(url, json=body).status_code == 401, url


def test_photo_route_requires_auth(client, fixtures):
    import uuid

    assert client.get(f"/api/photos/{uuid.uuid4()}").status_code == 401


def test_public_routes_do_not_require_auth(client):
    assert client.get("/report").status_code == 200
    assert client.get("/healthz").status_code == 200
    r = client.tc.post(
        "/api/public/report",
        json={"note": "cow near the flyover", "ulb_id": None},
        headers={"X-Requested-With": "GauTrack"},
    )
    assert r.status_code == 201


def test_login_and_me(client, fixtures):
    assert client.login("t_rwr_field").status_code == 200
    me = client.get("/api/me").json()
    assert me["username"] == "t_rwr_field"
    assert me["role"] == "field_officer"
    assert me["ulb_id"] == fixtures["rwr"]


def test_write_without_requested_with_header_is_rejected(client, fixtures):
    """A cross-site form POST cannot set a custom header — that is the point."""
    client.login("t_rwr_field")
    r = client.tc.post("/api/owners", json={"name": "Cross Site"})
    assert r.status_code == 403


def test_write_without_csrf_token_is_rejected(client, fixtures):
    client.login("t_rwr_field")
    r = client.tc.post(
        "/api/owners", json={"name": "No CSRF"}, headers={"X-Requested-With": "GauTrack"}
    )
    assert r.status_code == 403


def test_logout_revokes_the_session(client, fixtures):
    client.login("t_rwr_field")
    assert client.get("/api/me").status_code == 200
    client.tc.post("/api/auth/logout", headers={"X-Requested-With": "GauTrack"})
    assert client.get("/api/me").status_code == 401


def test_login_lockout_after_ten_failures(client, fixtures):
    """SPEC §7 / §1.4: 10 bad passwords locks the account for 15 minutes."""
    for i in range(10):
        r = client.login("t_locked", "wrong-password")
        assert r.status_code == 401, f"attempt {i} unexpectedly {r.status_code}"

    # 11th attempt, even with the CORRECT password, must be refused
    r = client.login("t_locked")
    assert r.status_code == 429
    assert "locked" in r.json()["detail"].lower()
