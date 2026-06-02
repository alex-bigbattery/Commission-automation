"""Supabase JWT verification for FastAPI."""

from __future__ import annotations

import os
from functools import lru_cache

import jwt
from jwt import InvalidTokenError

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "").lower() in ("1", "true", "yes")


def auth_enabled() -> bool:
    if AUTH_DISABLED:
        return False
    return bool(SUPABASE_JWT_SECRET and SUPABASE_URL)


@lru_cache(maxsize=1)
def _issuer() -> str:
    return f"{SUPABASE_URL}/auth/v1"


def verify_access_token(token: str) -> dict:
    """Validate a Supabase access token. Returns JWT claims."""
    if not auth_enabled():
        return {"sub": "local-dev", "role": "authenticated"}
    try:
        return jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
            issuer=_issuer(),
        )
    except InvalidTokenError as exc:
        raise ValueError("Invalid or expired token") from exc
