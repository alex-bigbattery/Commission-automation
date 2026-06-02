from __future__ import annotations

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

from src.db.connection import DB_PATH, database_label, init_database, using_postgres
from src.db.repository import DatabaseRepository

TABLES = (
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


def _print_sample_lines(conn, table: str, parent_col: str, limit: int = 5) -> None:
    rows = conn.execute(
        f"""
        SELECT {parent_col}, line_index, line_item_id, sku, item_name, quantity
        FROM {table}
        ORDER BY {parent_col}, line_index
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not rows:
        print(f"  (no rows in {table})")
        return
    for row in rows:
        print(
            f"  {parent_col}={row[parent_col]} idx={row['line_index']} "
            f"line_item_id={row['line_item_id']} sku={row['sku']} "
            f"qty={row['quantity']} name={row['item_name']}"
        )


def main() -> None:
    init_database()
    if using_postgres():
        print(f"Database: postgres (DATABASE_URL)")
    else:
        print(f"Database: {DB_PATH}")
        print(f"Exists: {DB_PATH.exists()}")
    print(f"Backend: {database_label()}\n")

    with DatabaseRepository() as repo:
        conn = repo.conn

        print("Table row counts:")
        for table in TABLES:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            print(f"  {table}: {int(row['c']) if row else 0}")

        so_with_lines = conn.execute(
            """
            SELECT COUNT(DISTINCT salesorder_id) AS c
            FROM sales_order_lines
            """
        ).fetchone()
        inv_with_lines = conn.execute(
            """
            SELECT COUNT(DISTINCT invoice_id) AS c
            FROM invoice_lines
            """
        ).fetchone()
        avg_so_lines = conn.execute(
            """
            SELECT AVG(line_count) AS avg_lines FROM (
                SELECT salesorder_id, COUNT(*) AS line_count
                FROM sales_order_lines
                GROUP BY salesorder_id
            )
            """
        ).fetchone()
        avg_inv_lines = conn.execute(
            """
            SELECT AVG(line_count) AS avg_lines FROM (
                SELECT invoice_id, COUNT(*) AS line_count
                FROM invoice_lines
                GROUP BY invoice_id
            )
            """
        ).fetchone()

        print("\nLine coverage:")
        print(f"  sales orders with lines: {so_with_lines['c'] if so_with_lines else 0}")
        print(f"  avg lines per sales order: {round(avg_so_lines['avg_lines'] or 0, 2)}")
        print(f"  invoices with lines: {inv_with_lines['c'] if inv_with_lines else 0}")
        print(f"  avg lines per invoice: {round(avg_inv_lines['avg_lines'] or 0, 2)}")

        pay_with_apps = conn.execute(
            "SELECT COUNT(DISTINCT payment_id) AS c FROM customer_payment_invoices"
        ).fetchone()
        print(f"  payments with invoice applications: {pay_with_apps['c'] if pay_with_apps else 0}")

        print("\nFirst 5 sales_order_lines:")
        _print_sample_lines(conn, "sales_order_lines", "salesorder_id", limit=5)

        print("\nFirst 5 invoice_lines:")
        _print_sample_lines(conn, "invoice_lines", "invoice_id", limit=5)

        sample_so = conn.execute(
            """
            SELECT so.salesorder_id, so.salesorder_number, COUNT(l.line_index) AS line_count
            FROM sales_orders so
            LEFT JOIN sales_order_lines l ON l.salesorder_id = so.salesorder_id
            GROUP BY so.salesorder_id
            ORDER BY line_count DESC, so.salesorder_id
            LIMIT 5
            """
        ).fetchall()
        print("\nSample sales orders (line count):")
        for row in sample_so:
            print(
                f"  {row['salesorder_number']} ({row['salesorder_id']}): {row['line_count']} lines"
            )

        sample_inv = conn.execute(
            """
            SELECT i.invoice_id, i.invoice_number, COUNT(l.line_index) AS line_count
            FROM invoices i
            LEFT JOIN invoice_lines l ON l.invoice_id = i.invoice_id
            GROUP BY i.invoice_id
            ORDER BY line_count DESC, i.invoice_id
            LIMIT 5
            """
        ).fetchall()
        print("\nSample invoices (line count):")
        for row in sample_inv:
            print(f"  {row['invoice_number']} ({row['invoice_id']}): {row['line_count']} lines")

        print("\nLatest 10 sync runs:")
        runs = repo.latest_sync_runs(limit=10)
        if not runs:
            print("  (none)")
        for run in runs:
            print(
                f"  sync_id={run.get('sync_id')} module={run.get('module')} status={run.get('status')} "
                f"fetched={run.get('records_fetched', 0)} inserted={run.get('records_inserted', 0)} "
                f"updated={run.get('records_updated', 0)}"
            )
            if run.get("error_message"):
                print(f"    error: {run['error_message']}")
            print(f"    started={run.get('started_at')} finished={run.get('finished_at')}")


if __name__ == "__main__":
    main()
