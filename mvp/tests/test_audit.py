"""SPEC §7: the chain verifies OK, and tampering with a row directly in SQL makes
it report BROKEN."""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

import audit  # noqa: E402


def test_writes_produce_audit_rows_attributed_to_the_actor(client, fixtures, owner_session):
    client.login("t_rwr_field")
    r = client.post("/api/owners", json={"name": "Audited Owner", "ward_or_village": "Nahar"})
    assert r.status_code == 201
    owner_id = r.json()["owner"]["id"]

    row = owner_session.execute(
        text("SELECT action, table_name, actor_user_id, after FROM audit_log "
             "WHERE table_name = 'owners' AND row_id = :r ORDER BY id DESC LIMIT 1"),
        {"r": owner_id},
    ).mappings().one()
    assert row["action"] == "INSERT"
    assert str(row["actor_user_id"]) == str(fixtures["u_rwr_field"])
    assert row["after"]["name"] == "Audited Owner"


def test_password_hashes_never_reach_the_audit_log(client, fixtures, owner_session):
    client.login("t_super")
    r = client.post("/api/users", json={"username": "audit_probe", "full_name": "Probe", "role": "viewer"})
    assert r.status_code == 201
    row = owner_session.execute(
        text("SELECT after FROM audit_log WHERE table_name = 'users' ORDER BY id DESC LIMIT 1")
    ).scalar_one()
    assert "password_hash" not in row
    assert "totp_secret" not in row


def test_chain_is_ok_after_normal_use(client, fixtures, owner_session):
    client.login("t_rwr_field")
    client.post("/api/owners", json={"name": "Chain Owner"})
    result = audit.verify_all(owner_session)
    assert result["ok"] is True, result
    assert result["audit_log"]["rows"] > 0
    assert result["events"]["rows"] > 0


def test_verify_endpoint_agrees(client, fixtures):
    client.login("t_auditor")
    body = client.get("/api/audit/verify").json()
    assert body["ok"] is True
    assert len(body["audit_log"]["tip_hash"]) == 64


def test_tampering_with_an_audit_row_is_detected(client, fixtures, owner_session):
    """Simulate an insider with direct database access editing a record."""
    client.login("t_rwr_field")
    r = client.post("/api/owners", json={"name": "Tamper Target", "ward_or_village": "Kund"})
    assert r.status_code == 201

    assert audit.verify_all(owner_session)["ok"] is True

    victim = owner_session.execute(
        text("SELECT id FROM audit_log WHERE table_name='owners' ORDER BY id DESC LIMIT 1")
    ).scalar_one()
    original = owner_session.execute(
        text("SELECT after FROM audit_log WHERE id = :i"), {"i": victim}
    ).scalar_one()

    # audit_log is append-only, so even the schema owner has to switch the guard
    # off first — which is exactly the noisy, deliberate act we want to catch.
    owner_session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
    owner_session.execute(
        text("UPDATE audit_log SET after = jsonb_set(after, '{name}', '\"Someone Else\"') WHERE id = :i"),
        {"i": victim},
    )
    owner_session.commit()

    broken = audit.verify_all(owner_session)
    assert broken["ok"] is False
    assert broken["audit_log"]["ok"] is False
    first = broken["audit_log"]["first_broken"]
    assert first["id"] == victim
    assert first["content_broken"] is True

    # ---- restore and confirm the chain heals ----
    owner_session.execute(
        text("UPDATE audit_log SET after = :a WHERE id = :i"),
        {"a": original if isinstance(original, str) else __import__("json").dumps(original), "i": victim},
    )
    owner_session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
    owner_session.commit()

    assert audit.verify_all(owner_session)["ok"] is True


def _save_audit_row(conn, row_id):
    return conn.execute(
        text("SELECT id, ts, actor_user_id, ip, action, table_name, row_id, before, after, prev_hash, hash "
             "FROM audit_log WHERE id = :i"),
        {"i": row_id},
    ).mappings().one()


def _reinsert_audit_row(conn, saved):
    import json

    conn.execute(
        text("INSERT INTO audit_log (id, ts, actor_user_id, ip, action, table_name, row_id, "
             "before, after, prev_hash, hash) "
             "VALUES (:id,:ts,:actor,:ip,:action,:table,:row,:before,:after,:prev,:hash)"),
        {
            "id": saved["id"], "ts": saved["ts"], "actor": saved["actor_user_id"], "ip": saved["ip"],
            "action": saved["action"], "table": saved["table_name"], "row": saved["row_id"],
            "before": json.dumps(saved["before"]) if saved["before"] is not None else None,
            "after": json.dumps(saved["after"]) if saved["after"] is not None else None,
            "prev": saved["prev_hash"], "hash": saved["hash"],
        },
    )


def test_deleting_a_middle_audit_row_breaks_the_link(client, fixtures, owner_session):
    """Cutting a row out of the middle of the chain leaves the next row pointing
    at a hash that is no longer there."""
    client.login("t_rwr_field")
    client.post("/api/owners", json={"name": "Link Target A"})
    client.post("/api/owners", json={"name": "Link Target B"})
    client.post("/api/owners", json={"name": "Link Target C"})

    ids = [r[0] for r in owner_session.execute(
        text("SELECT id FROM audit_log ORDER BY id DESC LIMIT 3")
    ).all()]
    victim = ids[1]                       # a middle row, not the tip
    saved = _save_audit_row(owner_session, victim)

    owner_session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
    owner_session.execute(text("DELETE FROM audit_log WHERE id = :i"), {"i": victim})
    owner_session.commit()

    broken = audit.verify_all(owner_session)
    assert broken["ok"] is False
    assert broken["audit_log"]["first_broken"]["link_broken"] is True

    _reinsert_audit_row(owner_session, saved)
    owner_session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
    owner_session.commit()
    assert audit.verify_all(owner_session)["ok"] is True


def test_truncating_the_tip_is_caught_by_the_published_anchor_not_the_chain(
    client, fixtures, owner_session
):
    """An honest statement of the limit of a bare hash chain.

    Lopping rows off the END leaves a shorter chain that is still internally
    consistent — no self-check can see it. What catches it is the tip hash
    published each day by scripts/anchor.py: the recorded anchor no longer
    appears anywhere in the chain. This test documents that property, which is
    the whole reason the anchor script exists.
    """
    client.login("t_rwr_field")
    client.post("/api/owners", json={"name": "Anchor Target"})

    # yesterday's published anchor
    anchored_tip = audit.verify_all(owner_session)["audit_log"]["tip_hash"]
    assert audit.verify_all(owner_session)["ok"] is True

    tip_id = owner_session.execute(text("SELECT id FROM audit_log ORDER BY id DESC LIMIT 1")).scalar_one()
    saved = _save_audit_row(owner_session, tip_id)

    owner_session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
    owner_session.execute(text("DELETE FROM audit_log WHERE id = :i"), {"i": tip_id})
    owner_session.commit()

    after = audit.verify_all(owner_session)
    assert after["ok"] is True, "a truncated chain is still self-consistent — this is expected"

    # ...but the published hash is gone, which is how the auditor notices.
    still_present = owner_session.execute(
        text("SELECT count(*) FROM audit_log WHERE hash = :h"), {"h": anchored_tip}
    ).scalar_one()
    assert still_present == 0, "the anchor published earlier no longer appears in the chain"

    _reinsert_audit_row(owner_session, saved)
    owner_session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
    owner_session.commit()
    assert audit.verify_all(owner_session)["ok"] is True
    assert owner_session.execute(
        text("SELECT count(*) FROM audit_log WHERE hash = :h"), {"h": anchored_tip}
    ).scalar_one() == 1


def test_event_chain_detects_tampering(client, fixtures, owner_session):
    client.login("t_rwr_field")
    client.post("/api/owners", json={"name": "Event Chain Owner"})
    assert audit.verify_event_chain(owner_session)["ok"] is True

    seq, payload = owner_session.execute(
        text("SELECT seq, payload FROM events ORDER BY seq DESC LIMIT 1")
    ).one()

    owner_session.execute(text("ALTER TABLE events DISABLE TRIGGER events_append_only"))
    owner_session.execute(
        text("UPDATE events SET payload = '{\"forged\": true}'::jsonb WHERE seq = :s"), {"s": seq}
    )
    owner_session.commit()

    result = audit.verify_event_chain(owner_session)
    assert result["ok"] is False
    assert result["first_broken"]["seq"] == seq

    owner_session.execute(
        text("UPDATE events SET payload = :p WHERE seq = :s"),
        {"p": __import__("json").dumps(payload), "s": seq},
    )
    owner_session.execute(text("ALTER TABLE events ENABLE TRIGGER events_append_only"))
    owner_session.commit()
    assert audit.verify_event_chain(owner_session)["ok"] is True
