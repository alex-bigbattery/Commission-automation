"""End-to-end tests for ``build_salespeople_from_sqlite``.

These seed a throwaway SQLite database with the minimum Zoho-shaped rows the
engine reads (a sales order with its line items, an invoice + invoice line, an
item, and an optional shipment), then run the real pipeline and assert on the
resulting commission. They exercise routing, MAP/discount/tier math, AR status,
and the returns-netting rule together — the wiring that the pure-function unit
tests do not cover.
"""
from __future__ import annotations

import json

import pytest

from src.commission.sqlite_to_workbook import (
    DEFAULT_TIERS,
    build_salespeople_from_sqlite,
)
from src.db.connection import get_connection, init_database


RLP = {"WIDGET": 100.0}
YEAR, MONTH = 2026, 3
SYNCED = "2026-03-31T00:00:00"


def _insert_sales_order(conn, *, so_id, so_number, salesperson, line_items, salesreturns=None):
    raw = {"line_items": line_items}
    if salesreturns is not None:
        raw["salesreturns"] = salesreturns
    conn.execute(
        """
        INSERT INTO sales_orders
          (salesorder_id, salesorder_number, order_date, customer_name,
           salesperson_name, delivery_method, raw_json, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (so_id, so_number, f"{YEAR:04d}-{MONTH:02d}-05", "Acme Co",
         salesperson, "Freight", json.dumps(raw), SYNCED),
    )


def _insert_invoice(conn, *, invoice_id, invoice_number, so_number, balance, sales_team):
    raw = {"custom_fields": [{"label": "Sales Team", "value": sales_team}]}
    conn.execute(
        """
        INSERT INTO invoices
          (invoice_id, invoice_number, invoice_date, salesorder_number, status,
           balance, customer_name, salesperson_name, raw_json, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (invoice_id, invoice_number, f"{YEAR:04d}-{MONTH:02d}-10", so_number,
         "paid", balance, "Acme Co", "", json.dumps(raw), SYNCED),
    )


def _insert_invoice_line(conn, *, invoice_id, line_index, sku, qty, rate, item_total):
    conn.execute(
        """
        INSERT INTO invoice_lines
          (invoice_id, line_index, sku, item_name, quantity, rate, item_total,
           raw_json, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (invoice_id, line_index, sku, sku, qty, rate, item_total, "{}", SYNCED),
    )


def _insert_item(conn, *, sku, rate):
    conn.execute(
        "INSERT INTO items (item_id, sku, name, rate, raw_json, synced_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sku, sku, sku, rate, "{}", SYNCED),
    )


def _seed_clean_sale(db_path):
    """One paid B2B product line for a roster rep, no returns, 0% discount."""
    init_database(db_path)
    conn = get_connection(db_path)
    try:
        _insert_item(conn, sku="WIDGET", rate=100.0)
        _insert_sales_order(
            conn,
            so_id="SO1",
            so_number="SO-1",
            salesperson="Paul Perlman",
            line_items=[{
                "sku": "WIDGET", "line_item_id": "L1",
                "quantity": 2, "quantity_invoiced": 2,
                "quantity_shipped": 2, "quantity_returned": 0,
            }],
        )
        _insert_invoice(conn, invoice_id="INV1", invoice_number="INV-1",
                        so_number="SO-1", balance=0.0, sales_team="B2B")
        _insert_invoice_line(conn, invoice_id="INV1", line_index=0,
                             sku="WIDGET", qty=2, rate=100.0, item_total=200.0)
        conn.commit()
    finally:
        conn.close()


def _seed_fully_returned_sale(db_path):
    """One paid B2B line fully returned within the commission month -> excluded."""
    init_database(db_path)
    conn = get_connection(db_path)
    try:
        _insert_item(conn, sku="WIDGET", rate=100.0)
        _insert_sales_order(
            conn,
            so_id="SO1",
            so_number="SO-1",
            salesperson="Paul Perlman",
            line_items=[{
                "sku": "WIDGET", "line_item_id": "L1",
                "quantity": 1, "quantity_invoiced": 1,
                "quantity_shipped": 1, "quantity_returned": 1,
            }],
            salesreturns=[{
                "salesreturn_number": "RMA-1",
                "date": f"{YEAR:04d}-{MONTH:02d}-20",
                "line_items": [{"salesorder_item_id": "L1", "quantity": 1}],
            }],
        )
        _insert_invoice(conn, invoice_id="INV1", invoice_number="INV-1",
                        so_number="SO-1", balance=0.0, sales_team="B2B")
        _insert_invoice_line(conn, invoice_id="INV1", line_index=0,
                             sku="WIDGET", qty=1, rate=100.0, item_total=100.0)
        conn.commit()
    finally:
        conn.close()


def _commissionable_rows(result):
    return [a for a in result.audit_rows if a["block"] == "commissionable"]


def test_clean_b2b_sale_pays_expected_commission(tmp_path):
    db_path = tmp_path / "clean.sqlite"
    _seed_clean_sale(db_path)

    result = build_salespeople_from_sqlite(
        YEAR, MONTH, db_path=db_path,
        tiers=DEFAULT_TIERS, rlp_map=RLP, apply_adjustments=False,
    )

    # 0% discount -> first salaried tier (5%); 200 * 0.05 = 10.00.
    assert result.totals_by_sheet["Paul"] == pytest.approx(10.0)
    assert sum(result.totals_by_sheet.values()) == pytest.approx(10.0)

    rows = _commissionable_rows(result)
    assert len(rows) == 1
    row = rows[0]
    assert row["pending"] is False
    assert row["excluded"] is False
    assert row["map"] == pytest.approx(100.0)
    assert row["final_rate"] == pytest.approx(0.05)
    assert row["final_commission"] == pytest.approx(10.0)
    assert row["qty_commissionable"] == pytest.approx(2.0)


def test_non_b2b_line_is_ignored(tmp_path):
    db_path = tmp_path / "b2c.sqlite"
    init_database(db_path)
    conn = get_connection(db_path)
    try:
        _insert_item(conn, sku="WIDGET", rate=100.0)
        _insert_sales_order(
            conn, so_id="SO1", so_number="SO-1", salesperson="Paul Perlman",
            line_items=[{"sku": "WIDGET", "line_item_id": "L1", "quantity": 1,
                         "quantity_invoiced": 1, "quantity_shipped": 1,
                         "quantity_returned": 0}],
        )
        # Sales Team is B2C -> the engine must skip the line entirely.
        _insert_invoice(conn, invoice_id="INV1", invoice_number="INV-1",
                        so_number="SO-1", balance=0.0, sales_team="B2C-Web")
        _insert_invoice_line(conn, invoice_id="INV1", line_index=0,
                             sku="WIDGET", qty=1, rate=100.0, item_total=100.0)
        conn.commit()
    finally:
        conn.close()

    result = build_salespeople_from_sqlite(
        YEAR, MONTH, db_path=db_path,
        tiers=DEFAULT_TIERS, rlp_map=RLP, apply_adjustments=False,
    )
    assert sum(result.totals_by_sheet.values()) == 0.0
    assert _commissionable_rows(result) == []


def test_map_warnings_when_no_accountant_snapshot(tmp_path):
    # No price_history rows at all -> the line is priced from R_LP fallback, so
    # the engine must flag both the missing official snapshot and the fallback
    # impact (period-agnostic, no hardcoded month).
    db_path = tmp_path / "warn.sqlite"
    _seed_clean_sale(db_path)

    result = build_salespeople_from_sqlite(
        YEAR, MONTH, db_path=db_path,
        tiers=DEFAULT_TIERS, rlp_map=RLP, apply_adjustments=False,
    )
    kpis = result.kpis
    assert kpis["accountant_fvprice_present"] is False
    assert kpis["rlp_fallback_lines"] == 1
    warnings = " ".join(kpis["map_warnings"])
    assert f"{YEAR:04d}-{MONTH:02d}" in warnings        # names this period, not April
    assert any("R_LP fallback" in w for w in kpis["map_warnings"])


def test_fully_returned_line_is_excluded(tmp_path):
    db_path = tmp_path / "returned.sqlite"
    _seed_fully_returned_sale(db_path)

    result = build_salespeople_from_sqlite(
        YEAR, MONTH, db_path=db_path,
        tiers=DEFAULT_TIERS, rlp_map=RLP, apply_adjustments=False,
    )

    assert sum(result.totals_by_sheet.values()) == 0.0
    rows = _commissionable_rows(result)
    assert len(rows) == 1
    row = rows[0]
    assert row["excluded"] is True
    assert row["final_commission"] == 0.0
    assert "FULLY_RETURNED" in row["flags"]
    assert row["return_status"] == "Fully Returned"
