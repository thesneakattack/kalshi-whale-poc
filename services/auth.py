"""
Google sign-in — inert until GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and
APP_SECRET_KEY are all set in .env. Until then AuthMiddleware is a no-op and
the app behaves exactly like before: fully open, no login wall.

Single-operator design: there's no user table and no per-user data. A
successful Google login just proves "this is me" and unlocks the one app
that already exists. Set ALLOWED_GOOGLE_EMAIL to lock it to your own address —
without it, anyone who completes Google's consent screen gets in, which
defeats the point once this is reachable from outside your own machine.

Session is a signed cookie (Starlette's SessionMiddleware, itsdangerous under
the hood) — no server-side session store needed for one operator.

ID-token verification uses Google's tokeninfo endpoint rather than pulling in
a JWT/JWKS library: one extra HTTP round-trip, zero new dependencies. Google's
own docs describe this endpoint as fine for exactly this scale and only steer
high-volume production services toward their client libraries instead.
"""
import base64
import hashlib
import os
from urllib.parse import urlencode

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from services.http_client import get_client

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

# Routes reachable without a session even when auth is configured, plus
# anything under /static/ (the login page's own CSS/JS, if it ever needs any).
PUBLIC_PATHS = {"/login", "/auth/login", "/auth/callback"}


def _cfg() -> dict:
    return {
        "client_id": os.getenv("GOOGLE_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
        "secret_key": os.getenv("APP_SECRET_KEY", "").strip(),
        "allowed_email": os.getenv("ALLOWED_GOOGLE_EMAIL", "").strip().lower(),
    }


def auth_configured() -> bool:
    c = _cfg()
    return bool(c["client_id"] and c["client_secret"] and c["secret_key"])


def session_secret_key() -> str:
    return _cfg()["secret_key"]


def fernet_key() -> bytes:
    """Derive a valid 32-byte urlsafe Fernet key from APP_SECRET_KEY (any-length string)."""
    return base64.urlsafe_b64encode(hashlib.sha256(session_secret_key().encode()).digest())


def build_login_url(redirect_uri: str, state: str) -> str:
    c = _cfg()
    params = {
        "client_id": c["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str, redirect_uri: str) -> dict:
    c = _cfg()
    resp = await get_client().post(GOOGLE_TOKEN_URL, data={
        "code": code,
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


async def verify_id_token(id_token: str) -> dict:
    """Validate signature+expiry via Google's tokeninfo endpoint, then check
    audience/verification/allow-list ourselves — tokeninfo doesn't know which
    app is asking."""
    resp = await get_client().get(GOOGLE_TOKENINFO_URL, params={"id_token": id_token}, timeout=10.0)
    resp.raise_for_status()
    claims = resp.json()

    if claims.get("aud") != _cfg()["client_id"]:
        raise ValueError("token audience mismatch")
    if str(claims.get("email_verified")).lower() != "true":
        raise ValueError("Google email not verified")
    allowed = _cfg()["allowed_email"]
    if allowed and claims.get("email", "").lower() != allowed:
        raise PermissionError(f"{claims.get('email')} is not the allowed account")
    return claims


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not auth_configured():
            return await call_next(request)  # today's fully-open behavior, unchanged

        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)

        if not request.session.get("user"):
            if path.startswith("/api/"):
                return JSONResponse({"error": "not authenticated"}, status_code=401)
            return RedirectResponse(url="/login")

        return await call_next(request)
