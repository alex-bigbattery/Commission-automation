from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.commission.sqlite_data_source import (  # noqa: E402
    load_commission_input,
    line_type_counts,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load normalized commission input from SQLite for a month."
    )
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--db", type=Path, default=None, help="Optional SQLite path")
    args = parser.parse_args()

    data = load_commission_input(args.year, args.month, db_path=args.db)
    counts = line_type_counts(data)

    print(f"Commission input — {args.year}-{args.month:02d}")
    print(f"  sales_orders: {len(data.sales_orders)}")
    print(f"  sales_order_lines: {len(data.sales_order_lines)}")
    print(f"  invoices: {len(data.invoices)}")
    print(f"  invoice_lines: {len(data.invoice_lines)}")
    print(f"  items: {len(data.items)}")
    print(f"  customer_payments: {len(data.customer_payments)}")
    print(f"  customer_payment_invoices: {len(data.customer_payment_invoices)}")
    print()
    print("Line classification (sales_order_lines + invoice_lines):")
    for key in (
        "product",
        "shipping",
        "credit_card_fee",
        "discount",
        "other_charge",
        "unknown",
        "commission_candidate",
    ):
        print(f"  {key}: {counts[key]}")
    print()
    print("Sample 10 normalized lines:")
    sample = data.normalized_lines_sample(10)
    if sample.empty:
        print("  (none)")
    else:
        cols = [
            "source",
            "document_id",
            "document_number",
            "line_index",
            "sku",
            "item_name",
            "quantity",
            "item_total",
            "line_type",
            "commission_candidate",
        ]
        present = [c for c in cols if c in sample.columns]
        for _, row in sample[present].iterrows():
            parts = [f"{col}={row[col]}" for col in present]
            print("  " + " | ".join(str(p) for p in parts))


if __name__ == "__main__":
    main()
