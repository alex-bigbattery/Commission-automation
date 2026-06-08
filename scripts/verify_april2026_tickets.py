"""Verify April 2026 ticket policy + reconciliation after Ticket# classification change."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.commission.ticket_classification import classify_ticket_number
from src.commission.sqlite_to_workbook import (
    _reconciliation_values,
    build_salespeople_from_sqlite,
    load_map_from_template,
    load_tiers_from_template,
)

YEAR, MONTH = 2026, 4
TEMPLATE = ROOT / "data" / "templates" / "master_template_clean.xlsx"


def row_for_invoice(rows, inv: str):
    return [r for r in rows if r.get("invoice") == inv]


def main() -> None:
    assert classify_ticket_number("650") == "real_ticket"
    assert classify_ticket_number("QUO-04421") == "quote_reference"
    assert classify_ticket_number("12345") == "other_ticket_reference"
    assert classify_ticket_number("") == "none"

    tiers = load_tiers_from_template(TEMPLATE)
    rlp = load_map_from_template(TEMPLATE)
    result = build_salespeople_from_sqlite(YEAR, MONTH, tiers=tiers, rlp_map=rlp, apply_adjustments=True)
    rows = result.audit_rows
    recon = _reconciliation_values(result)

    print("=== April 2026 ticket verification ===")
    print(f"total_commission (payable): {result.kpis.get('total_commission')}")
    print(f"check_a: {recon['check_a']}  check_b: {recon['check_b']}")
    print(f"real_ticket lines: {sum(1 for r in rows if 'REAL_TICKET' in str(r.get('flags') or ''))}")
    print(f"quote_reference lines: {sum(1 for r in rows if 'QUOTE_REFERENCE_IN_TICKET_FIELD' in str(r.get('flags') or ''))}")
    print(f"other_ticket_reference lines: {sum(1 for r in rows if 'OTHER_TICKET_REFERENCE' in str(r.get('flags') or ''))}")

    for inv in ("INV-05583", "INV-05669"):
        matches = row_for_invoice(rows, inv)
        print(f"\n--- {inv} ({len(matches)} lines) ---")
        for r in matches:
            print(json.dumps({
                "sku": r.get("sku"),
                "flags": r.get("flags"),
                "category_tags": r.get("category_tags"),
                "excluded": r.get("excluded"),
                "pending": r.get("pending"),
                "system_commission": r.get("system_commission"),
                "final_commission": r.get("final_commission"),
                "issue_found": r.get("issue_found"),
            }, indent=2))

    inv5583 = row_for_invoice(rows, "INV-05583")
    inv5669 = row_for_invoice(rows, "INV-05669")
    assert inv5583, "INV-05583 not found"
    assert inv5669, "INV-05669 not found"
    assert not any(r.get("excluded") for r in inv5583 if "QUOTE_REFERENCE" in str(r.get("flags") or "")), \
        "INV-05583 QUO line must not be auto-excluded"
    assert any("REAL_TICKET" in str(r.get("flags") or "") and r.get("excluded") for r in inv5669), \
        "INV-05669 real ticket must remain excluded"

    assert recon["check_a"] == 0 and recon["check_b"] == 0, "Check A/B must be 0"
    print("\nOK: assertions passed")


if __name__ == "__main__":
    main()
