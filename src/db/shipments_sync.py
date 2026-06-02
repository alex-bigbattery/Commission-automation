from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.zoho_client import ZohoApiError, ZohoAuthError, ZohoBooksClient

from .repository import DatabaseRepository
from .sales_orders_sync import month_chunks, parse_cli_date

MODULE_NAME = "shipments"

__all__ = [
    "MODULE_NAME",
    "ShipmentsSyncResult",
    "parse_cli_date",
    "month_chunks",
    "incremental_shipment_params",
    "fetch_shipments_from_zoho",
    "fetch_and_upsert_shipments",
    "sync_shipments",
    "sync_shipments_range",
]


@dataclass(frozen=True)
class ShipmentsSyncResult:
    sync_id: str
    run_id: int
    records_fetched: int
    date_start: str | None
    date_end: str | None
    status: str
    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)


def incremental_shipment_params(repo: DatabaseRepository) -> dict[str, Any]:
    from .incremental_sync import incremental_params_for_module

    params = incremental_params_for_module(repo, MODULE_NAME)
    params.pop("_incremental_source", None)
    params.pop("_incremental_module", None)
    return params


def fetch_shipments_from_zoho(
    client: ZohoBooksClient,
    params: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """
    Try Zoho shipmentorders and packages list endpoints.
    Returns (records, warnings, errors). Does not raise on endpoint failures.
    """
    warnings: list[str] = []
    errors: list[str] = []
    combined: list[dict[str, Any]] = []

    endpoints: list[tuple[str, str]] = [
        ("shipmentorders", "list_shipment_orders"),
        ("packages", "list_packages"),
    ]

    for endpoint_name, method_name in endpoints:
        try:
            list_fn = getattr(client, method_name)
            rows = list_fn(params=params)
            for row in rows:
                tagged = dict(row)
                tagged["_source_endpoint"] = endpoint_name
                combined.append(tagged)
            if rows:
                warnings.append(
                    f"INFO: {endpoint_name} returned {len(rows)} record(s)."
                )
        except ZohoAuthError:
            raise
        except ZohoApiError as exc:
            message = f"{endpoint_name} fetch failed: {exc}"
            errors.append(message)
            warnings.append(f"WARNING: {message}")
        except Exception as exc:
            message = f"{endpoint_name} fetch failed: {exc}"
            errors.append(message)
            warnings.append(f"WARNING: {message}")

    if not combined and errors:
        warnings.append("WARNING: No shipment/package data returned from any Zoho endpoint.")

    return combined, warnings, errors


def fetch_and_upsert_shipments(
    client: ZohoBooksClient,
    repo: DatabaseRepository,
    *,
    params: dict[str, Any],
) -> tuple[int, list[str], list[str]]:
    """Fetch from available endpoints and upsert shipment rows when data exists."""
    records, warnings, errors = fetch_shipments_from_zoho(client, params)
    if not records:
        return 0, warnings, errors
    count = repo.upsert_shipments(records)
    return count, warnings, errors


def sync_shipments(
    client: ZohoBooksClient,
    repo: DatabaseRepository,
    *,
    params: dict[str, Any],
    sync_id: str | None = None,
    log_sync_run: bool = True,
) -> ShipmentsSyncResult:
    """
    Sync shipments/packages into SQLite. Logs endpoint errors without raising
    when both endpoints fail (unless auth fails).
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
        count, warnings, errors = fetch_and_upsert_shipments(client, repo, params=params)
        all_failed = bool(errors) and count == 0
        status = "failed" if all_failed else "success"
        error_message = "; ".join(errors) if errors else None

        if log_sync_run:
            repo.finish_sync_run(
                run_id,
                status=status,
                records_fetched=count,
                records_inserted=count,
                records_updated=0,
                error_message=error_message,
            )

        return ShipmentsSyncResult(
            sync_id=sync_id,
            run_id=run_id,
            records_fetched=count,
            date_start=date_start,
            date_end=date_end,
            status=status,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )
    except ZohoAuthError:
        if log_sync_run:
            repo.finish_sync_run(
                run_id,
                status="failed",
                records_fetched=0,
                error_message="Zoho authentication failed",
            )
        raise
    except Exception as exc:
        if log_sync_run:
            repo.finish_sync_run(
                run_id,
                status="failed",
                records_fetched=0,
                error_message=str(exc),
            )
        raise


def sync_shipments_range(
    client: ZohoBooksClient,
    repo: DatabaseRepository,
    *,
    date_start: date,
    date_end: date,
    use_month_chunks: bool = True,
) -> dict[str, Any]:
    sync_id = repo.begin_sync_id()
    chunks = (
        month_chunks(date_start, date_end)
        if use_month_chunks
        else [(date_start.isoformat(), date_end.isoformat())]
    )
    total_rows = 0
    all_warnings: list[str] = []
    all_errors: list[str] = []
    chunk_results: list[dict[str, Any]] = []
    any_success = False

    for chunk_start, chunk_end in chunks:
        params: dict[str, Any] = {"date_start": chunk_start, "date_end": chunk_end}
        result = sync_shipments(
            client,
            repo,
            params=params,
            sync_id=sync_id,
        )
        total_rows += result.records_fetched
        all_warnings.extend(result.warnings)
        all_errors.extend(result.errors)
        if result.status == "success":
            any_success = True
        chunk_results.append(
            {
                "date_start": chunk_start,
                "date_end": chunk_end,
                "records_fetched": result.records_fetched,
                "status": result.status,
                "run_id": result.run_id,
            }
        )

    return {
        "sync_id": sync_id,
        "module": MODULE_NAME,
        "date_start": date_start.isoformat(),
        "date_end": date_end.isoformat(),
        "records_fetched": total_rows,
        "status": "success" if any_success or not all_errors else "failed",
        "warnings": all_warnings,
        "errors": all_errors,
        "chunks": chunk_results,
    }
