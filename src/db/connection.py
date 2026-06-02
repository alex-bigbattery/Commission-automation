from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from .db_utils import adapt_placeholders, duplicate_column_error, list_user_tables

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ["DB_PATH"]) if os.environ.get("DB_PATH") else (
    BASE_DIR / "data" / "db" / "commission_automation.sqlite"
)

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def using_postgres() -> bool:
    return bool(DATABASE_URL)


def database_label() -> str:
    if using_postgres():
        return "postgres"
    return "sqlite"


SCHEMA_SQLITE = """
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

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS zoho_sync_runs (
    id SERIAL PRIMARY KEY,
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
    shipping_charge DOUBLE PRECISION,
    delivery_method TEXT,
    sub_total DOUBLE PRECISION,
    total DOUBLE PRECISION,
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
    quantity DOUBLE PRECISION,
    rate DOUBLE PRECISION,
    discount TEXT,
    item_total DOUBLE PRECISION,
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
    balance DOUBLE PRECISION,
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
    quantity DOUBLE PRECISION,
    rate DOUBLE PRECISION,
    item_total DOUBLE PRECISION,
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
    shipping_charge DOUBLE PRECISION,
    salesorder_id TEXT,
    salesorder_number TEXT,
    customer_name TEXT,
    sku TEXT,
    quantity DOUBLE PRECISION,
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
    rate DOUBLE PRECISION,
    purchase_rate DOUBLE PRECISION,
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
    amount DOUBLE PRECISION,
    unused_amount DOUBLE PRECISION,
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
    amount_applied DOUBLE PRECISION,
    raw_json TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (payment_id, invoice_id, invoice_number),
    FOREIGN KEY (payment_id) REFERENCES customer_payments(payment_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS manual_adjustments (
    id SERIAL PRIMARY KEY,
    period_year INTEGER NOT NULL,
    period_month INTEGER NOT NULL,
    line_uid TEXT NOT NULL,
    sales_order_number TEXT,
    invoice_number TEXT,
    sku TEXT,
    original_salesperson TEXT,
    adjusted_salesperson TEXT,
    original_commissionable DOUBLE PRECISION,
    adjusted_commissionable DOUBLE PRECISION,
    original_map DOUBLE PRECISION,
    adjusted_map DOUBLE PRECISION,
    original_discount DOUBLE PRECISION,
    adjusted_discount DOUBLE PRECISION,
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

CREATE TABLE IF NOT EXISTS derived_shipments (
    id SERIAL PRIMARY KEY,
    salesorder_id TEXT,
    salesorder_number TEXT,
    package_number TEXT,
    shipment_number TEXT,
    shipment_date TEXT,
    shipment_status TEXT,
    carrier_name TEXT,
    tracking_number TEXT,
    shipping_charge DOUBLE PRECISION,
    quantity_shipped DOUBLE PRECISION,
    raw_json TEXT,
    derived_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_derived_ship_so ON derived_shipments(salesorder_number);
"""

SCHEMA_MIGRATIONS = (
    "ALTER TABLE zoho_sync_runs ADD COLUMN records_inserted INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE zoho_sync_runs ADD COLUMN records_updated INTEGER NOT NULL DEFAULT 0",
)


class DbCursor:
    def __init__(self, cursor: Any, postgres: bool) -> None:
        self._cursor = cursor
        self._postgres = postgres
        self._lastrowid: int | None = None

    @property
    def lastrowid(self) -> int | None:
        return self._lastrowid

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        return self._cursor.fetchall()


class DbConnection:
    """Unified connection wrapper for SQLite (local) and Postgres (production)."""

    def __init__(self, raw: Any, postgres: bool) -> None:
        self._conn = raw
        self.postgres = postgres

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> DbCursor:
        adapted = adapt_placeholders(sql, self.postgres)
        if params is None:
            cur = self._conn.execute(adapted)
        else:
            cur = self._conn.execute(adapted, params)
        wrapper = DbCursor(cur, self.postgres)
        if not self.postgres:
            wrapper._lastrowid = cur.lastrowid
        return wrapper

    def executemany(self, sql: str, params_seq: Iterable[Sequence[Any]]) -> None:
        adapted = adapt_placeholders(sql, self.postgres)
        if self.postgres:
            with self._conn.cursor() as cur:
                cur.executemany(adapted, params_seq)
        else:
            self._conn.executemany(adapted, params_seq)

    def executescript(self, sql: str) -> None:
        if self.postgres:
            for statement in _split_sql_statements(sql):
                if statement.strip():
                    try:
                        self._conn.execute(statement)
                        self._conn.commit()
                    except Exception:
                        self._conn.rollback()
                        raise
        else:
            self._conn.executescript(sql)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DbConnection:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _split_sql_statements(sql: str) -> list[str]:
    parts: list[str] = []
    for chunk in sql.split(";"):
        lines = [line for line in chunk.splitlines() if not line.strip().upper().startswith("PRAGMA")]
        statement = "\n".join(lines).strip()
        if statement:
            parts.append(statement)
    return parts


def _apply_schema_migrations(conn: DbConnection) -> None:
    for statement in SCHEMA_MIGRATIONS:
        try:
            conn.execute(statement)
            if conn.postgres:
                conn.commit()
        except Exception as exc:
            if conn.postgres:
                conn._conn.rollback()
            if not duplicate_column_error(exc, conn.postgres):
                raise


def _connect_postgres(url: str) -> DbConnection:
    import psycopg
    from psycopg.rows import dict_row

    raw = psycopg.connect(url, row_factory=dict_row)
    return DbConnection(raw, postgres=True)


def _connect_sqlite(path: Path) -> DbConnection:
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(path, timeout=60)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return DbConnection(raw, postgres=False)


def init_database(db_path: Path | None = None, database_url: str | None = None) -> str:
    """Create schema if missing. Safe to run multiple times."""
    url = (database_url or DATABASE_URL).strip()
    if url:
        with _connect_postgres(url) as conn:
            existing = set(list_user_tables(conn, postgres=True))
            if "sales_orders" not in existing:
                conn.executescript(SCHEMA_POSTGRES)
            _apply_schema_migrations(conn)
            conn.commit()
        return url

    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect_sqlite(path) as conn:
        conn.executescript(SCHEMA_SQLITE)
        _apply_schema_migrations(conn)
        conn.commit()
    return str(path)


def get_connection(
    db_path: Path | None = None,
    database_url: str | None = None,
) -> DbConnection:
    url = (database_url or DATABASE_URL).strip()
    if url:
        init_database(database_url=url)
        return _connect_postgres(url)

    path = db_path or DB_PATH
    if not path.parent.exists() or not path.exists():
        init_database(path)
    return _connect_sqlite(path)
