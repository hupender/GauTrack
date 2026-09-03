"""The survey fields added after the first field walk-through.

Three groups of optional columns, and the point of every test here is the word
*optional*: an officer standing in a lane with a hostile keeper must never be
blocked from finishing a registration because a box was left empty.  These
tests pin that down, along with the bounds that stop a typo entering the
register and the promise that everything captured comes back out in the CSV.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from ids import uuid7  # noqa: E402


def _owner(client, **extra):
    body = {"id": str(uuid7()), "name": "Survey Owner", "ward_or_village": "Kosli"}
    body.update(extra)
    return client.post("/api/owners", json=body)


def _animal(client, owner_id, tag, **extra):
    body = {
        "id": str(uuid7()), "owner_id": str(owner_id), "species": "cattle",
        "sex": "female", "tag_id": tag, "tag_type": "pashu_aadhaar_12",
    }
    body.update(extra)
    return client.post("/api/animals", json=body)


# ------------------------------------------------------------------ owners
def test_owner_records_declared_herd_and_premises_area(client, fixtures):
    client.login("t_rwr_field")
    r = _owner(client, self_declared_cattle_count=6, premises_area_sq_yards="250.5")
    assert r.status_code == 201, r.text
    owner = r.json()["owner"]
    assert owner["self_declared_cattle_count"] == 6
    assert float(owner["premises_area_sq_yards"]) == 250.5


def test_owner_survey_fields_are_optional(client, fixtures):
    """Left blank they must arrive as null, never as zero: "not asked" and
    "declared none" are different facts and the audit reading depends on it."""
    client.login("t_rwr_field")
    r = _owner(client)
    assert r.status_code == 201, r.text
    owner = r.json()["owner"]
    assert owner["self_declared_cattle_count"] is None
    assert owner["premises_area_sq_yards"] is None


def test_owner_declared_count_is_bounded(client, fixtures):
    client.login("t_rwr_field")
    assert _owner(client, self_declared_cattle_count=-1).status_code == 422
    assert _owner(client, self_declared_cattle_count=99999).status_code == 422


# ----------------------------------------------------------------- animals
def test_animal_records_age_in_years_and_two_marks(client, fixtures):
    client.login("t_rwr_field")
    r = _animal(
        client, fixtures["owner_rwr"], "444444444401",
        age_years="6.5",
        identification_mark_1="Broken left horn",
        identification_mark_2="White tip on tail",
    )
    assert r.status_code == 201, r.text
    a = r.json()
    assert float(a["age_years"]) == 6.5
    assert a["identification_mark_1"] == "Broken left horn"
    assert a["identification_mark_2"] == "White tip on tail"


def test_animal_marks_and_age_are_optional(client, fixtures):
    client.login("t_rwr_field")
    r = _animal(client, fixtures["owner_rwr"], "444444444402")
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["age_years"] is None
    assert a["identification_mark_1"] is None and a["identification_mark_2"] is None


def test_animal_age_years_is_bounded(client, fixtures):
    """A year typed into the age box ("1998") must be refused at the edge, not
    stored and not raised as a 500 out of the database CHECK."""
    client.login("t_rwr_field")
    assert _animal(client, fixtures["owner_rwr"], "444444444403", age_years="1998").status_code == 422
    assert _animal(client, fixtures["owner_rwr"], "444444444404", age_years="-1").status_code == 422


def test_marks_survive_a_tag_lookup(client, fixtures):
    """The reason the marks exist: an officer holding an animal whose tag has
    been cut off looks up what is left and confirms the identity by eye."""
    client.login("t_rwr_field")
    _animal(
        client, fixtures["owner_rwr"], "444444444405",
        identification_mark_1="Torn right ear", age_years="4",
    )
    out = client.get("/api/lookup/tag/444444444405").json()
    assert out["animal"]["identification_mark_1"] == "Torn right ear"
    assert float(out["animal"]["age_years"]) == 4


# ------------------------------------------------------------- corrections
def test_age_years_can_be_corrected(client, fixtures):
    """A Decimal has to survive the round trip into the correction event's JSONB
    payload; before the engine was given a serialiser this raised a 500."""
    client.login("t_rwr_field")
    created = _animal(client, fixtures["owner_rwr"], "444444444406", age_years="3").json()
    r = client.patch(
        f"/api/animals/{created['id']}",
        json={"age_years": "4.5", "identification_mark_2": "Hump scar",
              "reason": "keeper corrected the age on a follow-up visit"},
    )
    assert r.status_code == 200, r.text
    assert float(r.json()["age_years"]) == 4.5
    assert r.json()["identification_mark_2"] == "Hump scar"


# -------------------------------------------------------------------- CSV
def test_csv_exports_carry_the_survey_columns(client, fixtures):
    client.login("t_rwr_admin")
    owners = client.get("/api/export/owners.csv")
    assert owners.status_code == 200
    header = owners.text.splitlines()[0]
    assert "self_declared_cattle_count" in header
    assert "premises_area_sq_yards" in header

    animals = client.get("/api/export/animals.csv")
    assert animals.status_code == 200
    header = animals.text.splitlines()[0]
    for column in ("age_years", "identification_mark_1", "identification_mark_2"):
        assert column in header
