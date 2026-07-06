#!/usr/bin/env python3
"""
capture_zoho_item_price_snapshot.py

Forward-looking Zoho item price history capture.
Runs 3x daily (8 AM, 2 PM, 8 PM) to build a price history record.

Usage:
  python scripts/capture_zoho_item_price_snapshot.py --dry-run
  python scripts/capture_zoho_item_price_snapshot.py --apply
  python scripts/capture_zoho_item_price_snapshot.py --apply --limit 10
  python scripts/capture_zoho_item_price_snapshot.py --apply --sku FEAGL-48016-G2
  python scripts/capture_zoho_item_price_snapshot.py --dry-run --sku CNT100

Safety:
  - Never deletes records.
  - Does not write into official price_history.
  - Does not mutate accountant_fvprice_* or zoho_catalog_snapshot_* rows.
  - Does not change commission math or invoice automation.
  - Idempotent: safe to re-run; uses lock file to prevent overlapping runs.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dateutil.parser

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO / ".env")

from src.zoho_client import ZohoBooksClient, load_zoho_config, ZohoApiError, ZohoAuthError  # noqa: E402
from src.db.connection import get_connection, using_postgres  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CAPTURE_SOURCE = "zoho_item_price_capture"
LOCK_FILE = REPO / "data" / "locks" / "price_capture.lock"
LOG_DIR = REPO / "data" / "logs" / "price_capture"
# Rates within this absolute delta are treated as equal (avoids float noise)
RATE_TOLERANCE = 0.001
# Default rows per transaction when --apply commits incrementally.
# Keeps each transaction short enough to survive Supabase pooler timeouts.
DEFAULT_BATCH_SIZE = 50

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"{today}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger = logging.getLogger("price_capture")
    logger.setLevel(logging.INFO)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


log = _setup_logging()

# ---------------------------------------------------------------------------
# Schema  (Postgres; CREATE TABLE IF NOT EXISTS = idempotent)
# ---------------------------------------------------------------------------
SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS item_price_snapshot_runs (
    id             BIGSERIAL PRIMARY KEY,
    run_id         TEXT UNIQUE NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL,
    finished_at    TIMESTAMPTZ,
    status         TEXT,
    item_count     INTEGER,
    changed_count  INTEGER,
    error_count    INTEGER,
    notes          TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS item_price_snapshots (
    id                      BIGSERIAL PRIMARY KEY,
    run_id                  TEXT NOT NULL,
    captured_at             TIMESTAMPTZ NOT NULL,
    item_id                 TEXT NOT NULL,
    sku                     TEXT,
    name                    TEXT,
    rate                    NUMERIC(12,2),
    purchase_rate           NUMERIC(12,2),
    pricebook_rate          NUMERIC(12,2),
    status                  TEXT,
    item_type               TEXT,
    product_type            TEXT,
    zoho_last_modified_time TIMESTAMPTZ,
    raw_json                JSONB,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ips_item_captured ON item_price_snapshots(item_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_ips_sku_captured  ON item_price_snapshots(sku, captured_at);
CREATE INDEX IF NOT EXISTS idx_ips_run_id        ON item_price_snapshots(run_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_item_price_snapshots_run_item
  ON item_price_snapshots(run_id, item_id);

CREATE TABLE IF NOT EXISTS item_price_history (
    id                      BIGSERIAL PRIMARY KEY,
    item_id                 TEXT NOT NULL,
    sku                     TEXT,
    name                    TEXT,
    rate                    NUMERIC(12,2) NOT NULL,
    effective_from          TIMESTAMPTZ NOT NULL,
    effective_to            TIMESTAMPTZ,
    first_seen_run_id       TEXT,
    last_seen_run_id        TEXT,
    zoho_last_modified_time TIMESTAMPTZ,
    source                  TEXT DEFAULT 'zoho_item_price_capture',
    raw_json                JSONB,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_iph_item_eff ON item_price_history(item_id, effective_from, effective_to);
CREATE INDEX IF NOT EXISTS idx_iph_sku_eff  ON item_price_history(sku,     effective_from, effective_to);
CREATE INDEX IF NOT EXISTS idx_iph_source   ON item_price_history(source)\
"""

# ---------------------------------------------------------------------------
# Lock management (file-based; prevents overlapping scheduled runs)
# ---------------------------------------------------------------------------
def acquire_lock() -> None:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        pid_str = LOCK_FILE.read_text(encoding="utf-8").strip()
        raise RuntimeError(
            f"Another price capture run appears to be in progress (PID {pid_str}). "
            f"If stale, delete: {LOCK_FILE}"
        )
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")


def release_lock() -> None:
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except Exception as exc:
        log.warning("Failed to release lock: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_zoho_ts(ts_str: str | None) -> datetime | None:
    """Parse Zoho's ISO-ish timestamps (e.g. '2024-01-15T10:30:00+0530') → UTC datetime."""
    if not ts_str:
        return None
    try:
        return dateutil.parser.parse(ts_str).astimezone(timezone.utc)
    except Exception:
        return None


def _safe_rate(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _rates_changed(old_rate: float | None, new_rate: float | None) -> bool:
    """Return True if the two rates are meaningfully different."""
    if old_rate is None and new_rate is None:
        return False
    if old_rate is None or new_rate is None:
        return True
    return abs(old_rate - new_rate) >= RATE_TOLERANCE


def _extract_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw Zoho item dict to our capture fields."""
    return {
        "item_id": str(raw.get("item_id") or "").strip(),
        "sku": (raw.get("sku") or "").strip() or None,
        "name": raw.get("name") or None,
        "rate": _safe_rate(raw.get("rate")),
        "purchase_rate": _safe_rate(raw.get("purchase_rate")),
        # pricebook_rate requires a separate /pricebooks API call; not implemented yet
        "pricebook_rate": None,
        "status": raw.get("status") or None,
        "item_type": raw.get("item_type") or None,
        "product_type": raw.get("product_type") or None,
        "zoho_last_modified_time": _parse_zoho_ts(raw.get("last_modified_time")),
        "_raw": raw,
    }


# ---------------------------------------------------------------------------
# Zoho fetch
# ---------------------------------------------------------------------------
def fetch_all_items(
    client: ZohoBooksClient,
    limit: int | None = None,
    sku_filter: str | None = None,
) -> list[dict]:
    """Fetch active + inactive items from Zoho; return normalized list."""
    items_by_id: dict[str, dict] = {}
    max_retries = 3

    for status_label in ("active", "inactive"):
        filter_val = f"Status.{status_label.capitalize()}"
        log.info("Fetching %s items from Zoho (filter_by=%s) ...", status_label, filter_val)
        attempt = 0
        while True:
            try:
                count_before = len(items_by_id)
                for raw in client.paginate(
                    "items",
                    result_key="items",
                    params={"filter_by": filter_val},
                ):
                    item_id = str(raw.get("item_id") or "").strip()
                    if item_id:
                        items_by_id[item_id] = raw
                log.info(
                    "  %s items fetched: %d new",
                    status_label,
                    len(items_by_id) - count_before,
                )
                break
            except ZohoApiError as exc:
                attempt += 1
                if attempt >= max_retries:
                    log.error("Zoho API error after %d retries for %s: %s", max_retries, status_label, exc)
                    raise
                wait = 2.0 * (2 ** (attempt - 1))
                log.warning(
                    "Zoho API error (attempt %d/%d), retrying in %.0fs: %s",
                    attempt, max_retries, wait, exc,
                )
                time.sleep(wait)

    all_items = [_extract_item(raw) for raw in items_by_id.values()]
    log.info("Total items fetched: %d (active + inactive)", len(all_items))

    if sku_filter:
        sku_up = sku_filter.upper().strip()
        all_items = [it for it in all_items if (it["sku"] or "").upper() == sku_up]
        log.info("After --sku filter (%s): %d items", sku_filter, len(all_items))

    if limit is not None:
        all_items = all_items[:limit]
        log.info("After --limit %d: %d items", limit, len(all_items))

    return all_items


# ---------------------------------------------------------------------------
# DB schema bootstrap
# ---------------------------------------------------------------------------
def ensure_schema(conn) -> None:
    """Create forward-capture tables if not present. Idempotent."""
    log.info("Ensuring forward-capture schema exists ...")
    conn.executescript(SCHEMA_SQL)
    log.info("Schema ready.")


def _table_exists(conn, table_name: str) -> bool:
    """Return True if table is accessible (used to guard dry-run queries)."""
    try:
        conn.execute(f"SELECT 1 FROM {table_name} LIMIT 0")  # noqa: S608
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------
def get_open_history_row(conn, item_id: str) -> dict | None:
    """Return the latest open history row for item_id, or None."""
    cur = conn.execute(
        """
        SELECT id, rate, effective_from, first_seen_run_id
        FROM   item_price_history
        WHERE  item_id = ?
          AND  effective_to IS NULL
        ORDER  BY effective_from DESC
        LIMIT  1
        """,
        (item_id,),
    )
    rows = cur.fetchall()
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Core processing loop  (batched commits + reconnect-on-failure)
# ---------------------------------------------------------------------------
INSERT_SNAPSHOT_SQL = """\
INSERT INTO item_price_snapshots
    (run_id, captured_at, item_id, sku, name, rate, purchase_rate, pricebook_rate,
     status, item_type, product_type, zoho_last_modified_time, raw_json)
VALUES
    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS jsonb))
ON CONFLICT (run_id, item_id) DO NOTHING
"""

INSERT_HISTORY_SQL = """\
INSERT INTO item_price_history
    (item_id, sku, name, rate, effective_from, effective_to,
     first_seen_run_id, last_seen_run_id, zoho_last_modified_time,
     source, raw_json, created_at, updated_at)
VALUES
    (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, CAST(? AS jsonb), ?, ?)
"""

CLOSE_HISTORY_SQL = """\
UPDATE item_price_history
SET    effective_to    = ?,
       last_seen_run_id = ?,
       updated_at       = ?
WHERE  id = ?
"""

TOUCH_HISTORY_SQL = """\
UPDATE item_price_history
SET    last_seen_run_id = ?,
       updated_at       = ?
WHERE  id = ?
"""


def _operational_error_class():
    """Return the psycopg OperationalError class, or a sentinel that matches nothing.

    Dry-run paths never hit the retry branch, so falling back to a benign class
    when psycopg isn't importable is safe.
    """
    try:
        import psycopg
        return psycopg.OperationalError
    except Exception:
        class _NeverRaised(Exception):
            pass
        return _NeverRaised


def _snapshot_totals(totals: dict) -> dict:
    """Return a defensive copy of `totals` for batch-level rollback."""
    return {
        "snapshot_count":     totals["snapshot_count"],
        "changed_count":      totals["changed_count"],
        "unchanged_count":    totals["unchanged_count"],
        "new_item_count":     totals["new_item_count"],
        "error_count":        totals["error_count"],
        "missing_sku_items":  list(totals["missing_sku_items"]),
        "changes":            list(totals["changes"]),
    }


def _restore_totals(totals: dict, saved: dict) -> None:
    totals["snapshot_count"]    = saved["snapshot_count"]
    totals["changed_count"]     = saved["changed_count"]
    totals["unchanged_count"]   = saved["unchanged_count"]
    totals["new_item_count"]    = saved["new_item_count"]
    totals["error_count"]       = saved["error_count"]
    totals["missing_sku_items"] = list(saved["missing_sku_items"])
    totals["changes"]           = list(saved["changes"])


def _write_one_item(
    conn,
    item: dict,
    run_id: str,
    captured_at: datetime,
    dry_run: bool,
    history_available: bool,
    totals: dict,
) -> None:
    """Execute snapshot + history ops for a single item against `conn` (no commit)."""
    item_id = item["item_id"]
    if not item_id:
        totals["error_count"] += 1
        log.warning("Skipping item with no item_id: name=%s", item.get("name"))
        return

    if not item.get("sku"):
        totals["missing_sku_items"].append(item_id)

    new_rate = item["rate"]

    # ---- Snapshot row (idempotent via unique index + ON CONFLICT) --------
    totals["snapshot_count"] += 1
    if not dry_run:
        conn.execute(
            INSERT_SNAPSHOT_SQL,
            (
                run_id,
                captured_at,
                item_id,
                item["sku"],
                item["name"],
                new_rate,
                item["purchase_rate"],
                item["pricebook_rate"],
                item["status"],
                item["item_type"],
                item["product_type"],
                item["zoho_last_modified_time"],
                json.dumps(item["_raw"], default=str),
            ),
        )

    # ---- History tracking ------------------------------------------------
    if new_rate is None:
        log.debug("item %s (%s): no rate, skipping history update", item_id, item.get("sku"))
        return

    if not history_available:
        # Dry-run before first --apply; can't compare without tables.
        totals["new_item_count"] += 1
        totals["changes"].append({
            "action": "would_open_new",
            "item_id": item_id,
            "sku": item["sku"],
            "name": item["name"],
            "old_rate": None,
            "new_rate": new_rate,
        })
        return

    open_row = get_open_history_row(conn, item_id)

    if open_row is None:
        totals["new_item_count"] += 1
        totals["changes"].append({
            "action": "new_item",
            "item_id": item_id,
            "sku": item["sku"],
            "name": item["name"],
            "old_rate": None,
            "new_rate": new_rate,
        })
        if not dry_run:
            conn.execute(
                INSERT_HISTORY_SQL,
                (
                    item_id,
                    item["sku"],
                    item["name"],
                    new_rate,
                    captured_at,
                    run_id,
                    run_id,
                    item["zoho_last_modified_time"],
                    CAPTURE_SOURCE,
                    json.dumps(item["_raw"], default=str),
                    captured_at,
                    captured_at,
                ),
            )
        return

    old_rate = _safe_rate(open_row["rate"])

    if _rates_changed(old_rate, new_rate):
        totals["changed_count"] += 1
        totals["changes"].append({
            "action": "price_changed",
            "item_id": item_id,
            "sku": item["sku"],
            "name": item["name"],
            "old_rate": old_rate,
            "new_rate": new_rate,
        })
        if not dry_run:
            conn.execute(
                CLOSE_HISTORY_SQL,
                (captured_at, run_id, captured_at, open_row["id"]),
            )
            conn.execute(
                INSERT_HISTORY_SQL,
                (
                    item_id,
                    item["sku"],
                    item["name"],
                    new_rate,
                    captured_at,
                    run_id,
                    run_id,
                    item["zoho_last_modified_time"],
                    CAPTURE_SOURCE,
                    json.dumps(item["_raw"], default=str),
                    captured_at,
                    captured_at,
                ),
            )
    else:
        totals["unchanged_count"] += 1
        if not dry_run:
            conn.execute(
                TOUCH_HISTORY_SQL,
                (run_id, captured_at, open_row["id"]),
            )


def process_items(
    conn_holder: list,
    items: list[dict],
    run_id: str,
    captured_at: datetime,
    dry_run: bool,
    history_available: bool,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress: dict | None = None,
) -> dict[str, Any]:
    """
    Process items, committing every `batch_size` items in --apply mode.

    `conn_holder` is a single-element list holding the live DbConnection. The
    function reads `conn_holder[0]` for every DB call, so the retry path can
    replace the connection on transient OperationalError without the caller
    losing track.

    `progress` is a mutable dict updated as each batch successfully commits.
    Lets the caller (main's exception handler) report accurate partial progress
    when --apply dies midway.

    Retry policy (apply-mode only):
      - On psycopg.OperationalError during a batch, rollback (best-effort),
        close the connection, open a fresh one via get_connection(), and replay
        the same batch ONCE.
      - The unique index ux_item_price_snapshots_run_item + ON CONFLICT DO
        NOTHING on snapshot inserts ensures retries never duplicate rows.
      - History reads (`get_open_history_row`) are re-run on retry so the
        replay decisions reflect the post-rollback state.
    """
    if progress is None:
        progress = {}

    totals = {
        "snapshot_count":    0,
        "changed_count":     0,
        "unchanged_count":   0,
        "new_item_count":    0,
        "error_count":       0,
        "missing_sku_items": [],
        "changes":           [],
    }

    def _finalize_summary() -> dict[str, Any]:
        return {
            "snapshot_count":    totals["snapshot_count"],
            "changed_count":     totals["changed_count"],
            "unchanged_count":   totals["unchanged_count"],
            "new_item_count":    totals["new_item_count"],
            "error_count":       totals["error_count"],
            "missing_sku_count": len(totals["missing_sku_items"]),
            "missing_sku_items": list(totals["missing_sku_items"])[:20],
            "changes":           list(totals["changes"]),
        }

    # --- DRY-RUN: in-memory only, no commits / no transactions --------
    if dry_run:
        for item in items:
            _write_one_item(
                conn_holder[0], item, run_id, captured_at,
                dry_run=True, history_available=history_available, totals=totals,
            )
        return _finalize_summary()

    # --- APPLY: batched commits with one reconnect retry per batch ----
    OperationalError = _operational_error_class()
    total = len(items)
    items_done = 0
    batch_size = max(1, int(batch_size))
    batches_total = (total + batch_size - 1) // batch_size

    for batch_idx, batch_start in enumerate(range(0, total, batch_size), start=1):
        batch = items[batch_start:batch_start + batch_size]
        saved = _snapshot_totals(totals)

        last_exc = None
        for attempt in (1, 2):
            try:
                for item in batch:
                    _write_one_item(
                        conn_holder[0], item, run_id, captured_at,
                        dry_run=False, history_available=history_available, totals=totals,
                    )
                conn_holder[0].commit()
                items_done += len(batch)
                progress["snapshot_count"]   = totals["snapshot_count"]
                progress["changed_count"]    = totals["changed_count"]
                progress["new_item_count"]   = totals["new_item_count"]
                progress["error_count"]      = totals["error_count"]
                progress["items_committed"]  = items_done
                progress["batches_committed"] = batch_idx
                log.info(
                    "  batch %d/%d committed (%d/%d items) attempt=%d",
                    batch_idx, batches_total, items_done, total, attempt,
                )
                break
            except OperationalError as exc:
                last_exc = exc
                _restore_totals(totals, saved)
                if attempt >= 2:
                    log.error(
                        "Batch %d/%d failed after 1 retry: %s",
                        batch_idx, batches_total, exc,
                    )
                    raise
                log.warning(
                    "Batch %d/%d failed (attempt %d/2): %s — reconnecting and retrying...",
                    batch_idx, batches_total, attempt, exc,
                )
                try:
                    conn_holder[0].rollback()
                except Exception:
                    pass
                try:
                    conn_holder[0].close()
                except Exception:
                    pass
                conn_holder[0] = get_connection()
            except Exception:
                _restore_totals(totals, saved)
                try:
                    conn_holder[0].rollback()
                except Exception:
                    pass
                raise

    return _finalize_summary()


# ---------------------------------------------------------------------------
# Run record helpers
# ---------------------------------------------------------------------------
def insert_run_record(conn, run_id: str, started_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO item_price_snapshot_runs
            (run_id, started_at, status)
        VALUES (?, ?, 'running')
        """,
        (run_id, started_at),
    )
    conn.commit()


def finalize_run_record(
    conn,
    run_id: str,
    finished_at: datetime,
    status: str,
    item_count: int,
    changed_count: int,
    error_count: int,
    notes: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE item_price_snapshot_runs
        SET    finished_at   = ?,
               status        = ?,
               item_count    = ?,
               changed_count = ?,
               error_count   = ?
        WHERE  run_id = ?
        """,
        (finished_at, status, item_count, changed_count, error_count, run_id),
    )
    if notes is not None:
        conn.execute(
            "UPDATE item_price_snapshot_runs SET notes = ? WHERE run_id = ?",
            (notes, run_id),
        )
    conn.commit()


def mark_run_failed(
    conn,
    run_id: str,
    error_message: str,
    progress: dict | None = None,
) -> None:
    """Mark a run as failed with finished_at = NOW() and an error note.

    Writes accurate partial-progress counters from `progress` (if provided) so
    operators can see exactly how far the apply got before it died. Best-effort:
    swallows secondary exceptions so the original failure can propagate up.
    """
    try:
        try:
            conn.rollback()
        except Exception:
            pass
        progress = progress or {}
        item_count    = progress.get("items_committed")
        changed_count = (progress.get("changed_count") or 0) + (progress.get("new_item_count") or 0)
        error_count   = progress.get("error_count")
        batches       = progress.get("batches_committed")
        note_parts = [f"FAILED: {str(error_message)[:400]}"]
        if batches is not None:
            note_parts.append(f"batches_committed={batches}")
        if item_count is not None:
            note_parts.append(f"items_committed={item_count}")
        notes = " | ".join(note_parts)[:1000]

        conn.execute(
            """
            UPDATE item_price_snapshot_runs
            SET    status        = 'failed',
                   finished_at   = NOW(),
                   item_count    = COALESCE(?, item_count),
                   changed_count = COALESCE(?, changed_count),
                   error_count   = COALESCE(?, error_count),
                   notes         = ?
            WHERE  run_id = ?
              AND  status = 'running'
            """,
            (item_count, changed_count, error_count, notes, run_id),
        )
        conn.commit()
    except Exception as exc:
        log.warning("Failed to mark run %s as failed: %s", run_id, exc)


# ---------------------------------------------------------------------------
# Verification: --verify-latest-run
# ---------------------------------------------------------------------------
def verify_latest_run(conn) -> int:
    """Print diagnostic info for the most recent capture run.

    Read-only: no INSERT / UPDATE / DELETE. Safe to run any time.
    Exit code 0 if the latest run is complete (status='ok' and snapshot count
    matches item_count), 1 otherwise.
    """
    # Check the run table exists at all
    if not _table_exists(conn, "item_price_snapshot_runs"):
        print("item_price_snapshot_runs table does not exist yet. Run --apply once to initialize.")
        return 1

    latest = conn.execute(
        """
        SELECT run_id, status, item_count, changed_count, error_count,
               started_at, finished_at, notes
        FROM   item_price_snapshot_runs
        ORDER  BY started_at DESC
        LIMIT  1
        """
    ).fetchone()

    if latest is None:
        print("No runs found in item_price_snapshot_runs.")
        return 1

    run_id        = latest["run_id"]
    status        = latest["status"]
    item_count    = latest["item_count"]
    changed_count = latest["changed_count"]
    error_count   = latest["error_count"]
    started_at    = latest["started_at"]
    finished_at   = latest["finished_at"]
    notes         = latest["notes"]

    # Count actual snapshot rows persisted for this run
    snap_rows = None
    if _table_exists(conn, "item_price_snapshots"):
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM item_price_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        snap_rows = int(row["n"]) if row else 0

    # Count total open history rows (system-wide, not just this run)
    open_hist_total = None
    if _table_exists(conn, "item_price_history"):
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM item_price_history WHERE effective_to IS NULL"
        ).fetchone()
        open_hist_total = int(row["n"]) if row else 0

    expected = item_count
    if expected is None:
        complete = False
        complete_reason = "item_count is NULL (run never finalized)"
    elif status != "ok":
        complete = False
        complete_reason = f"status={status!r} (expected 'ok')"
    elif snap_rows is None:
        complete = False
        complete_reason = "item_price_snapshots table missing"
    elif snap_rows != expected:
        complete = False
        complete_reason = f"snapshot rows ({snap_rows}) != expected ({expected})"
    else:
        complete = True
        complete_reason = "status=ok and snapshot row count matches item_count"

    print()
    print("=" * 70)
    print("  Latest Price-Capture Run  [VERIFY]")
    print("=" * 70)
    print(f"  run_id                       : {run_id}")
    print(f"  status                       : {status}")
    print(f"  started_at                   : {started_at}")
    print(f"  finished_at                  : {finished_at}")
    print(f"  item_count (expected)        : {item_count}")
    print(f"  changed_count                : {changed_count}")
    print(f"  error_count                  : {error_count}")
    print(f"  snapshot rows for this run   : {snap_rows}")
    print(f"  total open item_price_history: {open_hist_total}")
    print(f"  complete?                    : {'YES' if complete else 'NO'}  ({complete_reason})")
    if notes:
        print(f"  notes                        : {notes}")
    print("=" * 70)
    print()
    return 0 if complete else 1


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------
def _print_summary(
    label: str,
    items: list[dict],
    result: dict,
    run_id: str,
    captured_at: datetime,
    sku_spotlight: list[str],
) -> None:
    print()
    print("=" * 70)
    print(f"  Price Capture Summary  [{label}]")
    print(f"  run_id     : {run_id}")
    print(f"  captured_at: {captured_at.isoformat()}")
    print("=" * 70)
    print(f"  Total items fetched       : {len(items)}")
    print(f"  Snapshot rows (would write): {result['snapshot_count']}")
    print(f"  New items (no prior hist.) : {result['new_item_count']}")
    print(f"  Price changes detected     : {result['changed_count']}")
    print(f"  Prices unchanged           : {result['unchanged_count']}")
    print(f"  Items missing SKU          : {result['missing_sku_count']}")
    print(f"  Errors                     : {result['error_count']}")

    # Spotlight SKUs
    for target_sku in sku_spotlight:
        sku_up = target_sku.upper()
        matching = [it for it in items if (it.get("sku") or "").upper() == sku_up]
        if not matching:
            print(f"\n  SKU {target_sku}: NOT FOUND in Zoho fetch")
            continue
        it = matching[0]
        item_change = next(
            (c for c in result["changes"] if (c.get("sku") or "").upper() == sku_up),
            None,
        )
        print(f"\n  SKU {target_sku}:")
        print(f"    item_id  : {it['item_id']}")
        print(f"    name     : {it['name']}")
        print(f"    rate     : {it['rate']}")
        print(f"    status   : {it['status']}")
        if item_change:
            print(f"    action   : {item_change['action']}")
            print(f"    old_rate : {item_change['old_rate']}")
            print(f"    new_rate : {item_change['new_rate']}")
        else:
            print("    action   : unchanged (no history change)")

    # Sample of changes
    changes = result["changes"]
    if changes:
        print(f"\n  Sample changes (up to 10 of {len(changes)}):")
        for ch in changes[:10]:
            sku_s = ch.get("sku") or ch["item_id"]
            old_r = f"{ch['old_rate']:.4f}" if ch["old_rate"] is not None else "n/a"
            new_r = f"{ch['new_rate']:.4f}" if ch["new_rate"] is not None else "n/a"
            print(f"    [{ch['action']:<20}] {sku_s:<25} {old_r:>10} -> {new_r}")

    print()
    print("  price_history (official)   : NOT WRITTEN (forward-capture only)")
    print("  commission math            : UNCHANGED")
    print("  invoice automation         : UNCHANGED")
    print("=" * 70)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Forward-looking Zoho item price snapshot capture.",
    )
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument("--dry-run", action="store_true", default=True, help="No DB writes (default).")
    mode.add_argument("--apply", action="store_true", help="Write snapshots and history to DB.")
    mode.add_argument(
        "--verify-latest-run",
        action="store_true",
        help="Read-only: print diagnostic info about the most recent capture run and exit.",
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Only process first N items.")
    parser.add_argument("--sku", type=str, default=None, metavar="SKU", help="Only process items with this SKU.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        metavar="N",
        help=(
            f"During --apply, commit every N items (default {DEFAULT_BATCH_SIZE}). "
            "Keeps each transaction short enough to survive Supabase pooler timeouts."
        ),
    )
    args = parser.parse_args()

    # ---- Verification mode (read-only; short-circuit) ---------------------
    if args.verify_latest_run:
        if not using_postgres():
            log.error(
                "DATABASE_URL is not set. This script requires Postgres. "
                "Set DATABASE_URL in .env and retry."
            )
            return 1
        try:
            verify_conn = get_connection()
        except Exception as exc:
            log.error("Could not connect to Postgres: %s", exc)
            return 1
        try:
            return verify_latest_run(verify_conn)
        finally:
            try:
                verify_conn.close()
            except Exception:
                pass

    apply_mode = args.apply
    dry_run = not apply_mode

    log.info("=" * 60)
    log.info(
        "Price capture starting  mode=%s  batch_size=%d",
        "APPLY" if apply_mode else "DRY-RUN",
        args.batch_size,
    )
    log.info("=" * 60)

    # Guard: must be Postgres
    if not using_postgres():
        log.error(
            "DATABASE_URL is not set. This script requires Postgres. "
            "Set DATABASE_URL in .env and retry."
        )
        return 1

    # Validate batch size
    if args.batch_size < 1:
        log.error("--batch-size must be >= 1 (got %d).", args.batch_size)
        return 1

    # Lock (apply mode only)
    if apply_mode:
        try:
            acquire_lock()
        except RuntimeError as exc:
            log.error("Cannot start: %s", exc)
            return 1

    run_id = f"pricecap_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    captured_at = _utcnow()
    exit_code = 0
    conn_holder: list = [None]
    progress: dict = {}

    try:
        # Zoho fetch
        try:
            cfg = load_zoho_config()
            client = ZohoBooksClient(cfg)
        except ZohoAuthError as exc:
            log.error("Zoho auth failed: %s", exc)
            return 1

        items = fetch_all_items(client, limit=args.limit, sku_filter=args.sku)
        if not items:
            log.warning("No items returned from Zoho. Check filters or Zoho connectivity.")
            return 0

        # DB
        conn_holder[0] = get_connection()

        if apply_mode:
            ensure_schema(conn_holder[0])
            insert_run_record(conn_holder[0], run_id, captured_at)

        history_available = _table_exists(conn_holder[0], "item_price_history")
        if dry_run and not history_available:
            log.info(
                "item_price_history table not yet created (first run). "
                "History comparison not available in dry-run mode. "
                "Run --apply once to initialize schema."
            )

        result = process_items(
            conn_holder=conn_holder,
            items=items,
            run_id=run_id,
            captured_at=captured_at,
            dry_run=dry_run,
            history_available=history_available,
            batch_size=args.batch_size,
            progress=progress,
        )

        if apply_mode:
            finalize_run_record(
                conn_holder[0],
                run_id=run_id,
                finished_at=_utcnow(),
                status="ok",
                item_count=len(items),
                changed_count=result["changed_count"] + result["new_item_count"],
                error_count=result["error_count"],
                notes=(
                    f"batch_size={args.batch_size} "
                    f"batches_committed={progress.get('batches_committed', 0)} "
                    f"items_committed={progress.get('items_committed', 0)}"
                ),
            )

        _print_summary(
            label="DRY-RUN (no writes)" if dry_run else "APPLIED",
            items=items,
            result=result,
            run_id=run_id,
            captured_at=captured_at,
            sku_spotlight=["FEAGL-48016-G2", "CNT100"],
        )

        log.info(
            "Done. items=%d snapshots=%d changed=%d new=%d errors=%d",
            len(items),
            result["snapshot_count"],
            result["changed_count"],
            result["new_item_count"],
            result["error_count"],
        )

    except Exception as exc:
        log.exception("Unhandled error during price capture: %s", exc)
        if apply_mode and conn_holder[0] is not None:
            # Try marking the run as failed on the existing connection. If that
            # connection is dead, open a fresh one for the bookkeeping write.
            try:
                mark_run_failed(conn_holder[0], run_id, str(exc), progress=progress)
            except Exception:
                try:
                    fresh = get_connection()
                    mark_run_failed(fresh, run_id, str(exc), progress=progress)
                    try:
                        fresh.close()
                    except Exception:
                        pass
                except Exception as bookkeeping_exc:
                    log.warning(
                        "Could not mark run %s as failed (connection unrecoverable): %s",
                        run_id, bookkeeping_exc,
                    )
        exit_code = 1
    finally:
        if apply_mode:
            release_lock()
        if conn_holder[0] is not None:
            try:
                conn_holder[0].close()
            except Exception:
                pass

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
