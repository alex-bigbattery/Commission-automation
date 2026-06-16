"""Tests for the commission email allowlist (parity with the affiliate dashboard)."""
from __future__ import annotations

import pytest

from backend.auth_allowlist import (
    DEFAULT_ALLOWED_EMAILS,
    is_allowed_email,
    to_dashboard_email,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("alex.g", "alex.g@bigbattery.com"),
        ("alex.g@bigbattery.com", "alex.g@bigbattery.com"),
        ("  Alex.G  ", "alex.g@bigbattery.com"),
        ("JENNIFER.Z@BIGBATTERY.COM", "jennifer.z@bigbattery.com"),
        ("", ""),
        (None, ""),
    ],
)
def test_to_dashboard_email(raw, expected):
    assert to_dashboard_email(raw) == expected


def test_allowed_emails_default_membership():
    # Every default email is allowed (case-insensitive), in bare or full form.
    for email in DEFAULT_ALLOWED_EMAILS:
        assert is_allowed_email(email)
        assert is_allowed_email(email.upper())
        assert is_allowed_email(to_dashboard_email(email.split("@")[0]))


def test_non_allowlisted_email_rejected():
    assert is_allowed_email("intruder@bigbattery.com") is False
    assert is_allowed_email("someone@gmail.com") is False
    assert is_allowed_email("") is False
    assert is_allowed_email(None) is False
