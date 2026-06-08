from __future__ import annotations

import calendar
import os
from datetime import date
from typing import Any

from src.zoho_client import ZohoApiError, ZohoBooksClient

from .customer_payments_sync import fetch_and_upsert_customer_payments
from .invoices_sync import fetch_and_upsert_invoices
from .repository import DatabaseRepository
from .sales_orders_sync import fetch_and_upsert_sales_orders
from .shipments_sync import fetch_and_upsert_shipments
from .zoho_price_history_sync import sync_zoho_prices_to_history


_TRUTHY_DISABLED = frozenset({"1", "true", "yes", "on"})
_FALSY_DISABLED = frozenset({"0", "false", "no", "off", ""})


def _env_disabled(var_name: str) -> bool:
    """Return True if the env var unambiguously means DISABLED.

    Convention: '1'/'true'/'yes'/'on' (case-insensitive) = disabled. Anything in
    '0'/'false'/'no'/'off'/'' or unset = enabled. An unrecognized value defaults to
    NOT disabled (fail-safe: hook stays on rather than silently off).
    """
    val = (os.environ.get(var_name) or "").strip().lower()
    if val in _TRUTHY_DISABLED:
        return True
    if val in _FALSY_DISABLED:
        return False
    # Unknown value: log loudly via the caller's warning channel; treat as enabled.
    return False


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
            # After items are upserted, sync the live Zoho prices into price_history
            # (SCD-2). Inserts a new row only when items.rate actually changed for a
            # given SKU; closes the previously-open row at today-1 in the same
            # transaction. Accountant snapshot rows are filtered out by source label
            # and are never touched.
            # Opt-out: COMMISSION_DISABLE_PRICE_HISTORY_SYNC = '1'/'true'/'yes'/'on'.
            # '0'/'false'/'no'/'off'/'' (or unset) means ENABLED.
            _env_var = "COMMISSION_DISABLE_PRICE_HISTORY_SYNC"
            _env_val = os.environ.get(_env_var)
            if _env_disabled(_env_var):
                warnings.append(
                    f"price_history sync: disabled by env {_env_var}={_env_val!r}"
                )
            else:
                # Log enable state explicitly so the on/off decision is observable.
                if _env_val is not None and _env_val.strip() != "":
                    norm = _env_val.strip().lower()
                    if norm not in _FALSY_DISABLED:
                        warnings.append(
                            f"price_history sync: enabled (unrecognized {_env_var}="
                            f"{_env_val!r} treated as enabled)"
                        )
                    else:
                        warnings.append(f"price_history sync: enabled ({_env_var}={_env_val!r})")
                else:
                    warnings.append("price_history sync: enabled")
                try:
                    ph_summary = sync_zoho_prices_to_history(repo.conn)
                    warnings.append(
                        f"price_history sync: scanned={ph_summary.scanned} "
                        f"inserted_new={ph_summary.inserted_new} "
                        f"changed_closed_old={ph_summary.changed_closed_old} "
                        f"updated_same_day={ph_summary.updated_same_day} "
                        f"unchanged={ph_summary.unchanged} "
                        f"skipped_blank_sku={ph_summary.skipped_blank_sku} "
                        f"skipped_invalid_price={ph_summary.skipped_invalid_price} "
                        f"errors={len(ph_summary.errors)}"
                    )
                    for err in ph_summary.errors:
                        warnings.append(f"price_history sync error: {err}")
                except Exception as exc:
                    # Items sync already committed; do NOT raise (would mark the whole
                    # run as failed). Surface the failure in warnings instead.
                    warnings.append(f"price_history sync failed: {exc}")
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
