"""Integration test for expected return clawbacks across months.

Scenario: a B2B line is invoiced and paid in March, then fully returned in April.
Because the return lands AFTER the commission month, the engine pays it in March
(flag ``RETURN_AFTER_COMMISSION_MONTH``) and a clawback is expected in April.
``generate_expected_clawbacks(2026, 4)`` must surface exactly that negative.
"""
from __future__ import annotations

import json

import pytest

from src.commission.return_clawbacks import generate_expected_clawbacks
from src.db.connection import get_connection, init_database


SYNCED = "2026-04-30T00:00:00"


def _seed_sale_returned_next_month(db_path):
    init_database(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO items (item_id, sku, name, rate, raw_json, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("WIDGET", "WIDGET", "WIDGET", 100.0, "{}", SYNCED),
        )
        so_raw = {
            "line_items": [{
                "sku": "WIDGET", "line_item_id": "L1",
                "quantity": 1, "quantity_invoiced": 1,
                "quantity_shipped": 1, "quantity_returned": 1,
            }],
            "salesreturns": [{
                "salesreturn_number": "RMA-9",
                "date": "2026-04-15",   # return lands in April, after the March sale
                "line_items": [{"salesorder_item_id": "L1", "quantity": 1}],
            }],
        }
        conn.execute(
            """
            INSERT INTO sales_orders
              (salesorder_id, salesorder_number, order_date, customer_name,
               salesperson_name, delivery_method, raw_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("SO9", "SO-9", "2026-03-05", "Acme Co", "Paul Perlman",
             "Freight", json.dumps(so_raw), SYNCED),
        )
        inv_raw = {"custom_fields": [{"label": "Sales Team", "value": "B2B"}]}
        conn.execute(
            """
            INSERT INTO invoices
              (invoice_id, invoice_number, invoice_date, salesorder_number, status,
               balance, customer_name, salesperson_name, raw_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("INV9", "INV-9", "2026-03-10", "SO-9", "paid", 0.0, "Acme Co", "",
             json.dumps(inv_raw), SYNCED),
        )
        conn.execute(
            """
            INSERT INTO invoice_lines
              (invoice_id, line_index, sku, item_name, quantity, rate, item_total,
               raw_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("INV9", 0, "WIDGET", "WIDGET", 1, 100.0, 100.0, "{}", SYNCED),
        )
        conn.commit()
    finally:
        conn.close()


def test_expected_clawback_in_return_month(tmp_path):
    db_path = tmp_path / "clawback.sqlite"
    _seed_sale_returned_next_month(db_path)

    # template_path=None -> DEFAULT_TIERS + MAP from items.rate (100).
    clawbacks, returns_in_month = generate_expected_clawbacks(
        2026, 4, template_path=None, db_path=db_path
    )

    assert len(returns_in_month) == 1
    assert len(clawbacks) == 1
    cb = clawbacks[0]
    assert cb["invoice_month"] == "2026-03"
    assert cb["clawback_month"] == "2026-04"
    assert cb["sku"] == "WIDGET"
    assert cb["rma_number"] == "RMA-9"
    # 0% discount -> 5% salaried tier; 100 * 0.05 = 5.00 paid in March.
    assert cb["original_commission"] == pytest.approx(5.0)
    assert cb["expected_clawback"] == pytest.approx(-5.0)


def test_no_clawback_when_no_returns(tmp_path):
    """A month with no returns yields no clawbacks (and does not error)."""
    db_path = tmp_path / "empty.sqlite"
    init_database(db_path)
    clawbacks, returns_in_month = generate_expected_clawbacks(
        2026, 4, template_path=None, db_path=db_path
    )
    assert clawbacks == []
    assert returns_in_month == []
