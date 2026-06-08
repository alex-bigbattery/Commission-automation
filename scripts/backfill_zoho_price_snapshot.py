"""Backfill a closed-month price_history snapshot from Zoho catalog (items.rate).

Use when a month has coverage gaps and you want prices sourced from Zoho Books
instead of Excel R_LP / accountant workbooks. Reads the local ``items`` table
(populated by Zoho sync) — does NOT read Excel.

By default only inserts rows for SKUs with NO existing coverage for the target
month. Use ``--replace-sources`` to delete Excel/other rows for that month first
(e.g. migrate ``imported_rlp_2026_01`` to Zoho catalog).

Examples:
    # Preview April 2026 gaps filled from Zoho catalog
    python -m scripts.backfill_zoho_price_snapshot --snapshot-month 2026-04 --dry-run

    # Replace imported R_LP for January with Zoho catalog prices
    python -m scripts.backfill_zoho_price_snapshot --snapshot-month 2026-01 \\
        --replace-sources imported_rlp_2026_01 --confirm 2026-01

    # Apply (requires --confirm echoing the month)
    python -m scripts.backfill_zoho_price_snapshot --snapshot-month 2026-04 --confirm 2026-04
"""
from __future__ import annotations

import argparse
import calendar
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.db.connection import DB_PATH, get_connection, init_database  # noqa: E402

SOURCE_PREFIX = "zoho_catalog_snapshot_"
FAR_FUTURE = "9999-12-31"


def _month_bounds(snapshot_month: str) -> tuple[str, str]:
    y, m = (int(p) for p in snapshot_month.split("-", 1))
    last = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}"


def _covers_month(effective_from: str, effective_to: str, month_from: str, month_to: str) -> bool:
    """True when the row window covers every day in [month_from, month_to]."""
    if not effective_from or not month_from:
        return False
    if effective_from > month_to:
        return False
    et = str(effective_to or "")
    if et >= FAR_FUTURE:
        return True
    return et >= month_from


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill price_history for a month from Zoho items.rate (no Excel)."
    )
    p.add_argument("--snapshot-month", required=True, help="YYYY-MM bucket (e.g. 2026-04)")
    p.add_argument("--dry-run", action="store_true", help="Plan only; no DB writes.")
    p.add_argument("--confirm", help="Must equal --snapshot-month to apply writes.")
    p.add_argument("--limit", type=int, default=0, help="Cap inserts (0 = no cap).")
    p.add_argument(
        "--replace-sources",
        help="Comma-separated source labels to DELETE for this snapshot_month before insert "
        "(e.g. imported_rlp_2026_01).",
    )
    return p.parse_args()


def _backup_db() -> None:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if os.environ.get("DATABASE_URL"):
        print(
            "!! WARNING: Postgres backend — no automatic backup. "
            "Ensure pg_dump or PITR before applying.\n",
            file=sys.stderr,
            flush=True,
        )
        return
    bak = DB_PATH.with_name(DB_PATH.name + f".BAK-{ts}")
    shutil.copy2(DB_PATH, bak)
    print(f"DB backup -> {bak.name}")


def _skus_covered_for_month(
    conn,
    month_from: str,
    month_to: str,
    *,
    ignore_sources: set[str] | None = None,
) -> set[str]:
    rows = conn.execute(
        "SELECT UPPER(sku) AS sku_u, effective_from, effective_to, source "
        "FROM price_history "
        "WHERE sku IS NOT NULL AND sku != '' "
        "AND effective_from <= ? "
        "AND (effective_to >= ? OR effective_to = ?)",
        (month_to, month_from, FAR_FUTURE),
    ).fetchall()
    covered: set[str] = set()
    for r in rows:
        src = str(r["source"] or "")
        if ignore_sources and src in ignore_sources:
            continue
        sku = str(r["sku_u"] or "").strip().upper()
        if sku and _covers_month(str(r["effective_from"]), str(r["effective_to"]), month_from, month_to):
            covered.add(sku)
    return covered


def _count_replace_deletes(conn, snapshot_month: str, replace_sources: list[str]) -> int:
    if not replace_sources:
        return 0
    placeholders = ",".join("?" for _ in replace_sources)
    return int(conn.execute(
        f"SELECT COUNT(*) AS c FROM price_history "
        f"WHERE snapshot_month=? AND source IN ({placeholders})",
        (snapshot_month, *replace_sources),
    ).fetchone()["c"])


def plan_backfill(
    conn,
    snapshot_month: str,
    *,
    limit: int = 0,
    replace_sources: list[str] | None = None,
) -> dict[str, Any]:
    month_from, month_to = _month_bounds(snapshot_month)
    source = f"{SOURCE_PREFIX}{snapshot_month.replace('-', '_')}"
    ignore = set(replace_sources or [])

    covered = _skus_covered_for_month(conn, month_from, month_to, ignore_sources=ignore or None)
    would_delete = _count_replace_deletes(conn, snapshot_month, replace_sources or [])
    items = conn.execute(
        "SELECT sku, item_id, rate FROM items WHERE sku IS NOT NULL AND sku != '' AND rate > 0"
    ).fetchall()

    to_insert: list[tuple[str, str | None, float]] = []
    skipped_invalid = 0
    for row in items:
        sku = str(row["sku"] or "").strip().upper()
        if not sku or sku in covered:
            continue
        try:
            price = float(row["rate"])
        except (TypeError, ValueError):
            skipped_invalid += 1
            continue
        if price <= 0:
            skipped_invalid += 1
            continue
        item_id = str(row["item_id"]).strip() if row["item_id"] else None
        to_insert.append((sku, item_id, price))
        if limit and len(to_insert) >= limit:
            break

    to_insert.sort(key=lambda x: x[0])
    return {
        "snapshot_month": snapshot_month,
        "source": source,
        "month_from": month_from,
        "month_to": month_to,
        "replace_sources": list(replace_sources or []),
        "would_delete": would_delete,
        "covered_skus": len(covered),
        "zoho_items": len(items),
        "would_insert": len(to_insert),
        "skipped_invalid": skipped_invalid,
        "rows": to_insert,
    }


def apply_backfill(conn, plan: dict[str, Any]) -> tuple[int, int]:
    captured = datetime.now().isoformat(timespec="seconds")
    payload = [
        (sku, item_id, price, plan["month_from"], plan["month_to"],
         plan["source"], plan["snapshot_month"], captured)
        for sku, item_id, price in plan["rows"]
    ]
    deleted = 0
    if plan.get("replace_sources"):
        placeholders = ",".join("?" for _ in plan["replace_sources"])
        conn.execute(
            f"DELETE FROM price_history WHERE snapshot_month=? AND source IN ({placeholders})",
            (plan["snapshot_month"], *plan["replace_sources"]),
        )
        deleted = int(plan.get("would_delete") or 0)

    if not payload:
        conn.commit()
        return deleted, 0

    conn.executemany(
        "INSERT INTO price_history "
        "(sku, item_id, map_price, effective_from, effective_to, source, snapshot_month, captured_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        payload,
    )
    conn.commit()
    return deleted, len(payload)


def main() -> int:
    args = _parse_args()
    try:
        datetime.strptime(args.snapshot_month + "-01", "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit(f"Invalid --snapshot-month {args.snapshot_month!r}: {exc}") from exc

    if not args.dry_run:
        if not args.confirm or args.confirm != args.snapshot_month:
            raise SystemExit(
                f"Refusing to apply without --confirm {args.snapshot_month}. "
                "Pass --dry-run to preview first."
            )

    replace_sources = [s.strip() for s in (args.replace_sources or "").split(",") if s.strip()]

    init_database()
    conn = get_connection()
    try:
        plan = plan_backfill(
            conn, args.snapshot_month, limit=args.limit, replace_sources=replace_sources or None,
        )
    finally:
        conn.close()

    print(f"Zoho catalog backfill plan for {plan['snapshot_month']}")
    print(f"  source           : {plan['source']}")
    print(f"  window           : {plan['month_from']} .. {plan['month_to']}")
    if plan["replace_sources"]:
        print(f"  replace_sources  : {', '.join(plan['replace_sources'])}")
        print(f"  would_delete     : {plan['would_delete']}")
    print(f"  SKUs with month coverage already: {plan['covered_skus']}")
    print(f"  Zoho items (rate>0)            : {plan['zoho_items']}")
    print(f"  would_insert                   : {plan['would_insert']}")
    print(f"  skipped_invalid                : {plan['skipped_invalid']}")
    if plan["rows"][:8]:
        print("  sample:")
        for sku, _iid, price in plan["rows"][:8]:
            print(f"    {sku:<40} ${price:.2f}")

    if args.dry_run:
        print("\n[dry-run] No DB writes.")
        return 0

    _backup_db()
    conn = get_connection()
    try:
        deleted, inserted = apply_backfill(conn, plan)
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM price_history WHERE snapshot_month=? AND source=?",
            (plan["snapshot_month"], plan["source"]),
        ).fetchone()["c"]
        print(f"\nDeleted {deleted} rows. Inserted {inserted} rows.")
        print(f"Total for {plan['source']}: {total}")
    finally:
        conn.close()

    print("Running validator...")
    import subprocess
    rc = subprocess.call([sys.executable, str(REPO / "scripts" / "validate_price_history.py")])
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
