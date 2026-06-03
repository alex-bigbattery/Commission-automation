"""
Regenerate the May 2026 B2B workbook and print a focused report.

READ-ONLY against the database (only SELECTs via the engine); writes a NEW
workbook file (does not overwrite the historical commission_b2b_may_2026.xlsx).
Does NOT call Zoho.

Run only when no Zoho import is in progress:
    python scripts/regen_may_2026_report.py
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.commission.sqlite_to_workbook import (  # noqa: E402
    generate_commission_workbook, _reconciliation_values,
)
from src.commission.roster import roster_rep_sheet_keys  # noqa: E402

_ap = argparse.ArgumentParser()
_ap.add_argument("--year", type=int, default=2026)
_ap.add_argument("--month", type=int, default=5)
_ap.add_argument("--no-save", action="store_true",
                 help="Validation mode: write to a temp file and delete it (no real output).")
_args = _ap.parse_args()

YEAR, MONTH = _args.year, _args.month
TEMPLATE = REPO / "data" / "templates" / "master_template_clean.xlsx"
if _args.no_save:
    OUTPUT = Path(tempfile.gettempdir()) / f"_validate_{YEAR}_{MONTH}.xlsx"
else:
    OUTPUT = REPO / "data" / "output" / f"{YEAR}-{MONTH}_Commission B2B.xlsx"
OLD_META = REPO / "data" / "output" / "commission_b2b_may_2026.meta.json"

ROSTER = set(roster_rep_sheet_keys())


def money(x) -> str:
    return f"${float(x or 0):,.2f}"


def main() -> None:
    print("=" * 74)
    print(f" REGENERATE {calendar.month_name[MONTH]} {YEAR}  (read-only DB; no Zoho; new file)")
    print("=" * 74)

    # ---- BEFORE (from prior meta, if available; only meaningful for May) ----
    before = {}
    if MONTH == 5 and OLD_META.exists():
        try:
            before = json.loads(OLD_META.read_text(encoding="utf-8")).get("kpis", {})
        except Exception:
            before = {}
    print("\n[BEFORE] from prior meta (commission_b2b_may_2026.meta.json):")
    print(f"  total_commission : {money(before.get('total_commission'))}")
    print(f"  commissionable   : {before.get('commissionable_lines', 'n/a')}")
    print(f"  exceptions_count : {before.get('exceptions_count', 'n/a')}")
    print(f"  pending_lines    : {before.get('pending_lines', 'NOT RECORDED in prior meta')}")

    # ---- REGENERATE ----
    if not TEMPLATE.exists():
        raise SystemExit(f"Template not found: {TEMPLATE}")
    result = generate_commission_workbook(
        YEAR, MONTH, template_path=TEMPLATE, output_path=OUTPUT
    )
    k = result.kpis
    rows = result.audit_rows

    print(f"\n[OK] wrote {OUTPUT.name}  ({OUTPUT.stat().st_size:,} bytes)")

    # ---- 1. pending before/after ----
    print("\n(1) PENDING LINES")
    print(f"  before : {before.get('pending_lines', 'NOT RECORDED')}")
    print(f"  after  : {k.get('pending_lines')}   "
          f"(revenue {money(k.get('pending_revenue'))}, est. comm {money(k.get('pending_commission'))})")

    # ---- 2 & 3. ticket lines ----
    ticket_rows = [r for r in rows if "TICKET_NUMBER" in str(r.get("flags") or "")]
    print(f"\n(2) LINES WITH Ticket# (cf_ticket): {len(ticket_rows)}")
    # unique invoices / SOs
    inv = sorted({r.get("invoice") for r in ticket_rows if r.get("invoice")})
    so = sorted({r.get("sales_order") for r in ticket_rows if r.get("sales_order")})
    print(f"    unique invoices: {len(inv)} | unique sales orders: {len(so)}")

    print("\n(3) WHICH SALES ORDERS / INVOICES HAVE Ticket#")
    if not ticket_rows:
        print("    (none)")
    else:
        print(f"    {'Invoice':<12} {'SalesOrder':<12} {'SalesTeam':<22} {'Final':<14} "
              f"{'Pending':<8} {'Revenue':>12}")
        for r in ticket_rows:
            print(f"    {str(r.get('invoice') or ''):<12} {str(r.get('sales_order') or ''):<12} "
                  f"{str(r.get('sales_team') or '')[:21]:<22} "
                  f"{str(r.get('final_commission_assignment') or '')[:13]:<14} "
                  f"{('YES' if r.get('pending') else 'no'):<8} {money(r.get('revenue')):>12}")

    # ---- 4. B2B payable lines moved to pending BECAUSE of ticket ----
    moved = [
        r for r in ticket_rows
        if r.get("pending")
        and str(r.get("sales_team") or "").lower().startswith("b2b")
        and str(r.get("system_salesperson") or "") in ROSTER
    ]
    print(f"\n(4) B2B PAYABLE LINES HELD (pending) BECAUSE OF Ticket#: {len(moved)}")
    for r in moved:
        print(f"    {r.get('invoice')} / {r.get('sales_order')} -> would-be rep "
              f"{r.get('system_salesperson')} | revenue {money(r.get('revenue'))} | "
              f"est. comm {money(r.get('system_commission'))}")
    if not moved:
        print("    (none — in May the Ticket# invoices are not B2B-to-a-roster-rep)")

    # ---- 5. new total to pay ----
    gross = sum(v for v in result.totals_by_sheet.values())
    print("\n(5) TOTALS")
    print(f"  Total to Pay (rep + Bruce, KPI): {money(k.get('total_commission'))}")
    print(f"  Gross sheet commission         : {money(gross)}  "
          f"(Company Acct at full normal; only 20% is paid via Bruce)")
    for sheet, tot in result.totals_by_sheet.items():
        if tot:
            print(f"      {sheet:<14} {money(tot)}")

    # ---- 6. draft status ----
    print("\n(6) DRAFT / FINAL")
    is_draft = bool(k.get("is_draft"))
    print(f"  status: {'DRAFT' if is_draft else 'FINAL'}")
    reasons = []
    if (k.get("pending_lines") or 0) > 0:
        reasons.append(f"{k.get('pending_lines')} pending line(s)")
    if not k.get("shipment_data_present"):
        reasons.append("shipments not synced")
    if k.get("approval_incomplete"):
        reasons.append("unapproved adjustments")
    print(f"  draft because: {', '.join(reasons) if reasons else '(nothing pending — FINAL)'}")

    # ---- 7. business-rule breakdown (from recordings) ----
    def has(flag):
        return [r for r in rows if flag in str(r.get("flags") or "")]

    price_anom = has("PRICE_ANOMALY")
    bruce = has("COMPANY_ACCOUNT")
    marshall = has("EXECUTIVE_ACCOUNT")
    inactive = has("KNOWN_INACTIVE")
    b2c_rep = has("B2C_COUPON_RULE")
    unpaid = has("UNPAID")
    neg = has("NEGATIVE_BALANCE")
    # kit/$0 lines never become audit rows (they are excluded pre-line); count from exceptions
    kit_zero = [e for e in result.exceptions
                if "kit" in e.reason.lower() or e.reason.startswith("$0")]
    pay_confirm = [e for e in result.exceptions if "confirm before payout" in e.reason.lower()]

    print("\n(7) BUSINESS-RULE BREAKDOWN (from the recordings)")
    print(f"  Ticket# lines (cf_ticket present)      : {len(ticket_rows)}")
    print(f"  Possible ticket / price anomaly        : {len(price_anom)}")
    for r in price_anom[:10]:
        print(f"       {r.get('invoice')} / {r.get('sales_order')} {r.get('sku')} "
              f"revenue {money(r.get('revenue'))}")
    print(f"  Bruce -> Company Account lines         : {len(bruce)}")
    print(f"  Marshall/Eric -> Executive lines       : {len(marshall)}")
    print(f"  Known-inactive names (held)            : {len(inactive)}")
    print(f"  B2C coupon reps (Dylan/CS, held)       : {len(b2c_rep)}")
    print(f"  Kit / $0 lines excluded                : {len(kit_zero)}")
    print(f"  Payment-confirmation (unpaid) lines    : {len(unpaid)}")
    print(f"  Negative-balance lines                 : {len(neg)}")

    # ---- 8. Bruce / Executive / reconciliation (Marshall's rules) ----
    recon = _reconciliation_values(result)
    company_norm = recon["company_commission"]          # NORMAL comm on Bruce lines (Company Acct sheet)
    bruce_20 = round(company_norm * 0.20, 2)
    bruce_15 = round(recon["rep_commission"] * 0.15, 2)
    exec_rev = sum(r.get("revenue", 0) for r in marshall)
    exec_comm = sum(r.get("final_commission", 0) for r in marshall)

    print("\n(8) BRUCE / EXECUTIVE / RECONCILIATION")
    print(f"  Bruce Company Account lines            : {len(bruce)}")
    print(f"  Company Account NORMAL commission      : {money(company_norm)}")
    print(f"  Bruce 20% of Company Account           : {money(bruce_20)}")
    print(f"  Bruce 15% of rep commission            : {money(bruce_15)}")
    print(f"  TOTAL Bruce Commission (15%+20%)       : {money(recon['bruce'])}")
    print(f"  Rep commission (roster reps)           : {money(recon['rep_commission'])}")
    print(f"  TOTAL TO PAY (rep + Bruce)             : {money(recon['total_to_pay'])}")
    print(f"  Executive lines                        : {len(marshall)}  "
          f"revenue {money(exec_rev)}  commission {money(exec_comm)}")
    print(f"  Check A (sheets - rep)                 : {money(recon['check_a'])}")
    print(f"  Check B (rep+Bruce - total)            : {money(recon['check_b'])}")
    not_double = abs((recon['rep_commission'] + recon['bruce']) - recon['total_to_pay']) < 0.005
    print(f"  Company NOT double-counted             : "
          f"{'YES — total = rep + Bruce only' if not_double else 'NO — CHECK!'}")

    # ---- 9. Company Acct breakdown by original Zoho salesperson ----
    comp_rows = [r for r in rows if str(r.get("final_commission_assignment")) == "Company Account"]
    by_orig = {}
    for r in comp_rows:
        o = r.get("original_zoho_salesperson") or "(blank)"
        g = by_orig.setdefault(o, {"n": 0, "rev": 0.0, "comm": 0.0})
        g["n"] += 1
        g["rev"] += r.get("revenue", 0) or 0
        g["comm"] += r.get("final_commission", 0) or 0
    print("\n(9) COMPANY ACCT SHEET — by original Zoho salesperson")
    for o, g in sorted(by_orig.items(), key=lambda kv: -kv[1]["comm"]):
        print(f"    {o:<20} lines {g['n']:>3}  revenue {money(g['rev']):>13}  "
              f"normal comm {money(g['comm']):>11}  (Bruce 20% = {money(g['comm']*0.20)})")
    lit = by_orig.get("Company Account", {"n": 0, "rev": 0.0, "comm": 0.0})
    print(f"  Literal 'Company Account' invoices     : {lit['n']}")
    print(f"  Literal 'Company Account' revenue      : {money(lit['rev'])}")
    print(f"  Literal normal commission -> sheet     : {money(lit['comm'])}")
    print(f"  Bruce 20% impact from literal          : {money(lit['comm'] * 0.20)}")

    print("\n" + "=" * 74)

    if _args.no_save:
        try:
            OUTPUT.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
