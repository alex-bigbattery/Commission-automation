from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.zoho_client import ZohoAuthError, ZohoBooksClient, load_zoho_config


def _print_keys(label: str, obj: dict[str, Any]) -> None:
    print(f"\n{label} top-level keys ({len(obj)}):")
    for key in sorted(obj.keys()):
        value = obj[key]
        if isinstance(value, list):
            print(f"  {key}: list[{len(value)}]")
        elif isinstance(value, dict):
            print(f"  {key}: dict[{len(value)}]")
        else:
            print(f"  {key}: {type(value).__name__}")


def _print_line_items(label: str, items: Any) -> None:
    if not isinstance(items, list):
        print(f"  {label}: not a list ({type(items).__name__})")
        return
    print(f"  {label}: {len(items)} line(s)")
    if not items:
        return
    first = items[0]
    if isinstance(first, dict):
        print(f"  first line_item keys: {sorted(first.keys())}")
        print(f"  first line_item sample: {json.dumps(first, default=str)[:500]}")
    else:
        print(f"  first line_item type: {type(first).__name__}")


def inspect_sales_order(client: ZohoBooksClient) -> None:
    print("=" * 60)
    print("SALES ORDER")
    print("=" * 60)
    orders = client.list_sales_orders(
        params={"date_start": "2026-05-25", "date_end": "2026-06-01", "per_page": 5}
    )
    if not orders:
        print("No sales orders returned for sample window.")
        return

    sample = orders[0]
    so_id = sample.get("salesorder_id")
    print(f"Sample salesorder_id: {so_id}")
    print(f"Sample salesorder_number: {sample.get('salesorder_number')}")

    _print_keys("List response", sample)
    _print_line_items("list line_items", sample.get("line_items"))

    if not so_id:
        print("Cannot fetch detail without salesorder_id.")
        return

    detail = client.get_sales_order(str(so_id))
    _print_keys("Detail response", detail)
    _print_line_items("detail line_items", detail.get("line_items"))


def inspect_invoice(client: ZohoBooksClient) -> None:
    print("\n" + "=" * 60)
    print("INVOICE")
    print("=" * 60)
    invoices = client.list_invoices(
        params={"date_start": "2026-05-25", "date_end": "2026-06-01", "per_page": 5}
    )
    if not invoices:
        print("No invoices returned for sample window.")
        return

    sample = invoices[0]
    inv_id = sample.get("invoice_id")
    print(f"Sample invoice_id: {inv_id}")
    print(f"Sample invoice_number: {sample.get('invoice_number')}")

    _print_keys("List response", sample)
    _print_line_items("list line_items", sample.get("line_items"))

    if not inv_id:
        print("Cannot fetch detail without invoice_id.")
        return

    detail = client.get_invoice(str(inv_id))
    _print_keys("Detail response", detail)
    _print_line_items("detail line_items", detail.get("line_items"))


def inspect_payment(client: ZohoBooksClient) -> None:
    print("\n" + "=" * 60)
    print("CUSTOMER PAYMENT")
    print("=" * 60)
    payments = client.list_customer_payments(
        params={"date_start": "2026-05-25", "date_end": "2026-06-01", "per_page": 5}
    )
    if not payments:
        print("No payments returned for sample window.")
        return

    sample = payments[0]
    payment_id = sample.get("payment_id")
    print(f"Sample payment_id: {payment_id}")

    _print_keys("List response", sample)
    for key in ("invoices", "invoice_payments", "applied_invoices"):
        val = sample.get(key)
        if val is not None:
            print(f"  list {key}: {type(val).__name__} len={len(val) if isinstance(val, list) else 'n/a'}")

    if not payment_id:
        return

    detail = client.get_customer_payment(str(payment_id))
    _print_keys("Detail response", detail)
    for key in ("invoices", "invoice_payments", "applied_invoices"):
        val = detail.get(key)
        if val is not None:
            print(f"  detail {key}: {type(val).__name__} len={len(val) if isinstance(val, list) else 'n/a'}")
            if isinstance(val, list) and val and isinstance(val[0], dict):
                print(f"    first keys: {sorted(val[0].keys())}")


def main() -> None:
    try:
        config = load_zoho_config()
    except ZohoAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    client = ZohoBooksClient(config)
    client.refresh_access_token()
    print("Zoho detail response debug (no secrets printed)\n")

    inspect_sales_order(client)
    inspect_invoice(client)
    inspect_payment(client)


if __name__ == "__main__":
    main()
