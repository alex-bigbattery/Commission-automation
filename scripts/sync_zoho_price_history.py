"""CLI wrapper around src.db.zoho_price_history_sync.sync_zoho_prices_to_history.

Default mode is --dry-run (no DB writes). Pass --apply to actually mutate. Always
runs the validator at the end.

Examples:
    python -m scripts.sync_zoho_price_history --dry-run
    python -m scripts.sync_zoho_price_history --apply
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.db.connection import get_connection, init_database, DB_PATH  # noqa: E402
from src.db.zoho_price_history_sync import sync_zoho_prices_to_history  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync items.rate into price_history (SCD-2).")
    mode = p.add_mutually_exclusive_group(required=False)
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="Plan only; no DB writes (default).")
    mode.add_argument("--apply", action="store_true",
                      help="Actually write changes to the DB.")
    p.add_argument("--max-plan-rows", type=int, default=40,
                   help="Cap on per-action plan rows printed (default: 40).")
    p.add_argument("--skip-validator", action="store_true",
                   help="Do not run scripts/validate_price_history.py after.")
    return p.parse_args()


def _print_plans(summary, cap: int) -> None:
    inserts = [p for p in summary.plans if p.action == "insert_new"]
    changes = [p for p in summary.plans if p.action == "close_old_and_insert"]
    invalids = [p for p in summary.plans if p.action == "skipped_invalid"]

    if inserts:
        print(f"\nINSERT NEW ({len(inserts)}):")
        for pl in inserts[:cap]:
            print(f"  {pl.sku:<20} new={pl.new_price:<12.4f}  eff_from={pl.effective_from}  ({pl.reason})")
        if len(inserts) > cap:
            print(f"  ... and {len(inserts) - cap} more")

    if changes:
        print(f"\nCLOSE OLD + INSERT NEW ({len(changes)}):")
        for pl in changes[:cap]:
            print(f"  {pl.sku:<20} {pl.old_price:<10.4f} -> {pl.new_price:<10.4f}  eff_from={pl.effective_from}")
        if len(changes) > cap:
            print(f"  ... and {len(changes) - cap} more")

    if invalids:
        print(f"\nSKIPPED INVALID PRICE ({len(invalids)}):")
        for pl in invalids[: min(cap, 15)]:
            print(f"  {pl.sku:<20} reason={pl.reason}")
        if len(invalids) > 15:
            print(f"  ... and {len(invalids) - 15} more")


def _backup_warning_or_copy(applying: bool) -> None:
    """SQLite: copy DB file. Postgres: warn loudly to stderr."""
    if not applying:
        return
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if os.environ.get("DATABASE_URL"):
        msg = (
            "!! WARNING: Postgres backend in use. This script does NOT take a backup.\n"
            "!! UPDATE+INSERT is one transaction (rolled back on error), but a SUCCESSFUL\n"
            "!! apply that updates the wrong rows can only be undone from pg_dump or PITR.\n"
            f"!! Timestamp: {ts}\n"
        )
        print(msg, file=sys.stderr, flush=True)
    else:
        bak = DB_PATH.with_name(DB_PATH.name + f".BAK-{ts}")
        try:
            import shutil
            shutil.copy2(DB_PATH, bak)
            print(f"DB backup -> {bak.name}")
        except Exception as exc:
            print(f"!! backup failed: {exc}", file=sys.stderr, flush=True)


def main() -> int:
    args = _parse_args()
    applying = bool(args.apply)
    dry_run = not applying

    init_database()
    _backup_warning_or_copy(applying)

    conn = get_connection()
    try:
        summary = sync_zoho_prices_to_history(conn, dry_run=dry_run)
    finally:
        conn.close()

    print(summary.pretty())
    _print_plans(summary, args.max_plan_rows)

    if not args.skip_validator:
        print("\n--- validator ---")
        rc = subprocess.call(
            [sys.executable, str(REPO / "scripts" / "validate_price_history.py")]
        )
        if rc != 0:
            print(f"\nVALIDATOR FAILED (exit {rc}) — investigate before next sync.")
            return rc

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
