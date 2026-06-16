"""Expected return clawback report (read-only — does not apply adjustments)."""
from __future__ import annotations

import calendar
import json
from datetime import date
from typing import Any

from src.commission.returns import load_return_metadata_map, parse_return_date
from src.commission.sqlite_to_workbook import (
    _load_invoice_lines_with_context,
    build_salespeople_from_sqlite,
    load_map_from_template,
    load_tiers_from_template,
    parse_date,
)
from src.db.adjustments import make_line_uid
from src.db.connection import DbConnection, get_connection, init_database


def list_returns_in_month(conn: DbConnection, year: int, month: int) -> list[dict[str, Any]]:
    """All SO salesreturns with date in the given month."""
    start = date(year, month, 1)
    last = calendar.monthrange(year, month)[1]
    end = date(year, month, last)
    rows = conn.execute(
        "SELECT salesorder_number, salesorder_id, raw_json FROM sales_orders WHERE raw_json IS NOT NULL"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        so_num = str(row["salesorder_number"] or "").strip()
        so_id = str(row["salesorder_id"] or "")
        try:
            order = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            continue
        sku_by_line_id: dict[str, str] = {}
        for li in order.get("line_items") or []:
            sku = str(li.get("sku") or "").strip().upper()
            lid = str(li.get("line_item_id") or "")
            if sku and lid:
                sku_by_line_id[lid] = sku
        for sr in order.get("salesreturns") or []:
            ret_date = parse_return_date(sr.get("date"))
            if not ret_date or ret_date < start or ret_date > end:
                continue
            rma = str(sr.get("salesreturn_number") or "").strip()
            for sli in sr.get("line_items") or []:
                so_item_id = str(sli.get("salesorder_item_id") or "")
                sku = sku_by_line_id.get(so_item_id, "") or str(sli.get("name") or "").strip().upper()
                if not sku:
                    continue
                out.append({
                    "sales_order": so_num,
                    "salesorder_id": so_id,
                    "sku": sku,
                    "return_date": ret_date.isoformat(),
                    "rma_number": rma,
                    "return_qty": float(sli.get("quantity") or 0),
                })
    out.sort(key=lambda r: (r["return_date"], r["sales_order"], r["sku"]))

    if out:
        for item in out:
            inv_row = conn.execute(
                """
                SELECT i.invoice_number, i.invoice_date
                FROM invoices i
                INNER JOIN invoice_lines il ON il.invoice_id = i.invoice_id
                WHERE i.salesorder_number = ? AND UPPER(il.sku) = ?
                ORDER BY i.invoice_date DESC, i.invoice_number DESC
                LIMIT 1
                """,
                (item["sales_order"], item["sku"]),
            ).fetchone()
            if inv_row:
                item["invoice_number"] = str(inv_row["invoice_number"] or "")
                item["invoice_date"] = str(inv_row["invoice_date"] or "")[:10]
            else:
                item["invoice_number"] = ""
                item["invoice_date"] = ""
    return out


def _invoice_months_for_returns(
    conn: DbConnection,
    returns_in_month: list[dict[str, Any]],
    clawback_year: int,
    clawback_month: int,
) -> set[tuple[int, int]]:
    """Distinct (year, month) of invoices tied to returned SO+SKU, before clawback month."""
    clawback_start = date(clawback_year, clawback_month, 1)
    months: set[tuple[int, int]] = set()
    for item in returns_in_month:
        rows = conn.execute(
            """
            SELECT DISTINCT i.invoice_date
            FROM invoices i
            INNER JOIN invoice_lines il ON il.invoice_id = i.invoice_id
            WHERE i.salesorder_number = ? AND UPPER(il.sku) = ?
            """,
            (item["sales_order"], item["sku"]),
        ).fetchall()
        for row in rows:
            inv_dt = parse_date(row["invoice_date"])
            if not inv_dt or inv_dt >= clawback_start:
                continue
            months.add((inv_dt.year, inv_dt.month))
    return months


def generate_expected_clawbacks(
    clawback_year: int,
    clawback_month: int,
    *,
    template_path: Any,
    db_path: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (clawback_rows, all_returns_in_month).

    Clawback rows are invoice lines paid in an earlier month under
    RETURN_AFTER_COMMISSION_MONTH where the RMA date falls in clawback_month.
    """
    init_database(db_path)
    conn = get_connection(db_path)
    try:
        returns_in_month = list_returns_in_month(conn, clawback_year, clawback_month)
        return_keys = {(r["salesorder_id"], r["sku"]) for r in returns_in_month}
        invoice_months = _invoice_months_for_returns(
            conn, returns_in_month, clawback_year, clawback_month
        )
    finally:
        conn.close()

    clawbacks: list[dict[str, Any]] = []
    tiers = load_tiers_from_template(template_path)
    rlp = load_map_from_template(template_path)

    for y, m in sorted(invoice_months):
        result = build_salespeople_from_sqlite(
            y, m, db_path=db_path, tiers=tiers, rlp_map=rlp, apply_adjustments=True
        )
        audit_by_uid = {a["line_uid"]: a for a in result.audit_rows}

        conn = get_connection(db_path)
        try:
            lines = _load_invoice_lines_with_context(conn, y, m)
            meta_map = load_return_metadata_map(
                conn, {ln.salesorder_id for ln in lines if ln.salesorder_id}
            )
        finally:
            conn.close()

        for rec in lines:
            key = (str(rec.salesorder_id or ""), rec.sku.strip().upper())
            if key not in return_keys:
                continue
            meta = meta_map.get(key, {})
            ret_date = meta.get("return_date")
            if isinstance(ret_date, str):
                ret_date = parse_return_date(ret_date)
            if not ret_date or ret_date.year != clawback_year or ret_date.month != clawback_month:
                continue
            line_uid = make_line_uid(rec.invoice_number, rec.sku, rec.salesorder_number)
            audit = audit_by_uid.get(line_uid, {})
            flags = str(audit.get("flags") or "")
            if "RETURN_AFTER_COMMISSION_MONTH" not in flags:
                continue
            commission = float(audit.get("final_commission") or audit.get("system_commission") or 0)
            clawbacks.append({
                "clawback_month": f"{clawback_year:04d}-{clawback_month:02d}",
                "invoice_month": f"{y:04d}-{m:02d}",
                "invoice_number": rec.invoice_number,
                "invoice_date": rec.invoice_date.isoformat() if rec.invoice_date else "",
                "sku": rec.sku.strip().upper(),
                "sales_order": rec.salesorder_number,
                "return_date": ret_date.isoformat(),
                "rma_number": meta.get("rma_number", ""),
                "rep": audit.get("system_salesperson") or audit.get("original_zoho_salesperson") or "",
                "original_commission": round(commission, 2),
                "expected_clawback": round(-commission, 2),
                "line_uid": line_uid,
            })

    clawbacks.sort(key=lambda r: (r["return_date"], r["invoice_number"], r["sku"]))
    return clawbacks, returns_in_month
