"""Unit tests for the pure commission-math helpers.

These functions decide every commission dollar (tier lookup, implied discount,
and the rep/Bruce payout split), so they are pinned with explicit examples.
"""
from __future__ import annotations

import pytest

from src.commission import sqlite_to_workbook as eng
from src.commission.sqlite_to_workbook import (
    DEFAULT_TIERS,
    commission_rate,
    implied_discount,
    _payout_breakdown,
)
from src.commission.roster import ALL_SHEETS_ORDERED, COMPANY_SHEET


# ---- commission_rate (tier lookup) ------------------------------------------


@pytest.mark.parametrize(
    "discount, expected_salaried, expected_non_salaried",
    [
        (0.00, 0.05, 0.10),   # first tier
        (0.04, 0.05, 0.10),   # below 0.05 -> still first tier
        (0.05, 0.04, 0.08),   # exactly on the 0.05 boundary
        (0.07, 0.04, 0.08),
        (0.10, 0.03, 0.06),
        (0.15, 0.02, 0.04),
        (0.20, 0.01, 0.02),
        (0.26, 0.00, 0.00),   # top tier -> zero rate
        (0.55, 0.00, 0.00),   # above the table -> stays at the last tier
    ],
)
def test_commission_rate_tiers(discount, expected_salaried, expected_non_salaried):
    assert commission_rate(discount, "salaried", DEFAULT_TIERS) == expected_salaried
    assert commission_rate(discount, "non_salaried", DEFAULT_TIERS) == expected_non_salaried


def test_commission_rate_boundary_is_inclusive():
    # The tier threshold is reached with a 1e-9 epsilon, so a value a hair under
    # 0.05 still lands on the first tier and exactly 0.05 moves up.
    assert commission_rate(0.04999, "salaried", DEFAULT_TIERS) == 0.05
    assert commission_rate(0.05, "salaried", DEFAULT_TIERS) == 0.04


def test_commission_rate_empty_tiers_is_zero():
    assert commission_rate(0.1, "salaried", []) == 0.0


def test_commission_rate_defaults_to_salaried_column():
    # Any rate_type other than "non_salaried" reads the salaried column.
    assert commission_rate(0.0, "anything-else", DEFAULT_TIERS) == 0.05


# ---- implied_discount -------------------------------------------------------


def test_implied_discount_basic():
    # MAP 100 x qty 1, sold for 80 -> 20% discount.
    assert implied_discount(80.0, 100.0, 1) == pytest.approx(0.20)


def test_implied_discount_quantity_scales_base():
    # MAP 100 x qty 2 = 200 base, sold for 150 -> 25% discount.
    assert implied_discount(150.0, 100.0, 2) == pytest.approx(0.25)


def test_implied_discount_clamped_to_zero_when_over_map():
    # Sold above MAP -> negative discount clamps to 0 (never a negative tier).
    assert implied_discount(120.0, 100.0, 1) == 0.0


def test_implied_discount_clamped_to_one():
    assert implied_discount(0.0, 100.0, 1) == 1.0


@pytest.mark.parametrize("map_price, qty", [(0.0, 1), (100.0, 0), (-5.0, 1)])
def test_implied_discount_zero_base_returns_zero(map_price, qty):
    assert implied_discount(50.0, map_price, qty) == 0.0


# ---- _payout_breakdown (rep + Bruce split) ----------------------------------


def _first_rep_sheet() -> str:
    return next(s for s, _ in ALL_SHEETS_ORDERED if s != COMPANY_SHEET)


def test_payout_breakdown_rep_plus_bruce():
    rep_sheet = _first_rep_sheet()
    totals = {rep_sheet: 1000.0, COMPANY_SHEET: 500.0}

    rep, company, bruce, total = _payout_breakdown(totals)

    assert rep == 1000.0
    assert company == 500.0
    # Bruce = rep_rate * rep + company_rate * company; total = rep + bruce.
    expected_bruce = round(
        1000.0 * eng.BRUCE_REP_RATE + 500.0 * eng.BRUCE_COMPANY_RATE, 2
    )
    assert bruce == expected_bruce
    assert total == round(1000.0 + expected_bruce, 2)


def test_payout_breakdown_company_not_paid_directly():
    # The full Company Acct commission is NOT in the total — only Bruce's slice of
    # it is. So a company-only month pays just company_rate * company.
    rep, company, bruce, total = _payout_breakdown({COMPANY_SHEET: 1000.0})
    assert rep == 0.0
    assert company == 1000.0
    assert bruce == round(1000.0 * eng.BRUCE_COMPANY_RATE, 2)
    assert total == bruce  # rep is zero, so total == bruce


def test_payout_breakdown_empty_is_zero():
    rep, company, bruce, total = _payout_breakdown({})
    assert (rep, company, bruce, total) == (0.0, 0.0, 0.0, 0.0)
