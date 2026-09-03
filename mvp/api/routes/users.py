from __future__ import annotations

import secrets
import uuid

import pyotp
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import auth
from auth import Principal, get_principal
from db import get_db
from ids import uuid7
from models import Role, Ulb, User
from schemas import UserCreateOut, UserIn, UserOut

router = APIRouter(prefix="/api/users", tags=["users"])

_WORDS = "kosli bawal masani kund jatusana nahar rewari dharuhera".split()


def _temp_password() -> str:
    """Readable but high-entropy: two dictionary words + 6 random digits +
    4 random chars (~60 bits).  Written down once, changed on first login."""
    return (
        f"{secrets.choice(_WORDS).capitalize()}-{secrets.choice(_WORDS)}-"
        f"{secrets.randbelow(900000) + 100000}-{secrets.token_hex(2)}"
    )


def _require_user_admin(principal: Principal) -> None:
    if not principal.scope.can_manage_users:
        raise HTTPException(status_code=403, detail="super_admin only")


@router.get("", response_model=list[UserOut])
def list_users(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_user_admin(principal)
    rows = db.execute(select(User).order_by(User.username)).scalars().all()
    out = [UserOut.model_validate(u) for u in rows]
    db.commit()
    return out


@router.post("", response_model=UserCreateOut, status_code=201)
def create_user(payload: UserIn, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_user_admin(principal)
    username = payload.username.strip().lower()
    if db.execute(select(User).where(User.username == username)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="username already exists")
    if payload.role in {Role.ulb_admin, Role.field_officer} and payload.ulb_id is None:
        raise HTTPException(status_code=400, detail="this role requires a ULB")
    if payload.ulb_id is not None and db.get(Ulb, payload.ulb_id) is None:
        raise HTTPException(status_code=400, detail="unknown ULB")

    temp = _temp_password()
    secret = auth.new_totp_secret() if payload.totp_enabled else None
    user = User(
        id=uuid7(),
        username=username,
        password_hash=auth.hash_password(temp),
        full_name=payload.full_name.strip(),
        role=payload.role,
        ulb_id=payload.ulb_id,
        phone=payload.phone,
        totp_secret=secret,
        totp_enabled=bool(payload.totp_enabled),
    )
    db.add(user)
    db.flush()
    uri = (
        pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="GauTrack Rewari")
        if secret
        else None
    )
    out = UserCreateOut(user=UserOut.model_validate(user), temp_password=temp, totp_provisioning_uri=uri)
    db.commit()
    return out


@router.post("/{user_id}/reset_password", response_model=UserCreateOut)
def reset_password(user_id: uuid.UUID, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_user_admin(principal)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="not found")
    temp = _temp_password()
    user.password_hash = auth.hash_password(temp)
    user.failed_logins = 0
    user.locked_until = None
    # A password reset must not leave old sessions alive.
    auth.revoke_all_sessions_for(db, user.id)
    out = UserCreateOut(user=UserOut.model_validate(user), temp_password=temp)
    db.commit()
    return out


@router.post("/{user_id}/toggle", response_model=UserOut)
def toggle_user(user_id: uuid.UUID, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    _require_user_admin(principal)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="not found")
    if user.id == principal.scope.user_id:
        raise HTTPException(status_code=400, detail="cannot disable your own account")
    user.is_active = not user.is_active
    if not user.is_active:
        auth.revoke_all_sessions_for(db, user.id)
    out = UserOut.model_validate(user)
    db.commit()
    return out
