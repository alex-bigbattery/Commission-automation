"""
Derive shipment data locally from sales_orders.raw_json (no Zoho call).

The Zoho shipments/packages endpoint is unauthorized, but the SO detail JSON
already carries a `packages` array (carrier, shipment date/status, tracking,
package/shipment numbers). This module extracts that into the `derived_shipments`
table so the commission workbook can populate Shipment Date / Status / Carrier /
Shipping Charge without calling Zoho and without modifying raw Zoho data.

Run:  python -m src.db.shipments_backfill
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.connection import DB_PATH, get_connection, init_database


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _packages_from_so(order: dict) -> list[dict]:
    """Return a list of derived shipment dicts for one sales order."""
    so_id = str(order.get("salesorder_id") or "")
    so_no = str(order.get("salesorder_number") or "")
    so_charge = order.get("shipping_charge")
    try:
        so_charge = float(so_charge or 0)
    except (TypeError, ValueError):
        so_charge = 0.0

    packages = order.get("packages") or []
    out: list[dict] = []
    if isinstance(packages, list) and packages:
        for pkg in packages:
            if not isinstance(pkg, dict):
                continue
            out.append({
                "salesorder_id": so_id,
                "salesorder_number": so_no,
                "package_number": str(pkg.get("package_number") or ""),
                "shipment_number": str(pkg.get("shipment_number") or ""),
                "shipment_date": str(pkg.get("shipment_date") or pkg.get("date") or ""),
                "shipment_status": str(pkg.get("shipment_status") or pkg.get("status") or ""),
                "carrier_name": str(pkg.get("carrier") or pkg.get("delivery_method") or ""),
                "tracking_number": str(pkg.get("tracking_number") or ""),
                "shipping_charge": so_charge,
                "quantity_shipped": float(pkg.get("quantity") or 0) if str(pkg.get("quantity") or "").strip() != "" else 0.0,
                "raw_json": json.dumps(pkg, default=str),
            })
        return out

    # Fallback: no packages array, but the SO header may still show it shipped.
    top_date = str(order.get("shipment_date") or "")
    top_status = str(order.get("shipped_status") or "")
    if top_date or top_status:
        out.append({
            "salesorder_id": so_id,
            "salesorder_number": so_no,
            "package_number": "",
            "shipment_number": "",
            "shipment_date": top_date,
            "shipment_status": top_status,
            "carrier_name": str(order.get("delivery_method") or ""),
            "tracking_number": "",
            "shipping_charge": so_charge,
            "quantity_shipped": float(order.get("quantity_shipped") or 0) if str(order.get("quantity_shipped") or "").strip() != "" else 0.0,
            "raw_json": "",
        })
    return out


def backfill_derived_shipments(db_path: Path | None = None) -> dict:
    """Rebuild derived_shipments from every sales_orders.raw_json. Returns stats."""
    init_database(db_path)
    conn = get_connection(db_path)
    derived_at = _utc_now()
    stats = {"sales_orders_scanned": 0, "sos_with_shipment": 0, "derived_rows": 0}
    try:
        # Full rebuild — idempotent.
        conn.execute("DELETE FROM derived_shipments")
        rows = conn.execute("SELECT raw_json FROM sales_orders").fetchall()
        stats["sales_orders_scanned"] = len(rows)
        for r in rows:
            try:
                order = json.loads(r["raw_json"])
            except Exception:
                continue
            derived = _packages_from_so(order)
            if not derived:
                continue
            stats["sos_with_shipment"] += 1
            for d in derived:
                conn.execute(
                    """
                    INSERT INTO derived_shipments (
                        salesorder_id, salesorder_number, package_number, shipment_number,
                        shipment_date, shipment_status, carrier_name, tracking_number,
                        shipping_charge, quantity_shipped, raw_json, derived_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        d["salesorder_id"], d["salesorder_number"], d["package_number"],
                        d["shipment_number"], d["shipment_date"], d["shipment_status"],
                        d["carrier_name"], d["tracking_number"], d["shipping_charge"],
                        d["quantity_shipped"], d["raw_json"], derived_at,
                    ),
                )
                stats["derived_rows"] += 1
        conn.commit()
    finally:
        conn.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Derive shipments from sales_orders.raw_json (no Zoho).")
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args()
    db = Path(args.db_path) if args.db_path else None
    stats = backfill_derived_shipments(db)
    print("Derived shipments backfill complete (no Zoho call):")
    print(f"  Database: {db or DB_PATH}")
    print(f"  Sales orders scanned:      {stats['sales_orders_scanned']:,}")
    print(f"  SOs with shipment data:    {stats['sos_with_shipment']:,}")
    print(f"  Derived shipment rows:     {stats['derived_rows']:,}")


if __name__ == "__main__":
    main()
