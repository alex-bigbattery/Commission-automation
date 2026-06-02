from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.db.connection import DB_PATH, init_database
from src.db.repository import DatabaseRepository
from src.db.shipments_sync import (
    incremental_shipment_params,
    parse_cli_date,
    sync_shipments,
    sync_shipments_range,
)
from src.zoho_client import ZohoAuthError, ZohoBooksClient, load_zoho_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Zoho Books shipments/packages into SQLite (tolerates endpoint failures)."
    )
    parser.add_argument(
        "--mode",
        choices=["full", "incremental", "window"],
        default="window",
        help="full: date range by month chunks; incremental: since last shipments sync; window: explicit dates",
    )
    parser.add_argument("--date-start", dest="date_start", default=None)
    parser.add_argument("--date-end", dest="date_end", default="today")
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

    exit_code = 0

    with DatabaseRepository() as repo:
        if args.mode == "incremental":
            params = incremental_shipment_params(repo)
            print(
                f"Incremental shipments sync: {params.get('date_start')} -> {params.get('date_end')}"
            )
            result = sync_shipments(client, repo, params=params)
            summary = {
                "sync_id": result.sync_id,
                "records_fetched": result.records_fetched,
                "status": result.status,
            }
            warnings = list(result.warnings)
            errors = list(result.errors)
        elif args.mode == "full":
            if not args.date_start:
                print("ERROR: --date-start is required for full mode.", file=sys.stderr)
                raise SystemExit(2)
            date_start = parse_cli_date(args.date_start)
            date_end = parse_cli_date(args.date_end)
            print(f"Full shipments sync {date_start} -> {date_end}")
            batch = sync_shipments_range(
                client,
                repo,
                date_start=date_start,
                date_end=date_end,
                use_month_chunks=True,
            )
            summary = {
                "sync_id": batch["sync_id"],
                "records_fetched": batch["records_fetched"],
                "status": batch["status"],
            }
            warnings = batch.get("warnings") or []
            errors = batch.get("errors") or []
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
            print(f"Shipments sync window {params['date_start']} -> {params['date_end']}")
            result = sync_shipments(client, repo, params=params)
            summary = {
                "sync_id": result.sync_id,
                "records_fetched": result.records_fetched,
                "status": result.status,
            }
            warnings = list(result.warnings)
            errors = list(result.errors)

    counts = DatabaseRepository().table_counts()

    print("\nShipments sync finished:")
    print(f"  Database: {DB_PATH}")
    print(f"  Sync ID: {summary['sync_id']}")
    print(f"  Status: {summary['status']}")
    print(f"  Shipment rows upserted (this run): {summary['records_fetched']}")
    print(f"  shipments table total: {counts['shipments']}")

    for line in warnings:
        print(line, file=sys.stderr)
    for line in errors:
        print(f"ERROR (logged, not fatal): {line}", file=sys.stderr)

    if summary["status"] == "failed":
        print(
            "\nNo shipment data stored — all Zoho shipment/package endpoints failed. "
            "Check API scopes and organization settings.",
            file=sys.stderr,
        )
        exit_code = 0

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
