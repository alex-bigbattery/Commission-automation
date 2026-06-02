from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any

from src.zoho_client import ZohoBooksClient

from .repository import DatabaseRepository
from .sales_orders_sync import month_chunks, parse_cli_date
from .zoho_lines import payment_invoice_applications

MODULE_NAME = "customer_payments"

__all__ = [
    "MODULE_NAME",
    "CustomerPaymentsSyncResult",
    "parse_cli_date",
    "month_chunks",
    "incremental_customer_payment_params",
    "ensure_payment_details",
    "fetch_and_upsert_customer_payments",
    "sync_customer_payments",
    "sync_customer_payments_range",
]


@dataclass(frozen=True)
class CustomerPaymentsSyncResult:
    sync_id: str
    run_id: int
    records_fetched: int
    date_start: str | None
    date_end: str | None
    status: str


def incremental_customer_payment_params(repo: DatabaseRepository) -> dict[str, Any]:
    from .incremental_sync import incremental_params_for_module

    params = incremental_params_for_module(repo, MODULE_NAME)
    params.pop("_incremental_source", None)
    params.pop("_incremental_module", None)
    return params


def enrich_payment(
    client: ZohoBooksClient,
    payment: dict[str, Any],
    *,
    skip_details: bool,
) -> dict[str, Any]:
    if skip_details:
        return payment
    payment_id = payment.get("payment_id")
    if not payment_id:
        return payment
    detail = client.get_customer_payment(str(payment_id))
    merged = dict(payment)
    if detail:
        merged.update(detail)
    return merged


def fetch_and_upsert_customer_payments(
    client: ZohoBooksClient,
    repo: DatabaseRepository,
    *,
    params: dict[str, Any],
    skip_details: bool = False,
    detail_delay_seconds: float = 0.15,
) -> tuple[int, int, list[str]]:
    """List customer payments, fetch detail, upsert payments + invoice applications."""
    warnings: list[str] = []
    records = client.list_customer_payments(params=params)
    enriched: list[dict[str, Any]] = []

    for record in records:
        merged = enrich_payment(client, record, skip_details=skip_details)
        enriched.append(merged)
        if not skip_details and detail_delay_seconds > 0:
            time.sleep(detail_delay_seconds)

    application_rows = sum(len(payment_invoice_applications(payment)) for payment in enriched)
    payments_without_apps = sum(
        1 for payment in enriched if not payment_invoice_applications(payment)
    )
    if payments_without_apps:
        warnings.append(
            f"WARNING: {payments_without_apps} customer payment(s) have no invoice applications in Zoho detail."
        )

    payments_count = repo.upsert_customer_payments(enriched)
    return payments_count, application_rows, warnings


def sync_customer_payments(
    client: ZohoBooksClient,
    repo: DatabaseRepository,
    *,
    params: dict[str, Any],
    sync_id: str | None = None,
    skip_details: bool = False,
    log_sync_run: bool = True,
) -> CustomerPaymentsSyncResult:
    """
    Fetch Zoho customer payments, upsert by payment_id into SQLite
    (customer_payments + customer_payment_invoices), and log zoho_sync_runs.
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
        count, app_rows, sync_warnings = fetch_and_upsert_customer_payments(
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
                records_inserted=app_rows,
            )
        return CustomerPaymentsSyncResult(
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


def sync_customer_payments_range(
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
    total_payments = 0
    chunk_results: list[dict[str, Any]] = []

    for chunk_start, chunk_end in chunks:
        params: dict[str, Any] = {"date_start": chunk_start, "date_end": chunk_end}
        result = sync_customer_payments(
            client,
            repo,
            params=params,
            sync_id=sync_id,
            skip_details=skip_details,
        )
        total_payments += result.records_fetched
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
        "records_fetched": total_payments,
        "chunks": chunk_results,
    }
