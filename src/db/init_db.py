from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.db.connection import DB_PATH, init_database

EXPECTED_TABLES = (
    "zoho_sync_runs",
    "sales_orders",
    "sales_order_lines",
    "invoices",
    "invoice_lines",
    "shipments",
    "items",
    "customer_payments",
    "customer_payment_invoices",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize the commission automation SQLite database (schema only, no Zoho sync)."
    )
    parser.add_argument(
        "--db-path",
        default=str(DB_PATH),
        help=f"Database file path (default: {DB_PATH})",
    )
    return parser.parse_args()


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)

    path = init_database(db_path)

    with sqlite3.connect(path) as conn:
        tables = list_tables(conn)

    missing = [name for name in EXPECTED_TABLES if name not in tables]
    if missing:
        print(f"ERROR: Missing tables after init: {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(1)

    print(f"Database initialized: {path}")
    print("Tables:")
    for name in EXPECTED_TABLES:
        print(f"  - {name}")


if __name__ == "__main__":
    main()
