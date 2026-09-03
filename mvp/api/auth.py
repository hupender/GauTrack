"""Authentication: argon2id passwords, server-side sessions, TOTP, login
throttling and CSRF.  No JWTs — a session row can be revoked instantly, a signed
token cannot."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import uuid

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from authz import Scope, scope_for
from config import settings
from db import get_db, set_db_actor
from models import LoginAttempt, Role, SessionRow, User

SESSION_COOKIE = "gt_session"
CSRF_COOKIE = "gt_csrf"
REQUESTED_WITH_HEADER = "x-requested-with"
REQUESTED_WITH_VALUE = "GauTrack"
CSRF_HEADER = "x-csrf-token"

# argon2id.  64 MiB / 3 passes / 4 lanes is the OWASP baseline; comfortable for a
# handful of logins a minute on a 2-4 vCPU box.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)

ADMINISH_ROLES = {Role.super_admin, Role.ulb_admin, Role.viewer, Role.auditor}


# --------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# --------------------------------------------------------------------------- helpers
def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def client_ip(request: Request) -> str:
    """Trust X-Forwarded-For only as far as TRUSTED_PROXY_COUNT hops.

    Blindly trusting the header would let anyone spoof their IP and walk around
    the login rate limit.
    """
    n = settings.trusted_proxy_count
    if n > 0:
        xff = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) >= n:
            return parts[-n][:45]
    # "0.0.0.0" here is a label meaning "no peer address", not a bind address.
    return (request.client.host if request.client else "0.0.0.0")[:45]  # nosec B104


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def idle_minutes_for(role: Role) -> int:
    return (
        settings.session_idle_minutes_admin
        if role in ADMINISH_ROLES
        else settings.session_idle_minutes_field
    )


# --------------------------------------------------------------------------- throttling
def _count_attempts(db: Session, *, kind: str, ip: str | None, username: str | None, interval: str) -> int:
    stmt = text(
        "SELECT count(*) FROM login_attempts "
        "WHERE kind = :kind AND ts > now() - CAST(:interval AS interval) "
        "  AND (CAST(:ip AS text) IS NULL OR ip = :ip) "
        "  AND (CAST(:username AS text) IS NULL OR username = :username)"
    )
    return int(db.execute(stmt, {"kind": kind, "ip": ip, "username": username, "interval": interval}).scalar_one())


def record_attempt(db: Session, *, ip: str, username: str | None, ok: bool, kind: str = "login") -> None:
    db.add(LoginAttempt(ip=ip, username=username, ok=ok, kind=kind))
    db.flush()


def check_login_rate(db: Session, *, ip: str, username: str) -> None:
    """5 attempts/min/IP and 10 attempts/hour/username (SPEC §1.4)."""
    if _count_attempts(db, kind="login", ip=ip, username=None, interval="1 minute") >= settings.login_max_per_min_per_ip:
        raise HTTPException(status_code=429, detail="too many login attempts from this address")
    if _count_attempts(db, kind="login", ip=None, username=username, interval="1 hour") >= settings.login_max_per_hour_per_user:
        raise HTTPException(status_code=429, detail="too many login attempts for this account")


def check_public_report_rate(db: Session, *, ip: str) -> None:
    if _count_attempts(db, kind="public_report", ip=ip, username=None, interval="1 hour") >= settings.public_report_per_hour_per_ip:
        raise HTTPException(status_code=429, detail="too many reports from this address; try again later")


# --------------------------------------------------------------------------- sessions
def create_session(db: Session, user: User, request: Request) -> tuple[str, str, dt.datetime]:
    """Returns (cookie token, csrf token, expiry).  Only the *hash* of the cookie
    token is persisted."""
    token = secrets.token_hex(32)  # 256 bits
    csrf = secrets.token_hex(32)
    expires = now() + dt.timedelta(minutes=idle_minutes_for(user.role))
    db.add(
        SessionRow(
            id=_token_id(token),
            user_id=user.id,
            csrf_token=csrf,
            expires_at=expires,
            ip=client_ip(request),
            ua=(request.headers.get("user-agent") or "")[:300],
        )
    )
    db.flush()
    return token, csrf, expires


def load_session(db: Session, token: str | None) -> tuple[SessionRow, User] | None:
    if not token or len(token) != 64:
        return None
    row = db.get(SessionRow, _token_id(token))
    if row is None or row.revoked_at is not None or row.expires_at <= now():
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return row, user


def touch_session(db: Session, row: SessionRow, user: User) -> None:
    """Sliding idle expiry.  Written at most once a minute to avoid a write per
    request."""
    new_expiry = now() + dt.timedelta(minutes=idle_minutes_for(user.role))
    if (new_expiry - row.expires_at).total_seconds() > 60:
        row.expires_at = new_expiry


def revoke_session(db: Session, token: str | None) -> None:
    if not token:
        return
    row = db.get(SessionRow, _token_id(token))
    if row is not None and row.revoked_at is None:
        row.revoked_at = now()


def revoke_all_sessions_for(db: Session, user_id: uuid.UUID) -> None:
    db.execute(
        text("UPDATE sessions SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL"),
        {"u": str(user_id)},
    )


# --------------------------------------------------------------------------- TOTP
def verify_totp(secret: str, code: str | None) -> bool:
    if not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


def new_totp_secret() -> str:
    return pyotp.random_base32()


# --------------------------------------------------------------------------- login
def authenticate(db: Session, request: Request, username: str, password: str, totp_code: str | None):
    """Returns the User, or raises.  Constant-ish work on the unhappy path so a
    missing username is not distinguishable by timing."""
    ip = client_ip(request)
    username = (username or "").strip().lower()
    check_login_rate(db, ip=ip, username=username)

    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()

    if user is None:
        record_attempt(db, ip=ip, username=username, ok=False)
        # burn comparable CPU so absent vs present accounts look the same
        verify_password(
            "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHRzb21lc2E$"
            "F0mYeZ1UeqPbmJ0IPBjXwYdJfd0h5S4H8lXWq6IqDaU",
            password,
        )
        raise HTTPException(status_code=401, detail="invalid username or password")

    if user.locked_until is not None and user.locked_until > now():
        record_attempt(db, ip=ip, username=username, ok=False)
        raise HTTPException(status_code=429, detail="account temporarily locked; try again later")

    if not user.is_active:
        record_attempt(db, ip=ip, username=username, ok=False)
        raise HTTPException(status_code=401, detail="invalid username or password")

    if not verify_password(user.password_hash, password):
        user.failed_logins += 1
        if user.failed_logins >= settings.login_lockout_failures:
            user.locked_until = now() + dt.timedelta(minutes=settings.login_lockout_minutes)
        record_attempt(db, ip=ip, username=username, ok=False)
        raise HTTPException(status_code=401, detail="invalid username or password")

    if user.totp_enabled and user.totp_secret:
        if not verify_totp(user.totp_secret, totp_code):
            user.failed_logins += 1
            if user.failed_logins >= settings.login_lockout_failures:
                user.locked_until = now() + dt.timedelta(minutes=settings.login_lockout_minutes)
            record_attempt(db, ip=ip, username=username, ok=False)
            raise HTTPException(status_code=401, detail="invalid or missing second factor")

    user.failed_logins = 0
    user.locked_until = None
    record_attempt(db, ip=ip, username=username, ok=True)
    return user


# --------------------------------------------------------------------------- CSRF
def csrf_ok(request: Request, expected: str | None) -> bool:
    if not expected:
        return False
    supplied = request.headers.get(CSRF_HEADER) or ""
    return hmac.compare_digest(supplied, expected)


def double_submit_ok(request: Request, form_token: str | None) -> bool:
    """Classic double-submit for the one real HTML <form> we serve (admin login):
    the cookie value must equal the form field value."""
    cookie = request.cookies.get(CSRF_COOKIE) or ""
    return bool(form_token) and hmac.compare_digest(cookie, form_token or "")


def new_csrf_cookie_value() -> str:
    return secrets.token_hex(32)


# --------------------------------------------------------------------------- dependencies
class Principal:
    """What a request handler gets: the user, its scope, the session row."""

    __slots__ = ("user", "scope", "session", "ip")

    def __init__(self, user: User, scope: Scope, session: SessionRow, ip: str):
        self.user = user
        self.scope = scope
        self.session = session
        self.ip = ip


def _unauthenticated() -> HTTPException:
    return HTTPException(status_code=401, detail="authentication required")


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    """The single gate every non-public route passes through."""
    loaded = load_session(db, request.cookies.get(SESSION_COOKIE))
    if loaded is None:
        raise _unauthenticated()
    row, user = loaded
    touch_session(db, row, user)
    ip = client_ip(request)
    # publish the actor to Postgres so the audit triggers can attribute writes
    set_db_actor(db, user.id, ip)

    method = request.method.upper()
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        # Same-site cookie + custom header + CSRF token: a cross-site form post
        # cannot set the header, and a cross-origin fetch cannot read the token.
        if request.headers.get(REQUESTED_WITH_HEADER) != REQUESTED_WITH_VALUE:
            raise HTTPException(status_code=403, detail="missing X-Requested-With: GauTrack")
        if not csrf_ok(request, row.csrf_token):
            raise HTTPException(status_code=403, detail="bad or missing CSRF token")

    principal = Principal(user, scope_for(user), row, ip)
    request.state.principal = principal
    return principal


def set_session_cookies(response, token: str, csrf: str, expires: dt.datetime) -> None:
    max_age = max(int((expires - now()).total_seconds()), 0)
    secure = bool(settings.cookie_secure)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,      # unreadable from JavaScript, so XSS cannot exfiltrate it
        secure=secure,
        samesite="lax",     # not sent on cross-site POSTs
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,     # deliberately readable: the client echoes it back in a header
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
