from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

import stats as stats_svc
from auth import Principal, get_principal
from db import get_db

router = APIRouter(prefix="/api/stats", tags=["stats"])

# Every role, including `viewer`, may read these: they are aggregates with no
# personal data (SPEC §3).


@router.get("/summary")
def summary(
    ulb: int | None = None,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    out = stats_svc.summary(db, principal.scope, ulb)
    db.commit()
    return out


@router.get("/timeseries")
def timeseries(
    ulb: int | None = None,
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    out = stats_svc.timeseries(db, principal.scope, ulb, days)
    db.commit()
    return out


@router.get("/by_ulb")
def by_ulb(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    out = stats_svc.by_ulb(db, principal.scope)
    db.commit()
    return out


@router.get("/species_sex")
def species_sex(ulb: int | None = None, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    out = stats_svc.species_sex(db, principal.scope, ulb)
    db.commit()
    return out


@router.get("/hourly")
def hourly(ulb: int | None = None, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    out = stats_svc.sightings_by_hour(db, principal.scope, ulb)
    db.commit()
    return out


@router.get("/repeat_offenders")
def repeat_offenders(
    ulb: int | None = None,
    limit: int = Query(default=10, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    out = stats_svc.repeat_offenders(db, principal.scope, ulb, limit)
    db.commit()
    return out


@router.get("/shelters")
def shelters(ulb: int | None = None, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    out = stats_svc.shelters(db, principal.scope, ulb)
    db.commit()
    return out


@router.get("/sightings_geo")
def sightings_geo(
    ulb: int | None = None,
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    out = stats_svc.sightings_geo(db, principal.scope, ulb, days)
    db.commit()
    return out


@router.get("/leaderboard")
def leaderboard(ulb: int | None = None, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    out = stats_svc.leaderboard(db, principal.scope, ulb)
    db.commit()
    return out
