"""Smoke test for read-only price history lookup APIs."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.commission.settings_read import (  # noqa: E402
    get_price_history_for_sku,
    search_price_history,
)
from src.commission.sqlite_to_workbook import (  # noqa: E402
    _reconciliation_values,
    build_salespeople_from_sqlite,
    load_map_from_template,
    load_tiers_from_template,
)
from src.db.connection import get_connection, init_database  # noqa: E402

TEMPLATE = ROOT / "data" / "templates" / "master_template_clean.xlsx"
YEAR, MONTH = 2026, 4


def main() -> None:
    init_database()
    conn = get_connection()
    before = conn.execute("SELECT COUNT(*) AS c FROM price_history").fetchone()["c"]

    search = search_price_history("INV030", limit=10)
    assert search["read_only"] is True
    assert search["count"] >= 1, "expected INV030 in search results"
    sku = search["results"][0]["sku"]
    print(f"search hit: {sku} rows={search['results'][0]['row_count']}")

    detail = get_price_history_for_sku(sku, template_path=TEMPLATE)
    assert detail["read_only"] is True
    assert detail["row_count"] >= 1
    assert all(r["effective_to_display"] in ("Current", r["effective_to"]) for r in detail["rows"])

    acct = [r for r in detail["rows"] if "accountant" in r["source"].lower()]
    zoho = [r for r in detail["rows"] if r["source"].startswith("zoho_sync_")]
    if acct:
        print(f"accountant row: {acct[0]['source']} snap={acct[0]['snapshot_month']}")
    if zoho:
        print(f"zoho row: {zoho[0]['source']} to={zoho[0]['effective_to_display']}")

    after = conn.execute("SELECT COUNT(*) AS c FROM price_history").fetchone()["c"]
    assert before == after, "price_history row count must not change"

    tiers = load_tiers_from_template(TEMPLATE)
    rlp = load_map_from_template(TEMPLATE)
    result = build_salespeople_from_sqlite(YEAR, MONTH, tiers=tiers, rlp_map=rlp, apply_adjustments=True)
    recon = _reconciliation_values(result)
    print(f"April total: {result.kpis.get('total_commission')} check_a={recon['check_a']} check_b={recon['check_b']}")
    assert recon["check_a"] == 0.0 and recon["check_b"] == 0.0

    print("OK: price history lookup verification passed")


if __name__ == "__main__":
    main()
