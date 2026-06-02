from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from src.zoho_client import ZohoApiError, ZohoAuthError, ZohoBooksClient, load_zoho_config
except ImportError:
    from zoho_client import ZohoApiError, ZohoAuthError, ZohoBooksClient, load_zoho_config


BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "zoho_raw"
EXPORT_DIR = BASE_DIR / "data" / "zoho"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Zoho Books data with OAuth refresh token and export JSON + Excel."
    )
    parser.add_argument("--date", help="Single day filter (YYYY-MM-DD).")
    parser.add_argument("--date-start", help="Start date filter (YYYY-MM-DD).")
    parser.add_argument("--date-end", help="End date filter (YYYY-MM-DD).")
    parser.add_argument("--year", type=int, help="Month filter year (use with --month).")
    parser.add_argument("--month", type=int, help="Month filter 1-12 (use with --year).")
    parser.add_argument(
        "--skip-details",
        action="store_true",
        help="Skip per-record detail fetch for line items.",
    )
    return parser.parse_args()


def parse_iso_date(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%d")
    return value


def resolve_date_range(args: argparse.Namespace) -> tuple[str | None, str | None, str]:
    if args.date:
        day = parse_iso_date(args.date)
        return day, day, day

    if args.date_start or args.date_end:
        if not (args.date_start and args.date_end):
            raise SystemExit("Provide both --date-start and --date-end.")
        start = parse_iso_date(args.date_start)
        end = parse_iso_date(args.date_end)
        label = start if start == end else f"{start}_to_{end}"
        return start, end, label

    if args.year or args.month:
        if not (args.year and args.month):
            raise SystemExit("Provide both --year and --month.")
        last_day = calendar.monthrange(args.year, args.month)[1]
        start = date(args.year, args.month, 1).isoformat()
        end = date(args.year, args.month, last_day).isoformat()
        label = f"{args.year}-{args.month:02d}"
        return start, end, label

    return None, None, "all"


def custom_field_map(record: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in record.get("custom_fields") or []:
        if not isinstance(field, dict):
            continue
        label = str(field.get("label") or field.get("api_name") or "").strip()
        value = field.get("value")
        if label:
            values[label.lower()] = "" if value is None else str(value)
    return values


def pick_custom(custom: dict[str, str], *labels: str) -> str:
    for label in labels:
        value = custom.get(label.lower())
        if value not in (None, ""):
            return value
    return ""


def ensure_line_items(client: ZohoBooksClient, record: dict[str, Any], resource: str) -> dict[str, Any]:
    from src.db.zoho_lines import enrich_invoice, enrich_sales_order, has_meaningful_line_items

    if resource == "salesorders":
        return enrich_sales_order(client, record, skip_details=False)
    if resource == "invoices":
        return enrich_invoice(client, record, skip_details=False)
    if has_meaningful_line_items(record):
        return record
    return record


def flatten_sales_orders(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for order in records:
        custom = custom_field_map(order)
        for line in order.get("line_items") or [{}]:
            rows.append(
                {
                    "Sales person": order.get("salesperson_name") or order.get("salesperson") or "",
                    "SalesOrder Number": order.get("salesorder_number", ""),
                    "Order Date": order.get("date", ""),
                    "Reference#": order.get("reference_number", ""),
                    "SalesOrder ID": order.get("salesorder_id", ""),
                    "Status": order.get("status", ""),
                    "Customer Name": order.get("customer_name", ""),
                    "SKU": line.get("sku", ""),
                    "Item Name": line.get("name", ""),
                    "Quantity Ordered": line.get("quantity", 0),
                    "Item Price": line.get("rate", 0),
                    "Discount": line.get("discount", 0),
                    "Item Total": line.get("item_total", 0),
                    "Shipping Charge": order.get("shipping_charge", 0),
                    "Delivery Method": order.get("delivery_method", ""),
                    "Payment Terms Label": order.get("payment_terms_label", ""),
                    "CF.Coupon(s)": pick_custom(custom, "Coupon(s)", "Coupons", "Coupon"),
                    "CF.Ticket#": pick_custom(custom, "Ticket#", "Ticket"),
                }
            )
    return pd.DataFrame(rows)


def flatten_invoices(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for invoice in records:
        custom = custom_field_map(invoice)
        for line in invoice.get("line_items") or [{}]:
            rows.append(
                {
                    "Invoice Date": invoice.get("date", ""),
                    "Invoice Number": invoice.get("invoice_number", ""),
                    "Invoice Status": invoice.get("status", ""),
                    "Balance": invoice.get("balance", 0),
                    "Sales Order Number": invoice.get("reference_number", "")
                    or invoice.get("salesorder_number", ""),
                    "Customer Name": invoice.get("customer_name", ""),
                    "SKU": line.get("sku", ""),
                    "Quantity": line.get("quantity", 0),
                    "Item Total": line.get("item_total", 0),
                    "Sales person": invoice.get("salesperson_name", ""),
                    "Payment Terms Label": invoice.get("payment_terms_label", ""),
                    "CF.Sales Team": pick_custom(custom, "Sales Team"),
                }
            )
    return pd.DataFrame(rows)


def flatten_shipments(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for shipment in records:
        for line in shipment.get("line_items") or [{}]:
            rows.append(
                {
                    "Shipment Date": shipment.get("date", shipment.get("shipment_date", "")),
                    "Shipment Number": shipment.get(
                        "shipment_number",
                        shipment.get("shipmentorder_number", shipment.get("package_number", "")),
                    ),
                    "Status": shipment.get("status", ""),
                    "Carrier Name": shipment.get("carrier", shipment.get("delivery_method", "")),
                    "Tracking Number": shipment.get("tracking_number", ""),
                    "Shipping Charge": shipment.get("shipping_charge", 0),
                    "Sales Order Number": shipment.get("salesorder_number", ""),
                    "Customer Name": shipment.get("customer_name", ""),
                    "SKU": line.get("sku", ""),
                    "Quantity": line.get("quantity", 0),
                    "Source Endpoint": shipment.get("_source_endpoint", ""),
                }
            )
    if not rows:
        return pd.DataFrame(
            columns=[
                "Shipment Date",
                "Shipment Number",
                "Status",
                "Carrier Name",
                "Tracking Number",
                "Shipping Charge",
                "Sales Order Number",
                "Customer Name",
                "SKU",
                "Quantity",
                "Source Endpoint",
            ]
        )
    return pd.DataFrame(rows)


def flatten_items(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in records:
        rows.append(
            {
                "Item ID": item.get("item_id", ""),
                "SKU": item.get("sku", ""),
                "Name": item.get("name", ""),
                "Status": item.get("status", ""),
                "Rate": item.get("rate", 0),
                "Purchase Rate": item.get("purchase_rate", 0),
                "Unit": item.get("unit", ""),
                "Product Type": item.get("product_type", ""),
                "Description": item.get("description", ""),
            }
        )
    return pd.DataFrame(rows)


def flatten_customer_payments(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for payment in records:
        rows.append(
            {
                "Payment Date": payment.get("date", ""),
                "Payment Number": payment.get("payment_number", ""),
                "Customer Name": payment.get("customer_name", ""),
                "Payment Mode": payment.get("payment_mode", ""),
                "Amount": payment.get("amount", 0),
                "Unused Amount": payment.get("unused_amount", 0),
                "Reference Number": payment.get("reference_number", ""),
                "Invoice Numbers": ", ".join(
                    inv.get("invoice_number", "")
                    for inv in (payment.get("invoices") or [])
                    if isinstance(inv, dict)
                ),
            }
        )
    return pd.DataFrame(rows)


def fetch_with_details(
    client: ZohoBooksClient,
    records: list[dict[str, Any]],
    resource: str,
    *,
    skip_details: bool,
) -> list[dict[str, Any]]:
    if skip_details:
        return records
    return [ensure_line_items(client, record, resource) for record in records]


def fetch_shipments(client: ZohoBooksClient, params: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        from src.db.shipments_sync import fetch_shipments_from_zoho
    except ImportError:
        from db.shipments_sync import fetch_shipments_from_zoho

    combined, warnings, errors = fetch_shipments_from_zoho(client, params)
    for err in errors:
        if err not in warnings:
            warnings.append(f"WARNING: {err}" if not err.startswith("WARNING") else err)
    return combined, warnings


def save_raw_json(raw_dir: Path, label: str, resource: str, payload: Any) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{resource}_{label}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def write_workbook(
    export_dir: Path,
    label: str,
    sheets: dict[str, pd.DataFrame],
) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"zoho_export_{label}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, frame in sheets.items():
            safe_name = sheet_name[:31]
            (frame if not frame.empty else pd.DataFrame({"Message": ["No rows returned"]})).to_excel(
                writer, sheet_name=safe_name, index=False
            )
    return path


def main() -> None:
    args = parse_args()

    try:
        config = load_zoho_config()
    except ZohoAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    date_start, date_end, label = resolve_date_range(args)
    params: dict[str, Any] = {}
    if date_start and date_end:
        params["date_start"] = date_start
        params["date_end"] = date_end

    client = ZohoBooksClient(config)

    try:
        print("Refreshing Zoho access token...")
        client.refresh_access_token()
        print("Connected to Zoho Books.")
    except ZohoAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    warnings: list[str] = []
    sheets: dict[str, pd.DataFrame] = {}

    try:
        print("Fetching sales orders...")
        sales_raw = client.list_sales_orders(params=params)
        sales_raw = fetch_with_details(client, sales_raw, "salesorders", skip_details=args.skip_details)
        save_raw_json(RAW_DIR, label, "sales_orders", sales_raw)
        sheets["Sales Orders"] = flatten_sales_orders(sales_raw)
        print(f"  rows: {len(sheets['Sales Orders'])}")

        print("Fetching invoices...")
        invoices_raw = client.list_invoices(params=params)
        invoices_raw = fetch_with_details(client, invoices_raw, "invoices", skip_details=args.skip_details)
        save_raw_json(RAW_DIR, label, "invoices", invoices_raw)
        sheets["Invoices"] = flatten_invoices(invoices_raw)
        print(f"  rows: {len(sheets['Invoices'])}")

        print("Fetching items...")
        items_raw = client.list_items()
        save_raw_json(RAW_DIR, label, "items", items_raw)
        sheets["Items"] = flatten_items(items_raw)
        print(f"  rows: {len(sheets['Items'])}")

        print("Fetching customer payments...")
        payments_raw = client.list_customer_payments(params=params)
        save_raw_json(RAW_DIR, label, "customer_payments", payments_raw)
        sheets["Customer Payments"] = flatten_customer_payments(payments_raw)
        print(f"  rows: {len(sheets['Customer Payments'])}")

        print("Fetching shipments/packages...")
        shipments_raw, shipment_warnings = fetch_shipments(client, params)
        warnings.extend(shipment_warnings)
        save_raw_json(RAW_DIR, label, "shipments", shipments_raw)
        sheets["Shipments"] = flatten_shipments(shipments_raw)
        print(f"  rows: {len(sheets['Shipments'])}")

    except ZohoAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except ZohoApiError as exc:
        print(f"ERROR: Zoho API request failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    workbook_path = write_workbook(EXPORT_DIR, label, sheets)

    for warning in warnings:
        print(warning, file=sys.stderr)

    print("\nExport complete:")
    print(f"  Excel: {workbook_path}")
    print(f"  Raw JSON dir: {RAW_DIR}")


if __name__ == "__main__":
    main()
