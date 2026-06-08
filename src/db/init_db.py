from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

from src.db.connection import DB_PATH, DATABASE_URL, database_label, get_connection, init_database, using_postgres
from src.db.db_utils import list_user_tables

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
    "manual_adjustments",
    "derived_shipments",
    "price_history",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize the commission automation database schema (SQLite or Postgres)."
    )
    parser.add_argument(
        "--db-path",
        default=str(DB_PATH),
        help=f"SQLite file path when DATABASE_URL is not set (default: {DB_PATH})",
    )
    return parser.parse_args()


def _safe_target_label(target: str) -> str:
    if target.startswith("postgresql://") or target.startswith("postgres://"):
        return "postgres (DATABASE_URL)"
    return target


def main() -> None:
    args = parse_args()
    if using_postgres():
        target = init_database(database_url=DATABASE_URL)
    else:
        target = init_database(Path(args.db_path))

    with get_connection() as conn:
        tables = list_user_tables(conn, conn.postgres)

    missing = [name for name in EXPECTED_TABLES if name not in tables]
    if missing:
        print(f"ERROR: Missing tables after init: {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(1)

    print(f"Database initialized ({database_label()}): {_safe_target_label(target)}")
    print("Tables:")
    for name in EXPECTED_TABLES:
        print(f"  - {name}")


if __name__ == "__main__":
    main()
