"""Zoho-sync hook for price_history (SCD-2, append-only with closed windows).

Why this exists: ``items.rate`` is the LIVE Zoho catalog price (overwritten on every
sync). The commission engine needs the price effective AT THE SALE DATE, not today's
price. This module records the ``items.rate`` history into ``price_history`` so the
resolver can look up the period-correct MAP for any past invoice/SO/shipment.

Boundary contracts (enforced in code AND at the SQL level, not just docs):
  * NEVER reads/writes ``items`` table (that's ``DatabaseRepository.upsert_items``).
  * NEVER touches R_LP (the curated template fallback).
  * NEVER touches ACCOUNTANT snapshot rows. The "active Zoho-sync row" query is gated
    on ``source LIKE 'zoho\\_sync\\_2%' ESCAPE '\\'`` (literal underscores, year-starting
    timestamp) so accountant rows with source like ``accountant_fvprice_2026_04`` are
    invisible to BOTH the diff lookup and the UPDATE statement that closes old rows.
    The close-old UPDATE re-asserts the same filter in its WHERE clause as a SQL-level
    invariant (defense-in-depth against future refactors).
  * NEVER changes ``snapshot_month`` of any prior row. Inserts are always
    ``snapshot_month='live'``.
  * Atomic per-sync: UPDATEs (close old) + INSERTs (open new) + same-day in-place
    UPDATEs commit together; on error the txn rolls back (BOTH SQLite and Postgres
    via ``DbConnection.rollback()``) so we never leave half-closed windows.

Resolver interaction: closed-month snapshots (snapshot_month != 'live') take priority
over live Zoho-sync rows UNCONDITIONALLY for any sale date their window covers. A
live row only prices a sale when no closed-month snapshot covers that date. So
inserting a forever-open live row today CANNOT retroactively change April 2026's
commission calculation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from src.db.connection import DbConnection


# All live Zoho-sync rows share these sentinels.
FAR_FUTURE = "9999-12-31"
LIVE_SNAPSHOT_MONTH = "live"
SOURCE_PREFIX = "zoho_sync_"
# Tight LIKE pattern: literal underscores (escaped so SQL `_` wildcard doesn't apply)
# AND requires a year-starting timestamp (4-digit year beginning with 2). Mismatched
# operator-inserted sources like 'Zoho_Sync_x' or 'zoho_sync_' (empty timestamp) are
# rejected by this filter. The ESCAPE '\\' clause is portable across SQLite/Postgres.
SOURCE_LIKE = r"zoho\_sync\_2%"


@dataclass
class SyncPlan:
    """One per non-trivial decision (insert_new / close_old_and_insert /
    updated_same_day / skipped_*)."""
    sku: str
    item_id: str | None
    old_price: float | None
    new_price: float
    effective_from: str
    action: str
    reason: str = ""


@dataclass
class SyncSummary:
    scanned: int = 0
    skipped_blank_sku: int = 0
    skipped_invalid_price: int = 0
    unchanged: int = 0
    inserted_new: int = 0
    changed_closed_old: int = 0
    updated_same_day: int = 0
    errors: list[str] = field(default_factory=list)
    plans: list[SyncPlan] = field(default_factory=list)
    source_label: str = ""
    effective_from: str = ""
    dry_run: bool = False

    def pretty(self) -> str:
        lines = [
            f"price_history sync summary  ({'DRY-RUN' if self.dry_run else 'APPLIED'})",
            f"  source_label         : {self.source_label}",
            f"  effective_from       : {self.effective_from}",
            f"  scanned              : {self.scanned}",
            f"  skipped_blank_sku    : {self.skipped_blank_sku}",
            f"  skipped_invalid_price: {self.skipped_invalid_price}",
            f"  unchanged            : {self.unchanged}",
            f"  inserted_new         : {self.inserted_new}",
            f"  changed_closed_old   : {self.changed_closed_old}",
            f"  updated_same_day     : {self.updated_same_day}",
            f"  errors               : {len(self.errors)}",
        ]
        for e in self.errors[:10]:
            lines.append(f"    ! {e}")
        return "\n".join(lines)


def _source_label(sync_ts: datetime) -> str:
    return f"{SOURCE_PREFIX}{sync_ts.strftime('%Y%m%d%H%M%S')}"


def _load_active_zoho_rows(conn: DbConnection, today_iso: str) -> dict[str, dict[str, Any]]:
    """Latest active Zoho-sync row per SKU. 'Active' = source matches the tight
    'zoho\\_sync\\_2%' pattern AND effective_to >= today. Excludes accountant snapshots
    by construction (the LIKE pattern's literal underscores + year-2 prefix cannot
    match accountant_* labels).
    """
    rows = conn.execute(
        "SELECT sku, map_price, effective_from, effective_to, source, id "
        "FROM price_history "
        r"WHERE source LIKE ? ESCAPE '\' AND effective_to >= ? "
        "ORDER BY sku, effective_from DESC, id DESC",
        (SOURCE_LIKE, today_iso),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        sku = str(row["sku"] or "").strip().upper()
        if not sku or sku in out:
            continue
        out[sku] = {
            "id": int(row["id"]),
            "price": float(row["map_price"]),
            "effective_from": str(row["effective_from"]),
            "effective_to": str(row["effective_to"]),
            "source": str(row["source"]),
        }
    return out


def sync_zoho_prices_to_history(
    conn: DbConnection,
    *,
    dry_run: bool = False,
    sync_ts: datetime | None = None,
    items_rows: Iterable[dict[str, Any]] | None = None,
) -> SyncSummary:
    """SCD-2 sync from items.rate -> price_history (live forward-looking rows).

    Rules (matching the operator spec):
      1. Normalize SKU: strip + upper. Skip blank.
      2. items.rate is the price source.
      3. NULL/<=0 price: skip + record SyncPlan(action='skipped_invalid').
      4. Active Zoho row lookup: ``source LIKE 'zoho\\_sync\\_2%' ESCAPE '\\'`` AND
         ``effective_to >= today``. Accountant snapshots are NOT considered here.
      5. (Resolver-level priority handled in src/commission/sqlite_to_workbook.py.)
      6. No active Zoho row: insert (effective_from=today, effective_to=9999-12-31,
         source=zoho_sync_<ts>, snapshot_month='live').
      7. Same price: no-op (unchanged).
      8. Changed price, OLDER effective_from than today: close old (effective_to=
         today-1) + insert new (same template as #6).
      8b. Changed price, SAME-DAY active row (active.effective_from == today AND
         active.effective_to == 9999-12-31): UPDATE the active row's map_price,
         source, captured_at IN PLACE. Counted as updated_same_day. Prevents losing
         intra-day corrections AND avoids creating an inverted close-and-replace
         window. Audit cost: the previous intra-day price is overwritten (no second
         row in history for the same day).
      9. Atomic: UPDATE(close_old)+UPDATE(same_day)+INSERT(new) in one commit;
         rolls back on error for BOTH SQLite and Postgres via DbConnection.rollback().
     10. dry_run=True: returns SyncSummary with .plans populated, no writes.
     11. Summary fields: scanned / skipped_blank_sku / skipped_invalid_price /
         unchanged / inserted_new / changed_closed_old / updated_same_day / errors.
    """
    sync_ts = sync_ts or datetime.now()
    today_iso = sync_ts.strftime("%Y-%m-%d")
    yesterday_iso = (sync_ts - timedelta(days=1)).strftime("%Y-%m-%d")
    captured_at = sync_ts.isoformat(timespec="seconds")
    source_label = _source_label(sync_ts)

    summary = SyncSummary(
        source_label=source_label,
        effective_from=today_iso,
        dry_run=dry_run,
    )

    if items_rows is None:
        items_rows = conn.execute(
            "SELECT sku, item_id, rate FROM items"
        ).fetchall()

    active_by_sku = _load_active_zoho_rows(conn, today_iso)

    to_close: list[int] = []
    to_insert: list[tuple[Any, ...]] = []
    to_update_in_place: list[tuple[Any, ...]] = []  # (map_price, source, captured_at, id)

    for row in items_rows:
        summary.scanned += 1
        sku_raw = row["sku"] if not isinstance(row, dict) else row.get("sku")
        sku = str(sku_raw or "").strip().upper()
        if not sku:
            summary.skipped_blank_sku += 1
            continue

        rate_raw = row["rate"] if not isinstance(row, dict) else row.get("rate")
        try:
            price = float(rate_raw) if rate_raw is not None else None
        except (TypeError, ValueError):
            price = None
        if price is None or price <= 0:
            summary.skipped_invalid_price += 1
            summary.plans.append(SyncPlan(
                sku=sku, item_id=None, old_price=None, new_price=0.0,
                effective_from="", action="skipped_invalid",
                reason=f"items.rate={rate_raw!r}",
            ))
            continue

        item_id_raw = row["item_id"] if not isinstance(row, dict) else row.get("item_id")
        item_id = str(item_id_raw).strip() if item_id_raw else None

        active = active_by_sku.get(sku)
        if active is None:
            summary.inserted_new += 1
            summary.plans.append(SyncPlan(
                sku=sku, item_id=item_id, old_price=None, new_price=price,
                effective_from=today_iso, action="insert_new",
                reason="no prior Zoho-sync row",
            ))
            to_insert.append((
                sku, item_id, price, today_iso, FAR_FUTURE,
                source_label, LIVE_SNAPSHOT_MONTH, captured_at,
            ))
        elif abs(active["price"] - price) < 1e-9:
            summary.unchanged += 1
        else:
            # Price changed. Two sub-cases.
            same_day = (active["effective_from"] == today_iso
                        and active["effective_to"] == FAR_FUTURE)
            if same_day:
                # Same-day in-place correction. Overwrite map_price/source/captured_at
                # on the existing row. Audit cost: the prior intra-day price is lost
                # (no closed-window history for it). The trade-off is documented;
                # otherwise an intra-day correction would be invisible until tomorrow.
                summary.updated_same_day += 1
                summary.plans.append(SyncPlan(
                    sku=sku, item_id=item_id,
                    old_price=active["price"], new_price=price,
                    effective_from=today_iso, action="updated_same_day",
                    reason=f"same-day correction {active['price']:.4f} -> {price:.4f}",
                ))
                to_update_in_place.append((price, source_label, captured_at, active["id"]))
            else:
                # Standard SCD-2 close-old + insert-new.
                summary.changed_closed_old += 1
                summary.plans.append(SyncPlan(
                    sku=sku, item_id=item_id,
                    old_price=active["price"], new_price=price,
                    effective_from=today_iso, action="close_old_and_insert",
                    reason=f"price {active['price']:.4f} -> {price:.4f}",
                ))
                to_close.append(active["id"])
                to_insert.append((
                    sku, item_id, price, today_iso, FAR_FUTURE,
                    source_label, LIVE_SNAPSHOT_MONTH, captured_at,
                ))

    if dry_run:
        return summary

    # Atomic: close-old + same-day-update + insert-new in one commit. Rolls back on
    # error for BOTH SQLite and Postgres. The close-old UPDATE re-asserts the
    # source LIKE filter as a SQL-level invariant — accountant rows cannot be touched
    # even if a future refactor accidentally leaks an accountant id into to_close.
    has_work = bool(to_close or to_insert or to_update_in_place)
    if not has_work:
        # No-op sync (steady state). Skip the commit roundtrip entirely so the
        # atomicity narrative stays honest.
        return summary
    try:
        if to_close:
            placeholders = ",".join("?" for _ in to_close)
            close_sql = (
                f"UPDATE price_history SET effective_to=? "
                f"WHERE id IN ({placeholders}) "
                r"AND source LIKE ? ESCAPE '\'"
            )
            conn.execute(close_sql, (yesterday_iso, *to_close, SOURCE_LIKE))
        if to_update_in_place:
            # Per-row UPDATE keyed on id, gated by the source filter (defense-in-depth).
            in_place_sql = (
                "UPDATE price_history SET map_price=?, source=?, captured_at=? "
                r"WHERE id=? AND source LIKE ? ESCAPE '\'"
            )
            for (price, src, cap, row_id) in to_update_in_place:
                conn.execute(in_place_sql, (price, src, cap, row_id, SOURCE_LIKE))
        if to_insert:
            conn.executemany(
                "INSERT INTO price_history "
                "(sku, item_id, map_price, effective_from, effective_to, source, snapshot_month, captured_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                to_insert,
            )
        conn.commit()
    except Exception as exc:
        # Rollback for BOTH backends. SQLite stdlib opens an implicit transaction for
        # DML; without this rollback the half-applied UPDATE close-old would be
        # promoted to durable state by ANY subsequent commit() on the same conn
        # (e.g. repo.finish_sync_run), leaving a SKU's live window dangling.
        try:
            conn.rollback()
        except Exception as rb_exc:
            # Visible failure of the rollback itself — record but still re-raise the
            # original exception so the upstream caller sees the real cause.
            summary.errors.append(
                f"rollback failed after sync error (original={type(exc).__name__}): {rb_exc}"
            )
        summary.errors.append(f"transaction failed: {exc}")
        raise

    return summary
