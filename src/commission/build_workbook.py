"""
CLI: build Jennifer-style commission workbook from SQLite.

Usage:
  # Master workbook (all salespeople + B2B Summary)
  python -m src.commission.build_workbook --year 2026 --month 3

  # One salesperson only
  python -m src.commission.build_workbook --year 2026 --month 3 --salesperson Brett

Outputs to data/output/ by default.
"""

from __future__ import annotations

import argparse
import calendar
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.commission.sqlite_to_workbook import (
    ALL_SHEETS_ORDERED,
    generate_commission_workbook,
    generate_salesperson_workbook,
)

TEMPLATES_DIR = BASE_DIR / "data" / "templates"
OUTPUT_DIR = BASE_DIR / "data" / "output"
MASTER_TEMPLATE = TEMPLATES_DIR / "master_template_clean.xlsx"
SP_TEMPLATE = TEMPLATES_DIR / "salesperson_template_clean.xlsx"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build a Jennifer-style commission workbook from SQLite operational data."
    )
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--month", type=int, required=True, choices=range(1, 13))
    p.add_argument(
        "--salesperson",
        choices=[s for s, _ in ALL_SHEETS_ORDERED],
        help="If set, build a single-sheet workbook for this salesperson only.",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Override SQLite path (defaults to data/db/commission_automation.sqlite).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output xlsx path. Default depends on --salesperson.",
    )
    p.add_argument(
        "--template",
        type=Path,
        default=None,
        help="Override template path (advanced).",
    )
    return p.parse_args()


def default_master_output(year: int, month: int) -> Path:
    month_name = calendar.month_name[month].lower()
    return OUTPUT_DIR / f"commission_workbook_{month_name}_{year}.xlsx"


def default_salesperson_output(year: int, month: int, sheet: str) -> Path:
    month_name = calendar.month_name[month].lower()
    safe_sheet = sheet.replace(" ", "_").lower()
    return OUTPUT_DIR / f"commission_workbook_{safe_sheet}_{month_name}_{year}.xlsx"


def main() -> None:
    args = parse_args()

    if args.salesperson:
        template = args.template or SP_TEMPLATE
        output = args.output or default_salesperson_output(args.year, args.month, args.salesperson)
        if not template.exists():
            raise SystemExit(f"Template not found: {template}")
        result = generate_salesperson_workbook(
            year=args.year,
            month=args.month,
            salesperson_sheet=args.salesperson,
            template_path=template,
            output_path=output,
            db_path=args.db,
        )
        print(f"OK: salesperson workbook -> {output}")
        print(f"  Size: {output.stat().st_size:,} bytes")
    else:
        template = args.template or MASTER_TEMPLATE
        output = args.output or default_master_output(args.year, args.month)
        if not template.exists():
            raise SystemExit(f"Template not found: {template}")
        result = generate_commission_workbook(
            year=args.year,
            month=args.month,
            template_path=template,
            output_path=output,
            db_path=args.db,
        )
        print(f"OK: master workbook -> {output}")
        print(f"  Size: {output.stat().st_size:,} bytes")

    print("\nKPIs:")
    for key, val in result.kpis.items():
        print(f"  {key}: {val}")
    print(f"\nExceptions to review: {len(result.exceptions)}")
    for ex in result.exceptions[:25]:
        print(f"  [{ex.salesperson}] {ex.invoice_number} {ex.sku} ${ex.amount:,.2f} — {ex.reason}")
    if len(result.exceptions) > 25:
        print(f"  ... and {len(result.exceptions) - 25} more")
    print("\nPer-salesperson commission estimate:")
    for sheet, total in result.totals_by_sheet.items():
        if total:
            print(f"  {sheet}: ${total:,.2f}")


if __name__ == "__main__":
    main()
