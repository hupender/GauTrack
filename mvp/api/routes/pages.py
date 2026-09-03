"""Server-rendered pages: the admin dashboard, the CM view, the public report
form and the service worker.

The dashboard is Jinja + HTMX: the server sends HTML, the browser swaps
fragments.  There is no client-side state to trust and no API key in the page.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

import audit as audit_svc
import auth
import stats as stats_svc
from authz import Scope, apply_ulb_scope, mask_phone, scope_for
from config import API_DIR, settings
from db import get_db
from models import Animal, Event, Fine, Owner, Role, Ulb, User

router = APIRouter()
templates = Jinja2Templates(directory=str(API_DIR / "templates"))


def pct_class(pct: float | int | None, direction: str = "high_good") -> str:
    """Map a percentage onto the dashboard's one green/amber/red scale.

    Colour on these screens carries exactly one meaning: how a percentage is
    doing.  Direction has to be given because the same 95% is excellent for
    tagging coverage and an emergency for shelter occupancy, and a reader
    scanning the page should never have to work out which way round a bar is.
    """
    if pct is None:
        return "mid"
    p = float(pct)
    if direction == "high_bad":
        return "bad" if p >= 90 else "mid" if p >= 70 else "good"
    return "good" if p >= 75 else "mid" if p >= 40 else "bad"


def qs(**params) -> str:
    """Build a query string from the non-empty parameters, so a dashboard link
    can carry the ULB filter the user is currently looking at."""
    pairs = [f"{k}={v}" for k, v in params.items() if v not in (None, "", 0)]
    return ("?" + "&".join(pairs)) if pairs else ""


def asset_version() -> str:
    """A short stamp that changes whenever the dashboard's own CSS or JS does.

    Appended to every stylesheet and script tag as `?v=...`.  Without it a
    browser that has already cached `admin.css` will happily keep serving the
    old one after an update, which on a deployed machine looks exactly like
    "the fix did not work" -- the operator reloads, sees no change, and files a
    bug against software that is in fact already correct.  Changing the URL is
    the only instruction a browser cache reliably obeys.

    Computed once at start-up from the newest modification time in the static
    directory, so a restart is what publishes new front-end code.  Vendored
    libraries are excluded: their version is already in their filename.
    """
    static = API_DIR / "static"
    newest = 0.0
    for f in static.rglob("*"):
        if f.is_file() and "vendor" not in f.parts:
            newest = max(newest, f.stat().st_mtime)
    return format(int(newest), "x")


templates.env.globals["pct_class"] = pct_class
templates.env.globals["qs"] = qs
templates.env.globals["asset_v"] = asset_version()

STATIC_APP = API_DIR / "static" / "app"


class RedirectToLogin(Exception):
    def __init__(self, next_url: str = "/admin"):
        self.next_url = next_url


def page_principal(request: Request, db: Session = Depends(get_db)):
    """Session lookup for HTML pages: bounce to the login form instead of
    returning a bare 401."""
    loaded = auth.load_session(db, request.cookies.get(auth.SESSION_COOKIE))
    if loaded is None:
        raise RedirectToLogin(request.url.path)
    row, user = loaded
    auth.touch_session(db, row, user)
    from db import set_db_actor

    set_db_actor(db, user.id, auth.client_ip(request))
    return auth.Principal(user, scope_for(user), row, auth.client_ip(request))


def _ctx(request: Request, principal, **extra):
    return {
        "request": request,
        "csp_nonce": getattr(request.state, "csp_nonce", ""),
        "user": principal.user if principal else None,
        "scope": principal.scope if principal else None,
        "role": principal.user.role.value if principal else None,
        "csrf": principal.session.csrf_token if principal else "",
        "demo": settings.is_demo,
        "map_tiles_url": settings.map_tiles_url,
        "map_tiles_attribution": settings.map_tiles_attribution,
        **extra,
    }


def _ulbs(db: Session, scope: Scope):
    stmt = select(Ulb).order_by(Ulb.id)
    if scope.ulb_ids is not None:
        stmt = stmt.where(Ulb.id.in_(scope.ulb_ids or [-1]))
    return db.execute(stmt).scalars().all()


def _guard_dashboard(principal):
    if principal.user.role is Role.viewer:
        # viewer sees aggregates only
        raise HTTPException(status_code=307, headers={"Location": "/cm"})
    if not principal.scope.can_read_entities:
        raise HTTPException(status_code=403, detail="not permitted")


# --------------------------------------------------------------------------- login
@router.get("/admin/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/admin"):
    token = auth.new_csrf_cookie_value()
    response = templates.TemplateResponse(
        request,
        "admin/login.html",
        {
            "request": request,
            "csp_nonce": getattr(request.state, "csp_nonce", ""),
            "csrf": token,
            "next": next if next.startswith("/") else "/admin",
            "demo": settings.is_demo,
            "error": request.query_params.get("error"),
        },
    )
    # double-submit: the same value lands in a cookie and in a hidden field
    response.set_cookie(
        auth.CSRF_COOKIE,
        token,
        httponly=False,
        secure=bool(settings.cookie_secure),
        samesite="lax",
        max_age=1800,
        path="/",
    )
    return response


@router.post("/admin/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    totp_code: str = Form(default=""),
    csrf_token: str = Form(default=""),
    next: str = Form(default="/admin"),
    db: Session = Depends(get_db),
):
    # CSRF double-submit on the one true HTML form we serve (SPEC §1.4)
    if not auth.double_submit_ok(request, csrf_token):
        return RedirectResponse("/admin/login?error=Session+expired,+please+try+again", status_code=303)
    try:
        user = auth.authenticate(db, request, username, password, totp_code or None)
    except HTTPException as exc:
        db.commit()  # keep the recorded failed attempt
        msg = exc.detail if isinstance(exc.detail, str) else "Login failed"
        return RedirectResponse(f"/admin/login?error={msg.replace(' ', '+')}", status_code=303)

    token, csrf, expires = auth.create_session(db, user, request)
    db.commit()
    target = next if next.startswith("/") else "/admin"
    if user.role is Role.viewer:
        target = "/cm"
    response = RedirectResponse(target, status_code=303)
    auth.set_session_cookies(response, token, csrf, expires)
    return response


@router.post("/admin/logout")
def logout(request: Request, csrf_token: str = Form(default=""), db: Session = Depends(get_db)):
    auth.revoke_session(db, request.cookies.get(auth.SESSION_COOKIE))
    db.commit()
    response = RedirectResponse("/admin/login", status_code=303)
    auth.clear_session_cookies(response)
    return response


# --------------------------------------------------------------------------- dashboard
@router.get("/admin", response_class=HTMLResponse)
def admin_overview(
    request: Request,
    ulb: int | None = None,
    principal=Depends(page_principal),
    db: Session = Depends(get_db),
):
    _guard_dashboard(principal)
    scope = principal.scope
    ctx = _ctx(
        request,
        principal,
        page="overview",
        ulbs=_ulbs(db, scope),
        ulb=ulb,
        summary=stats_svc.summary(db, scope, ulb),
        by_ulb=stats_svc.by_ulb(db, scope),
        shelters=stats_svc.shelters(db, scope, ulb),
        offenders=stats_svc.repeat_offenders(db, scope, ulb, 10),
        leaderboard=stats_svc.leaderboard(db, scope, ulb),
    )
    db.commit()
    return templates.TemplateResponse(request, "admin/overview.html", ctx)


@router.get("/admin/partials/kpis", response_class=HTMLResponse)
def admin_kpis(
    request: Request,
    ulb: int | None = None,
    principal=Depends(page_principal),
    db: Session = Depends(get_db),
):
    """HTMX polls this every 60 s (SPEC §5)."""
    _guard_dashboard(principal)
    ctx = _ctx(request, principal, summary=stats_svc.summary(db, principal.scope, ulb), ulb=ulb)
    db.commit()
    return templates.TemplateResponse(request, "admin/_kpis.html", ctx)


@router.get("/admin/owners", response_class=HTMLResponse)
def admin_owners(
    request: Request,
    q: str | None = None,
    ulb: int | None = None,
    principal=Depends(page_principal),
    db: Session = Depends(get_db),
):
    _guard_dashboard(principal)
    scope = principal.scope
    stmt = apply_ulb_scope(select(Owner).where(Owner.merged_into.is_(None)), Owner.ulb_id, scope)
    if ulb:
        stmt = stmt.where(Owner.ulb_id == ulb)
    if q:
        stmt = stmt.where(Owner.name.ilike(f"%{q}%"))
    rows = db.execute(stmt.order_by(desc(Owner.created_at)).limit(200)).scalars().all()
    codes = {u.id: u.code for u in db.execute(select(Ulb)).scalars()}
    # One grouped count for the whole page rather than a query per row: the list
    # shows what each keeper declared against what is actually on the register,
    # and the gap between the two is the under-declaration signal.
    counts: dict = {}
    if rows:
        counts = dict(
            db.execute(
                select(Animal.owner_id, func.count())
                .where(Animal.owner_id.in_([o.id for o in rows]))
                .group_by(Animal.owner_id)
            ).all()
        )
    items = [
        {
            "id": o.id,
            "name": o.name,
            "relation_name": o.relation_name,
            "phone": mask_phone(o.phone_norm, scope.sees_pii_for_ulb(o.ulb_id)),
            "ward_or_village": o.ward_or_village,
            "keeper_type": o.keeper_type.value,
            "declared": o.self_declared_cattle_count,
            "area": o.premises_area_sq_yards,
            "animal_count": counts.get(o.id, 0),
            "ulb": codes.get(o.ulb_id, ""),
            "created_at": o.created_at,
        }
        for o in rows
    ]
    ctx = _ctx(request, principal, page="owners", items=items, q=q or "", ulbs=_ulbs(db, scope), ulb=ulb)
    db.commit()
    return templates.TemplateResponse(request, "admin/owners.html", ctx)


@router.get("/admin/owners/{owner_id}", response_class=HTMLResponse)
def admin_owner_detail(
    request: Request, owner_id: uuid.UUID, principal=Depends(page_principal), db: Session = Depends(get_db)
):
    _guard_dashboard(principal)
    scope = principal.scope
    # scoped query — prevents IDOR
    owner = db.execute(
        apply_ulb_scope(select(Owner).where(Owner.id == owner_id), Owner.ulb_id, scope)
    ).scalar_one_or_none()
    if owner is None:
        raise HTTPException(status_code=404, detail="not found")
    animals = db.execute(select(Animal).where(Animal.owner_id == owner.id).order_by(Animal.created_at)).scalars().all()
    events = db.execute(
        select(Event).where(Event.owner_id == owner.id).order_by(desc(Event.occurred_at)).limit(50)
    ).scalars().all()
    fines = db.execute(select(Fine).where(Fine.owner_id == owner.id).order_by(desc(Fine.issued_at))).scalars().all()
    ctx = _ctx(
        request,
        principal,
        page="owners",
        owner=owner,
        phone=mask_phone(owner.phone_norm, scope.sees_pii_for_ulb(owner.ulb_id)),
        animals=animals,
        events=events,
        fines=fines,
        all_owners=db.execute(
            apply_ulb_scope(select(Owner).where(Owner.merged_into.is_(None), Owner.id != owner.id), Owner.ulb_id, scope)
            .order_by(Owner.name)
            .limit(300)
        ).scalars().all()
        if scope.can_merge_owners
        else [],
    )
    db.commit()
    return templates.TemplateResponse(request, "admin/owner_detail.html", ctx)


@router.get("/admin/animals", response_class=HTMLResponse)
def admin_animals(
    request: Request,
    q: str | None = None,
    ulb: int | None = None,
    status: str | None = None,
    principal=Depends(page_principal),
    db: Session = Depends(get_db),
):
    _guard_dashboard(principal)
    scope = principal.scope
    stmt = apply_ulb_scope(select(Animal), Animal.ulb_id, scope)
    if ulb:
        stmt = stmt.where(Animal.ulb_id == ulb)
    if status:
        stmt = stmt.where(Animal.status == status)
    if q:
        stmt = stmt.where(Animal.tag_id.ilike(f"%{q}%"))
    rows = db.execute(stmt.order_by(desc(Animal.created_at)).limit(200)).scalars().all()
    codes = {u.id: u.code for u in db.execute(select(Ulb)).scalars()}
    owners = {
        o.id: o.name
        for o in db.execute(select(Owner).where(Owner.id.in_([a.owner_id for a in rows if a.owner_id]))).scalars()
    } if rows else {}
    ctx = _ctx(
        request,
        principal,
        page="animals",
        items=rows,
        codes=codes,
        owners=owners,
        q=q or "",
        ulbs=_ulbs(db, scope),
        ulb=ulb,
        status=status or "",
    )
    db.commit()
    return templates.TemplateResponse(request, "admin/animals.html", ctx)


@router.get("/admin/animals/{animal_id}", response_class=HTMLResponse)
def admin_animal_detail(
    request: Request, animal_id: uuid.UUID, principal=Depends(page_principal), db: Session = Depends(get_db)
):
    _guard_dashboard(principal)
    scope = principal.scope
    animal = db.execute(
        apply_ulb_scope(select(Animal).where(Animal.id == animal_id), Animal.ulb_id, scope)
    ).scalar_one_or_none()  # scoped query — prevents IDOR
    if animal is None:
        raise HTTPException(status_code=404, detail="not found")
    owner = db.get(Owner, animal.owner_id) if animal.owner_id else None
    events = db.execute(
        select(Event).where(Event.animal_id == animal.id).order_by(desc(Event.occurred_at)).limit(100)
    ).scalars().all()
    users = {u.id: u.full_name for u in db.execute(select(User)).scalars()}
    ctx = _ctx(request, principal, page="animals", animal=animal, owner=owner, events=events, users=users)
    db.commit()
    return templates.TemplateResponse(request, "admin/animal_detail.html", ctx)


@router.get("/admin/events", response_class=HTMLResponse)
def admin_events(
    request: Request,
    type: str | None = None,
    ulb: int | None = None,
    principal=Depends(page_principal),
    db: Session = Depends(get_db),
):
    _guard_dashboard(principal)
    scope = principal.scope
    stmt = apply_ulb_scope(select(Event), Event.ulb_id, scope)
    if type:
        stmt = stmt.where(Event.type == type)
    if ulb:
        stmt = stmt.where(Event.ulb_id == ulb)
    rows = db.execute(stmt.order_by(desc(Event.seq)).limit(200)).scalars().all()
    users = {u.id: u.full_name for u in db.execute(select(User)).scalars()}
    ctx = _ctx(
        request, principal, page="events", items=rows, users=users, ulbs=_ulbs(db, scope), ulb=ulb, type=type or ""
    )
    db.commit()
    return templates.TemplateResponse(request, "admin/events.html", ctx)


@router.get("/admin/fines", response_class=HTMLResponse)
def admin_fines(request: Request, principal=Depends(page_principal), db: Session = Depends(get_db)):
    _guard_dashboard(principal)
    scope = principal.scope
    rows = db.execute(
        apply_ulb_scope(select(Fine), Fine.ulb_id, scope).order_by(desc(Fine.issued_at)).limit(200)
    ).scalars().all()
    owners = {
        o.id: o.name
        for o in db.execute(select(Owner).where(Owner.id.in_([f.owner_id for f in rows if f.owner_id]))).scalars()
    } if rows else {}
    total = db.execute(
        apply_ulb_scope(select(func.coalesce(func.sum(Fine.amount), 0)), Fine.ulb_id, scope)
    ).scalar_one()
    ctx = _ctx(request, principal, page="fines", items=rows, owners=owners, total=float(total or 0))
    db.commit()
    return templates.TemplateResponse(request, "admin/fines.html", ctx)


@router.get("/admin/shelters", response_class=HTMLResponse)
def admin_shelters(request: Request, principal=Depends(page_principal), db: Session = Depends(get_db)):
    _guard_dashboard(principal)
    rows = stats_svc.shelters(db, principal.scope, None)
    ctx = _ctx(request, principal, page="shelters", items=rows)
    db.commit()
    return templates.TemplateResponse(request, "admin/shelters.html", ctx)


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request, principal=Depends(page_principal), db: Session = Depends(get_db)):
    if not principal.scope.can_manage_users:
        raise HTTPException(status_code=403, detail="super_admin only")
    rows = db.execute(select(User).order_by(User.username)).scalars().all()
    ctx = _ctx(request, principal, page="users", items=rows, ulbs=db.execute(select(Ulb).order_by(Ulb.id)).scalars().all())
    db.commit()
    return templates.TemplateResponse(request, "admin/users.html", ctx)


@router.get("/admin/audit", response_class=HTMLResponse)
def admin_audit(
    request: Request,
    table: str | None = None,
    row_id: str | None = None,
    principal=Depends(page_principal),
    db: Session = Depends(get_db),
):
    if not principal.scope.can_read_audit:
        raise HTTPException(status_code=403, detail="auditor or super_admin only")
    rows = audit_svc.list_audit(db, table=table, row_id=row_id, limit=200)
    ctx = _ctx(request, principal, page="audit", items=rows, table=table or "", row_id=row_id or "")
    db.commit()
    return templates.TemplateResponse(request, "admin/audit.html", ctx)


# --------------------------------------------------------------------------- CM view
@router.get("/cm", response_class=HTMLResponse)
def cm_view(request: Request, principal=Depends(page_principal), db: Session = Depends(get_db)):
    """Aggregates only — safe to project on a screen."""
    scope = principal.scope
    ctx = _ctx(
        request,
        principal,
        page="cm",
        summary=stats_svc.summary(db, scope, None),
        by_ulb=stats_svc.by_ulb(db, scope),
        shelters=stats_svc.shelters(db, scope, None),
        hourly=stats_svc.sightings_by_hour(db, scope, None),
    )
    db.commit()
    return templates.TemplateResponse(request, "cm.html", ctx)


# --------------------------------------------------------------------------- public
@router.get("/report", response_class=HTMLResponse)
def report_page(request: Request, db: Session = Depends(get_db)):
    ulbs = db.execute(select(Ulb).order_by(Ulb.id)).scalars().all()
    return templates.TemplateResponse(
        request,
        "report.html",
        {
            "request": request,
            "csp_nonce": getattr(request.state, "csp_nonce", ""),
            "ulbs": ulbs,
            "demo": settings.is_demo,
        },
    )


# --------------------------------------------------------------------------- PWA
@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/")


@router.get("/app/sw.js", include_in_schema=False)
def service_worker():
    """Served from /app/ with an explicit JavaScript content type and
    `Service-Worker-Allowed`, otherwise the browser refuses to register it."""
    return FileResponse(
        STATIC_APP / "sw.js",
        media_type="text/javascript",
        headers={"Service-Worker-Allowed": "/app/", "Cache-Control": "no-cache"},
    )
