from __future__ import annotations

import calendar
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import time

from src.zoho_client import ZohoBooksClient

from .repository import DatabaseRepository
from .zoho_lines import enrich_sales_order, extract_line_items

MODULE_NAME = "sales_orders"


@dataclass(frozen=True)
class SalesOrdersSyncResult:
    sync_id: str
    run_id: int
    records_fetched: int
    date_start: str | None
    date_end: str | None
    status: str


def parse_cli_date(value: str) -> date:
    lowered = value.strip().lower()
    if lowered in {"today", "now"}:
        return date.today()
    return date.fromisoformat(value)


def month_chunks(date_start: date, date_end: date) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    cursor = date(date_start.year, date_start.month, 1)
    while cursor <= date_end:
        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        chunk_start = max(cursor, date_start)
        chunk_end = min(date(date_end.year, date_end.month, last_day), date_end)
        chunks.append((chunk_start.isoformat(), chunk_end.isoformat()))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return chunks


def incremental_sales_order_params(repo: DatabaseRepository) -> dict[str, Any]:
    from .incremental_sync import incremental_params_for_module

    params = incremental_params_for_module(repo, MODULE_NAME)
    params.pop("_incremental_source", None)
    params.pop("_incremental_module", None)
    return params


def fetch_and_upsert_sales_orders(
    client: ZohoBooksClient,
    repo: DatabaseRepository,
    *,
    params: dict[str, Any],
    skip_details: bool = False,
    detail_delay_seconds: float = 0.15,
) -> tuple[int, int, list[str]]:
    """
    List sales orders, fetch full detail per order (unless skip_details), upsert headers + lines.

    Returns (orders_upserted, line_rows_upserted, warnings).
    """
    warnings: list[str] = []
    records = client.list_sales_orders(params=params)
    enriched: list[dict[str, Any]] = []

    for record in records:
        merged = enrich_sales_order(client, record, skip_details=skip_details)
        enriched.append(merged)
        if not skip_details and detail_delay_seconds > 0:
            time.sleep(detail_delay_seconds)

    line_rows = sum(len(extract_line_items(order)) for order in enriched)
    if not skip_details and enriched and line_rows == 0:
        warnings.append(
            "WARNING: No sales order line_items returned after detail fetch; "
            "check Zoho API scopes or debug_detail_response.py."
        )

    orders_count = repo.upsert_sales_orders(enriched)
    return orders_count, line_rows, warnings


def sync_sales_orders(
    client: ZohoBooksClient,
    repo: DatabaseRepository,
    *,
    params: dict[str, Any],
    sync_id: str | None = None,
    skip_details: bool = False,
    log_sync_run: bool = True,
) -> SalesOrdersSyncResult:
    """
    Fetch Zoho sales orders (with line-item details), upsert into SQLite,
    and optionally record one row in zoho_sync_runs for this window.
    """
    sync_id = sync_id or repo.begin_sync_id()
    date_start = params.get("date_start")
    date_end = params.get("date_end")

    run_id = 0
    if log_sync_run:
        run_id = repo.start_sync_run(
            sync_id=sync_id,
            module=MODULE_NAME,
            date_start=date_start,
            date_end=date_end,
        )

    try:
        count, line_rows, sync_warnings = fetch_and_upsert_sales_orders(
            client,
            repo,
            params=params,
            skip_details=skip_details,
        )
        for warning in sync_warnings:
            print(warning, file=sys.stderr)
        if log_sync_run:
            repo.finish_sync_run(
                run_id,
                status="success",
                records_fetched=count,
                records_inserted=line_rows,
            )
        return SalesOrdersSyncResult(
            sync_id=sync_id,
            run_id=run_id,
            records_fetched=count,
            date_start=date_start,
            date_end=date_end,
            status="success",
        )
    except Exception as exc:
        if log_sync_run:
            repo.finish_sync_run(
                run_id,
                status="failed",
                records_fetched=0,
                error_message=str(exc),
            )
        raise


def sync_sales_orders_range(
    client: ZohoBooksClient,
    repo: DatabaseRepository,
    *,
    date_start: date,
    date_end: date,
    skip_details: bool = False,
    use_month_chunks: bool = True,
) -> dict[str, Any]:
    sync_id = repo.begin_sync_id()
    chunks = (
        month_chunks(date_start, date_end)
        if use_month_chunks
        else [(date_start.isoformat(), date_end.isoformat())]
    )
    total_orders = 0
    chunk_results: list[dict[str, Any]] = []

    for chunk_start, chunk_end in chunks:
        params: dict[str, Any] = {"date_start": chunk_start, "date_end": chunk_end}
        result = sync_sales_orders(
            client,
            repo,
            params=params,
            sync_id=sync_id,
            skip_details=skip_details,
        )
        total_orders += result.records_fetched
        chunk_results.append(
            {
                "date_start": chunk_start,
                "date_end": chunk_end,
                "records_fetched": result.records_fetched,
                "run_id": result.run_id,
            }
        )

    return {
        "sync_id": sync_id,
        "module": MODULE_NAME,
        "date_start": date_start.isoformat(),
        "date_end": date_end.isoformat(),
        "records_fetched": total_orders,
        "chunks": chunk_results,
    }
