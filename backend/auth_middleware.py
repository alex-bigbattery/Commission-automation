"""HTTP middleware: require Supabase JWT on /api/* (except health)."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import auth_enabled, verify_access_token
from .auth_allowlist import is_allowed_email

PUBLIC_API_PATHS = frozenset({"/api/health"})


class SupabaseAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if not path.startswith("/api") or path in PUBLIC_API_PATHS:
            return await call_next(request)

        if not auth_enabled():
            return await call_next(request)

        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})

        token = header[7:].strip()
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})

        try:
            claims = verify_access_token(token)
        except ValueError:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        # Same gate as the affiliate dashboard: a valid Supabase user is not
        # enough — the email must be on the allowlist.
        if not is_allowed_email(claims.get("email")):
            return JSONResponse(
                status_code=403,
                content={"detail": "Tu cuenta no tiene acceso al sistema de comisiones."},
            )

        return await call_next(request)
