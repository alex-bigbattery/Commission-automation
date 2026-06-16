"""Unit tests for period-correct MAP resolution (``_resolve_map_price``).

This is the most safety-critical helper in the engine: it decides which MAP a
line is priced against, which in turn drives the discount and the commission
tier. The contract under test:

  1. A closed-month snapshot whose window covers the sale date is absolute
     authority — it wins even over a live row with a more recent effective_from.
  2. A live (Zoho-sync) row is used only when no closed-month snapshot covers
     the date.
  3. Inside a bucket the latest effective_from wins (later load on ties).
  4. Window bounds are inclusive on both ends.
  5. When nothing covers the date, fall back to the curated R_LP / items map.
"""
from __future__ import annotations

from datetime import date

from src.commission.sqlite_to_workbook import _resolve_map_price


SKU = "WIDGET"


def test_falls_back_when_no_history():
    price = _resolve_map_price(SKU, date(2026, 4, 10), {}, {SKU: 99.0})
    assert price == 99.0


def test_falls_back_when_history_window_does_not_cover():
    hist = {SKU: [(date(2026, 1, 1), date(2026, 1, 31), 50.0, False)]}
    price = _resolve_map_price(SKU, date(2026, 4, 10), hist, {SKU: 99.0})
    assert price == 99.0


def test_falls_back_when_sale_date_unknown():
    hist = {SKU: [(date(2026, 1, 1), date(2026, 12, 31), 50.0, False)]}
    # No as_of date -> snapshot cannot be applied, use fallback.
    assert _resolve_map_price(SKU, None, hist, {SKU: 99.0}) == 99.0


def test_snapshot_within_window_wins_over_fallback():
    hist = {SKU: [(date(2026, 4, 1), date(2026, 4, 30), 50.0, False)]}
    assert _resolve_map_price(SKU, date(2026, 4, 15), hist, {SKU: 99.0}) == 50.0


def test_window_bounds_inclusive():
    hist = {SKU: [(date(2026, 4, 1), date(2026, 4, 30), 50.0, False)]}
    assert _resolve_map_price(SKU, date(2026, 4, 1), hist, {SKU: 99.0}) == 50.0
    assert _resolve_map_price(SKU, date(2026, 4, 30), hist, {SKU: 99.0}) == 50.0


def test_closed_month_wins_over_live_for_covered_date():
    # Both cover the date; the live row even has a LATER effective_from, but the
    # closed-month snapshot is absolute authority for any date it covers.
    hist = {
        SKU: [
            (date(2026, 4, 1), date(2026, 4, 30), 50.0, False),   # closed month
            (date(2026, 4, 10), date(2099, 12, 31), 75.0, True),  # live, later eff_from
        ]
    }
    assert _resolve_map_price(SKU, date(2026, 4, 20), hist, {SKU: 99.0}) == 50.0


def test_live_used_when_no_closed_month_covers():
    hist = {
        SKU: [
            (date(2026, 1, 1), date(2026, 1, 31), 50.0, False),   # closed, doesn't cover
            (date(2026, 3, 1), date(2099, 12, 31), 75.0, True),   # live, covers April
        ]
    }
    assert _resolve_map_price(SKU, date(2026, 4, 20), hist, {SKU: 99.0}) == 75.0


def test_latest_effective_from_wins_within_closed_bucket():
    # Two closed-month snapshots both cover the date; the later effective_from
    # (a correction) wins.
    hist = {
        SKU: [
            (date(2026, 4, 1), date(2026, 4, 30), 50.0, False),
            (date(2026, 4, 5), date(2026, 4, 30), 60.0, False),
        ]
    }
    assert _resolve_map_price(SKU, date(2026, 4, 20), hist, {SKU: 99.0}) == 60.0


def test_unknown_sku_uses_fallback():
    hist = {SKU: [(date(2026, 4, 1), date(2026, 4, 30), 50.0, False)]}
    assert _resolve_map_price("OTHER", date(2026, 4, 15), hist, {"OTHER": 12.0}) == 12.0


def test_missing_everywhere_is_zero():
    assert _resolve_map_price("GHOST", date(2026, 4, 15), {}, {}) == 0.0
