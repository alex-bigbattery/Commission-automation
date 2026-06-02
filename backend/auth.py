"""Supabase JWT verification for FastAPI."""

from __future__ import annotations

import os
from functools import lru_cache

import jwt
from jwt import InvalidTokenError, PyJWKClient

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "").lower() in ("1", "true", "yes")


def auth_enabled() -> bool:
    if AUTH_DISABLED:
        return False
    return bool(SUPABASE_URL)


@lru_cache(maxsize=1)
def _issuer() -> str:
    return f"{SUPABASE_URL}/auth/v1"


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    return PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")


def verify_access_token(token: str) -> dict:
    """Validate a Supabase access token. Returns JWT claims."""
    if not auth_enabled():
        return {"sub": "local-dev", "role": "authenticated"}

    issuer = _issuer()
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "HS256")

    try:
        if alg in ("ES256", "RS256"):
            key = _jwks_client().get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                key.key,
                algorithms=[alg],
                audience="authenticated",
                issuer=issuer,
                leeway=60,
            )
        if SUPABASE_JWT_SECRET:
            return jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
                issuer=issuer,
                leeway=60,
            )
        raise ValueError("Token uses HS256 but SUPABASE_JWT_SECRET is not set")
    except InvalidTokenError as exc:
        raise ValueError("Invalid or expired token") from exc
