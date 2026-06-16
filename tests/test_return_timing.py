"""Unit tests for return timing commission rules."""
from __future__ import annotations

from datetime import date

from src.commission.returns import apply_return_timing_rule, commission_month_end


APRIL_END = commission_month_end(2026, 4)


def test_fully_returned_in_month_excluded():
    result = apply_return_timing_rule(
        invoiced_qty=1,
        returned_qty=1,
        shipped_qty=1,
        fallback_qty=1,
        item_total=100.0,
        return_date=date(2026, 4, 14),
        commission_month_end=APRIL_END,
    )
    assert result.flags == ("FULLY_RETURNED",)
    assert result.exclude is True
    assert result.comm_amount == 0.0
    assert result.return_status == "Fully Returned"


def test_fully_returned_after_month_paid_with_flag():
    result = apply_return_timing_rule(
        invoiced_qty=1,
        returned_qty=1,
        shipped_qty=1,
        fallback_qty=1,
        item_total=2550.19,
        return_date=date(2026, 5, 21),
        commission_month_end=APRIL_END,
    )
    assert result.flags == ("RETURN_AFTER_COMMISSION_MONTH",)
    assert result.exclude is False
    assert result.comm_amount == 2550.19
    assert result.comm_qty == 1.0
    assert result.factor == 1.0
    assert result.return_status == "Return After Period"


def test_fully_returned_missing_date_conservative_exclude():
    result = apply_return_timing_rule(
        invoiced_qty=1,
        returned_qty=1,
        item_total=50.0,
        return_date=None,
        commission_month_end=APRIL_END,
    )
    assert result.flags == ("FULLY_RETURNED",)
    assert result.exclude is True


def test_partial_return_unchanged():
    result = apply_return_timing_rule(
        invoiced_qty=2,
        returned_qty=1,
        item_total=200.0,
        return_date=date(2026, 5, 21),
        commission_month_end=APRIL_END,
    )
    assert result.flags == ("PARTIALLY_RETURNED",)
    assert result.exclude is False
    assert result.comm_amount == 100.0


def test_no_return_unchanged():
    result = apply_return_timing_rule(
        invoiced_qty=1,
        returned_qty=0,
        item_total=75.0,
        return_date=None,
        commission_month_end=APRIL_END,
    )
    assert result.flags == ()
    assert result.exclude is False
    assert result.comm_amount == 75.0
