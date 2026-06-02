from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.db.connection import DB_PATH, init_database
from src.db.incremental_sync import (
    DEFAULT_FALLBACK_DAYS,
    incremental_sync_plan,
    run_incremental_sync,
)
from src.db.repository import DatabaseRepository
from src.zoho_client import ZohoAuthError, ZohoBooksClient, load_zoho_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Incremental Zoho → SQLite sync using date_start/date_end rolling windows. "
            "last_modified_time is off by default (Zoho rejects YYYY-MM-DD)."
        )
    )
    parser.add_argument(
        "--fallback-days",
        type=int,
        default=DEFAULT_FALLBACK_DAYS,
        help=f"Rolling window when no prior sync exists (default: {DEFAULT_FALLBACK_DAYS})",
    )
    parser.add_argument("--skip-details", action="store_true")
    parser.add_argument(
        "--use-last-modified",
        action="store_true",
        help="Attempt last_modified_time (experimental; format must match Zoho API)",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print per-module fetch windows without calling Zoho",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first core module error (default: continue other modules)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_database()

    with DatabaseRepository() as repo:
        plan = incremental_sync_plan(
            repo,
            use_last_modified=args.use_last_modified,
            fallback_days=args.fallback_days,
        )
        print("Incremental sync plan:")
        for entry in plan:
            optional = " (optional)" if entry.get("optional") else ""
            print(
                f"  {entry['module']}{optional}: source={entry['source']}, params={entry['params']}"
            )
        if args.use_last_modified:
            print("  NOTE: --use-last-modified is enabled (may fail if format is wrong).")
        else:
            print("  NOTE: last_modified_time is NOT sent (date window only).")

        if args.plan_only:
            return

    try:
        config = load_zoho_config()
    except ZohoAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    client = ZohoBooksClient(config)
    print("\nRefreshing Zoho access token...")
    client.refresh_access_token()
    print("Connected to Zoho Books.")

    with DatabaseRepository() as repo:
        result = run_incremental_sync(
            client,
            repo,
            skip_details=args.skip_details,
            continue_on_module_error=not args.fail_fast,
            fallback_days=args.fallback_days,
            use_last_modified=args.use_last_modified,
        )

    print("\nIncremental sync complete:")
    print(f"  Database: {DB_PATH}")
    print(f"  Sync ID: {result['sync_id']}")
    print(f"  Status: {result['status']}")
    for module, count in sorted(result["totals"].items()):
        mod_result = (result.get("module_results") or {}).get(module, {})
        mod_status = mod_result.get("status", "?")
        print(f"  {module}: {count} rows ({mod_status})")

    for line in result.get("warnings") or []:
        print(line, file=sys.stderr)
    for line in result.get("errors") or []:
        print(f"ERROR: {line}", file=sys.stderr)

    counts = DatabaseRepository().table_counts()
    print("\nTable row counts:")
    for table, count in counts.items():
        print(f"  {table}: {count}")

    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
