from __future__ import annotations

from typing import Any

from src.zoho_client import ZohoBooksClient


def extract_line_items(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Return line item dicts from a Zoho Books sales order or invoice record."""
    for key in ("line_items", "lineitems"):
        raw = record.get(key)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
    return []


def has_meaningful_line_items(record: dict[str, Any]) -> bool:
    lines = extract_line_items(record)
    if not lines:
        return False
    return any(
        line.get("line_item_id")
        or line.get("item_id")
        or line.get("sku")
        or line.get("name")
        or line.get("description")
        for line in lines
    )


def synthetic_line_item_id(
    parent_id: str,
    line_index: int,
    line: dict[str, Any],
    *,
    prefix: str,
) -> str:
    existing = line.get("line_item_id") or line.get("item_id") or line.get("line_itemid")
    if existing not in (None, ""):
        return str(existing)
    sku = str(line.get("sku") or "nosku").strip() or "nosku"
    return f"{prefix}:{parent_id}:{line_index}:{sku}"


def enrich_sales_order(client: ZohoBooksClient, record: dict[str, Any], *, skip_details: bool) -> dict[str, Any]:
    if skip_details:
        return record
    salesorder_id = record.get("salesorder_id")
    if not salesorder_id:
        return record
    detail = client.get_sales_order(str(salesorder_id))
    merged = dict(record)
    if detail:
        merged.update(detail)
    return merged


def enrich_invoice(client: ZohoBooksClient, record: dict[str, Any], *, skip_details: bool) -> dict[str, Any]:
    if skip_details:
        return record
    invoice_id = record.get("invoice_id")
    if not invoice_id:
        return record
    detail = client.get_invoice(str(invoice_id))
    merged = dict(record)
    if detail:
        merged.update(detail)
    return merged


def payment_invoice_applications(payment: dict[str, Any]) -> list[dict[str, Any]]:
    """Invoice applications on a customer payment (list or detail payload)."""
    for key in ("invoices", "invoice_payments", "applied_invoices"):
        raw = payment.get(key)
        if isinstance(raw, list):
            apps = [row for row in raw if isinstance(row, dict)]
            if apps:
                return apps
    return []
