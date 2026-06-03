"""
PRICE_ANOMALY report (read-only, light): scan months for product lines whose
invoiced amount is far above MAP (the engine's PRICE_ANOMALY signal — a likely
mis-keyed ticket, e.g. a $3k item invoiced at $400k).

Mirrors the engine's logic (same MAP source, tiers, factor) but skips the heavy
sales-order returns/shipment loading, so it runs fast even for big months.

Usage:
    python scripts/price_anomaly_report.py --months 3,4,5 --year 2026
"""
from __future__ import annotations

import argparse
import calendar
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.db.connection import get_connection
from src.commission.sqlite_to_workbook import (
    _load_invoice_meta_map, _load_item_map, PRICE_ANOMALY_FACTOR,
    load_tiers_from_template, load_map_from_template,
    implied_discount, commission_rate, rate_type_for,
)
from src.commission.line_classification import classify_line_type
from src.commission.roster import resolve_roster_sheet

TEMPLATE = REPO / "data" / "templates" / "master_template_clean.xlsx"


def money(x) -> str:
    return f"${float(x or 0):,.2f}"


def is_b2b(team: str) -> bool:
    t = (team or "").strip().lower()
    return t.startswith("b2b") or t.startswith("exe") or "comp. account" in t


def scan_month(conn, year: int, month: int, map_by_sku: dict, tiers, all_teams: bool = False) -> dict:
    meta = _load_invoice_meta_map(conn, year, month)
    inv_ids = list(meta.keys())
    if not inv_ids:
        return {"count": 0, "sos": set(), "invoices": set(), "commission": 0.0, "examples": []}

    # invoice header info (columns only — no raw_json)
    info: dict[str, dict] = {}
    so_numbers: set[str] = set()
    ph = ",".join("?" * len(inv_ids))
    for r in conn.execute(
        f"SELECT invoice_id, invoice_number, salesorder_number, salesperson_name "
        f"FROM invoices WHERE invoice_id IN ({ph})", inv_ids
    ).fetchall():
        info[str(r["invoice_id"])] = {
            "invoice_number": r["invoice_number"] or "",
            "salesorder_number": r["salesorder_number"] or "",
            "inv_salesperson": r["salesperson_name"] or "",
        }
        if r["salesorder_number"]:
            so_numbers.add(r["salesorder_number"])

    # SO salesperson (column only)
    so_sp: dict[str, str] = {}
    if so_numbers:
        sos = list(so_numbers)
        ph2 = ",".join("?" * len(sos))
        for r in conn.execute(
            f"SELECT salesorder_number, salesperson_name FROM sales_orders "
            f"WHERE salesorder_number IN ({ph2})", sos
        ).fetchall():
            so_sp[r["salesorder_number"]] = r["salesperson_name"] or ""

    # invoice lines (no raw_json)
    anomalies = []
    top_ratios = []  # (ratio, label) for ALL valid lines, to confirm detector works
    affected_sos: set[str] = set()
    affected_invs: set[str] = set()
    total_comm = 0.0
    for r in conn.execute(
        f"SELECT invoice_id, sku, item_name, quantity, item_total, rate "
        f"FROM invoice_lines WHERE invoice_id IN ({ph})", inv_ids
    ).fetchall():
        iid = str(r["invoice_id"])
        team = meta.get(iid, {}).get("sales_team", "")
        if not all_teams and not is_b2b(team):
            continue
        sku = (r["sku"] or "").strip()
        qty = float(r["quantity"] or 0)
        item_total = float(r["item_total"] or 0)
        rate0 = float(r["rate"] or 0)
        if item_total <= 0 or qty <= 0:
            continue
        lt = classify_line_type(sku=sku, item_name=(r["item_name"] or ""),
                                item_total=item_total, quantity=qty, rate=rate0)
        if lt != "product":
            continue
        mp = map_by_sku.get(sku.upper(), 0.0)
        if mp <= 0:
            continue
        ratio = item_total / (mp * qty)
        top_ratios.append((ratio, f"{sku} x{qty:g} MAP {money(mp)} -> {money(item_total)} ({ratio:.2f}x)"))
        if item_total <= mp * qty * PRICE_ANOMALY_FACTOR:
            continue

        # anomaly!
        h = info.get(iid, {})
        full_name = so_sp.get(h.get("salesorder_number", ""), "") or h.get("inv_salesperson", "")
        rt = rate_type_for(resolve_roster_sheet(full_name) or "Paul")
        disc = implied_discount(item_total, mp, qty)  # clamps to 0 when revenue >> MAP
        rate = commission_rate(disc, rt, tiers) if mp > 0 else 0.0
        # Only B2B/Exe lines would ever be paid; B2C is non-commissionable anyway.
        comm = (item_total * rate) if is_b2b(team) else 0.0
        total_comm += comm
        affected_sos.add(h.get("salesorder_number", ""))
        affected_invs.add(h.get("invoice_number", ""))
        anomalies.append({
            "invoice": h.get("invoice_number", ""), "so": h.get("salesorder_number", ""),
            "sku": sku, "qty": qty, "map": mp, "invoiced": item_total,
            "ratio": item_total / (mp * qty), "salesperson": full_name,
            "team": team, "est_commission": comm,
        })

    anomalies.sort(key=lambda a: a["ratio"], reverse=True)
    top_ratios.sort(reverse=True)
    return {"count": len(anomalies), "sos": affected_sos - {""},
            "invoices": affected_invs - {""}, "commission": total_comm,
            "examples": anomalies, "top_ratios": top_ratios[:5],
            "lines_checked": len(top_ratios)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--months", default="1,2,3,4,5")
    ap.add_argument("--all-teams", action="store_true",
                    help="Scan ALL sales teams (incl. B2C), not just B2B/Exe.")
    args = ap.parse_args()
    months = [int(m) for m in args.months.split(",") if m.strip()]

    tiers = load_tiers_from_template(TEMPLATE)
    rlp = load_map_from_template(TEMPLATE)

    scope = "ALL sales teams (incl. B2C)" if args.all_teams else "B2B/Exe product lines"
    print("=" * 70)
    print(f" PRICE_ANOMALY REPORT  (invoiced > {PRICE_ANOMALY_FACTOR:g}x MAP*qty, {scope})")
    print(f" Months: {months}  Year: {args.year}")
    print("=" * 70)

    grand = {"count": 0, "sos": set(), "invoices": set(), "commission": 0.0}
    with get_connection() as conn:
        item_map = _load_item_map(conn)
        map_by_sku = {**item_map, **rlp}
        for m in months:
            res = scan_month(conn, args.year, m, map_by_sku, tiers, all_teams=args.all_teams)
            print(f"\n### {calendar.month_name[m]} {args.year}")
            print(f"  PRICE_ANOMALY count            : {res['count']}")
            print(f"  Sales Orders affected          : {len(res['sos'])}  {sorted(res['sos'])}")
            print(f"  Invoices affected              : {len(res['invoices'])}  {sorted(res['invoices'])}")
            print(f"  Commission amount affected     : {money(res['commission'])}")
            if res["examples"]:
                print("  Examples (top by ratio):")
                for a in res["examples"][:8]:
                    print(f"    {a['invoice']} / {a['so']} | {a['sku']} x{a['qty']:g} | "
                          f"MAP {money(a['map'])} -> invoiced {money(a['invoiced'])} "
                          f"({a['ratio']:.0f}x) | {a['salesperson']} [{a['team']}] | "
                          f"est. comm {money(a['est_commission'])}")
            # diagnostic: confirm the detector can fire — show the highest ratios seen
            print(f"  [diag] B2B/Exe product lines checked: {res['lines_checked']} | "
                  f"highest invoiced/MAP ratios:")
            for ratio, label in res["top_ratios"]:
                print(f"         {label}")
            grand["count"] += res["count"]
            grand["sos"] |= res["sos"]
            grand["invoices"] |= res["invoices"]
            grand["commission"] += res["commission"]

    print("\n" + "-" * 70)
    print(" COMBINED")
    print(f"  PRICE_ANOMALY count        : {grand['count']}")
    print(f"  Sales Orders affected      : {len(grand['sos'])}")
    print(f"  Invoices affected          : {len(grand['invoices'])}")
    print(f"  Commission amount affected : {money(grand['commission'])}")
    print("=" * 70)


if __name__ == "__main__":
    main()
