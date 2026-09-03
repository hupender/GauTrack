"""Dashboard aggregates.

Every query here is aggregate-only and carries no personal data except the
repeat-offender list, which is name-masked for the `viewer` role.  That is what
lets the CM / public dashboard share the same endpoints as the admin view.

Citizen reports (`events.payload.source = 'public'`, unauthenticated) are deliberately
EXCLUDED from every headline sighting number here: an unverified public channel must not
be able to manufacture a "spike" the day before a review (council R1, security lens).
They remain visible in the events feed for officers to verify and act on.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from authz import Scope
from models import Role


def _window(scope: Scope, ulb: int | None) -> dict[str, Any]:
    """Common bind parameters: the ULB restriction is derived from the session,
    never taken from the query string alone."""
    # NB: stats_ulb_ids, not ulb_ids — `viewer` has no per-record access but is
    # district-wide for aggregates. See authz.Scope.stats_ulb_ids.
    scope_ulbs = scope.stats_ulb_ids
    if scope_ulbs is None:
        allowed: list[int] | None = None
    else:
        allowed = list(scope_ulbs)
    if ulb is not None:
        if allowed is None:
            allowed = [ulb]
        elif ulb in allowed:
            allowed = [ulb]
        else:
            allowed = [-1]  # asked for a ULB outside scope: return nothing
    return {
        "all_ulbs": allowed is None,
        "ulbs": allowed or [],
    }


_ULB_FILTER = "(:all_ulbs OR {col} = ANY(:ulbs))"


def summary(db: Session, scope: Scope, ulb: int | None) -> dict[str, Any]:
    p = _window(scope, ulb)
    a = _ULB_FILTER.format(col="a.ulb_id")
    o = _ULB_FILTER.format(col="o.ulb_id")
    e = _ULB_FILTER.format(col="e.ulb_id")
    f = _ULB_FILTER.format(col="f.ulb_id")
    s = _ULB_FILTER.format(col="s.ulb_id")

    row = db.execute(
        text(
            f"""
            SELECT
              (SELECT count(*) FROM owners o WHERE o.merged_into IS NULL AND {o})           AS owners,
              (SELECT count(*) FROM animals a WHERE {a})                                    AS animals,
              (SELECT count(*) FROM animals a WHERE a.tag_id IS NOT NULL AND {a})           AS tagged,
              (SELECT count(*) FROM animals a WHERE a.created_at::date = current_date
                                                AND a.tag_id IS NOT NULL AND {a})           AS tagged_today,
              (SELECT count(*) FROM animals a WHERE a.status = 'impounded' AND {a})         AS impounded_now,
              (SELECT count(*) FROM animals a WHERE a.status = 'in_gaushala' AND {a})       AS in_gaushala,
              (SELECT count(*) FROM events e WHERE e.type = 'sighting_road' AND coalesce(e.payload->>'source','') <> 'public'
                                               AND e.occurred_at > now() - interval '7 days'
                                               AND {e})                                     AS sightings_7d,
              (SELECT count(*) FROM events e WHERE e.type = 'sighting_road' AND coalesce(e.payload->>'source','') <> 'public'
                                               AND e.occurred_at::date = current_date
                                               AND {e})                                     AS sightings_today,
              (SELECT coalesce(count(*),0) FROM fines f WHERE {f})                          AS fines_issued,
              (SELECT coalesce(sum(f.amount),0) FROM fines f WHERE {f})                     AS fines_amount,
              (SELECT coalesce(sum(f.amount),0) FROM fines f WHERE f.status = 'paid' AND {f}) AS fines_collected,
              (SELECT count(*) FROM (SELECT f.owner_id FROM fines f WHERE {f}
                                      GROUP BY f.owner_id HAVING count(*) > 1) x)           AS repeat_offenders,
              (SELECT coalesce(sum(s.current_count),0) FROM shelters s WHERE {s})           AS shelter_occupancy,
              (SELECT coalesce(sum(s.capacity),0) FROM shelters s WHERE {s})                AS shelter_capacity
            """
        ),
        p,
    ).mappings().one()

    d = dict(row)
    d["fines_amount"] = float(d["fines_amount"] or 0)
    d["fines_collected"] = float(d["fines_collected"] or 0)
    d["tagged_pct"] = round(100.0 * d["tagged"] / d["animals"], 1) if d["animals"] else 0.0
    d["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return d


def timeseries(db: Session, scope: Scope, ulb: int | None, days: int = 30) -> dict[str, Any]:
    p = _window(scope, ulb)
    p["days"] = max(1, min(days, 365))
    rows = db.execute(
        text(
            f"""
            WITH d AS (
              SELECT generate_series(current_date - (CAST(:days AS int) - 1), current_date, '1 day')::date AS day
            )
            SELECT d.day,
                   (SELECT count(*) FROM animals a
                     WHERE a.created_at::date = d.day AND {_ULB_FILTER.format(col='a.ulb_id')}) AS animals,
                   (SELECT count(*) FROM owners o
                     WHERE o.created_at::date = d.day AND {_ULB_FILTER.format(col='o.ulb_id')}) AS owners,
                   (SELECT count(*) FROM events e
                     WHERE e.type = 'sighting_road' AND coalesce(e.payload->>'source','') <> 'public' AND e.occurred_at::date = d.day
                       AND {_ULB_FILTER.format(col='e.ulb_id')}) AS sightings
              FROM d ORDER BY d.day
            """
        ),
        p,
    ).mappings().all()
    return {
        "labels": [r["day"].isoformat() for r in rows],
        "animals": [r["animals"] for r in rows],
        "owners": [r["owners"] for r in rows],
        "sightings": [r["sightings"] for r in rows],
    }


def by_ulb(db: Session, scope: Scope) -> list[dict[str, Any]]:
    p = _window(scope, None)
    rows = db.execute(
        text(
            f"""
            SELECT u.id, u.code, u.name,
                   (SELECT count(*) FROM animals a WHERE a.ulb_id = u.id) AS animals,
                   (SELECT count(*) FROM animals a WHERE a.ulb_id = u.id AND a.tag_id IS NOT NULL) AS tagged,
                   (SELECT count(*) FROM owners o WHERE o.ulb_id = u.id AND o.merged_into IS NULL) AS owners,
                   (SELECT count(*) FROM events e WHERE e.ulb_id = u.id AND e.type = 'sighting_road' AND coalesce(e.payload->>'source','') <> 'public'
                     AND e.occurred_at > now() - interval '30 days') AS sightings_30d
              FROM ulbs u
             WHERE {_ULB_FILTER.format(col='u.id')}
             ORDER BY u.id
            """
        ),
        p,
    ).mappings().all()
    return [dict(r) for r in rows]


def species_sex(db: Session, scope: Scope, ulb: int | None) -> list[dict[str, Any]]:
    p = _window(scope, ulb)
    rows = db.execute(
        text(
            f"""
            SELECT a.species::text AS species, a.sex::text AS sex, count(*) AS n
              FROM animals a WHERE {_ULB_FILTER.format(col='a.ulb_id')}
             GROUP BY 1,2 ORDER BY 1,2
            """
        ),
        p,
    ).mappings().all()
    return [dict(r) for r in rows]


def sightings_by_hour(db: Session, scope: Scope, ulb: int | None) -> dict[str, Any]:
    p = _window(scope, ulb)
    rows = db.execute(
        text(
            f"""
            SELECT extract(hour FROM e.occurred_at AT TIME ZONE 'Asia/Kolkata')::int AS hr, count(*) AS n
              FROM events e
             WHERE e.type = 'sighting_road' AND coalesce(e.payload->>'source','') <> 'public' AND {_ULB_FILTER.format(col='e.ulb_id')}
             GROUP BY 1 ORDER BY 1
            """
        ),
        p,
    ).mappings().all()
    counts = {int(r["hr"]): int(r["n"]) for r in rows}
    return {"labels": [f"{h:02d}" for h in range(24)], "counts": [counts.get(h, 0) for h in range(24)]}


def repeat_offenders(db: Session, scope: Scope, ulb: int | None, limit: int = 10) -> list[dict[str, Any]]:
    p = _window(scope, ulb)
    p["lim"] = max(1, min(limit, 100))
    rows = db.execute(
        text(
            f"""
            SELECT o.id, o.name, o.ward_or_village, o.ulb_id,
                   count(f.id) AS offences,
                   coalesce(sum(f.amount),0) AS total,
                   coalesce(sum(f.amount) FILTER (WHERE f.status = 'paid'),0) AS paid
              FROM fines f JOIN owners o ON o.id = f.owner_id
             WHERE {_ULB_FILTER.format(col='f.ulb_id')}
             GROUP BY o.id, o.name, o.ward_or_village, o.ulb_id
             HAVING count(f.id) >= 1
             ORDER BY offences DESC, total DESC
             LIMIT :lim
            """
        ),
        p,
    ).mappings().all()

    out = []
    for r in rows:
        name = r["name"]
        if scope.role is Role.viewer:
            # aggregate dashboards never carry an identifiable owner name
            name = (name.split(" ")[0][:1] + "***") if name else "***"
        out.append(
            {
                "owner_id": str(r["id"]) if scope.role is not Role.viewer else None,
                "name": name,
                "ward_or_village": r["ward_or_village"] if scope.role is not Role.viewer else None,
                "ulb_id": r["ulb_id"],
                "offences": int(r["offences"]),
                "total": float(r["total"]),
                "paid": float(r["paid"]),
            }
        )
    return out


def shelters(db: Session, scope: Scope, ulb: int | None) -> list[dict[str, Any]]:
    p = _window(scope, ulb)
    rows = db.execute(
        text(
            f"""
            SELECT s.id, s.name, s.kind::text AS kind, s.capacity, s.current_count, s.ulb_id, s.lat, s.lng
              FROM shelters s WHERE {_ULB_FILTER.format(col='s.ulb_id')}
             ORDER BY s.ulb_id, s.name
            """
        ),
        p,
    ).mappings().all()
    return [dict(r) | {"pct": round(100.0 * r["current_count"] / r["capacity"], 1) if r["capacity"] else 0.0} for r in rows]


def sightings_geo(db: Session, scope: Scope, ulb: int | None, days: int = 30) -> list[dict[str, Any]]:
    """Rounded to ~100 m and returned without any owner/animal identifier."""
    p = _window(scope, ulb)
    p["days"] = max(1, min(days, 365))
    rows = db.execute(
        text(
            f"""
            SELECT round(e.lat::numeric, 3) AS lat, round(e.lng::numeric, 3) AS lng,
                   count(*) AS n, max(e.occurred_at) AS last_seen
              FROM events e
             WHERE e.type = 'sighting_road' AND coalesce(e.payload->>'source','') <> 'public' AND e.lat IS NOT NULL AND e.lng IS NOT NULL
               AND e.occurred_at > now() - CAST(CAST(:days AS int) || ' days' AS interval)
               AND {_ULB_FILTER.format(col='e.ulb_id')}
             GROUP BY 1,2 ORDER BY n DESC LIMIT 2000
            """
        ),
        p,
    ).mappings().all()
    return [
        {"lat": float(r["lat"]), "lng": float(r["lng"]), "n": int(r["n"]), "last_seen": r["last_seen"].isoformat()}
        for r in rows
    ]


def leaderboard(db: Session, scope: Scope, ulb: int | None) -> list[dict[str, Any]]:
    p = _window(scope, ulb)
    rows = db.execute(
        text(
            f"""
            SELECT u.id, u.full_name, u.username, u.ulb_id,
                   count(*) FILTER (WHERE a.created_at::date = current_date) AS today,
                   count(*) FILTER (WHERE a.created_at > now() - interval '7 days') AS week,
                   count(*) AS total
              FROM animals a JOIN users u ON u.id = a.created_by
             WHERE {_ULB_FILTER.format(col='a.ulb_id')}
             GROUP BY u.id, u.full_name, u.username, u.ulb_id
             ORDER BY week DESC, total DESC LIMIT 15
            """
        ),
        p,
    ).mappings().all()
    return [
        {
            "user_id": str(r["id"]),
            "full_name": r["full_name"] if scope.role is not Role.viewer else "Field team",
            "ulb_id": r["ulb_id"],
            "today": int(r["today"]),
            "week": int(r["week"]),
            "total": int(r["total"]),
        }
        for r in rows
    ]
