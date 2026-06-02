from __future__ import annotations

import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
# DB location is configurable so the cloud deploy (Render) can point at a
# persistent disk via the DB_PATH env var. Locally it stays under data/db/.
DB_PATH = Path(os.environ["DB_PATH"]) if os.environ.get("DB_PATH") else (
    BASE_DIR / "data" / "db" / "commission_automation.sqlite"
)

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS zoho_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    date_start TEXT,
    date_end TEXT,
    module TEXT NOT NULL,
    records_fetched INTEGER NOT NULL DEFAULT 0,
    records_inserted INTEGER NOT NULL DEFAULT 0,
    records_updated INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_zoho_sync_runs_sync_id ON zoho_sync_runs(sync_id);
CREATE INDEX IF NOT EXISTS idx_zoho_sync_runs_finished ON zoho_sync_runs(finished_at);

CREATE TABLE IF NOT EXISTS sales_orders (
    salesorder_id TEXT PRIMARY KEY,
    salesorder_number TEXT,
    order_date TEXT,
    reference_number TEXT,
    status TEXT,
    customer_id TEXT,
    customer_name TEXT,
    salesperson_name TEXT,
    shipping_charge REAL,
    delivery_method TEXT,
    sub_total REAL,
    total REAL,
    last_modified_time TEXT,
    raw_json TEXT NOT NULL,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sales_orders_number ON sales_orders(salesorder_number);
CREATE INDEX IF NOT EXISTS idx_sales_orders_order_date ON sales_orders(order_date);

CREATE TABLE IF NOT EXISTS sales_order_lines (
    salesorder_id TEXT NOT NULL,
    line_index INTEGER NOT NULL,
    line_item_id TEXT,
    sku TEXT,
    item_name TEXT,
    quantity REAL,
    rate REAL,
    discount TEXT,
    item_total REAL,
    raw_json TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (salesorder_id, line_index),
    FOREIGN KEY (salesorder_id) REFERENCES sales_orders(salesorder_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_so_lines_sku ON sales_order_lines(sku);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY,
    invoice_number TEXT,
    invoice_date TEXT,
    reference_number TEXT,
    salesorder_number TEXT,
    status TEXT,
    balance REAL,
    customer_name TEXT,
    salesperson_name TEXT,
    due_date TEXT,
    last_modified_time TEXT,
    raw_json TEXT NOT NULL,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_invoices_number ON invoices(invoice_number);
CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(invoice_date);

CREATE TABLE IF NOT EXISTS invoice_lines (
    invoice_id TEXT NOT NULL,
    line_index INTEGER NOT NULL,
    line_item_id TEXT,
    sku TEXT,
    item_name TEXT,
    quantity REAL,
    rate REAL,
    item_total REAL,
    raw_json TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (invoice_id, line_index),
    FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_key TEXT PRIMARY KEY,
    shipment_id TEXT,
    shipment_number TEXT,
    shipment_date TEXT,
    status TEXT,
    carrier_name TEXT,
    tracking_number TEXT,
    shipping_charge REAL,
    salesorder_id TEXT,
    salesorder_number TEXT,
    customer_name TEXT,
    sku TEXT,
    quantity REAL,
    source_endpoint TEXT,
    last_modified_time TEXT,
    raw_json TEXT NOT NULL,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shipments_so ON shipments(salesorder_number);
CREATE INDEX IF NOT EXISTS idx_shipments_date ON shipments(shipment_date);

CREATE TABLE IF NOT EXISTS items (
    item_id TEXT PRIMARY KEY,
    sku TEXT,
    name TEXT,
    status TEXT,
    rate REAL,
    purchase_rate REAL,
    unit TEXT,
    product_type TEXT,
    description TEXT,
    last_modified_time TEXT,
    raw_json TEXT NOT NULL,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_sku ON items(sku);

CREATE TABLE IF NOT EXISTS customer_payments (
    payment_id TEXT PRIMARY KEY,
    payment_number TEXT,
    payment_date TEXT,
    customer_name TEXT,
    payment_mode TEXT,
    amount REAL,
    unused_amount REAL,
    reference_number TEXT,
    last_modified_time TEXT,
    raw_json TEXT NOT NULL,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payments_date ON customer_payments(payment_date);

CREATE TABLE IF NOT EXISTS customer_payment_invoices (
    payment_id TEXT NOT NULL,
    invoice_id TEXT,
    invoice_number TEXT,
    amount_applied REAL,
    raw_json TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (payment_id, invoice_id, invoice_number),
    FOREIGN KEY (payment_id) REFERENCES customer_payments(payment_id) ON DELETE CASCADE
);

-- Accounting review layer. Adjustments are applied AFTER the automated SQLite
-- calculation and BEFORE final export. Raw Zoho tables are never modified.
CREATE TABLE IF NOT EXISTS manual_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_year INTEGER NOT NULL,
    period_month INTEGER NOT NULL,
    line_uid TEXT NOT NULL,
    sales_order_number TEXT,
    invoice_number TEXT,
    sku TEXT,
    original_salesperson TEXT,
    adjusted_salesperson TEXT,
    original_commissionable REAL,
    adjusted_commissionable REAL,
    original_map REAL,
    adjusted_map REAL,
    original_discount REAL,
    adjusted_discount REAL,
    exclude_flag INTEGER NOT NULL DEFAULT 0,
    classification TEXT,
    reason TEXT,
    reviewer TEXT,
    approval_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(period_year, period_month, line_uid)
);

CREATE INDEX IF NOT EXISTS idx_adjustments_period ON manual_adjustments(period_year, period_month);

-- Shipment data derived locally from sales_orders.raw_json packages (no Zoho call).
-- Used as a fallback when the Zoho shipments table is empty/unauthorized.
CREATE TABLE IF NOT EXISTS derived_shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    salesorder_id TEXT,
    salesorder_number TEXT,
    package_number TEXT,
    shipment_number TEXT,
    shipment_date TEXT,
    shipment_status TEXT,
    carrier_name TEXT,
    tracking_number TEXT,
    shipping_charge REAL,
    quantity_shipped REAL,
    raw_json TEXT,
    derived_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_derived_ship_so ON derived_shipments(salesorder_number);
"""


SCHEMA_MIGRATIONS = (
    "ALTER TABLE zoho_sync_runs ADD COLUMN records_inserted INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE zoho_sync_runs ADD COLUMN records_updated INTEGER NOT NULL DEFAULT 0",
)


def _apply_schema_migrations(conn: sqlite3.Connection) -> None:
    for statement in SCHEMA_MIGRATIONS:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


def init_database(db_path: Path | None = None) -> Path:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(path) as conn:
        conn.executescript(SCHEMA_SQL)
        _apply_schema_migrations(conn)
        conn.commit()
    return path


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    if not path.parent.exists():
        init_database(path)
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
