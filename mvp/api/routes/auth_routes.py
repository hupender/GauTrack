from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

import auth
from config import settings
from db import get_db
from models import Ulb, User
from schemas import LoginIn, MeOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _me(user: User, csrf: str, db: Session) -> MeOut:
    ulb = db.get(Ulb, user.ulb_id) if user.ulb_id else None
    return MeOut(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        ulb_id=user.ulb_id,
        ulb_code=ulb.code if ulb else None,
        ulb_name=ulb.name if ulb else None,
        csrf_token=csrf,
        demo=settings.is_demo,
    )


@router.post("/login", response_model=MeOut)
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    try:
        user = auth.authenticate(db, request, payload.username, payload.password, payload.totp_code)
    except HTTPException:
        # The failure counter and the attempt record must survive the 401 —
        # otherwise the rollback erases the evidence and lockout never triggers.
        db.commit()
        raise
    token, csrf, expires = auth.create_session(db, user, request)
    db.commit()
    auth.set_session_cookies(response, token, csrf, expires)
    return _me(user, csrf, db)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    # Deliberately not behind get_principal: logging out must work even if the
    # CSRF token was lost, and revoking a session can only ever reduce access.
    auth.revoke_session(db, request.cookies.get(auth.SESSION_COOKIE))
    db.commit()
    auth.clear_session_cookies(response)
    return {"ok": True}


me_router = APIRouter(prefix="/api", tags=["auth"])


@me_router.get("/me", response_model=MeOut)
def me(principal: auth.Principal = Depends(auth.get_principal), db: Session = Depends(get_db)):
    out = _me(principal.user, principal.session.csrf_token, db)
    db.commit()  # persists the sliding session expiry
    return out
