"""CSV export: who may take a bulk copy, of what, and is it recorded.

Bulk extraction of a registry's personal data is the highest-value action in the
whole system for an attacker (or a careless clerk with a USB stick), so the rules
around it get their own tests.
"""
from __future__ import annotations


def test_unauthenticated_cannot_export(client):
    assert client.get("/api/export/owners.csv").status_code == 401


def test_field_officer_cannot_bulk_export(client, fixtures):
    """A field officer may look up any tag, but may not walk away with the list."""
    client.login("t_rwr_field")
    r = client.get("/api/export/owners.csv")
    assert r.status_code == 403


def test_viewer_cannot_bulk_export(client, fixtures):
    """The CM/press role sees aggregates only, never personal records."""
    client.login("t_viewer")
    assert client.get("/api/export/owners.csv").status_code == 403


def test_super_admin_exports_all_ulbs(client, fixtures):
    client.login("t_super")
    r = client.get("/api/export/owners.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    body = r.text
    assert "name" in body.splitlines()[0]
    assert "Owner RWR" in body and "Owner BWL" in body


def test_ulb_admin_export_is_scoped_to_own_ulb(client, fixtures):
    """Scope on screen is scope in the file: RWR admin must not get BWL rows."""
    client.login("t_rwr_admin")
    body = client.get("/api/export/owners.csv").text
    assert "Owner RWR" in body
    assert "Owner BWL" not in body


def test_ulb_admin_cannot_widen_scope_via_filter(client, fixtures):
    client.login("t_rwr_admin")
    r = client.get(f"/api/export/owners.csv?ulb={fixtures['bwl']}")
    assert r.status_code == 403


def test_export_never_includes_secrets(client, fixtures):
    """phone_hash is a secret-salted digest; password hashes live in a table with no export."""
    client.login("t_super")
    header = client.get("/api/export/owners.csv").text.splitlines()[0]
    assert "phone_hash" not in header
    assert "password" not in header


def test_csv_formula_injection_is_defused(client, fixtures):
    """An owner named `=cmd()` must not execute when the clerk opens the file."""
    client.login("t_super")
    payload = {
        "id": "01a01000-0000-7000-8000-00000000ce11",
        "name": "=HYPERLINK(\"http://evil\",\"click\")",
        "ulb_id": fixtures["rwr"],
        "keeper_type": "household",
    }
    created = client.post("/api/owners", json=payload)
    assert created.status_code in (200, 201), created.text
    body = client.get("/api/export/owners.csv").text
    # every occurrence must carry the neutralising leading apostrophe
    assert "'=HYPERLINK" in body, "formula should be neutralised with a leading quote"
    assert body.count("=HYPERLINK") == body.count("'=HYPERLINK"), "an unquoted formula reached the file"
    # and enums must be exported as values, not Python reprs
    assert "KeeperType." not in body, "enum exported as a Python repr"
    assert "household" in body


def test_export_is_recorded_and_append_only(client, fixtures):
    """Every bulk copy is logged, and the log cannot be erased afterwards."""
    import pytest
    from sqlalchemy import text as _t
    from sqlalchemy.exc import DBAPIError

    from db import SessionLocal

    client.login("t_auditor")
    assert client.get("/api/export/animals.csv").status_code == 200
    with SessionLocal() as db:
        n = db.execute(_t("SELECT count(*) FROM export_log WHERE dataset='animals'")).scalar_one()
        assert n >= 1, "the export must be recorded"
        with pytest.raises(DBAPIError):
            db.execute(_t("DELETE FROM export_log"))
        db.rollback()


def test_date_filter_slices_the_export(client, fixtures):
    """'Send me just this quarter' has to work, or people export everything."""
    client.login("t_super")
    empty = client.get("/api/export/fines.csv?from=1990-01-01&to=1990-12-31")
    assert empty.status_code == 200
    assert len(empty.text.strip().splitlines()) == 1, "header only, no rows in that range"


def test_unknown_dataset_is_404(client, fixtures):
    client.login("t_super")
    assert client.get("/api/export/passwords.csv").status_code == 404
