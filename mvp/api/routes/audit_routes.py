from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

import audit as audit_svc
from auth import Principal, get_principal
from db import get_db

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _require_auditor(principal: Principal) -> None:
    if not principal.scope.can_read_audit:
        raise HTTPException(status_code=403, detail="auditor or super_admin only")


@router.get("")
def list_audit(
    table: str | None = Query(default=None, max_length=40),
    row_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    _require_auditor(principal)
    rows = audit_svc.list_audit(db, table=table, row_id=row_id, limit=limit, offset=offset)
    db.commit()
    return {"items": rows}


@router.get("/verify")
def verify(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Recompute both hash chains.  A BROKEN result means somebody edited the
    database behind the application's back."""
    _require_auditor(principal)
    result = audit_svc.verify_all(db)
    db.commit()
    return result
