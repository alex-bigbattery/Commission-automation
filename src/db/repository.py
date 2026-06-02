from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .connection import get_connection, init_database
from .zoho_lines import extract_line_items, payment_invoice_applications, synthetic_line_item_id


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def custom_field_map(record: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for field in record.get("custom_fields") or []:
        if not isinstance(field, dict):
            continue
        label = str(field.get("label") or field.get("api_name") or "").strip()
        value = field.get("value")
        if label:
            out[label] = "" if value is None else str(value)
    return out


class DatabaseRepository:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._owns_connection = conn is None
        self.conn = conn or get_connection()
        init_database()

    def close(self) -> None:
        if self._owns_connection:
            self.conn.close()

    def __enter__(self) -> DatabaseRepository:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def begin_sync_id(self) -> str:
        return str(uuid.uuid4())

    def start_sync_run(
        self,
        *,
        sync_id: str,
        module: str,
        date_start: str | None = None,
        date_end: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO zoho_sync_runs (
                sync_id, started_at, status, date_start, date_end, module, records_fetched
            ) VALUES (?, ?, 'running', ?, ?, ?, 0)
            """,
            (sync_id, utc_now_iso(), date_start, date_end, module),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_sync_run(
        self,
        run_id: int,
        *,
        status: str,
        records_fetched: int,
        records_inserted: int | None = None,
        records_updated: int | None = None,
        error_message: str | None = None,
    ) -> None:
        inserted = records_fetched if records_inserted is None else records_inserted
        updated = 0 if records_updated is None else records_updated
        self.conn.execute(
            """
            UPDATE zoho_sync_runs
            SET finished_at = ?, status = ?, records_fetched = ?,
                records_inserted = ?, records_updated = ?, error_message = ?
            WHERE id = ?
            """,
            (utc_now_iso(), status, records_fetched, inserted, updated, error_message, run_id),
        )
        self.conn.commit()

    def upsert_sales_orders(self, records: list[dict[str, Any]]) -> int:
        synced_at = utc_now_iso()
        count = 0
        for order in records:
            so_id = str(order.get("salesorder_id") or "").strip()
            if not so_id:
                continue
            self.conn.execute(
                """
                INSERT INTO sales_orders (
                    salesorder_id, salesorder_number, order_date, reference_number, status,
                    customer_id, customer_name, salesperson_name, shipping_charge, delivery_method,
                    sub_total, total, last_modified_time, raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(salesorder_id) DO UPDATE SET
                    salesorder_number = excluded.salesorder_number,
                    order_date = excluded.order_date,
                    reference_number = excluded.reference_number,
                    status = excluded.status,
                    customer_id = excluded.customer_id,
                    customer_name = excluded.customer_name,
                    salesperson_name = excluded.salesperson_name,
                    shipping_charge = excluded.shipping_charge,
                    delivery_method = excluded.delivery_method,
                    sub_total = excluded.sub_total,
                    total = excluded.total,
                    last_modified_time = excluded.last_modified_time,
                    raw_json = excluded.raw_json,
                    synced_at = excluded.synced_at
                """,
                (
                    so_id,
                    order.get("salesorder_number"),
                    order.get("date"),
                    order.get("reference_number"),
                    order.get("status"),
                    order.get("customer_id"),
                    order.get("customer_name"),
                    order.get("salesperson_name") or order.get("salesperson"),
                    float(order.get("shipping_charge") or 0),
                    order.get("delivery_method"),
                    float(order.get("sub_total") or 0),
                    float(order.get("total") or 0),
                    order.get("last_modified_time"),
                    json.dumps(order, default=str),
                    synced_at,
                ),
            )
            lines = extract_line_items(order)
            if lines:
                self.conn.execute("DELETE FROM sales_order_lines WHERE salesorder_id = ?", (so_id,))
            for idx, line in enumerate(lines):
                line_id = synthetic_line_item_id(so_id, idx, line, prefix="so")
                item_name = line.get("name") or line.get("item_name") or line.get("description") or ""
                self.conn.execute(
                    """
                    INSERT INTO sales_order_lines (
                        salesorder_id, line_index, line_item_id, sku, item_name,
                        quantity, rate, discount, item_total, raw_json, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(salesorder_id, line_index) DO UPDATE SET
                        line_item_id = excluded.line_item_id,
                        sku = excluded.sku,
                        item_name = excluded.item_name,
                        quantity = excluded.quantity,
                        rate = excluded.rate,
                        discount = excluded.discount,
                        item_total = excluded.item_total,
                        raw_json = excluded.raw_json,
                        synced_at = excluded.synced_at
                    """,
                    (
                        so_id,
                        idx,
                        line_id,
                        line.get("sku"),
                        item_name,
                        float(line.get("quantity") or 0),
                        float(line.get("rate") or 0),
                        str(line.get("discount") or ""),
                        float(line.get("item_total") or 0),
                        json.dumps(line, default=str),
                        synced_at,
                    ),
                )
            count += 1
        self.conn.commit()
        return count

    def upsert_invoices(self, records: list[dict[str, Any]]) -> int:
        synced_at = utc_now_iso()
        count = 0
        for invoice in records:
            inv_id = str(invoice.get("invoice_id") or "").strip()
            if not inv_id:
                continue
            self.conn.execute(
                """
                INSERT INTO invoices (
                    invoice_id, invoice_number, invoice_date, reference_number, salesorder_number,
                    status, balance, customer_name, salesperson_name, due_date,
                    last_modified_time, raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(invoice_id) DO UPDATE SET
                    invoice_number = excluded.invoice_number,
                    invoice_date = excluded.invoice_date,
                    reference_number = excluded.reference_number,
                    salesorder_number = excluded.salesorder_number,
                    status = excluded.status,
                    balance = excluded.balance,
                    customer_name = excluded.customer_name,
                    salesperson_name = excluded.salesperson_name,
                    due_date = excluded.due_date,
                    last_modified_time = excluded.last_modified_time,
                    raw_json = excluded.raw_json,
                    synced_at = excluded.synced_at
                """,
                (
                    inv_id,
                    invoice.get("invoice_number"),
                    invoice.get("date"),
                    invoice.get("reference_number"),
                    invoice.get("salesorder_number") or invoice.get("reference_number"),
                    invoice.get("status"),
                    float(invoice.get("balance") or 0),
                    invoice.get("customer_name"),
                    invoice.get("salesperson_name"),
                    invoice.get("due_date"),
                    invoice.get("last_modified_time"),
                    json.dumps(invoice, default=str),
                    synced_at,
                ),
            )
            lines = extract_line_items(invoice)
            if lines:
                self.conn.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (inv_id,))
            for idx, line in enumerate(lines):
                line_id = synthetic_line_item_id(inv_id, idx, line, prefix="inv")
                item_name = line.get("name") or line.get("item_name") or line.get("description") or ""
                self.conn.execute(
                    """
                    INSERT INTO invoice_lines (
                        invoice_id, line_index, line_item_id, sku, item_name,
                        quantity, rate, item_total, raw_json, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(invoice_id, line_index) DO UPDATE SET
                        line_item_id = excluded.line_item_id,
                        sku = excluded.sku,
                        item_name = excluded.item_name,
                        quantity = excluded.quantity,
                        rate = excluded.rate,
                        item_total = excluded.item_total,
                        raw_json = excluded.raw_json,
                        synced_at = excluded.synced_at
                    """,
                    (
                        inv_id,
                        idx,
                        line_id,
                        line.get("sku"),
                        item_name,
                        float(line.get("quantity") or 0),
                        float(line.get("rate") or 0),
                        float(line.get("item_total") or 0),
                        json.dumps(line, default=str),
                        synced_at,
                    ),
                )
            count += 1
        self.conn.commit()
        return count

    def upsert_shipments(self, records: list[dict[str, Any]]) -> int:
        synced_at = utc_now_iso()
        count = 0
        for shipment in records:
            shipment_id = str(
                shipment.get("shipment_id")
                or shipment.get("shipmentorder_id")
                or shipment.get("package_id")
                or ""
            ).strip()
            source = str(shipment.get("_source_endpoint") or "shipmentorders")
            shipment_number = str(
                shipment.get("shipment_number")
                or shipment.get("shipmentorder_number")
                or shipment.get("package_number")
                or ""
            )
            lines = shipment.get("line_items") or [{}]
            for idx, line in enumerate(lines):
                if not isinstance(line, dict):
                    line = {}
                sku = str(line.get("sku") or "")
                key = f"{source}:{shipment_id}:{idx}:{sku}"
                self.conn.execute(
                    """
                    INSERT INTO shipments (
                        shipment_key, shipment_id, shipment_number, shipment_date, status,
                        carrier_name, tracking_number, shipping_charge, salesorder_id,
                        salesorder_number, customer_name, sku, quantity, source_endpoint,
                        last_modified_time, raw_json, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(shipment_key) DO UPDATE SET
                        shipment_id = excluded.shipment_id,
                        shipment_number = excluded.shipment_number,
                        shipment_date = excluded.shipment_date,
                        status = excluded.status,
                        carrier_name = excluded.carrier_name,
                        tracking_number = excluded.tracking_number,
                        shipping_charge = excluded.shipping_charge,
                        salesorder_id = excluded.salesorder_id,
                        salesorder_number = excluded.salesorder_number,
                        customer_name = excluded.customer_name,
                        sku = excluded.sku,
                        quantity = excluded.quantity,
                        source_endpoint = excluded.source_endpoint,
                        last_modified_time = excluded.last_modified_time,
                        raw_json = excluded.raw_json,
                        synced_at = excluded.synced_at
                    """,
                    (
                        key,
                        shipment_id,
                        shipment_number,
                        shipment.get("date") or shipment.get("shipment_date"),
                        shipment.get("status"),
                        shipment.get("carrier") or shipment.get("delivery_method"),
                        shipment.get("tracking_number"),
                        float(shipment.get("shipping_charge") or 0),
                        shipment.get("salesorder_id"),
                        shipment.get("salesorder_number"),
                        shipment.get("customer_name"),
                        sku,
                        float(line.get("quantity") or 0),
                        source,
                        shipment.get("last_modified_time"),
                        json.dumps({"header": shipment, "line": line}, default=str),
                        synced_at,
                    ),
                )
                count += 1
        self.conn.commit()
        return count

    def upsert_items(self, records: list[dict[str, Any]]) -> int:
        synced_at = utc_now_iso()
        count = 0
        for item in records:
            item_id = str(item.get("item_id") or "").strip()
            if not item_id:
                continue
            self.conn.execute(
                """
                INSERT INTO items (
                    item_id, sku, name, status, rate, purchase_rate, unit,
                    product_type, description, last_modified_time, raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    sku = excluded.sku,
                    name = excluded.name,
                    status = excluded.status,
                    rate = excluded.rate,
                    purchase_rate = excluded.purchase_rate,
                    unit = excluded.unit,
                    product_type = excluded.product_type,
                    description = excluded.description,
                    last_modified_time = excluded.last_modified_time,
                    raw_json = excluded.raw_json,
                    synced_at = excluded.synced_at
                """,
                (
                    item_id,
                    item.get("sku"),
                    item.get("name"),
                    item.get("status"),
                    float(item.get("rate") or 0),
                    float(item.get("purchase_rate") or 0),
                    item.get("unit"),
                    item.get("product_type"),
                    item.get("description"),
                    item.get("last_modified_time"),
                    json.dumps(item, default=str),
                    synced_at,
                ),
            )
            count += 1
        self.conn.commit()
        return count

    def upsert_customer_payments(self, records: list[dict[str, Any]]) -> int:
        synced_at = utc_now_iso()
        count = 0
        for payment in records:
            payment_id = str(payment.get("payment_id") or "").strip()
            if not payment_id:
                continue
            self.conn.execute(
                """
                INSERT INTO customer_payments (
                    payment_id, payment_number, payment_date, customer_name, payment_mode,
                    amount, unused_amount, reference_number, last_modified_time, raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(payment_id) DO UPDATE SET
                    payment_number = excluded.payment_number,
                    payment_date = excluded.payment_date,
                    customer_name = excluded.customer_name,
                    payment_mode = excluded.payment_mode,
                    amount = excluded.amount,
                    unused_amount = excluded.unused_amount,
                    reference_number = excluded.reference_number,
                    last_modified_time = excluded.last_modified_time,
                    raw_json = excluded.raw_json,
                    synced_at = excluded.synced_at
                """,
                (
                    payment_id,
                    payment.get("payment_number"),
                    payment.get("date"),
                    payment.get("customer_name"),
                    payment.get("payment_mode"),
                    float(payment.get("amount") or 0),
                    float(payment.get("unused_amount") or 0),
                    payment.get("reference_number"),
                    payment.get("last_modified_time"),
                    json.dumps(payment, default=str),
                    synced_at,
                ),
            )
            applications = payment_invoice_applications(payment)
            if applications:
                self.conn.execute(
                    "DELETE FROM customer_payment_invoices WHERE payment_id = ?",
                    (payment_id,),
                )
            for inv in applications:
                invoice_id = str(inv.get("invoice_id") or inv.get("invoiceid") or "")
                invoice_number = str(inv.get("invoice_number") or inv.get("invoice_no") or "")
                self.conn.execute(
                    """
                    INSERT INTO customer_payment_invoices (
                        payment_id, invoice_id, invoice_number, amount_applied, raw_json, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(payment_id, invoice_id, invoice_number) DO UPDATE SET
                        amount_applied = excluded.amount_applied,
                        raw_json = excluded.raw_json,
                        synced_at = excluded.synced_at
                    """,
                    (
                        payment_id,
                        invoice_id,
                        invoice_number,
                        float(inv.get("amount_applied") or inv.get("amount") or 0),
                        json.dumps(inv, default=str),
                        synced_at,
                    ),
                )
            count += 1
        self.conn.commit()
        return count

    def table_counts(self) -> dict[str, int]:
        tables = [
            "sales_orders",
            "sales_order_lines",
            "invoices",
            "invoice_lines",
            "shipments",
            "items",
            "customer_payments",
            "customer_payment_invoices",
        ]
        counts: dict[str, int] = {}
        for table in tables:
            row = self.conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            counts[table] = int(row["c"]) if row else 0
        return counts

    def last_successful_sync(self, module: str | None = None) -> dict[str, Any] | None:
        if module:
            row = self.conn.execute(
                """
                SELECT sync_id, finished_at, date_start, date_end, module, records_fetched
                FROM zoho_sync_runs
                WHERE status IN ('success', 'partial_success') AND module = ?
                ORDER BY finished_at DESC
                LIMIT 1
                """,
                (module,),
            ).fetchone()
        else:
            row = self.conn.execute(
                """
                SELECT sync_id, finished_at, date_start, date_end, module, records_fetched
                FROM zoho_sync_runs
                WHERE status IN ('success', 'partial_success')
                ORDER BY finished_at DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def latest_sync_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT sync_id, module, status, records_fetched, records_inserted,
                   records_updated, error_message, started_at, finished_at
            FROM zoho_sync_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_sync_batch_status(self) -> list[dict[str, Any]]:
        batch_row = self.conn.execute(
            """
            SELECT sync_id
            FROM zoho_sync_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not batch_row:
            return []
        sync_id = batch_row["sync_id"]
        rows = self.conn.execute(
            """
            SELECT id, sync_id, started_at, finished_at, status, date_start, date_end,
                   module, records_fetched, error_message
            FROM zoho_sync_runs
            WHERE sync_id = ?
            ORDER BY id
            """,
            (sync_id,),
        ).fetchall()
        return [dict(row) for row in rows]
