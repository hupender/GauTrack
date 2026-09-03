"""SPEC §7: the offline batch must be idempotent, and a duplicate tag must be a
conflict rather than a second animal."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from ids import uuid7  # noqa: E402


def _batch(owner_id, animal_id, tag):
    return {
        "device_id": str(uuid7()),
        "device_label": "test-device",
        "items": [
            {
                "kind": "owner",
                "id": str(owner_id),
                "data": {"name": "Sync Owner", "phone": "9998887770", "ward_or_village": "Kosli"},
            },
            {
                "kind": "animal",
                "id": str(animal_id),
                "data": {
                    "owner_id": str(owner_id),
                    "species": "cattle",
                    "sex": "female",
                    "tag_id": tag,
                    "tag_type": "pashu_aadhaar_12",
                },
            },
        ],
    }


def _count(conn, sql, params=None):
    from sqlalchemy import text

    return int(conn.execute(text(sql), params or {}).scalar_one())


def test_sync_batch_is_idempotent(client, fixtures, owner_session):
    client.login("t_rwr_field")
    owner_id, animal_id = uuid7(), uuid7()
    body = _batch(owner_id, animal_id, "333333333333")

    first = client.post("/api/sync", json=body)
    assert first.status_code == 200
    assert [r["status"] for r in first.json()["results"]] == ["created", "created"]

    # replay the identical batch, exactly as a device with a lost ack would
    second = client.post("/api/sync", json=body)
    assert second.status_code == 200
    assert [r["status"] for r in second.json()["results"]] == ["duplicate", "duplicate"]

    # and a third time for good measure
    third = client.post("/api/sync", json=body)
    assert [r["status"] for r in third.json()["results"]] == ["duplicate", "duplicate"]

    assert _count(owner_session, "SELECT count(*) FROM owners WHERE id = :i", {"i": str(owner_id)}) == 1
    assert _count(owner_session, "SELECT count(*) FROM animals WHERE id = :i", {"i": str(animal_id)}) == 1
    assert _count(owner_session, "SELECT count(*) FROM animals WHERE tag_id = '333333333333'") == 1


def test_same_tag_from_two_officers_is_a_conflict(client, fixtures, owner_session):
    """SPEC §1.9: the second registration is refused and the client is told what
    the tag is already on, so the officer can record an event instead."""
    tag = "444444444444"

    client.login("t_rwr_field")
    first = client.post("/api/sync", json=_batch(uuid7(), uuid7(), tag))
    assert [r["status"] for r in first.json()["results"]] == ["created", "created"]
    client.logout()

    client.login("t_rwr_field2")
    second = client.post("/api/sync", json=_batch(uuid7(), uuid7(), tag))
    results = {r["kind"]: r for r in second.json()["results"]}
    assert results["owner"]["status"] == "created"
    assert results["animal"]["status"] == "conflict"
    assert results["animal"]["reason"] == "tag already registered"
    assert results["animal"]["existing"]["tag_id"] == tag

    assert _count(owner_session, "SELECT count(*) FROM animals WHERE tag_id = :t", {"t": tag}) == 1


def test_conflict_on_one_item_does_not_lose_the_others(client, fixtures, owner_session):
    """Each item gets its own SAVEPOINT."""
    tag = "555555555555"
    client.login("t_rwr_field")
    client.post("/api/sync", json=_batch(uuid7(), uuid7(), tag))

    good_owner, bad_animal, good_animal = uuid7(), uuid7(), uuid7()
    body = {
        "items": [
            {"kind": "owner", "id": str(good_owner), "data": {"name": "Still Saved"}},
            {"kind": "animal", "id": str(bad_animal),
             "data": {"owner_id": str(good_owner), "species": "cattle",
                      "tag_id": tag, "tag_type": "pashu_aadhaar_12"}},
            {"kind": "animal", "id": str(good_animal),
             "data": {"owner_id": str(good_owner), "species": "buffalo",
                      "tag_id": "666666666666", "tag_type": "pashu_aadhaar_12"}},
        ]
    }
    out = client.post("/api/sync", json=body).json()["results"]
    assert [r["status"] for r in out] == ["created", "conflict", "created"]
    assert _count(owner_session, "SELECT count(*) FROM owners WHERE id = :i", {"i": str(good_owner)}) == 1
    assert _count(owner_session, "SELECT count(*) FROM animals WHERE id = :i", {"i": str(good_animal)}) == 1
    assert _count(owner_session, "SELECT count(*) FROM animals WHERE id = :i", {"i": str(bad_animal)}) == 0


def test_sync_rejects_a_write_outside_scope(client, fixtures):
    client.login("t_rwr_field")
    out = client.post(
        "/api/sync",
        json={"items": [{"kind": "owner", "id": str(uuid7()),
                         "data": {"name": "Elsewhere", "ulb_id": fixtures["bwl"]}}]},
    ).json()["results"]
    assert out[0]["status"] == "rejected"
    assert "ULB" in out[0]["reason"]


def test_sync_batch_size_is_capped(client, fixtures):
    client.login("t_rwr_field")
    items = [{"kind": "owner", "id": str(uuid7()), "data": {"name": f"O{i}"}} for i in range(201)]
    r = client.post("/api/sync", json={"items": items})
    assert r.status_code == 422  # pydantic max_length on the list


def test_event_side_effects(client, fixtures, owner_session):
    """impound -> impounded, gaushala_intake -> in_gaushala + shelter count,
    release -> released, fine_issued -> a fine row with the next offence number."""
    from sqlalchemy import text

    client.login("t_super")

    # a shelter to book the animal into
    shelter_id = owner_session.execute(
        text(
            "INSERT INTO shelters (ulb_id, name, kind, capacity, current_count) "
            "VALUES (:u, 'Test Gaushala', 'gaushala', 50, 0) RETURNING id"
        ),
        {"u": fixtures["rwr"]},
    ).scalar_one()
    owner_session.commit()

    animal = str(fixtures["animal_rwr"])
    base = {"animal_id": animal, "ulb_id": fixtures["rwr"]}

    r = client.post("/api/events", json=dict(base, id=str(uuid7()), type="impound"))
    assert r.status_code == 201
    assert owner_session.execute(
        text("SELECT status::text FROM animals WHERE id = :a"), {"a": animal}
    ).scalar_one() == "impounded"

    client.post("/api/events", json=dict(base, id=str(uuid7()), type="gaushala_intake",
                                         payload={"shelter_id": shelter_id}))
    assert owner_session.execute(
        text("SELECT status::text FROM animals WHERE id = :a"), {"a": animal}
    ).scalar_one() == "in_gaushala"
    assert owner_session.execute(
        text("SELECT current_count FROM shelters WHERE id = :s"), {"s": shelter_id}
    ).scalar_one() == 1

    client.post("/api/events", json=dict(base, id=str(uuid7()), type="release"))
    assert owner_session.execute(
        text("SELECT status::text FROM animals WHERE id = :a"), {"a": animal}
    ).scalar_one() == "released"
    assert owner_session.execute(
        text("SELECT current_count FROM shelters WHERE id = :s"), {"s": shelter_id}
    ).scalar_one() == 0

    client.post("/api/events", json=dict(base, id=str(uuid7()), type="fine_issued"))
    client.post("/api/events", json=dict(base, id=str(uuid7()), type="fine_issued"))
    rows = owner_session.execute(
        text("SELECT offence_number, amount FROM fines WHERE owner_id = :o ORDER BY offence_number"),
        {"o": str(fixtures["owner_rwr"])},
    ).all()
    assert [r[0] for r in rows] == [1, 2]
    assert float(rows[0][1]) == 5100.0 and float(rows[1][1]) == 11000.0


def test_events_are_append_only(client, fixtures, owner_session):
    """The database itself refuses to rewrite history (SPEC §1.7)."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    import pytest

    with pytest.raises(DBAPIError) as exc:
        owner_session.execute(text("UPDATE events SET payload = '{}'::jsonb"))
    assert "append-only" in str(exc.value)
    owner_session.rollback()

    with pytest.raises(DBAPIError) as exc:
        owner_session.execute(text("DELETE FROM events"))
    assert "append-only" in str(exc.value)
    owner_session.rollback()


def test_app_role_has_no_update_or_delete_on_events(client, fixtures):
    """Belt and braces: even without the trigger, the grant is not there."""
    from sqlalchemy import text

    from config import settings
    from db import engine

    with engine.connect() as conn:
        for priv in ("UPDATE", "DELETE"):
            has = conn.execute(
                text("SELECT has_table_privilege(:role, 'events', :priv)"),
                {"role": settings.postgres_app_user, "priv": priv},
            ).scalar_one()
            assert has is False, f"app role must not hold {priv} on events"
        assert conn.execute(
            text("SELECT has_table_privilege(:role, 'audit_log', 'INSERT')"),
            {"role": settings.postgres_app_user},
        ).scalar_one() is False
