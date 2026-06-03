from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.commission.line_classification import classify_line_type, is_commission_candidate
from src.db.connection import DB_PATH, DbConnection, get_connection, init_database, using_postgres
from src.db.db_utils import date_prefix_expr
from src.fetch_zoho_data import (
    flatten_customer_payments,
    flatten_invoices,
    flatten_sales_orders,
    flatten_shipments,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
INPUT_DIR = BASE_DIR / "data" / "input"

DEFAULT_DATA_SOURCE = "sqlite"


@dataclass(frozen=True)
class PeriodDataFrames:
    """Zoho operational data for one commission month (Excel-compatible column names)."""

    sales_orders: pd.DataFrame
    invoices: pd.DataFrame
    shipments: pd.DataFrame
    items: pd.DataFrame
    payments: pd.DataFrame


@dataclass(frozen=True)
class CommissionInputDataFrames:
    """Normalized commission input: SQLite tables with classified line rows."""

    sales_orders: pd.DataFrame
    sales_order_lines: pd.DataFrame
    invoices: pd.DataFrame
    invoice_lines: pd.DataFrame
    items: pd.DataFrame
    customer_payments: pd.DataFrame
    customer_payment_invoices: pd.DataFrame

    def normalized_lines_sample(self, n: int = 10) -> pd.DataFrame:
        """First *n* lines from sales orders and invoices (mixed)."""
        parts: list[pd.DataFrame] = []
        for source, frame, id_col, num_col in (
            ("sales_order", self.sales_order_lines, "salesorder_id", "salesorder_number"),
            ("invoice", self.invoice_lines, "invoice_id", "invoice_number"),
        ):
            if frame.empty:
                continue
            sample = frame.head(max(1, n // 2)).copy()
            sample["source"] = source
            sample["document_id"] = sample[id_col]
            sample["document_number"] = (
                sample[num_col] if num_col in sample.columns else None
            )
            parts.append(sample)
        if not parts:
            return pd.DataFrame()
        combined = pd.concat(parts, ignore_index=True)
        return combined.head(n)


def month_bounds(year: int, month: int) -> tuple[str, str]:
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def _rows_to_dataframe(rows: list[Any]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(row) for row in rows])


def _enrich_line_dataframe(lines: pd.DataFrame) -> pd.DataFrame:
    if lines.empty:
        out = lines.copy()
        out["line_type"] = pd.Series(dtype=str)
        out["commission_candidate"] = pd.Series(dtype=bool)
        return out

    out = lines.copy()
    out["line_type"] = out.apply(
        lambda row: classify_line_type(
            sku=row.get("sku"),
            item_name=row.get("item_name"),
            item_total=row.get("item_total"),
            quantity=row.get("quantity"),
            rate=row.get("rate"),
        ),
        axis=1,
    )
    out["commission_candidate"] = out["line_type"].map(is_commission_candidate)
    return out


def _month_date_filter(column: str, conn: DbConnection) -> str:
    prefix = date_prefix_expr(column, conn.postgres)
    return f"{prefix} >= ? AND {prefix} <= ?"


def load_sales_orders_table(
    conn: DbConnection, year: int, month: int
) -> pd.DataFrame:
    start, end = month_bounds(year, month)
    date_filter = _month_date_filter("order_date", conn)
    rows = conn.execute(
        f"""
        SELECT salesorder_id, salesorder_number, order_date, reference_number,
               status, customer_id, customer_name, salesperson_name,
               shipping_charge, delivery_method, sub_total, total,
               last_modified_time, synced_at
        FROM sales_orders
        WHERE {date_filter}
        ORDER BY order_date, salesorder_number
        """,
        (start, end),
    ).fetchall()
    return _rows_to_dataframe(rows)


def load_sales_order_lines_table(
    conn: DbConnection, year: int, month: int
) -> pd.DataFrame:
    start, end = month_bounds(year, month)
    date_filter = _month_date_filter("so.order_date", conn)
    rows = conn.execute(
        f"""
        SELECT l.salesorder_id, l.line_index, l.line_item_id, l.sku, l.item_name,
               l.quantity, l.rate, l.discount, l.item_total, l.synced_at,
               so.salesorder_number, so.order_date, so.customer_name, so.salesperson_name
        FROM sales_order_lines l
        INNER JOIN sales_orders so ON so.salesorder_id = l.salesorder_id
        WHERE {date_filter}
        ORDER BY so.order_date, l.salesorder_id, l.line_index
        """,
        (start, end),
    ).fetchall()
    return _enrich_line_dataframe(_rows_to_dataframe(rows))


def load_invoices_table(conn: DbConnection, year: int, month: int) -> pd.DataFrame:
    start, end = month_bounds(year, month)
    date_filter = _month_date_filter("invoice_date", conn)
    rows = conn.execute(
        f"""
        SELECT invoice_id, invoice_number, invoice_date, reference_number,
               salesorder_number, status, balance, customer_name, salesperson_name,
               due_date, last_modified_time, synced_at
        FROM invoices
        WHERE {date_filter}
        ORDER BY invoice_date, invoice_number
        """,
        (start, end),
    ).fetchall()
    return _rows_to_dataframe(rows)


def load_invoice_lines_table(conn: DbConnection, year: int, month: int) -> pd.DataFrame:
    start, end = month_bounds(year, month)
    date_filter = _month_date_filter("i.invoice_date", conn)
    rows = conn.execute(
        f"""
        SELECT l.invoice_id, l.line_index, l.line_item_id, l.sku, l.item_name,
               l.quantity, l.rate, l.item_total, l.synced_at,
               i.invoice_number, i.invoice_date, i.customer_name, i.salesperson_name,
               i.salesorder_number
        FROM invoice_lines l
        INNER JOIN invoices i ON i.invoice_id = l.invoice_id
        WHERE {date_filter}
        ORDER BY i.invoice_date, l.invoice_id, l.line_index
        """,
        (start, end),
    ).fetchall()
    return _enrich_line_dataframe(_rows_to_dataframe(rows))


def load_items_table(conn: DbConnection) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT item_id, sku, name, status, rate, purchase_rate, unit,
               product_type, description, last_modified_time, synced_at
        FROM items
        ORDER BY sku, name
        """
    ).fetchall()
    return _rows_to_dataframe(rows)


def load_customer_payments_table(
    conn: DbConnection, year: int, month: int
) -> pd.DataFrame:
    start, end = month_bounds(year, month)
    date_filter = _month_date_filter("payment_date", conn)
    rows = conn.execute(
        f"""
        SELECT payment_id, payment_number, payment_date, customer_name,
               payment_mode, amount, unused_amount, reference_number,
               last_modified_time, synced_at
        FROM customer_payments
        WHERE {date_filter}
        ORDER BY payment_date, payment_number
        """,
        (start, end),
    ).fetchall()
    return _rows_to_dataframe(rows)


def load_customer_payment_invoices_table(
    conn: DbConnection, year: int, month: int
) -> pd.DataFrame:
    start, end = month_bounds(year, month)
    date_filter = _month_date_filter("cp.payment_date", conn)
    rows = conn.execute(
        f"""
        SELECT cpi.payment_id, cpi.invoice_id, cpi.invoice_number,
               cpi.amount_applied, cpi.synced_at,
               cp.payment_number, cp.payment_date, cp.customer_name
        FROM customer_payment_invoices cpi
        INNER JOIN customer_payments cp ON cp.payment_id = cpi.payment_id
        WHERE {date_filter}
        ORDER BY cp.payment_date, cpi.payment_id, cpi.invoice_number
        """,
        (start, end),
    ).fetchall()
    return _rows_to_dataframe(rows)


def load_commission_input(
    year: int, month: int, db_path: Path | None = None
) -> CommissionInputDataFrames:
    """Load normalized commission input tables for a month from SQLite."""
    init_database(db_path)
    conn = get_connection(db_path)
    try:
        return CommissionInputDataFrames(
            sales_orders=load_sales_orders_table(conn, year, month),
            sales_order_lines=load_sales_order_lines_table(conn, year, month),
            invoices=load_invoices_table(conn, year, month),
            invoice_lines=load_invoice_lines_table(conn, year, month),
            items=load_items_table(conn),
            customer_payments=load_customer_payments_table(conn, year, month),
            customer_payment_invoices=load_customer_payment_invoices_table(
                conn, year, month
            ),
        )
    finally:
        conn.close()


def line_type_counts(data: CommissionInputDataFrames) -> dict[str, int]:
    """Aggregate ``line_type`` counts across sales order and invoice lines."""
    counts = {
        "product": 0,
        "shipping": 0,
        "credit_card_fee": 0,
        "discount": 0,
        "other_charge": 0,
        "unknown": 0,
        "commission_candidate": 0,
    }
    for frame in (data.sales_order_lines, data.invoice_lines):
        if frame.empty or "line_type" not in frame.columns:
            continue
        for line_type, n in frame["line_type"].value_counts().items():
            key = str(line_type)
            if key in counts:
                counts[key] += int(n)
        if "commission_candidate" in frame.columns:
            counts["commission_candidate"] += int(frame["commission_candidate"].sum())
    return counts


def _load_sales_order_records(conn: DbConnection, year: int, month: int) -> list[dict[str, Any]]:
    start, end = month_bounds(year, month)
    records: list[dict[str, Any]] = []
    date_filter = _month_date_filter("order_date", conn)
    rows = conn.execute(
        f"""
        SELECT salesorder_id, raw_json
        FROM sales_orders
        WHERE {date_filter}
        """,
        (start, end),
    ).fetchall()
    for row in rows:
        order = json.loads(row["raw_json"])
        so_id = str(row["salesorder_id"] or order.get("salesorder_id") or "")
        line_rows = conn.execute(
            """
            SELECT raw_json FROM sales_order_lines
            WHERE salesorder_id = ?
            ORDER BY line_index
            """,
            (so_id,),
        ).fetchall()
        if line_rows:
            order["line_items"] = [json.loads(item["raw_json"]) for item in line_rows]
        records.append(order)
    return records


def _load_invoice_records(conn: DbConnection, year: int, month: int) -> list[dict[str, Any]]:
    start, end = month_bounds(year, month)
    records: list[dict[str, Any]] = []
    date_filter = _month_date_filter("invoice_date", conn)
    rows = conn.execute(
        f"""
        SELECT invoice_id, raw_json
        FROM invoices
        WHERE {date_filter}
        """,
        (start, end),
    ).fetchall()
    for row in rows:
        invoice = json.loads(row["raw_json"])
        inv_id = str(row["invoice_id"] or invoice.get("invoice_id") or "")
        line_rows = conn.execute(
            """
            SELECT raw_json FROM invoice_lines
            WHERE invoice_id = ?
            ORDER BY line_index
            """,
            (inv_id,),
        ).fetchall()
        if line_rows:
            invoice["line_items"] = [json.loads(item["raw_json"]) for item in line_rows]
        records.append(invoice)
    return records


def _load_shipment_records(conn: DbConnection, year: int, month: int) -> list[dict[str, Any]]:
    """Rebuild header-shaped shipment dicts (with line_items) from line-level SQLite rows."""
    start, end = month_bounds(year, month)
    date_filter = _month_date_filter("shipment_date", conn)
    shipment_rows = conn.execute(
        f"""
        SELECT shipment_key, shipment_id, shipment_number, shipment_date, status,
               carrier_name, tracking_number, shipping_charge, salesorder_id,
               salesorder_number, customer_name, sku, quantity, source_endpoint, raw_json
        FROM shipments
        WHERE {date_filter}
        ORDER BY shipment_key
        """,
        (start, end),
    ).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for row in shipment_rows:
        key = str(row["shipment_key"])
        payload = json.loads(row["raw_json"])
        header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
        line = payload.get("line") if isinstance(payload.get("line"), dict) else {}

        if key not in grouped:
            grouped[key] = {
                "shipment_id": row["shipment_id"] or header.get("shipment_id"),
                "shipmentorder_id": header.get("shipmentorder_id") or row["shipment_id"],
                "package_id": header.get("package_id"),
                "shipment_number": row["shipment_number"]
                or header.get("shipment_number", header.get("shipmentorder_number")),
                "date": row["shipment_date"] or header.get("date"),
                "status": row["status"] or header.get("status"),
                "carrier": row["carrier_name"] or header.get("carrier"),
                "tracking_number": row["tracking_number"] or header.get("tracking_number"),
                "shipping_charge": row["shipping_charge"] or header.get("shipping_charge", 0),
                "salesorder_id": row["salesorder_id"] or header.get("salesorder_id"),
                "salesorder_number": row["salesorder_number"] or header.get("salesorder_number"),
                "customer_name": row["customer_name"] or header.get("customer_name"),
                "_source_endpoint": row["source_endpoint"] or header.get("_source_endpoint", ""),
                "line_items": [],
            }

        sku = row["sku"] or line.get("sku", "")
        qty = row["quantity"] if row["quantity"] is not None else line.get("quantity", 0)
        if sku or qty:
            grouped[key]["line_items"].append({"sku": sku, "quantity": qty, **line})

    records = list(grouped.values())
    for record in records:
        if not record.get("line_items"):
            record["line_items"] = [{}]
    return records


def load_sales_orders(conn: DbConnection, year: int, month: int) -> pd.DataFrame:
    records = _load_sales_order_records(conn, year, month)
    frame = flatten_sales_orders(records)
    if frame.empty:
        return frame
    if "Quantity" not in frame.columns and "Quantity Ordered" in frame.columns:
        frame["Quantity"] = frame["Quantity Ordered"]
    return frame


def load_invoices(conn: DbConnection, year: int, month: int) -> pd.DataFrame:
    records = _load_invoice_records(conn, year, month)
    frame = flatten_invoices(records)
    if frame.empty:
        return frame
    invoice_ids: list[str] = []
    for rec in records:
        inv_id = str(rec.get("invoice_id") or "")
        for _line in rec.get("line_items") or [{}]:
            invoice_ids.append(inv_id)
    if len(invoice_ids) == len(frame):
        frame["Invoice ID"] = invoice_ids
    return frame


def load_shipments(conn: DbConnection, year: int, month: int) -> pd.DataFrame:
    records = _load_shipment_records(conn, year, month)
    frame = flatten_shipments(records)
    if frame.empty:
        return frame
    so_ids: list[str] = []
    shipment_ids: list[str] = []
    for rec in records:
        sid = str(rec.get("salesorder_id") or "")
        shid = str(rec.get("shipment_id") or rec.get("shipmentorder_id") or rec.get("package_id") or "")
        for _line in rec.get("line_items") or [{}]:
            so_ids.append(sid)
            shipment_ids.append(shid)
    if len(so_ids) == len(frame):
        frame["Sales Order ID"] = so_ids
    if len(shipment_ids) == len(frame):
        frame["Shipment ID"] = shipment_ids
    return frame


def load_items(conn: DbConnection) -> pd.DataFrame:
    from src.fetch_zoho_data import flatten_items

    records = [
        json.loads(row["raw_json"])
        for row in conn.execute("SELECT raw_json FROM items ORDER BY sku").fetchall()
    ]
    return flatten_items(records)


def load_payments(conn: DbConnection, year: int, month: int) -> pd.DataFrame:
    start, end = month_bounds(year, month)
    date_filter = _month_date_filter("payment_date", conn)
    records = [
        json.loads(row["raw_json"])
        for row in conn.execute(
            f"""
            SELECT raw_json
            FROM customer_payments
            WHERE {date_filter}
            """,
            (start, end),
        ).fetchall()
    ]
    return flatten_customer_payments(records)


def load_period_dataframes(year: int, month: int, db_path: Path | None = None) -> PeriodDataFrames:
    """
    Load normalized Zoho data for a commission month from SQLite.

    Column names match Zoho Excel exports so ``main.build_*`` functions accept these
    DataFrames directly (no Excel read required).
    """
    init_database(db_path)
    conn = get_connection(db_path)
    try:
        return PeriodDataFrames(
            sales_orders=load_sales_orders(conn, year, month),
            invoices=load_invoices(conn, year, month),
            shipments=load_shipments(conn, year, month),
            items=load_items(conn),
            payments=load_payments(conn, year, month),
        )
    finally:
        conn.close()


def _count_in_month(conn, table: str, date_col: str, year: int, month: int) -> int:
    """Lightweight COUNT(*) for a month — avoids loading/parsing every raw_json row."""
    start, end = month_bounds(year, month)
    expr = date_prefix_expr(date_col, conn.postgres)
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM {table} WHERE {expr} >= ? AND {expr} <= ?",
        (start, end),
    ).fetchone()
    return int(row["c"] or 0)


def period_counts(year: int, month: int, db_path: Path | None = None) -> dict[str, int]:
    """Row counts per module for a month using cheap COUNT(*) queries (no DataFrame build)."""
    init_database(db_path)
    conn = get_connection(db_path)
    try:
        return {
            "sales_orders": _count_in_month(conn, "sales_orders", "order_date", year, month),
            "invoices": _count_in_month(conn, "invoices", "invoice_date", year, month),
            "shipments": _count_in_month(conn, "shipments", "shipment_date", year, month),
            "items": int(conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"] or 0),
            "payments": _count_in_month(conn, "customer_payments", "payment_date", year, month),
        }
    finally:
        conn.close()


def has_period_data(year: int, month: int, db_path: Path | None = None) -> bool:
    """True when SQLite has sales orders AND invoices for the month (cheap counts)."""
    init_database(db_path)
    conn = get_connection(db_path)
    try:
        if _count_in_month(conn, "sales_orders", "order_date", year, month) == 0:
            return False
        return _count_in_month(conn, "invoices", "invoice_date", year, month) > 0
    finally:
        conn.close()


def export_period_to_input(year: int, month: int, db_path: Path | None = None) -> dict[str, Path]:
    """
    Optional: write SQLite period data to ``data/input/`` Excel files (legacy / debugging).
    Not used by the default commission engine path.
    """
    month_name = calendar.month_name[month]
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    frames = load_period_dataframes(year, month, db_path)

    paths = {
        "sales_orders": INPUT_DIR / f"Sales_Orders {month_name} {year}.xlsx",
        "invoices": INPUT_DIR / f"Invoices {month_name} {year}.xlsx",
        "shipments": INPUT_DIR / f"Shipments {month_name} {year}.xlsx",
        "items": INPUT_DIR / f"Items {month_name} {year}.xlsx",
    }

    frames.sales_orders.to_excel(paths["sales_orders"], index=False)
    frames.invoices.to_excel(paths["invoices"], index=False)
    frames.shipments.to_excel(paths["shipments"], index=False)
    frames.items.to_excel(paths["items"], index=False)

    return paths


def database_status(db_path: Path | None = None) -> dict[str, Any]:
    init_database(db_path)
    path = db_path or DB_PATH
    conn = get_connection(path)
    try:
        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])
            for table in [
                "sales_orders",
                "invoices",
                "shipments",
                "items",
                "customer_payments",
            ]
        }
        last = conn.execute(
            """
            SELECT finished_at, status, sync_id, date_start, date_end
            FROM zoho_sync_runs
            WHERE status = 'success'
            ORDER BY finished_at DESC
            LIMIT 1
            """
        ).fetchone()
        latest_batch = conn.execute(
            """
            SELECT sync_id, started_at, finished_at, status, module, records_fetched, error_message
            FROM zoho_sync_runs
            ORDER BY started_at DESC
            LIMIT 20
            """
        ).fetchall()
        return {
            "database_backend": "postgres" if using_postgres() else "sqlite",
            "database_path": str(path) if not using_postgres() else "DATABASE_URL",
            "exists": True if using_postgres() else path.exists(),
            "last_sync_time": last["finished_at"] if last else None,
            "last_sync_status": last["status"] if last else None,
            "counts": counts,
            "latest_runs": [dict(row) for row in latest_batch],
        }
    finally:
        conn.close()
