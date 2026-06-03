"""
Verify Zoho full-name -> roster sheet-key mapping (READ-ONLY).

- Prints the ACTIVE alias map (SALESPERSON_FULL_TO_SHEET).
- Tests resolve_roster_sheet() on the canonical full names.
- Scans the month's actual Zoho salesperson names and buckets each:
    ROSTER -> sheet | Company | Executive | Inactive | B2C | UNMAPPED (pending).
- Reports any Zoho names that do NOT map to the roster.

No Zoho calls; no writes. Run when no Zoho import is in progress:
    python scripts/verify_roster_mapping.py --month 5 --year 2026
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.db.connection import get_connection
from src.commission.roster import (
    SALESPERSON_FULL_TO_SHEET, ROSTER_SHEETS, resolve_roster_sheet,
    classify_special_person, is_known_inactive, is_b2c_coupon_rep,
    format_zoho_salesperson, MISSING_ZOHO_LABEL,
)

CANONICAL = [
    "Paul Perlman", "Jose Ayala", "Michael Ayala", "Brett Bern",
    "Weston Fields", "Jim Sutton", "Leslie Neipert", "Carmen Daetz", "Garrett Lockhart",
]


def bucket(name: str) -> tuple[str, str]:
    sheet = resolve_roster_sheet(name)
    if sheet:
        return "ROSTER", sheet
    sp = classify_special_person(name)
    if sp:
        return sp["category"].upper(), ""  # COMPANY / EXECUTIVE
    if is_known_inactive(name):
        return "INACTIVE", ""
    if is_b2c_coupon_rep(name):
        return "B2C", ""
    if not name or name == MISSING_ZOHO_LABEL:
        return "MISSING", ""
    return "UNMAPPED", ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--month", type=int, default=5)
    args = ap.parse_args()
    prefix = f"{args.year}-{args.month:02d}%"

    print("=" * 72)
    print(" ROSTER MAPPING VERIFICATION")
    print("=" * 72)

    print("\n(1) ACTIVE ALIAS MAP  (Zoho full name -> sheet key)")
    print("    Roster sheet keys:", ", ".join(sorted(ROSTER_SHEETS)))
    for full, sheet in sorted(SALESPERSON_FULL_TO_SHEET.items()):
        mark = "" if sheet in ROSTER_SHEETS else "   (target not in roster)"
        print(f"    {full:<26} -> {sheet}{mark}")

    print("\n(2) CANONICAL NAME RESOLUTION")
    ok = True
    for full in CANONICAL:
        sheet = resolve_roster_sheet(full)
        status = "OK" if sheet else "NOT MAPPED"
        if not sheet:
            ok = False
        print(f"    {full:<26} -> {sheet or '(none)'}   [{status}]")
    print(f"    => {'all canonical names map correctly' if ok else 'SOME NAMES DO NOT MAP'}")

    print(f"\n(3) ACTUAL ZOHO NAMES IN {args.year}-{args.month:02d}")
    counts: Counter = Counter()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT i.salesorder_number AS so_num, i.salesperson_name AS inv_sp, "
            "so.salesperson_name AS so_sp "
            "FROM invoices i "
            "LEFT JOIN sales_orders so ON so.salesorder_number = i.salesorder_number "
            "WHERE i.invoice_date LIKE ?",
            (prefix,),
        ).fetchall()
    for r in rows:
        eff = (r["so_sp"] or r["inv_sp"] or "").strip()
        counts[eff] += 1

    buckets: dict[str, list[tuple[str, int]]] = {}
    for name, n in counts.items():
        b, sheet = bucket(name)
        label = f"{name or '(blank)'}" + (f" -> {sheet}" if sheet else "")
        buckets.setdefault(b, []).append((label, n))

    order = ["ROSTER", "COMPANY", "EXECUTIVE", "B2C", "INACTIVE", "MISSING", "UNMAPPED"]
    for b in order:
        items = buckets.get(b, [])
        if not items:
            continue
        total = sum(n for _, n in items)
        print(f"\n  [{b}]  {len(items)} distinct name(s), {total} invoice(s):")
        for label, n in sorted(items, key=lambda x: -x[1]):
            print(f"      {n:>4}  {label}")

    print("\n(4) ZOHO NAMES THAT DO NOT MAP TO ROSTER (need attention)")
    unmapped = buckets.get("UNMAPPED", [])
    if not unmapped:
        print("    none — every non-special Zoho name maps to a roster sheet.")
    else:
        for label, n in sorted(unmapped, key=lambda x: -x[1]):
            print(f"      {n:>4}  {label}  -> Pending / needs review")
    print("=" * 72)


if __name__ == "__main__":
    main()
