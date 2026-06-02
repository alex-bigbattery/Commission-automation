"""
Single source of truth for the returned-quantity commission rule.

Both the B2B payable engine (``sqlite_to_workbook``) and the audit/reconciliation
engine (``main.py``) import this so the rule is defined exactly once.

Rule: commission is paid only on quantity kept (invoiced minus returned; fall
back to shipped, then to a provided fallback when SO-line quantities are absent).
"""

from __future__ import annotations


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def commissionable_quantity(
    invoiced_qty,
    returned_qty,
    shipped_qty=0.0,
    fallback_qty=0.0,
) -> tuple[float, float, str]:
    """Return (commissionable_qty, factor, return_status).

    * ``factor`` is the share of the line that stays commissionable (0..1) and is
      meant to scale the commissionable amount.
    * ``return_status`` is "" / "Fully Returned" / "Partially Returned".
    """
    invoiced = _f(invoiced_qty)
    returned = _f(returned_qty)
    shipped = _f(shipped_qty)
    fallback = _f(fallback_qty)

    base = invoiced if invoiced > 0 else (shipped if shipped > 0 else fallback)
    comm_qty = max(0.0, base - returned)
    factor = (comm_qty / base) if base > 0 else 1.0

    if returned <= 0:
        status = ""
    elif comm_qty <= 0:
        status = "Fully Returned"
    else:
        status = "Partially Returned"
    return comm_qty, factor, status
