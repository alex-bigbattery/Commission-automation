"""Quick read-only check that cf_ticket extraction works (May 2026 invoices)."""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.db.connection import get_connection
from src.commission.sqlite_to_workbook import _load_invoice_meta_map

with get_connection() as conn:
    meta = _load_invoice_meta_map(conn, 2026, 5)

with_ticket = {iid: m for iid, m in meta.items() if (m.get("ticket_number") or "").strip()}
print(f"May invoices loaded: {len(meta)}")
print(f"Invoices WITH a populated cf_ticket: {len(with_ticket)}")
print("By sales team:")
for team, n in Counter(m["sales_team"] or "(blank)" for m in with_ticket.values()).most_common():
    print(f"   {n:>3}  {team}")
print("Examples (invoice_id, ticket#, sales_team):")
for iid, m in list(with_ticket.items())[:8]:
    print(f"   {iid}  ticket={m['ticket_number']:<8} team={m['sales_team']}")
