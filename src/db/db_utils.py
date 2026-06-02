"""Small database helpers shared by SQLite and Postgres backends."""

from __future__ import annotations

import re
from typing import Any, Protocol


class RowLike(Protocol):
    def __getitem__(self, key: str | int) -> Any: ...
    def keys(self) -> Any: ...


def adapt_placeholders(sql: str, postgres: bool) -> str:
    """Convert SQLite-style ? placeholders to psycopg %s placeholders."""
    if not postgres:
        return sql
    return re.sub(r"\?", "%s", sql)


def date_prefix_expr(column: str, postgres: bool) -> str:
    """First 10 chars of an ISO date stored as TEXT (YYYY-MM-DD...)."""
    if postgres:
        return f"LEFT({column}, 10)"
    return f"substr({column}, 1, 10)"


def duplicate_column_error(exc: BaseException, postgres: bool) -> bool:
    message = str(exc).lower()
    if postgres:
        return "already exists" in message and "column" in message
    return "duplicate column" in message


def list_user_tables(conn: Any, postgres: bool) -> list[str]:
    if postgres:
        rows = conn.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
            """
        ).fetchall()
        return [str(row["tablename"]) for row in rows]
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row["name"]) for row in rows]
