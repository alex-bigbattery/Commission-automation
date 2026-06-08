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
SKU_INV31 = "INV031"
SKU_INV33 = "INV033"
TEMPLATE = ROOT / "data" / "templates" / "master_template_clean.xlsx"


def _price_on(matrix_row: dict, date_iso: str) -> float | None:
    cell = matrix_row.get("prices", {}).get(date_iso)
    return float(cell["map_price"]) if cell else None


def _cell_info(matrix_row: dict, date_iso: str) -> tuple[float | None, str | None]:
    cell = matrix_row.get("prices", {}).get(date_iso)
    if not cell:
        return None, None
    return float(cell["map_price"]), str(cell.get("source") or "")


def _verify_inv_skus(include_fallback: bool) -> bool:
    label = "ON" if include_fallback else "OFF"
    print(f"\n=== C) INV031 / INV033 monthly (fallback {label}) ===")
    matrix = get_price_history_matrix(
        q="INV03",
        from_date="2026-04-01",
        to_date="2026-06-01",
        granularity="monthly",
        include_fallback=include_fallback,
        limit=10,
        template_path=TEMPLATE,
    )
    assert matrix["include_fallback"] is include_fallback, matrix["include_fallback"]
    expectations = {
        SKU_INV31: {
            "2026-04-01": (3199.99, "accountant_fvprice_2026_04"),
            "2026-05-01": (3499.0, "imported_rlp_2026_05"),
            "2026-06-01": (3499.0 if include_fallback else None, "R_LP_template" if include_fallback else None),
        },
        SKU_INV33: {
            "2026-04-01": (1999.99, "accountant_fvprice_2026_04"),
            "2026-05-01": (1798.11, "imported_rlp_2026_05"),
            "2026-06-01": (1999.99 if include_fallback else None, "R_LP_template" if include_fallback else None),
        },
    }
    ok_all = True
    for sku, dates in expectations.items():
        row = next((r for r in matrix["rows"] if r["sku"] == sku), None)
        if not row:
            print(f"  FAIL: {sku} missing from matrix")
            ok_all = False
            continue
        for d, (exp_price, exp_source) in dates.items():
            price, source = _cell_info(row, d)
            price_ok = price == exp_price
            source_ok = (source == exp_source) if exp_source else source is None
            ok = price_ok and source_ok
            ok_all = ok_all and ok
            print(
                f"  {sku} {d}: price={price} source={source or '—'} "
                f"expected price={exp_price} source={exp_source or '—'} {'OK' if ok else 'FAIL'}"
            )
    return ok_all


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

    inv_ok_off = _verify_inv_skus(include_fallback=False)
    inv_ok_on = _verify_inv_skus(include_fallback=True)
    if not inv_ok_off or not inv_ok_on:
        return 1

    xlsx_off, _, _ = export_price_history_file(
        mode="matrix", fmt="xlsx", q="INV03",
        from_date="2026-04-01", to_date="2026-06-01", granularity="monthly",
        include_fallback=False, template_path=TEMPLATE,
    )
    off_path = OUT / "matrix_INV031_INV033_fallback_OFF.xlsx"
    off_path.write_bytes(xlsx_off)
    print(f"\n=== Excel sample (fallback OFF) ===\n  {off_path}")

    xlsx_on, _, _ = export_price_history_file(
        mode="matrix", fmt="xlsx", q="INV03",
        from_date="2026-04-01", to_date="2026-06-01", granularity="monthly",
        include_fallback=True, template_path=TEMPLATE,
    )
    on_path = OUT / "matrix_INV031_INV033_fallback_ON.xlsx"
    on_path.write_bytes(xlsx_on)
    print(f"=== Excel sample (fallback ON) ===\n  {on_path}")

    xlsx_bytes, xlsx_name, _ = export_price_history_file(
        mode="matrix", fmt="xlsx", q=SKU_A,
        from_date="2026-04-01", to_date="2026-06-08", granularity="monthly",
        template_path=TEMPLATE,
    )
    xlsx_path = OUT / xlsx_name
    xlsx_path.write_bytes(xlsx_bytes)
    print(f"\n=== Sample Excel ===\n  {xlsx_path}")

    csv_bytes, csv_name, _ = export_price_history_file(
        mode="matrix", fmt="csv", q=SKU_A,
        from_date="2026-04-01", to_date="2026-06-08", granularity="monthly",
        template_path=TEMPLATE,
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
        "row": {k: row_a[k] for k in ("sku", "current_map", "price_changed", "price_change_label", "price_changed_display", "prices", "coverage_gaps")},
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
