"""Validator for the price_history table.

Runs six independent checks (per the hardening contract). Exits 0 if every check is
clean, non-zero with a per-check summary if anything is off. Safe to run anytime —
no writes, no schema changes.

Checks (all on price_history rows only — never touches R_LP or items):
  1. duplicate rows on (sku, effective_from, snapshot_month, source)
  2. overlapping effective date windows per SKU
  3. null / blank / non-ISO effective_from or effective_to
  4. NULL or <= 0 map_price
  5. SKUs that are not upper-case, contain whitespace, or are empty
  6. effective window falls outside the declared snapshot_month
"""
from __future__ import annotations

import calendar
import re
import sys
from datetime import date as _date, datetime
from pathlib import Path
from typing import Any


_LIVE_SOURCE_RE = re.compile(r"^zoho_sync_\d{14}$")

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.db.connection import get_connection, init_database  # noqa: E402


def _iso(value: Any) -> _date | None:
    if value is None:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _month_bounds(snapshot_month: str | None) -> tuple[_date, _date] | None:
    if not snapshot_month:
        return None
    try:
        y, m = (int(p) for p in str(snapshot_month).split("-", 1))
    except (TypeError, ValueError):
        return None
    last = calendar.monthrange(y, m)[1]
    return _date(y, m, 1), _date(y, m, last)


def main() -> int:
    init_database()
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM price_history").fetchone()["c"]
        rows = conn.execute(
            "SELECT id, sku, map_price, effective_from, effective_to, source, snapshot_month "
            "FROM price_history ORDER BY sku, effective_from, id"
        ).fetchall()
    finally:
        conn.close()

    print(f"price_history rows: {total}")
    print("-" * 70)

    issues: dict[str, list[str]] = {
        "1_dup_identity": [],
        "2_overlap_per_sku": [],
        "3_bad_dates": [],
        "4_bad_price": [],
        "5_bad_sku": [],
        "6_window_outside_month": [],
        "7_closed_month_reload_collision": [],
        "8_live_future_effective_from": [],
        "9_live_source_format": [],
    }
    today = datetime.now().date()

    # 1) duplicate identity (sku, eff_from, snapshot_month, source)
    seen: dict[tuple[str, str, str | None, str | None], int] = {}
    for r in rows:
        k = (str(r["sku"]), str(r["effective_from"]),
             r["snapshot_month"], r["source"])
        if k in seen:
            seen[k] += 1
        else:
            seen[k] = 1
    for k, n in seen.items():
        if n > 1:
            issues["1_dup_identity"].append(
                f"x{n}  sku={k[0]} eff_from={k[1]} snapshot_month={k[2]} source={k[3]}"
            )

    # 3, 4, 5: per-row checks
    parsed: dict[str, list[tuple[int, _date, _date]]] = {}
    for r in rows:
        rid = int(r["id"])
        sku_raw = r["sku"]
        sku = str(sku_raw) if sku_raw is not None else ""
        if not sku or sku != sku.upper() or sku != sku.strip():
            issues["5_bad_sku"].append(f"id={rid} sku={sku_raw!r}")
        try:
            price = float(r["map_price"]) if r["map_price"] is not None else None
        except (TypeError, ValueError):
            price = None
        if price is None or price <= 0:
            issues["4_bad_price"].append(f"id={rid} sku={sku!r} map_price={r['map_price']!r}")
        ef = _iso(r["effective_from"])
        et = _iso(r["effective_to"])
        if ef is None or et is None or ef > et:
            issues["3_bad_dates"].append(
                f"id={rid} sku={sku!r} effective_from={r['effective_from']!r} effective_to={r['effective_to']!r}"
            )
            continue
        # 6) window inside declared snapshot_month
        mb = _month_bounds(r["snapshot_month"])
        if mb is not None:
            mb_from, mb_to = mb
            if ef < mb_from or et > mb_to:
                issues["6_window_outside_month"].append(
                    f"id={rid} sku={sku!r} snapshot_month={r['snapshot_month']} "
                    f"window=[{ef}..{et}] expected within [{mb_from}..{mb_to}]"
                )
        if sku:
            parsed.setdefault(sku, []).append((rid, ef, et))

    # 2) overlap per SKU. Sort by (eff_from, eff_to) and sweep: if the next row's
    #    eff_from falls at-or-before the MAX eff_to seen so far, there's an overlap.
    #    Tracking running_max_to (not just the previous row's eff_to) catches the
    #    3-row containment case A=[Apr 1..30], B=[Apr 5..10], C=[Apr 12..15] where
    #    C is inside A but B is between them after sorting.
    for sku, entries in parsed.items():
        entries.sort(key=lambda x: (x[1], x[2]))
        running_max_to = entries[0][2] if entries else None
        running_id = entries[0][0] if entries else None
        running_from = entries[0][1] if entries else None
        for i in range(1, len(entries)):
            this_id, this_from, this_to = entries[i]
            if this_from <= running_max_to:
                issues["2_overlap_per_sku"].append(
                    f"sku={sku!r} ids=({running_id},{this_id}) "
                    f"[{running_from}..{running_max_to}] overlaps [{this_from}..{this_to}]"
                )
            if this_to > running_max_to:
                running_max_to = this_to
                running_id = this_id
                running_from = this_from

    # 7) closed-month reload collision: same (sku, snapshot_month) with >1 distinct
    #    non-live sources. Surfaces e.g. accountant_fvprice_2026_04 vs
    #    accountant_fvprice_2026_04_v2 colliding on April for the same SKU.
    by_sku_month: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        sm = r["snapshot_month"]
        if sm is None:
            continue
        sm_s = str(sm).strip().lower()
        if sm_s == "live":
            continue
        sku = str(r["sku"] or "").strip()
        src = str(r["source"] or "").strip()
        if not sku or not src:
            continue
        by_sku_month.setdefault((sku, str(sm)), set()).add(src)
    for (sku, sm), sources in by_sku_month.items():
        if len(sources) > 1:
            issues["7_closed_month_reload_collision"].append(
                f"sku={sku!r} snapshot_month={sm} sources={sorted(sources)}"
            )

    # 8) live rows must have effective_from <= today (catches manual post-dating).
    # 9) live rows must have source matching ^zoho_sync_\d{14}$ (catches manual
    #    inserts with degenerate source labels like 'zoho_sync_' or 'manual_test').
    for r in rows:
        sm = r["snapshot_month"]
        if sm is None or str(sm).strip().lower() != "live":
            continue
        rid = int(r["id"])
        sku = str(r["sku"] or "").strip()
        ef = _iso(r["effective_from"])
        if ef is not None and ef > today:
            issues["8_live_future_effective_from"].append(
                f"id={rid} sku={sku!r} effective_from={r['effective_from']} > today={today.isoformat()}"
            )
        src = str(r["source"] or "")
        if not _LIVE_SOURCE_RE.match(src):
            issues["9_live_source_format"].append(
                f"id={rid} sku={sku!r} source={src!r} (expected ^zoho_sync_\\d{{14}}$)"
            )

    exit_code = 0
    labels = {
        "1_dup_identity": "1. duplicate (sku, eff_from, snapshot_month, source)",
        "2_overlap_per_sku": "2. overlapping effective windows per SKU",
        "3_bad_dates": "3. null / blank / non-ISO effective_from or effective_to (or eff_from > eff_to)",
        "4_bad_price": "4. NULL / <= 0 map_price",
        "5_bad_sku": "5. non-upper, whitespace, or empty SKU",
        "6_window_outside_month": "6. window falls outside declared snapshot_month",
        "7_closed_month_reload_collision": "7. closed-month reload collision (same sku + snapshot_month, >1 sources)",
        "8_live_future_effective_from": "8. live row has effective_from > today",
        "9_live_source_format": "9. live row source must match ^zoho_sync_\\d{14}$",
    }
    for key, label in labels.items():
        bad = issues[key]
        status = "OK" if not bad else f"FAIL ({len(bad)} rows)"
        print(f"  [{status}]  {label}")
        for line in bad[:15]:
            print(f"        - {line}")
        if len(bad) > 15:
            print(f"        ... and {len(bad) - 15} more")
        if bad:
            exit_code = 1

    print("-" * 70)
    if exit_code == 0:
        print("RESULT: clean [OK]")
    else:
        print("RESULT: issues found [FAIL]  (do NOT load Sync Zoho hook on top of a dirty table)")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
