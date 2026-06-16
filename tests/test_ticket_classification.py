"""Unit tests for CF.Ticket# classification and its commission effect.

Real support tickets (1-4 digit numbers) are non-commissionable and auto-excluded.
Quote references (QUO-...) are never auto-excluded. Anything else is held for
manual review (force_pending) but not excluded.
"""
from __future__ import annotations

import pytest

from src.commission.ticket_classification import (
    apply_ticket_flags,
    classify_ticket_number,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", "none"),
        ("   ", "none"),
        (None, "none"),
        ("650", "real_ticket"),
        ("1", "real_ticket"),
        ("1234", "real_ticket"),
        ("12345", "other_ticket_reference"),  # 5 digits -> not a real ticket
        ("QUO-04421", "quote_reference"),
        ("quo-1", "quote_reference"),
        ("SO-123", "other_ticket_reference"),
        ("warranty", "other_ticket_reference"),
    ],
)
def test_classify_ticket_number(value, expected):
    assert classify_ticket_number(value) == expected


def test_apply_flags_blank_is_noop():
    flags: list[str] = []
    exclude, pending = apply_ticket_flags("", flags)
    assert exclude is False and pending is False
    assert flags == []


def test_apply_flags_real_ticket_excludes():
    flags: list[str] = []
    exclude, pending = apply_ticket_flags("650", flags)
    assert exclude is True and pending is False
    assert "TICKET_NUMBER" in flags
    assert "REAL_TICKET" in flags


def test_apply_flags_quote_reference_neither_excludes_nor_holds():
    flags: list[str] = []
    exclude, pending = apply_ticket_flags("QUO-04421", flags)
    assert exclude is False and pending is False
    assert "QUOTE_REFERENCE_IN_TICKET_FIELD" in flags


def test_apply_flags_other_reference_forces_pending():
    flags: list[str] = []
    exclude, pending = apply_ticket_flags("warranty-claim", flags)
    assert exclude is False and pending is True
    assert "OTHER_TICKET_REFERENCE" in flags
