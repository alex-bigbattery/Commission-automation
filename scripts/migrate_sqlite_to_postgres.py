#!/usr/bin/env python3
"""Copy local SQLite data into Supabase Postgres (DATABASE_URL). Does not call Zoho."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

from src.db.connection import DB_PATH, get_connection, init_database
from src.db.db_utils import adapt_placeholders

CORE_TABLES = (
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
)

SERIAL_TABLES = ("zoho_sync_runs", "manual_adjustments", "derived_shipments")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate commission data from local SQLite to Postgres (DATABASE_URL)."
    )
    parser.add_argument(
        "--sqlite-path",
        default=str(DB_PATH),
        help=f"Source SQLite file (default: {DB_PATH})",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="Target Postgres URL (default: DATABASE_URL env var)",
    )
    return parser.parse_args()


def table_count_sqlite(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0


def table_count_postgres(conn, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
    return int(row["c"]) if row else 0


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(row[1]) for row in rows]


def postgres_columns(conn, table: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ?
        ORDER BY ordinal_position
        """,
        (table,),
    ).fetchall()
    return [str(row["column_name"]) for row in rows]


def copy_table(sqlite_conn: sqlite3.Connection, pg_conn, table: str, batch_size: int = 50) -> int:
    sqlite_cols = sqlite_columns(sqlite_conn, table)
    if not sqlite_cols:
        return 0
    pg_cols = set(postgres_columns(pg_conn, table))
    cols = [c for c in sqlite_cols if c in pg_cols]
    if not cols:
        return 0

    rows = sqlite_conn.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
    if not rows:
        return 0

    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    if table in SERIAL_TABLES and "id" in cols:
        conflict = "(id) DO UPDATE SET " + ", ".join(
            f"{c}=EXCLUDED.{c}" for c in cols if c != "id"
        )
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT {conflict}"
    elif table == "sales_order_lines":
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in ("salesorder_id", "line_index"))
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT (salesorder_id, line_index) DO UPDATE SET {updates}"
        )
    elif table == "invoice_lines":
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in ("invoice_id", "line_index"))
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT (invoice_id, line_index) DO UPDATE SET {updates}"
        )
    elif table == "customer_payment_invoices":
        updates = ", ".join(
            f"{c}=EXCLUDED.{c}" for c in cols if c not in ("payment_id", "invoice_id", "invoice_number")
        )
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT (payment_id, invoice_id, invoice_number) DO UPDATE SET {updates}"
        )
    elif table == "manual_adjustments":
        updates = ", ".join(
            f"{c}=EXCLUDED.{c}" for c in cols if c not in ("period_year", "period_month", "line_uid")
        )
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT (period_year, period_month, line_uid) DO UPDATE SET {updates}"
        )
    else:
        pk = cols[0]
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != pk)
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({pk}) DO UPDATE SET {updates}"
        )

    adapted = adapt_placeholders(sql, postgres=True)
    copied = 0
    batch: list[tuple] = []
    for row in rows:
        batch.append(tuple(row[c] for c in cols))
        if len(batch) >= batch_size:
            pg_conn.executemany(adapted, batch)
            pg_conn.commit()
            copied += len(batch)
            batch.clear()
    if batch:
        pg_conn.executemany(adapted, batch)
        pg_conn.commit()
        copied += len(batch)
    return copied


def reset_serial_sequences(pg_conn) -> None:
    for table in SERIAL_TABLES:
        pg_conn.execute(
            f"""
            SELECT setval(
                pg_get_serial_sequence('{table}', 'id'),
                COALESCE((SELECT MAX(id) FROM {table}), 1),
                true
            )
            """
        )
    pg_conn.commit()


def main() -> None:
    args = parse_args()
    sqlite_path = Path(args.sqlite_path)
    database_url = (args.database_url or "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required (env var or --database-url).")
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite file not found: {sqlite_path}")

    print(f"Source SQLite: {sqlite_path}")
    print("Target: Postgres (DATABASE_URL)")
    print()

    init_database(database_url=database_url)

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = get_connection(database_url=database_url)

    try:
        pg_conn.execute("SET statement_timeout TO 0")
        pg_conn.commit()
        print("Row counts BEFORE migration:")
        for table in CORE_TABLES:
            try:
                src = table_count_sqlite(sqlite_conn, table)
            except sqlite3.OperationalError:
                src = 0
            try:
                dst = table_count_postgres(pg_conn, table)
            except Exception:
                dst = 0
            print(f"  {table}: sqlite={src} postgres={dst}")

        print("\nCopying tables...")
        for table in CORE_TABLES:
            try:
                sqlite_conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
            except sqlite3.OperationalError:
                print(f"  {table}: skipped (not in SQLite)")
                continue
            copied = copy_table(sqlite_conn, pg_conn, table)
            print(f"  {table}: copied {copied} rows")

        reset_serial_sequences(pg_conn)

        print("\nRow counts AFTER migration:")
        summary: list[tuple[str, int, int, str]] = []
        for table in CORE_TABLES:
            try:
                src = table_count_sqlite(sqlite_conn, table)
            except sqlite3.OperationalError:
                src = 0
            try:
                dst = table_count_postgres(pg_conn, table)
            except Exception:
                dst = 0
            if src == 0 and dst == 0:
                status = "OK (empty)"
            elif src == dst:
                status = "OK"
            else:
                status = "MISMATCH"
            summary.append((table, src, dst, status))
            print(f"  {table}: sqlite={src} postgres={dst} [{status}]")

        print("\nMigration summary:")
        print(f"{'Table':<30} {'SQLite':>10} {'Postgres':>10}  Status")
        print("-" * 65)
        for table, src, dst, status in summary:
            print(f"{table:<30} {src:>10} {dst:>10}  {status}")
    finally:
        sqlite_conn.close()
        pg_conn.close()

    print("\nMigration complete.")


if __name__ == "__main__":
    main()
