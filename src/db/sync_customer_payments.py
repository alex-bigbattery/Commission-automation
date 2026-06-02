from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.db.connection import DB_PATH, init_database
from src.db.customer_payments_sync import (
    incremental_customer_payment_params,
    parse_cli_date,
    sync_customer_payments,
    sync_customer_payments_range,
)
from src.db.repository import DatabaseRepository
from src.zoho_client import ZohoAuthError, ZohoBooksClient, load_zoho_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Zoho Books customer payments (and invoice applications) into SQLite."
    )
    parser.add_argument(
        "--mode",
        choices=["full", "incremental", "window"],
        default="window",
        help="full: date range by month chunks; incremental: since last customer_payments sync; window: explicit dates",
    )
    parser.add_argument("--date-start", dest="date_start", default=None)
    parser.add_argument("--date-end", dest="date_end", default="today")
    parser.add_argument(
        "--skip-details",
        action="store_true",
        help="Skip per-payment detail API calls (invoice applications may be incomplete)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_database()

    try:
        config = load_zoho_config()
    except ZohoAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    client = ZohoBooksClient(config)
    print("Refreshing Zoho access token...")
    client.refresh_access_token()
    print("Connected to Zoho Books.")

    with DatabaseRepository() as repo:
        if args.mode == "incremental":
            params = incremental_customer_payment_params(repo)
            print(
                f"Incremental customer payments sync: {params.get('date_start')} -> {params.get('date_end')} "
                f"(last_modified_time={params.get('last_modified_time')})"
            )
            result = sync_customer_payments(
                client,
                repo,
                params=params,
                skip_details=args.skip_details,
            )
            summary = {
                "sync_id": result.sync_id,
                "records_fetched": result.records_fetched,
                "date_start": result.date_start,
                "date_end": result.date_end,
            }
        elif args.mode == "full":
            if not args.date_start:
                print("ERROR: --date-start is required for full mode.", file=sys.stderr)
                raise SystemExit(2)
            date_start = parse_cli_date(args.date_start)
            date_end = parse_cli_date(args.date_end)
            print(f"Full customer payments sync {date_start} -> {date_end}")
            summary = sync_customer_payments_range(
                client,
                repo,
                date_start=date_start,
                date_end=date_end,
                skip_details=args.skip_details,
                use_month_chunks=True,
            )
        else:
            if not args.date_start:
                print("ERROR: --date-start is required for window mode.", file=sys.stderr)
                raise SystemExit(2)
            date_start = parse_cli_date(args.date_start)
            date_end = parse_cli_date(args.date_end)
            params = {
                "date_start": date_start.isoformat(),
                "date_end": date_end.isoformat(),
            }
            print(
                f"Customer payments sync window {params['date_start']} -> {params['date_end']}"
            )
            result = sync_customer_payments(
                client,
                repo,
                params=params,
                skip_details=args.skip_details,
            )
            summary = {
                "sync_id": result.sync_id,
                "records_fetched": result.records_fetched,
                "date_start": result.date_start,
                "date_end": result.date_end,
            }

    counts = DatabaseRepository().table_counts()
    print("\nCustomer payments sync complete:")
    print(f"  Database: {DB_PATH}")
    print(f"  Sync ID: {summary['sync_id']}")
    print(f"  Payments upserted: {summary['records_fetched']}")
    print(f"  customer_payments rows: {counts['customer_payments']}")
    print(f"  customer_payment_invoices rows: {counts['customer_payment_invoices']}")


if __name__ == "__main__":
    main()
