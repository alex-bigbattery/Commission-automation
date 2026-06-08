"""Classify CF.Ticket# (cf_ticket) values for commission policy."""

from __future__ import annotations

from typing import Literal

TicketClass = Literal["none", "real_ticket", "quote_reference", "other_ticket_reference"]


def classify_ticket_number(value: str | None) -> TicketClass:
    """Classify a Ticket# field value.

    - real_ticket: numeric only, 1–4 digits (e.g. 650, 87, 1234)
    - quote_reference: starts with QUO or QUO- (e.g. QUO-04421)
    - other_ticket_reference: any other non-empty value
    - none: blank
    """
    raw = (value or "").strip()
    if not raw:
        return "none"
    if raw.upper().startswith("QUO"):
        return "quote_reference"
    if raw.isdigit() and 1 <= len(raw) <= 4:
        return "real_ticket"
    return "other_ticket_reference"


def apply_ticket_flags(raw: str, flags: list[str]) -> tuple[bool, bool]:
    """Append ticket flags and return (exclude_line, force_pending).

    ``TICKET_NUMBER`` is kept whenever the field is populated (diagnostic only).
    Only ``REAL_TICKET`` triggers automatic exclusion.
    """
    cls = classify_ticket_number(raw)
    if cls == "none":
        return False, False

    flags.append("TICKET_NUMBER")

    if cls == "real_ticket":
        flags.append("REAL_TICKET")
        return True, False
    if cls == "quote_reference":
        flags.append("QUOTE_REFERENCE_IN_TICKET_FIELD")
        return False, False

    flags.append("OTHER_TICKET_REFERENCE")
    return False, True
