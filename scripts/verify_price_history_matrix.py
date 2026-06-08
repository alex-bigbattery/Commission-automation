"""Read-only verification for price_history matrix API."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.commission.price_history_matrix import (
    export_price_history_file,
    get_price_history_matrix,
)
from src.commission.settings_read import get_price_history_for_sku
from src.db.connection import get_connection

OUT = ROOT / "data" / "exports"
SKU_A = "FEAGL-48016-G2"
SKU_B = "PWS015"


def _price_on(matrix_row: dict, date_iso: str) -> float | None:
    cell = matrix_row.get("prices", {}).get(date_iso)
    return float(cell["map_price"]) if cell else None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    before = conn.execute("SELECT COUNT(*) AS c FROM price_history").fetchone()["c"]
    conn.close()
    print(f"price_history rows (unchanged check): {before}")

    matrix = get_price_history_matrix(
        q=SKU_A,
        from_date="2026-04-01",
        to_date="2026-06-08",
        granularity="daily",
        limit=5,
    )
    row_a = next((r for r in matrix["rows"] if r["sku"] == SKU_A), None)
    print("\n=== A) FEAGL-48016-G2 matrix sample ===")
    if not row_a:
        print(f"FAIL: {SKU_A} not in matrix rows")
        return 1
    checks = [
        ("2026-04-01", 740.0),
        ("2026-04-15", 740.0),
        ("2026-05-15", 740.0),
        ("2026-06-01", None),
        ("2026-06-04", None),
        ("2026-06-05", 650.0),
        ("2026-06-08", 650.0),
    ]
    for d, expected in checks:
        got = _price_on(row_a, d)
        ok = got == expected
        print(f"  {d}: got={got} expected={expected} {'OK' if ok else 'FAIL'}")

    hist_a = get_price_history_for_sku(SKU_A)
    print(f"  history rows for {SKU_A}: {hist_a['row_count']}")
    for r in hist_a["rows"]:
        print(f"    {r['effective_from']} -> {r['effective_to_display']} ${r['map_price']} {r['source_kind']}")

    matrix_b = get_price_history_matrix(q=SKU_B, from_date="2026-03-01", to_date="2026-06-08", granularity="monthly", limit=5)
    row_b = next((r for r in matrix_b["rows"] if r["sku"] == SKU_B), None)
    print("\n=== B) PWS015 monthly matrix ===")
    if row_b:
        for d in matrix_b["dates"]:
            cell = row_b["prices"].get(d)
            print(f"  {d}: {cell['map_price'] if cell else '—'} ({cell['source_type'] if cell else 'no coverage'})")

    xlsx_bytes, xlsx_name, _ = export_price_history_file(
        mode="matrix", fmt="xlsx", q=SKU_A,
        from_date="2026-04-01", to_date="2026-06-08", granularity="monthly",
    )
    xlsx_path = OUT / xlsx_name
    xlsx_path.write_bytes(xlsx_bytes)
    print(f"\n=== Sample Excel ===\n  {xlsx_path}")

    csv_bytes, csv_name, _ = export_price_history_file(
        mode="matrix", fmt="csv", q=SKU_A,
        from_date="2026-04-01", to_date="2026-06-08", granularity="monthly",
    )
    csv_path = OUT / csv_name
    csv_path.write_bytes(csv_bytes)
    print(f"=== Sample CSV ===\n  {csv_path}")
    print(csv_bytes.decode("utf-8-sig")[:600])

    sample_path = OUT / "matrix_api_sample.json"
    sample_path.write_text(json.dumps({
        "from": matrix["from"],
        "to": matrix["to"],
        "granularity": matrix["granularity"],
        "dates": matrix["dates"][:8],
        "row": {k: row_a[k] for k in ("sku", "current_map", "latest_source", "prices", "coverage_gaps")},
    }, indent=2), encoding="utf-8")
    print(f"\n=== API sample JSON ===\n  {sample_path}")

    conn = get_connection()
    after = conn.execute("SELECT COUNT(*) AS c FROM price_history").fetchone()["c"]
    conn.close()
    assert before == after, f"price_history mutated: {before} -> {after}"
    print(f"\nprice_history row count unchanged: {after}")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
