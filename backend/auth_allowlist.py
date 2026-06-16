"""Email allowlist for the commission system.

Mirrors the affiliate dashboard's ``authConfig.js`` so the SAME Big Battery
people who use the affiliate dashboard can sign in here with the SAME Supabase
credentials. Both apps point at the same Supabase project, so this list is the
only thing gating who gets in.

Override the list with the ``COMMISSION_ALLOWED_EMAILS`` env var (comma-separated)
without a code change. An empty/whitespace override disables the allowlist
(everyone authenticated is allowed) — useful for local testing.
"""

from __future__ import annotations

import os

# Keep in sync with affiliate-dashboard/authConfig.js ALLOWED_DASHBOARD_EMAILS.
DEFAULT_ALLOWED_EMAILS: tuple[str, ...] = (
    "alex.g@bigbattery.com",
    "honey.g@bigbattery.com",
    "receivables@bigbattery.com",
    "jennifer.z@bigbattery.com",
    "santiago.o@bigbattery.com",
    "marshall@bigbattery.com",
    "kunal.d@bigbattery.com",
)

DASHBOARD_EMAIL_DOMAIN = "@bigbattery.com"


def _load_allowed() -> frozenset[str]:
    raw = os.environ.get("COMMISSION_ALLOWED_EMAILS", "")
    if raw.strip():
        return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())
    return frozenset(e.lower() for e in DEFAULT_ALLOWED_EMAILS)


ALLOWED_EMAILS: frozenset[str] = _load_allowed()


def to_dashboard_email(value: str | None) -> str:
    """``"alex.g"`` or ``"alex.g@bigbattery.com"`` -> ``alex.g@bigbattery.com``."""
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if "@" in raw:
        return raw
    return f"{raw}{DASHBOARD_EMAIL_DOMAIN}"


def is_allowed_email(email: str | None) -> bool:
    """True when ``email`` may access the commission system.

    An empty allowlist (explicitly cleared via env) means no gating — allow any
    authenticated user.
    """
    if not ALLOWED_EMAILS:
        return True
    return str(email or "").strip().lower() in ALLOWED_EMAILS
