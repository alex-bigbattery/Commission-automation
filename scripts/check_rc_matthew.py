"""Report CF.Sales Team for May invoices whose salesperson is 'RC Matthew' (read-only)."""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.db.connection import get_connection
from src.commission.sqlite_to_workbook import _load_invoice_meta_map

TARGET = "rc matthew"

with get_connection() as conn:
    meta = _load_invoice_meta_map(conn, 2026, 5)
    rows = conn.execute(
        "SELECT i.invoice_id, i.invoice_number, i.salesperson_name AS inv_sp, "
        "so.salesperson_name AS so_sp "
        "FROM invoices i "
        "LEFT JOIN sales_orders so ON so.salesorder_number = i.salesorder_number "
        "WHERE i.invoice_date LIKE ?",
        ("2026-05%",),
    ).fetchall()

matches = []
for r in rows:
    eff = (r["so_sp"] or r["inv_sp"] or "").strip()
    if eff.lower() == TARGET:
        team = meta.get(str(r["invoice_id"]), {}).get("sales_team", "")
        matches.append((r["invoice_number"], team))

print(f"RC Matthew invoices in May 2026: {len(matches)}")
print("\nBy CF.Sales Team:")
for team, n in Counter(t or "(blank)" for _, t in matches).most_common():
    print(f"   {n:>3}  {team}")

commissionable = [(inv, t) for inv, t in matches
                  if (t or "").lower().startswith("b2b")
                  or (t or "").lower().startswith("exe")
                  or "comp. account" in (t or "").lower()]
print(f"\nB2B / Exe./Comp. (commissionable) RC Matthew invoices: {len(commissionable)}")
for inv, t in commissionable:
    print(f"   {inv}  [{t}]")
if not commissionable:
    print("   none — all RC Matthew invoices are B2C/RC/non-commissionable (already excluded).")
