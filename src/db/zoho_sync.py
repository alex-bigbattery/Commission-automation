from __future__ import annotations

import calendar
from datetime import date
from typing import Any

from src.zoho_client import ZohoApiError, ZohoBooksClient

from .customer_payments_sync import fetch_and_upsert_customer_payments
from .invoices_sync import fetch_and_upsert_invoices
from .repository import DatabaseRepository
from .sales_orders_sync import fetch_and_upsert_sales_orders
from .shipments_sync import fetch_and_upsert_shipments


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


def sync_module(
    client: ZohoBooksClient,
    repo: DatabaseRepository,
    *,
    sync_id: str,
    module: str,
    params: dict[str, Any],
    skip_details: bool,
) -> tuple[int, list[str], str]:
    """Returns (records_fetched, warnings, module_status)."""
    warnings: list[str] = []
    run_id = repo.start_sync_run(
        sync_id=sync_id,
        module=module,
        date_start=params.get("date_start"),
        date_end=params.get("date_end"),
    )
    try:
        if module == "sales_orders":
            count, _lines, detail_warnings = fetch_and_upsert_sales_orders(
                client,
                repo,
                params=params,
                skip_details=skip_details,
            )
            warnings.extend(detail_warnings)
        elif module == "invoices":
            count, _lines, detail_warnings = fetch_and_upsert_invoices(
                client,
                repo,
                params=params,
                skip_details=skip_details,
            )
            warnings.extend(detail_warnings)
        elif module == "items":
            item_params = params if params else None
            records = client.list_items(params=item_params)
            count = repo.upsert_items(records)
        elif module == "customer_payments":
            count, _apps, pay_warnings = fetch_and_upsert_customer_payments(
                client,
                repo,
                params=params,
                skip_details=skip_details,
            )
            warnings.extend(pay_warnings)
        elif module == "shipments":
            count, shipment_warnings, shipment_errors = fetch_and_upsert_shipments(
                client,
                repo,
                params=params,
            )
            warnings.extend(shipment_warnings)
            if shipment_errors and count == 0:
                repo.finish_sync_run(
                    run_id,
                    status="failed",
                    records_fetched=0,
                    records_inserted=0,
                    records_updated=0,
                    error_message="; ".join(shipment_errors),
                )
                return 0, warnings, "failed"
            if shipment_errors:
                warnings.extend(f"WARNING: {err}" for err in shipment_errors)
        else:
            raise ValueError(f"Unknown module: {module}")

        repo.finish_sync_run(
            run_id,
            status="success",
            records_fetched=count,
            records_inserted=count,
            records_updated=0,
        )
        return count, warnings, "success"
    except Exception as exc:
        repo.finish_sync_run(
            run_id,
            status="failed",
            records_fetched=0,
            records_inserted=0,
            records_updated=0,
            error_message=str(exc),
        )
        raise


def run_sync(
    client: ZohoBooksClient,
    repo: DatabaseRepository,
    *,
    date_start: date,
    date_end: date,
    skip_details: bool = False,
    use_month_chunks: bool = True,
) -> dict[str, Any]:
    sync_id = repo.begin_sync_id()
    modules = ["sales_orders", "invoices", "items", "customer_payments", "shipments"]
    totals: dict[str, int] = {}
    warnings: list[str] = []

    chunks = month_chunks(date_start, date_end) if use_month_chunks else [(date_start.isoformat(), date_end.isoformat())]

    for chunk_start, chunk_end in chunks:
        params: dict[str, Any] = {"date_start": chunk_start, "date_end": chunk_end}
        for module in modules:
            module_params = dict(params)
            if module == "items":
                module_params = {}
            try:
                count, module_warnings, _status = sync_module(
                    client,
                    repo,
                    sync_id=sync_id,
                    module=module,
                    params=module_params,
                    skip_details=skip_details,
                )
                totals[module] = totals.get(module, 0) + count
                warnings.extend(module_warnings)
            except ZohoApiError:
                raise

    return {
        "sync_id": sync_id,
        "date_start": date_start.isoformat(),
        "date_end": date_end.isoformat(),
        "totals": totals,
        "warnings": warnings,
    }
