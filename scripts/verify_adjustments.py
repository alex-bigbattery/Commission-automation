"""
Verify the Manual Adjustments Layer end-to-end — SAFELY.

Safety design:
  * Works on a TEMP COPY of the SQLite DB. The real database (raw Zoho tables
    AND the real manual_adjustments table) is opened read-only for the copy and
    never written. The temp copy is deleted at the end.
  * Writes NO Excel files, so historical workbooks are untouched.
  * Does not call Zoho.

What it checks:
  1. Baseline system commission (apply_adjustments=False).
  2. EXCLUDE      -> line final_commission == 0; period total drops by system amount.
  3. REASSIGN     -> line moves to another salaried rep; grand total unchanged.
  4. OVERRIDE     -> commissionable + discount override -> final = override * tier(rate).
  5. system_amount / adjustment_amount / final_amount are emitted per line.
  6. Raw Zoho table row counts are identical before and after applying adjustments.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.commission.sqlite_to_workbook import (
    build_salespeople_from_sqlite,
    commission_rate,
    load_map_from_template,
    load_tiers_from_template,
    rate_type_for,
)
from src.db.adjustments import upsert_adjustment, list_adjustments
from src.db.connection import DB_PATH, get_connection

TPL = BASE_DIR / "data" / "templates" / "master_template_clean.xlsx"
YEAR, MONTH = 2026, 3
EPS = 0.01

SALARIED = {"Paul", "Jose", "Michael", "Jim", "Weston", "Company Acct"}

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        failures.append(name)


def zoho_counts(db_path: Path) -> dict[str, int]:
    conn = get_connection(db_path)
    try:
        return {
            t: int(conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"])
            for t in ("sales_orders", "sales_order_lines", "invoices", "invoice_lines", "items")
        }
    finally:
        conn.close()


def run(db_path: Path, apply: bool):
    return build_salespeople_from_sqlite(
        YEAR, MONTH, db_path=db_path, tiers=TIERS, rlp_map=RLP, apply_adjustments=apply
    )


def find_line(audit, **where):
    for row in audit:
        if all(row.get(k) == v for k, v in where.items()):
            return row
    return None


def main() -> None:
    global TIERS, RLP
    if not DB_PATH.exists():
        raise SystemExit(f"Real DB not found: {DB_PATH}")

    TIERS = load_tiers_from_template(TPL)
    RLP = load_map_from_template(TPL)

    real_adj_before = len(list_adjustments(YEAR, MONTH))  # real DB, read-only

    tmp = Path(tempfile.gettempdir()) / "commission_verify_copy.sqlite"
    if tmp.exists():
        tmp.unlink()
    shutil.copy2(DB_PATH, tmp)
    print(f"Working on temp copy: {tmp}")

    try:
        counts_before = zoho_counts(tmp)

        # 1) Baseline (no adjustments)
        base = run(tmp, apply=False)
        base_total = round(sum(base.totals_by_sheet.values()), 2)
        print(f"\nBaseline system commission total: ${base_total:,.2f} "
              f"(lines={base.kpis.get('commissionable_lines')})")

        # Pick a salaried commissionable line with positive system commission and MAP.
        target = None
        for row in base.audit_rows:
            if (row["block"] == "commissionable" and row["system_commission"] > 1.0
                    and row["salesperson"] in SALARIED and row["final_rate"] > 0):
                target = row
                break
        if not target:
            raise SystemExit("No suitable commissionable line found to test.")

        uid = target["line_uid"]
        src_sheet = target["salesperson"]
        sys_comm = target["system_commission"]
        sys_commissionable = target["system_commissionable"]
        print(f"Test line: {uid}  rep={src_sheet}  system_commission=${sys_comm:,.2f}")
        base_total_src = round(base.totals_by_sheet.get(src_sheet, 0), 2)

        # 2) EXCLUDE -------------------------------------------------------
        print("\n[Test] EXCLUDE")
        upsert_adjustment({
            "period_year": YEAR, "period_month": MONTH, "line_uid": uid,
            "invoice_number": target["invoice"], "sku": target["sku"],
            "sales_order_number": target["sales_order"],
            "original_salesperson": src_sheet, "original_commissionable": sys_commissionable,
            "exclude_flag": True, "reason": "verify: exclude", "reviewer": "verify-bot",
            "approval_status": "approved",
        }, db_path=tmp)
        r = run(tmp, apply=True)
        line = find_line(r.audit_rows, line_uid=uid)
        check("excluded line final_commission == 0", abs(line["final_commission"]) < EPS,
              f"final={line['final_commission']}")
        check("excluded line adjustment == -system", abs(line["adjustment"] + sys_comm) < EPS,
              f"adj={line['adjustment']} system={sys_comm}")
        check("excluded line flagged adjusted", line["adjusted"] is True)
        new_total_src = round(r.totals_by_sheet.get(src_sheet, 0), 2)
        check("rep total dropped by system amount",
              abs((base_total_src - new_total_src) - sys_comm) < EPS,
              f"before={base_total_src} after={new_total_src}")
        check("reviewer stored", line["reviewer"] == "verify-bot")

        # 3) REASSIGN to another salaried rep -----------------------------
        print("\n[Test] REASSIGN salesperson")
        dest_sheet = next(s for s in SALARIED if s != src_sheet)
        upsert_adjustment({
            "period_year": YEAR, "period_month": MONTH, "line_uid": uid,
            "invoice_number": target["invoice"], "sku": target["sku"],
            "sales_order_number": target["sales_order"],
            "original_salesperson": src_sheet, "adjusted_salesperson": dest_sheet,
            "reason": "verify: reassign", "reviewer": "verify-bot", "approval_status": "pending",
        }, db_path=tmp)
        r = run(tmp, apply=True)
        line = find_line(r.audit_rows, line_uid=uid)
        check("line salesperson reassigned", line["salesperson"] == dest_sheet,
              f"{src_sheet} -> {line['salesperson']}")
        check("grand total unchanged after reassign (same rate type)",
              abs(round(sum(r.totals_by_sheet.values()), 2) - base_total) < EPS,
              f"base={base_total} now={round(sum(r.totals_by_sheet.values()),2)}")
        check("dest rep gained the commission",
              abs(round(r.totals_by_sheet.get(dest_sheet, 0) - base.totals_by_sheet.get(dest_sheet, 0), 2) - sys_comm) < EPS)

        # 4) OVERRIDE commissionable + discount ---------------------------
        print("\n[Test] OVERRIDE commissionable + discount")
        override_amt = 1000.0
        override_disc = 0.10
        expected_rate = commission_rate(override_disc, rate_type_for(src_sheet), TIERS)
        expected_final = round(override_amt * expected_rate, 2)
        upsert_adjustment({
            "period_year": YEAR, "period_month": MONTH, "line_uid": uid,
            "invoice_number": target["invoice"], "sku": target["sku"],
            "sales_order_number": target["sales_order"],
            "original_salesperson": src_sheet,
            "original_commissionable": sys_commissionable,
            "adjusted_commissionable": override_amt, "adjusted_discount": override_disc,
            "reason": "verify: override", "reviewer": "verify-bot", "approval_status": "approved",
        }, db_path=tmp)
        r = run(tmp, apply=True)
        line = find_line(r.audit_rows, line_uid=uid)
        check("final_commissionable == override", abs(line["final_commissionable"] - override_amt) < EPS,
              f"final_commissionable={line['final_commissionable']}")
        check("final_rate == tier(override_discount)", abs(line["final_rate"] - expected_rate) < 1e-6,
              f"final_rate={line['final_rate']} expected={expected_rate}")
        check("final_commission == override * tier rate", abs(line["final_commission"] - expected_final) < EPS,
              f"final={line['final_commission']} expected={expected_final}")
        check("adjustment == final - system",
              abs(line["adjustment"] - (line["final_commission"] - line["system_commission"])) < EPS)

        # 5) Outputs present ----------------------------------------------
        print("\n[Test] system / adjustment / final present on every audit row")
        keys_ok = all(
            all(k in row for k in ("system_commission", "adjustment", "final_commission"))
            for row in r.audit_rows
        )
        check("audit rows carry system_amount/adjustment_amount/final_amount", keys_ok)

        # 6) Raw Zoho untouched -------------------------------------------
        print("\n[Test] raw Zoho data not modified")
        counts_after = zoho_counts(tmp)
        check("Zoho table row counts identical", counts_before == counts_after,
              f"{counts_before} vs {counts_after}")

        # Real DB adjustments untouched
        real_adj_after = len(list_adjustments(YEAR, MONTH))
        check("real manual_adjustments table untouched",
              real_adj_before == real_adj_after,
              f"before={real_adj_before} after={real_adj_after}")

    finally:
        # Best-effort cleanup. On Windows SQLite may briefly hold the file; retry.
        import gc
        import time as _t
        gc.collect()
        for _ in range(5):
            try:
                if tmp.exists():
                    tmp.unlink()
                print(f"\nTemp copy deleted: {tmp}")
                break
            except PermissionError:
                _t.sleep(0.3)
        else:
            print(f"\nNote: temp copy left at {tmp} (OS temp dir will reclaim it).")

    print("\n" + "=" * 56)
    if failures:
        print(f"RESULT: {len(failures)} CHECK(S) FAILED: {failures}")
        raise SystemExit(1)
    print("RESULT: ALL CHECKS PASSED — adjustments apply correctly, Zoho untouched.")


if __name__ == "__main__":
    main()
