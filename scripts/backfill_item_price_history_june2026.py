#!/usr/bin/env python3
"""Backfill item_price_history for 2026-06-01 .. 2026-06-08 (dashboard gap).

The forward capture (capture_zoho_item_price_snapshot.py) started 2026-06-09, so
the affiliate-dashboard Zoho Price History calendar is empty for Jun 1–8.

This script extends each item's *earliest* history period backward to cover that
gap, using the rate already stored from the first capture (assumption: price was
stable unless Zoho last_modified_time falls inside the gap).

Rules:
  - last_modified before 2026-06-01  → effective_from = 2026-06-01 00:00 UTC
  - last_modified on/after 2026-06-09 → effective_from = 2026-06-01 00:00 UTC
  - last_modified 2026-06-01 .. 2026-06-08 → effective_from = that date 00:00 UTC
    (Jun 1 .. mod_date-1 stay blank — price may have differed; listed in report)
  - Never deletes rows; only moves effective_from earlier on the oldest period.

Usage:
  py -3 -m scripts.backfill_item_price_history_june2026 --dry-run
  py -3 -m scripts.backfill_item_price_history_june2026 --apply
  py -3 -m scripts.backfill_item_price_history_june2026 --apply --respect-mod-dates
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

from src.db.connection import get_connection, init_database, using_postgres  # noqa: E402

BACKFILL_FROM = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
BACKFILL_TO_DATE = "2026-06-08"
SOURCE_TAG = "zoho_item_price_capture_backfill_20260601"
EXPORT = REPO / "data" / "exports" / "item_price_history_jun01_08_backfill_plan.csv"


def _parse_mod_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) < 10:
        return None
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d")
        return d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_plan(conn, *, respect_mod_dates: bool) -> list[dict]:
    rows = conn.execute(
        """
        WITH earliest AS (
            SELECT DISTINCT ON (h.item_id)
                   h.id, h.item_id, h.sku, h.name, h.rate, h.effective_from, h.source
            FROM item_price_history h
            ORDER BY h.item_id, h.effective_from ASC
        )
        SELECT e.id, e.item_id, e.sku, e.name, e.rate,
               e.effective_from, e.source,
               i.last_modified_time
        FROM earliest e
        LEFT JOIN items i ON i.item_id = e.item_id
        WHERE e.effective_from::date > ?
        ORDER BY e.sku
        """,
        (BACKFILL_TO_DATE,),
    ).fetchall()

    plan: list[dict] = []
    for r in rows:
        cur_from = r["effective_from"]
        if isinstance(cur_from, str):
            cur_from_dt = datetime.fromisoformat(cur_from.replace("Z", "+00:00"))
        else:
            cur_from_dt = cur_from
            if cur_from_dt.tzinfo is None:
                cur_from_dt = cur_from_dt.replace(tzinfo=timezone.utc)

        mod = _parse_mod_date(r["last_modified_time"])
        if respect_mod_dates and mod is not None and BACKFILL_FROM <= mod < datetime(2026, 6, 9, tzinfo=timezone.utc):
            new_from = mod.replace(hour=0, minute=0, second=0, microsecond=0)
            bucket = "partial_from_mod_date"
        else:
            new_from = BACKFILL_FROM
            bucket = "full_jun1_8"

        if new_from >= cur_from_dt:
            continue

        gap_days = (new_from.date() - BACKFILL_FROM.date()).days
        plan.append({
            "history_id": r["id"],
            "item_id": r["item_id"],
            "sku": r["sku"],
            "name": r["name"],
            "rate": float(r["rate"]) if r["rate"] is not None else None,
            "old_effective_from": cur_from_dt.isoformat(),
            "new_effective_from": new_from.isoformat(),
            "last_modified_time": r["last_modified_time"],
            "bucket": bucket,
            "gap_days_before_mod": gap_days if bucket == "partial_from_mod_date" else 0,
        })
    return plan


def write_report(plan: list[dict]) -> None:
    EXPORT.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "sku", "rate", "bucket", "new_effective_from", "old_effective_from",
        "last_modified_time", "gap_days_before_mod", "item_id", "history_id",
    ]
    with EXPORT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in plan:
            w.writerow(row)


def apply_plan(conn, plan: list[dict]) -> int:
    now = datetime.now(timezone.utc)
    n = 0
    for row in plan:
        conn.execute(
            """
            UPDATE item_price_history
            SET effective_from = ?,
                source = CASE
                    WHEN COALESCE(source, '') LIKE ? THEN source
                    ELSE COALESCE(source, 'zoho_item_price_capture') || ';' || ?
                END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                row["new_effective_from"],
                f"%{SOURCE_TAG}%",
                SOURCE_TAG,
                now,
                row["history_id"],
            ),
        )
        n += 1
    conn.commit()
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill item_price_history Jun 1–8 2026.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--respect-mod-dates",
        action="store_true",
        help="For items Zoho-modified Jun 1–8, only backfill from mod date (leaves earlier days blank).",
    )
    args = parser.parse_args()
    apply = args.apply

    if not using_postgres():
        print("ERROR: Postgres (DATABASE_URL) required — item_price_history lives in Supabase.")
        return 1

    init_database()
    conn = get_connection()

    plan = load_plan(conn, respect_mod_dates=args.respect_mod_dates)
    write_report(plan)

    full = sum(1 for p in plan if p["bucket"] == "full_jun1_8")
    partial = sum(1 for p in plan if p["bucket"] == "partial_from_mod_date")
    partial_gaps = sum(1 for p in plan if p["gap_days_before_mod"] > 0)

    print("=" * 60)
    print("  item_price_history backfill — 2026-06-01 .. 2026-06-08")
    print("=" * 60)
    print(f"  Rows to update          : {len(plan)}")
    print(f"  Full Jun 1–8 coverage   : {full}")
    print(f"  Partial (mod in gap)    : {partial} ({partial_gaps} with days still blank before mod date)")
    print(f"  Report                  : {EXPORT}")
    print(f"  Mode                    : {'APPLY' if apply else 'DRY-RUN'}")
    print("=" * 60)

    if partial_gaps:
        print("\n  SKUs with blank days before Zoho modification date in gap:")
        for p in plan:
            if p["gap_days_before_mod"] > 0:
                print(f"    {p['sku']} — {p['gap_days_before_mod']} day(s) before {p['last_modified_time'][:10]}")

    if not apply:
        print("\n  No DB changes (dry-run). Re-run with --apply to write.")
        return 0

    updated = apply_plan(conn, plan)
    print(f"\n  Updated {updated} item_price_history rows.")
    print("  Refresh Zoho Price History in the dashboard (Jun 1–8 should populate).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
