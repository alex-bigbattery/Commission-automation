"""Verify Track A: zoho_catalog_snapshot_* excluded from commission MAP resolution."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv

load_dotenv(REPO / ".env")

from src.commission.sqlite_to_workbook import (
    ZOHO_CATALOG_SNAPSHOT_PREFIX,
    _load_price_history,
    _resolve_map_price,
    _reconciliation_values,
    build_salespeople_from_sqlite,
    load_map_from_template,
    load_tiers_from_template,
)
from src.commission.settings_read import get_price_history_for_sku
from src.db.connection import get_connection, init_database

TPL = REPO / "data" / "templates" / "master_template_clean.xlsx"
YEAR, MONTH = 2026, 4
TEST_SKU = "FEAGL-48016-G2"
EXPECTED_TOTAL = 16034.98
TOLERANCE = 0.02


def main() -> int:
    init_database()
    conn = get_connection()

    ph_before = conn.execute("SELECT COUNT(*) AS c FROM price_history").fetchone()["c"]
    catalog_rows = conn.execute(
        "SELECT COUNT(*) AS c FROM price_history WHERE source LIKE ?",
        (f"{ZOHO_CATALOG_SNAPSHOT_PREFIX}%",),
    ).fetchone()["c"]
    acct_rows = conn.execute(
        "SELECT COUNT(*) AS c FROM price_history WHERE source = 'accountant_fvprice_2026_04'"
    ).fetchone()["c"]

    # Resolver must not load catalog rows (live zoho_sync may remain)
    ph_loaded = _load_price_history(conn)
    raw_catalog_count = conn.execute(
        "SELECT COUNT(*) AS c FROM price_history WHERE source LIKE ?",
        (f"{ZOHO_CATALOG_SNAPSHOT_PREFIX}%",),
    ).fetchone()["c"]
    commission_eligible_count = conn.execute(
        "SELECT COUNT(*) AS c FROM price_history WHERE source NOT LIKE ?",
        (f"{ZOHO_CATALOG_SNAPSHOT_PREFIX}%",),
    ).fetchone()["c"]
    loaded_entry_count = sum(len(v) for v in ph_loaded.values())
    assert raw_catalog_count > 0, "DB should still have catalog rows for audit"
    assert loaded_entry_count == commission_eligible_count, (
        f"Resolver loaded {loaded_entry_count} rows but expected {commission_eligible_count} "
        f"(excluding {raw_catalog_count} catalog rows)"
    )

    rlp = load_map_from_template(TPL)
    item_rates = {
        str(r["sku"]).strip().upper(): float(r["rate"])
        for r in conn.execute("SELECT sku, rate FROM items WHERE sku IS NOT NULL").fetchall()
        if r["sku"]
    }
    fallback = {**item_rates, **rlp}
    map_april = _resolve_map_price(TEST_SKU, date(2026, 4, 15), ph_loaded, fallback)
    assert map_april == 740.0, f"FEAGL April MAP expected 740 R_LP, got {map_april}"
    assert map_april != 650.0, "FEAGL must not use $650 catalog backfill"

    # Price History UI still shows catalog rows
    ui = get_price_history_for_sku(TEST_SKU)
    ui_sources = {r["source"] for r in ui["rows"]}
    assert any(s.startswith(ZOHO_CATALOG_SNAPSHOT_PREFIX) for s in ui_sources), (
        "Price History UI must still show catalog snapshot rows"
    )
    assert any(
        r.get("source_kind") == "zoho_catalog_snapshot" for r in ui["rows"]
    ), "UI source_kind must label catalog backfill"

    tiers = load_tiers_from_template(TPL)
    result = build_salespeople_from_sqlite(YEAR, MONTH, tiers=tiers, rlp_map=rlp)
    recon = _reconciliation_values(result)
    total = recon["total_to_pay"]

    feagl_lines = [
        r for r in result.audit_rows
        if str(r.get("sku", "")).upper() == TEST_SKU
    ]
    feagl_maps = {round(float(r.get("map") or 0), 2) for r in feagl_lines}
    rlp_flag_lines = sum(
        1 for r in result.audit_rows if "RLP_FALLBACK_NO_FVPRICE" in str(r.get("flags") or "")
    )

    ph_after = conn.execute("SELECT COUNT(*) AS c FROM price_history").fetchone()["c"]

    print("=== Track A verification ===")
    print(f"price_history rows: {ph_before} (unchanged: {ph_before == ph_after})")
    print(f"zoho_catalog_snapshot_* rows in DB: {catalog_rows} (audit only)")
    print(f"accountant_fvprice_2026_04 rows: {acct_rows}")
    print(f"total_to_pay: ${total:,.2f} (expected ${EXPECTED_TOTAL:,.2f})")
    print(f"check_a: {recon['check_a']}  check_b: {recon['check_b']}")
    print(f"FEAGL April MAP values in audit: {sorted(feagl_maps)}")
    print(f"RLP_FALLBACK_NO_FVPRICE lines: {rlp_flag_lines}")
    print(f"map_warnings: {result.kpis.get('map_warnings')}")

    errors: list[str] = []
    if abs(total - EXPECTED_TOTAL) > TOLERANCE:
        errors.append(f"total_to_pay {total} != {EXPECTED_TOTAL}")
    if recon["check_a"] != 0 or recon["check_b"] != 0:
        errors.append(f"Check A/B not zero: {recon['check_a']}, {recon['check_b']}")
    if ph_before != ph_after:
        errors.append("price_history row count changed")
    if 650.0 in feagl_maps:
        errors.append("FEAGL still using $650 catalog MAP in April audit")
    if 740.0 not in feagl_maps and feagl_lines:
        errors.append("FEAGL April not using $740 R_LP fallback")
    if acct_rows != 0:
        errors.append("unexpected accountant rows (expected 0 until restore)")
    if not result.kpis.get("map_warnings"):
        errors.append("missing map_warnings in KPIs")
    if "accountant_fvprice_2026_04 is missing" not in " ".join(result.kpis.get("map_warnings") or []):
        errors.append("April missing accountant warning not in map_warnings")

    if errors:
        print("\nFAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\nAll Track A checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
