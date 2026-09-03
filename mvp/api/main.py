"""GauTrack API + server-rendered admin dashboard.

Route groups
    /api/...   JSON, session-authenticated (except /api/public/*)
    /admin     Jinja + HTMX dashboard
    /cm        read-only aggregate view for the CM / press
    /report    public "cow on the road" form
    /app       offline-first field PWA (static files)
"""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import API_DIR, settings
from routes import (animals, audit_routes, auth_routes, events, export_routes, owners, pages,
                    photos_routes, public, stats_routes, users)

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("gautrack")

STATIC_DIR = API_DIR / "static"

@asynccontextmanager
async def lifespan(_: FastAPI):
    Path(settings.photo_dir).mkdir(parents=True, exist_ok=True)
    log.info("GauTrack started (demo=%s, cookie_secure=%s)", settings.is_demo, bool(settings.cookie_secure))
    yield


app = FastAPI(
    title="GauTrack — Rewari stray cattle registry",
    version="0.1.0",
    docs_url=None,      # no interactive docs in a government deployment
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def _tile_host() -> str:
    try:
        parsed = urlparse(settings.map_tiles_url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except ValueError:
        pass
    return ""


# The map tile host is the ONLY external origin the browser may contact, and
# only for images.  Everything else — scripts, styles, fonts, XHR — is 'self'.
#
# Inline <script> blocks are allowed only when they carry the per-request nonce,
# so injected markup cannot execute: an attacker cannot guess the nonce.
def _csp(nonce: str) -> str:
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "   # Leaflet/Chart.js set inline styles
        f"img-src 'self' data: blob: {_tile_host()}; ".replace("  ", " ")
        + "connect-src 'self'; "
        "font-src 'self'; "
        "worker-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "form-action 'self'"
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    nonce = secrets.token_urlsafe(16)
    request.state.csp_nonce = nonce          # templates read this
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", _csp(nonce))
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(self), microphone=()")
    if settings.cookie_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.exception_handler(pages.RedirectToLogin)
async def _redirect_to_login(request: Request, exc: pages.RedirectToLogin):
    from urllib.parse import quote

    return RedirectResponse(f"/admin/login?next={quote(exc.next_url, safe='/')}", status_code=303)


@app.exception_handler(500)
async def internal_error(request: Request, exc: Exception):
    # Never leak a stack trace or SQL to the client.
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "internal error"}, status_code=500)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


# ---- JSON API -------------------------------------------------------------
app.include_router(auth_routes.router)
app.include_router(auth_routes.me_router)
app.include_router(owners.router)
app.include_router(animals.router)
app.include_router(animals.lookup_router)
app.include_router(events.router)
app.include_router(photos_routes.router)
app.include_router(stats_routes.router)
app.include_router(audit_routes.router)
app.include_router(export_routes.router)
app.include_router(users.router)
app.include_router(public.router)

# ---- HTML -----------------------------------------------------------------
app.include_router(pages.router)

# ---- static ---------------------------------------------------------------
# Registered AFTER pages so the explicit /app/sw.js route wins.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/app", StaticFiles(directory=str(STATIC_DIR / "app"), html=True), name="app")
