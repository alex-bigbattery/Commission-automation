from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.db.connection import DB_PATH, init_database
from src.db.repository import DatabaseRepository
from src.db.incremental_sync import run_incremental_sync
from src.db.zoho_sync import parse_cli_date, run_sync
from src.zoho_client import ZohoAuthError, ZohoBooksClient, load_zoho_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Zoho Books data into SQLite.")
    parser.add_argument("--mode", choices=["full", "incremental"], required=True)
    parser.add_argument("--date-start", dest="date_start", default=None)
    parser.add_argument("--date-end", dest="date_end", default="today")
    parser.add_argument("--skip-details", action="store_true")
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
        if args.mode == "full":
            if not args.date_start:
                print("ERROR: --date-start is required for full sync.", file=sys.stderr)
                raise SystemExit(2)
            date_start = parse_cli_date(args.date_start)
            date_end = parse_cli_date(args.date_end)
            print(f"Full sync {date_start.isoformat()} -> {date_end.isoformat()} into {DB_PATH}")
            result = run_sync(
                client,
                repo,
                date_start=date_start,
                date_end=date_end,
                skip_details=args.skip_details,
                use_month_chunks=True,
            )
        else:
            print("Incremental sync (per-module last sync or 7-day fallback)...")
            result = run_incremental_sync(
                client,
                repo,
                skip_details=args.skip_details,
                continue_on_module_error=True,
            )

    print("\nSync complete:")
    print(f"  Database: {DB_PATH}")
    print(f"  Sync ID: {result['sync_id']}")
    for module, count in sorted(result["totals"].items()):
        print(f"  {module}: {count}")
    for warning in result.get("warnings") or []:
        print(warning, file=sys.stderr)

    counts = DatabaseRepository().table_counts()
    print("\nTable row counts:")
    for table, count in counts.items():
        print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
