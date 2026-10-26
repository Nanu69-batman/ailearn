"""Verify Supabase access tokens (docs/SUPABASE_PLAN.md, "Auth contract").

The browser sends ``Authorization: Bearer <access_token>``. Legacy projects
sign with HS256 (``SUPABASE_JWT_SECRET``); newer ones sign with ES256/RS256
and publish their keys at ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``.

- No header gives ``None``. A bad or expired token gives 401, whatever
  ``AUTH_REQUIRED`` is set to.
- With no Supabase configured at all, a Bearer token is ignored (``None``) so
  existing clients keep working.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request

from app.config import settings

log = logging.getLogger(__name__)

AUDIENCE = "authenticated"
LEEWAY_S = 30

#: One JWKS client per Supabase URL. PyJWKClient caches the fetched keys, so
#: keeping it at module level means one network fetch, not one per request.
_jwks_clients: dict[str, jwt.PyJWKClient] = {}


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str | None = None


def _jwks_client(url: str) -> jwt.PyJWKClient:
    client = _jwks_clients.get(url)
    if client is None:
        client = jwt.PyJWKClient(f"{url.rstrip('/')}/auth/v1/.well-known/jwks.json")
        _jwks_clients[url] = client
    return client


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="invalid token")
    return token.strip()


def _decode(token: str) -> dict:
    options = {"require": ["exp", "sub"]}
    if settings.supabase_jwt_secret:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience=AUDIENCE,
            leeway=LEEWAY_S,
            options=options,
        )
    signing_key = _jwks_client(settings.supabase_url).get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256", "RS256"],
        audience=AUDIENCE,
        leeway=LEEWAY_S,
        options=options,
    )


def user_from_token(token: str | None) -> AuthUser | None:
    """Verify a raw access token (HTTP header or WebSocket start message).

    ``None`` for no token, or when Supabase is not configured; 401 for a bad one.
    """
    if token is None:
        return None
    if not settings.supabase_jwt_secret and not settings.supabase_url:
        # No Supabase configured: ignore the token rather than reject it.
        return None
    try:
        claims = _decode(token)
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        log.info("supabase token rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="invalid token") from exc
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(status_code=401, detail="invalid token")
    email = claims.get("email")
    return AuthUser(id=sub, email=email if isinstance(email, str) else None)


def get_current_user(request: Request) -> AuthUser | None:
    """The signed-in Supabase user, or ``None`` for an anonymous caller."""
    return user_from_token(_bearer_token(request))


def require_user(
    user: Annotated[AuthUser | None, Depends(get_current_user)],
) -> AuthUser | None:
    """401 for anonymous callers when ``AUTH_REQUIRED`` is true, else pass through."""
    if user is None and settings.auth_required:
        raise HTTPException(status_code=401, detail="authentication required")
    return user
