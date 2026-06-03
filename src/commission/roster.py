"""
Approved B2B commission roster and assignment display helpers.

Roster members are configurable via COMMISSION_ROSTER (comma-separated sheet keys,
e.g. ``Paul,Jose,Michael``). Full-name aliases are defined in DEFAULT_ROSTER_ENTRIES.
"""

from __future__ import annotations

import os
from typing import Any

# Internal routing bucket for non-roster lines (never shown to Accounting).
ROUTING_UNASSIGNED = "(unassigned)"

COMPANY_SHEET = "Company Acct"
COMPANY_FULL = "Company Account"

# (sheet_key, zoho_full_name) — regular B2B reps only (Company Acct appended separately).
DEFAULT_ROSTER_ENTRIES: list[tuple[str, str]] = [
    ("Paul", "Paul Perlman"),
    ("Jose", "Jose Ayala"),
    ("Michael", "Michael Ayala"),
    ("Jim", "Jim Sutton"),
    ("Weston", "Weston Fields"),
    ("Brett", "Brett Bern"),
    ("Leslie", "Leslie Neipert"),
    ("Carmen", "Carmen Daetz"),
    ("Garrett", "Garrett Lockhart"),
    ("Kara", "Kara"),
]

EXTRA_NAME_ALIASES: dict[str, str] = {
    "Company Account": COMPANY_SHEET,
    "B2B Company Account": COMPANY_SHEET,
}

NON_SALARIED_SHEETS = frozenset({"Brett", "Leslie", "Carmen", "Garrett"})

MISSING_ZOHO_LABEL = "(missing in Zoho)"
ISSUE_NOT_IN_ROSTER = "Salesperson not in commission roster"
ISSUE_MISSING_ZOHO = "Missing salesperson in Zoho"
ACTION_NOT_IN_ROSTER = (
    "Classify as Company Account, Executive Account, Bruce Commission, "
    "assign to a salesperson, or add to roster"
)
ACTION_MISSING_ZOHO = "Assign salesperson"


def _parse_env_roster(raw: str) -> list[tuple[str, str]] | None:
    if not raw.strip():
        return None
    keys = [p.strip() for p in raw.split(",") if p.strip()]
    if not keys:
        return None
    default_by_sheet = {s: f for s, f in DEFAULT_ROSTER_ENTRIES}
    return [(k, default_by_sheet.get(k, k)) for k in keys]


def roster_rep_entries() -> list[tuple[str, str]]:
    override = _parse_env_roster(os.environ.get("COMMISSION_ROSTER", ""))
    return override if override is not None else list(DEFAULT_ROSTER_ENTRIES)


def build_catalog() -> tuple[list[tuple[str, str]], dict[str, str], frozenset[str]]:
    reps = roster_rep_entries()
    all_ordered = reps + [(COMPANY_SHEET, COMPANY_FULL)]
    full_to_sheet = {full: sheet for sheet, full in reps}
    full_to_sheet.update(EXTRA_NAME_ALIASES)
    roster_sheets = frozenset(s for s, _ in reps)
    return all_ordered, full_to_sheet, roster_sheets


ALL_SHEETS_ORDERED, SALESPERSON_FULL_TO_SHEET, ROSTER_SHEETS = build_catalog()


def roster_rep_sheet_keys() -> list[str]:
    return [s for s, _ in roster_rep_entries()]


def format_zoho_salesperson(raw: str | None) -> str:
    text = (raw or "").strip()
    return text if text else MISSING_ZOHO_LABEL


def resolve_roster_sheet(name: str | None) -> str | None:
    """Map a Zoho full name or sheet key to a roster sheet key, or None if not on roster."""
    if not name:
        return None
    n = str(name).strip()
    if n in SALESPERSON_FULL_TO_SHEET:
        sheet = SALESPERSON_FULL_TO_SHEET[n]
        return sheet if sheet in ROSTER_SHEETS else None
    if n in ROSTER_SHEETS:
        return n
    low = n.lower()
    for sheet, _full in roster_rep_entries():
        if sheet.lower() == low:
            return sheet
    return None


def accounting_category(classification: str) -> str:
    cls = (classification or "").lower()
    if cls == "company":
        return "Company Account"
    if cls == "executive":
        return "Executive Account"
    return ""


def final_commission_assignment(
    *,
    excluded: bool,
    classification: str,
    pending: bool,
    sheet: str,
) -> str:
    if excluded:
        return "Excluded"
    cat = accounting_category(classification)
    if cat:
        return cat
    if pending or sheet == ROUTING_UNASSIGNED:
        return "Pending"
    return sheet


def issue_found(row: dict[str, Any]) -> str:
    if row.get("issue_found"):
        return str(row["issue_found"])
    flags = str(row.get("flags") or "")
    if "FULLY_RETURNED" in flags:
        return "Fully returned — not commissionable"
    if "PARTIALLY_RETURNED" in flags:
        return "Partially returned"
    team = str(row.get("sales_team") or "").lower()
    zoho = str(row.get("original_zoho_salesperson") or "")
    if row.get("pending"):
        if zoho == MISSING_ZOHO_LABEL:
            return ISSUE_MISSING_ZOHO
        if "UNASSIGNED" in flags and zoho not in ("", MISSING_ZOHO_LABEL):
            return ISSUE_NOT_IN_ROSTER
        if "exe" in team or "comp" in team:
            return "Company / Executive account needs classification"
        if "UNASSIGNED" in flags:
            return ISSUE_NOT_IN_ROSTER
        return ISSUE_MISSING_ZOHO
    if "MISSING_MAP" in flags:
        return "MAP / discount difference"
    if "UNPAID" in flags:
        return "Invoice not paid yet"
    if row.get("block") == "shipping":
        return "Shipping line"
    if row.get("section") == "II":
        return "Prior-period order"
    return ""


def suggested_action(row: dict[str, Any]) -> str:
    if row.get("suggested_action"):
        return str(row["suggested_action"])
    flags = str(row.get("flags") or "")
    if "FULLY_RETURNED" in flags:
        return "Returned — verify $0 commission"
    if "PARTIALLY_RETURNED" in flags:
        return "Partial return — verify kept qty"
    if row.get("pending"):
        zoho = str(row.get("original_zoho_salesperson") or "")
        if zoho == MISSING_ZOHO_LABEL:
            return ACTION_MISSING_ZOHO
        if "UNASSIGNED" in flags and zoho not in ("", MISSING_ZOHO_LABEL):
            return ACTION_NOT_IN_ROSTER
        team = str(row.get("sales_team") or "").lower()
        if "exe" in team or "comp" in team:
            return "Classify as Company / Executive"
        if "UNASSIGNED" in flags:
            return ACTION_NOT_IN_ROSTER
        return ACTION_MISSING_ZOHO
    if "MISSING_MAP" in flags:
        return "Review MAP / discount"
    if "UNPAID" in flags:
        return "Confirm payment, then approve"
    if row.get("excluded"):
        return "Review exclusion"
    if (row.get("approval_status") or "").lower() == "approved":
        return "—"
    return "Approve if correct"


def enrich_audit_fields(
    *,
    zoho_salesperson: str,
    sys_sheet: str,
    sheet: str,
    excluded: bool,
    classification: str,
    pending: bool,
    flags: str,
    block: str,
    section: str,
    sales_team: str,
    approval_status: str,
) -> dict[str, str]:
    """Build display/audit columns shared by API, UI, and workbook export."""
    final_asgn = final_commission_assignment(
        excluded=excluded,
        classification=classification,
        pending=pending,
        sheet=sheet,
    )
    row = {
        "original_zoho_salesperson": zoho_salesperson,
        "final_commission_assignment": final_asgn,
        "accounting_category": accounting_category(classification),
        "salesperson": final_asgn,
        "system_salesperson": sys_sheet,
        "flags": flags,
        "pending": pending,
        "excluded": excluded,
        "classification": classification,
        "block": block,
        "section": section,
        "sales_team": sales_team,
        "approval_status": approval_status,
    }
    return {
        **row,
        "issue_found": issue_found(row),
        "suggested_action": suggested_action(row),
    }
