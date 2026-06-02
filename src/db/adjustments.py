"""
Manual adjustments repository (Accounting review layer).

Adjustments live in their own SQLite table and are applied AFTER the automated
commission calculation, BEFORE final export. Raw Zoho tables are never touched.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .connection import get_connection, init_database


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_line_uid(invoice_number: Any, sku: Any, sales_order_number: Any) -> str:
    """Stable identifier for a commission line within a period."""
    inv = str(invoice_number or "").strip()
    sk = str(sku or "").strip()
    so = str(sales_order_number or "").strip()
    return f"{inv}|{sk}|{so}"


# Columns a reviewer can set (the rest are managed automatically).
_NULLABLE_NUM = (
    "adjusted_commissionable",
    "adjusted_map",
    "adjusted_discount",
    "original_commissionable",
    "original_map",
    "original_discount",
)
_TEXT = (
    "sales_order_number",
    "invoice_number",
    "sku",
    "original_salesperson",
    "adjusted_salesperson",
    "classification",
    "reason",
    "reviewer",
    "approval_status",
)


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def upsert_adjustment(payload: dict[str, Any], db_path: Path | None = None) -> dict[str, Any]:
    """Create or update the single adjustment for (period, line_uid)."""
    init_database(db_path)
    year = int(payload["period_year"])
    month = int(payload["period_month"])
    line_uid = payload.get("line_uid") or make_line_uid(
        payload.get("invoice_number"), payload.get("sku"), payload.get("sales_order_number")
    )
    now = _now()
    conn = get_connection(db_path)
    try:
        existing = conn.execute(
            "SELECT id, created_at FROM manual_adjustments WHERE period_year=? AND period_month=? AND line_uid=?",
            (year, month, line_uid),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        values = {
            "period_year": year,
            "period_month": month,
            "line_uid": line_uid,
            "sales_order_number": payload.get("sales_order_number"),
            "invoice_number": payload.get("invoice_number"),
            "sku": payload.get("sku"),
            "original_salesperson": payload.get("original_salesperson"),
            "adjusted_salesperson": (payload.get("adjusted_salesperson") or None),
            "original_commissionable": _to_float(payload.get("original_commissionable")),
            "adjusted_commissionable": _to_float(payload.get("adjusted_commissionable")),
            "original_map": _to_float(payload.get("original_map")),
            "adjusted_map": _to_float(payload.get("adjusted_map")),
            "original_discount": _to_float(payload.get("original_discount")),
            "adjusted_discount": _to_float(payload.get("adjusted_discount")),
            "exclude_flag": 1 if payload.get("exclude_flag") else 0,
            "classification": (payload.get("classification") or None),
            "reason": payload.get("reason"),
            "reviewer": payload.get("reviewer"),
            "approval_status": payload.get("approval_status") or "pending",
            "created_at": created_at,
            "updated_at": now,
        }
        cols = list(values.keys())
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("created_at",))
        conn.execute(
            f"""
            INSERT INTO manual_adjustments ({",".join(cols)})
            VALUES ({placeholders})
            ON CONFLICT(period_year, period_month, line_uid) DO UPDATE SET {updates}
            """,
            [values[c] for c in cols],
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM manual_adjustments WHERE period_year=? AND period_month=? AND line_uid=?",
            (year, month, line_uid),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def list_adjustments(
    year: int,
    month: int,
    db_path: Path | None = None,
    **filters: Any,
) -> list[dict[str, Any]]:
    init_database(db_path)
    conn = get_connection(db_path)
    try:
        clauses = ["period_year=?", "period_month=?"]
        params: list[Any] = [year, month]
        for col in ("sales_order_number", "invoice_number", "sku", "approval_status"):
            val = filters.get(col)
            if val:
                clauses.append(f"{col} LIKE ?")
                params.append(f"%{val}%")
        sql = "SELECT * FROM manual_adjustments WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_adjustment_map(year: int, month: int, db_path: Path | None = None) -> dict[str, dict[str, Any]]:
    """{line_uid: adjustment_row} for fast application during calculation."""
    return {a["line_uid"]: a for a in list_adjustments(year, month, db_path=db_path)}


def delete_adjustment(adjustment_id: int, db_path: Path | None = None) -> bool:
    init_database(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.execute("DELETE FROM manual_adjustments WHERE id=?", (adjustment_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
