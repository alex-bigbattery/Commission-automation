"""Smoke test for the Zoho price_history sync hook.

Three scenarios, all using a SENTINEL SKU that does NOT exist in items (so it can
never collide with real catalog data). The test cleans up after itself via a
try/finally that DELETEs every row whose source matches the sentinel or whose SKU
is the sentinel.

1. close_old_and_insert: pre-seed an active live row with effective_from = yesterday
   so the hook will close it (eff_to = yesterday) and insert a new row for today.
2. updated_same_day: pre-seed an active live row with effective_from = today, then
   feed a different price — should UPDATE the row in place, no second row inserted.
3. rollback: monkeypatch conn.executemany to raise mid-INSERT; verify the close-old
   UPDATE is rolled back so the active row's effective_to is restored to 9999-12-31.

Verifies the post-state with direct SELECTs and prints PASS/FAIL per check. Exits 0
only when ALL checks pass.

Note: this script writes to the same DB the commission engine reads from. It uses a
sentinel SKU prefix (__SMOKE_TEST__) so it CANNOT touch real price_history rows
(accountant or zoho_sync). The cleanup runs even on failure.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.db.connection import get_connection, init_database  # noqa: E402
from src.db.zoho_price_history_sync import (  # noqa: E402
    FAR_FUTURE,
    LIVE_SNAPSHOT_MONTH,
    sync_zoho_prices_to_history,
)

SENTINEL = "__SMOKE_TEST_SKU__"
SENTINEL_SOURCE_PREFIX = "zoho_sync_20000101"  # matches the tight LIKE pattern


def _cleanup(conn) -> None:
    conn.execute(
        "DELETE FROM price_history WHERE sku=? OR source LIKE ?",
        (SENTINEL, f"{SENTINEL_SOURCE_PREFIX}%"),
    )
    conn.commit()


def _count(conn, sql: str, params: tuple) -> int:
    return int(conn.execute(sql, params).fetchone()["c"])


def _row(conn, sql: str, params: tuple):
    return conn.execute(sql, params).fetchone()


def _assert(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'OK' if cond else 'FAIL'}] {label}{('  -- ' + detail) if detail else ''}")
    return cond


def test_close_old_and_insert(conn) -> bool:
    """Pre-seed an active live row with effective_from=yesterday and price=$100.
    Feed items_rows with the same SKU at $120. Expect close-old + insert-new."""
    print("\n=== 1) close_old_and_insert ===")
    _cleanup(conn)
    today = datetime(2026, 6, 10)  # synthetic clock for predictable dates
    yesterday = today - timedelta(days=1)
    sentinel_source = f"{SENTINEL_SOURCE_PREFIX}010001"  # pre-existing live row
    conn.execute(
        "INSERT INTO price_history (sku, item_id, map_price, effective_from, "
        "effective_to, source, snapshot_month, captured_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (SENTINEL, "smoke-1", 100.0,
         yesterday.strftime("%Y-%m-%d"), FAR_FUTURE,
         sentinel_source, LIVE_SNAPSHOT_MONTH, yesterday.isoformat()),
    )
    conn.commit()

    summary = sync_zoho_prices_to_history(
        conn, dry_run=False, sync_ts=today,
        items_rows=[{"sku": SENTINEL, "item_id": "smoke-1", "rate": 120.0}],
    )

    all_ok = True
    all_ok &= _assert(
        "summary.changed_closed_old == 1",
        summary.changed_closed_old == 1,
        f"got {summary.changed_closed_old}",
    )
    all_ok &= _assert(
        "summary.inserted_new == 0",
        summary.inserted_new == 0,
        f"got {summary.inserted_new}",
    )
    all_ok &= _assert(
        "summary.updated_same_day == 0",
        summary.updated_same_day == 0,
        f"got {summary.updated_same_day}",
    )

    # The old row should now have effective_to = yesterday (closed)
    old = _row(
        conn,
        "SELECT effective_to, map_price FROM price_history WHERE sku=? AND source=?",
        (SENTINEL, sentinel_source),
    )
    expected_close = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    all_ok &= _assert(
        f"old row effective_to == {expected_close}",
        old is not None and old["effective_to"] == expected_close,
        f"got {old['effective_to'] if old else 'None'}",
    )
    all_ok &= _assert(
        "old row map_price unchanged at 100.0",
        old is not None and abs(float(old["map_price"]) - 100.0) < 1e-9,
    )

    # The new row should exist with map_price=120, effective_to=9999-12-31
    new = _row(
        conn,
        "SELECT effective_from, effective_to, map_price, source, snapshot_month "
        "FROM price_history WHERE sku=? AND effective_to=? "
        "ORDER BY id DESC",
        (SENTINEL, FAR_FUTURE),
    )
    all_ok &= _assert(
        "new row exists with effective_to=9999-12-31",
        new is not None,
    )
    if new is not None:
        all_ok &= _assert(
            f"new row effective_from == today ({today.strftime('%Y-%m-%d')})",
            new["effective_from"] == today.strftime("%Y-%m-%d"),
            f"got {new['effective_from']}",
        )
        all_ok &= _assert(
            "new row map_price == 120.0",
            abs(float(new["map_price"]) - 120.0) < 1e-9,
        )
        all_ok &= _assert(
            "new row snapshot_month == 'live'",
            str(new["snapshot_month"]).lower() == "live",
        )
        all_ok &= _assert(
            "new row source matches zoho_sync_<14digits>",
            new["source"].startswith("zoho_sync_") and len(new["source"]) == len("zoho_sync_") + 14,
            f"got {new['source']!r}",
        )

    return all_ok


def test_updated_same_day(conn) -> bool:
    """Pre-seed an active live row at effective_from=today, then feed a new price.
    Expect UPDATE in place — same row id, new price, no second row."""
    print("\n=== 2) updated_same_day ===")
    _cleanup(conn)
    today = datetime(2026, 6, 11)
    sentinel_source = f"{SENTINEL_SOURCE_PREFIX}020001"
    cur = conn.execute(
        "INSERT INTO price_history (sku, item_id, map_price, effective_from, "
        "effective_to, source, snapshot_month, captured_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (SENTINEL, "smoke-2", 200.0,
         today.strftime("%Y-%m-%d"), FAR_FUTURE,
         sentinel_source, LIVE_SNAPSHOT_MONTH, today.isoformat()),
    )
    seed_id = cur._lastrowid if hasattr(cur, "_lastrowid") and cur._lastrowid else None
    if seed_id is None:
        seed_row = _row(conn, "SELECT id FROM price_history WHERE source=?",
                        (sentinel_source,))
        seed_id = int(seed_row["id"]) if seed_row else None
    conn.commit()

    summary = sync_zoho_prices_to_history(
        conn, dry_run=False, sync_ts=today,
        items_rows=[{"sku": SENTINEL, "item_id": "smoke-2", "rate": 250.0}],
    )

    all_ok = True
    all_ok &= _assert(
        "summary.updated_same_day == 1",
        summary.updated_same_day == 1,
        f"got {summary.updated_same_day}",
    )
    all_ok &= _assert(
        "summary.changed_closed_old == 0",
        summary.changed_closed_old == 0,
    )
    all_ok &= _assert(
        "summary.inserted_new == 0",
        summary.inserted_new == 0,
    )

    # The SAME row id should now have the new price; only ONE row for this SKU
    n_rows = _count(conn, "SELECT COUNT(*) AS c FROM price_history WHERE sku=?", (SENTINEL,))
    all_ok &= _assert(
        "exactly 1 row for sentinel SKU after same-day update",
        n_rows == 1,
        f"got {n_rows}",
    )
    updated = _row(conn, "SELECT id, map_price, source, effective_to FROM price_history WHERE sku=?",
                   (SENTINEL,))
    if updated is not None:
        all_ok &= _assert(
            "same row id as seed",
            seed_id is None or int(updated["id"]) == seed_id,
            f"seed_id={seed_id}, after={updated['id']}",
        )
        all_ok &= _assert(
            "map_price == 250.0",
            abs(float(updated["map_price"]) - 250.0) < 1e-9,
        )
        all_ok &= _assert(
            "source updated to new zoho_sync_<14digit>",
            updated["source"] != sentinel_source
            and updated["source"].startswith("zoho_sync_")
            and len(updated["source"]) == len("zoho_sync_") + 14,
        )
        all_ok &= _assert(
            f"effective_to still {FAR_FUTURE}",
            updated["effective_to"] == FAR_FUTURE,
        )
    return all_ok


def test_rollback_on_insert_failure(conn) -> bool:
    """Pre-seed an active live row at yesterday. Force the executemany INSERT to
    raise by monkeypatching the DbConnection. Verify the close-old UPDATE is rolled
    back so effective_to is restored to 9999-12-31 and no new row was inserted."""
    print("\n=== 3) rollback on insert failure ===")
    _cleanup(conn)
    today = datetime(2026, 6, 12)
    yesterday = today - timedelta(days=1)
    sentinel_source = f"{SENTINEL_SOURCE_PREFIX}030001"
    conn.execute(
        "INSERT INTO price_history (sku, item_id, map_price, effective_from, "
        "effective_to, source, snapshot_month, captured_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (SENTINEL, "smoke-3", 300.0,
         yesterday.strftime("%Y-%m-%d"), FAR_FUTURE,
         sentinel_source, LIVE_SNAPSHOT_MONTH, yesterday.isoformat()),
    )
    conn.commit()

    pre_n = _count(conn, "SELECT COUNT(*) AS c FROM price_history WHERE sku=?", (SENTINEL,))
    pre_old = _row(
        conn,
        "SELECT effective_to FROM price_history WHERE source=?",
        (sentinel_source,),
    )

    # Monkey-patch executemany on this DbConnection to raise. The close-old UPDATE
    # runs via conn.execute (not executemany), so it will succeed first; then the
    # bulk INSERT via executemany hits the patched method and raises. We expect
    # the hook's except handler to call conn.rollback().
    orig_executemany = conn.executemany
    raised: list[str] = []

    def boom(*args, **kwargs):
        raised.append("yes")
        raise RuntimeError("smoke-test forced INSERT failure")

    conn.executemany = boom  # type: ignore[assignment]
    try:
        try:
            sync_zoho_prices_to_history(
                conn, dry_run=False, sync_ts=today,
                items_rows=[{"sku": SENTINEL, "item_id": "smoke-3", "rate": 333.0}],
            )
            print("  [FAIL] hook did NOT raise -- monkeypatch may have missed")
            raised_seen = False
        except RuntimeError as exc:
            raised_seen = "smoke-test forced INSERT failure" in str(exc)
    finally:
        conn.executemany = orig_executemany  # type: ignore[assignment]

    all_ok = True
    all_ok &= _assert("monkeypatch fired", bool(raised))
    all_ok &= _assert("hook raised the forced error", raised_seen)

    # Verify post-state: row count unchanged, old row's effective_to restored.
    post_n = _count(conn, "SELECT COUNT(*) AS c FROM price_history WHERE sku=?", (SENTINEL,))
    all_ok &= _assert(
        "row count unchanged after rollback",
        post_n == pre_n,
        f"pre={pre_n} post={post_n}",
    )
    post_old = _row(
        conn,
        "SELECT effective_to FROM price_history WHERE source=?",
        (sentinel_source,),
    )
    all_ok &= _assert(
        f"old row effective_to ROLLED BACK to {FAR_FUTURE} (was momentarily {today - timedelta(days=1)})",
        post_old is not None and post_old["effective_to"] == FAR_FUTURE,
        f"got {post_old['effective_to'] if post_old else 'None'}",
    )
    return all_ok


def main() -> int:
    init_database()
    conn = get_connection()
    overall = True
    try:
        overall &= test_close_old_and_insert(conn)
        overall &= test_updated_same_day(conn)
        overall &= test_rollback_on_insert_failure(conn)
    finally:
        _cleanup(conn)
        conn.close()
    print("\n" + "=" * 60)
    print("SMOKE TEST RESULT:", "ALL PASS" if overall else "FAIL")
    print("=" * 60)
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
