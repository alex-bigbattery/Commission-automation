"""
Single source of truth for the returned-quantity commission rule.

Both the B2B payable engine (``sqlite_to_workbook``) and the audit/reconciliation
engine (``main.py``) import this so the rule is defined exactly once.

Rule: commission is paid only on quantity kept (invoiced minus returned; fall
back to shipped, then to a provided fallback when SO-line quantities are absent).

Return timing (commission month vs return/RMA date):
  * Fully returned with return date on or before commission month end -> exclude.
  * Fully returned with return date after commission month end -> pay in invoice
    month (``RETURN_AFTER_COMMISSION_MONTH``); claw back in the return month.
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_return_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def commission_month_end(year: int, month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day)


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


@dataclass(frozen=True)
class ReturnTimingResult:
    comm_qty: float
    comm_amount: float
    factor: float
    flags: tuple[str, ...]
    exclude: bool
    return_status: str
    review_reason: str | None = None


def apply_return_timing_rule(
    *,
    invoiced_qty,
    returned_qty,
    shipped_qty=0.0,
    fallback_qty=0.0,
    item_total: float,
    return_date: date | None,
    commission_month_end: date,
) -> ReturnTimingResult:
    """Apply quantity + commission-month timing rules for a single invoice line."""
    comm_qty, factor, ret_status = commissionable_quantity(
        invoiced_qty, returned_qty, shipped_qty, fallback_qty
    )
    invoiced = _f(invoiced_qty)
    shipped = _f(shipped_qty)
    fallback = _f(fallback_qty)
    base_qty = invoiced if invoiced > 0 else (shipped if shipped > 0 else fallback)

    if ret_status != "Fully Returned":
        comm_amount = round(float(item_total or 0) * factor, 2)
        flags: tuple[str, ...] = ()
        if ret_status == "Partially Returned":
            flags = ("PARTIALLY_RETURNED",)
        return ReturnTimingResult(
            comm_qty=comm_qty,
            comm_amount=comm_amount,
            factor=factor,
            flags=flags,
            exclude=False,
            return_status=ret_status,
            review_reason=(
                f"Partial return: {_f(returned_qty):g} returned — commission on {comm_qty:g}"
                if ret_status == "Partially Returned"
                else None
            ),
        )

    # Fully returned — timing decides exclude vs pay-then-clawback.
    if return_date is not None and return_date > commission_month_end:
        full_amount = round(float(item_total or 0), 2)
        return ReturnTimingResult(
            comm_qty=base_qty,
            comm_amount=full_amount,
            factor=1.0,
            flags=("RETURN_AFTER_COMMISSION_MONTH",),
            exclude=False,
            return_status="Return After Period",
            review_reason=(
                f"Return/RMA date {return_date.isoformat()} is after commission month "
                f"(paid in invoice month; clawback expected in return month)"
            ),
        )

    return ReturnTimingResult(
        comm_qty=0.0,
        comm_amount=0.0,
        factor=0.0,
        flags=("FULLY_RETURNED",),
        exclude=True,
        return_status="Fully Returned",
        review_reason="Fully returned -> non-commissionable (excluded)",
    )


def parse_return_metadata_from_order(order: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """(sku_upper) -> {return_date, rma_number} from one SO raw_json payload."""
    sku_by_line_id: dict[str, str] = {}
    for li in order.get("line_items") or []:
        sku = str(li.get("sku") or "").strip().upper()
        line_id = str(li.get("line_item_id") or "")
        if sku and line_id:
            sku_by_line_id[line_id] = sku

    out: dict[str, dict[str, Any]] = {}
    for sr in order.get("salesreturns") or []:
        rma = str(sr.get("salesreturn_number") or "").strip()
        ret_date = parse_return_date(sr.get("date"))
        for sli in sr.get("line_items") or []:
            so_item_id = str(sli.get("salesorder_item_id") or "")
            sku = sku_by_line_id.get(so_item_id, "")
            if not sku:
                sku = str(sli.get("name") or "").strip().upper()
            if not sku:
                continue
            prev = out.get(sku)
            if prev is None or (
                ret_date and (prev.get("_date") is None or ret_date > prev["_date"])
            ):
                out[sku] = {
                    "return_date": ret_date,
                    "rma_number": rma,
                    "_date": ret_date,
                }
    for meta in out.values():
        meta.pop("_date", None)
    return out


def load_return_metadata_map(
    conn: Any,
    so_ids: Iterable[str],
) -> dict[tuple[str, str], dict[str, Any]]:
    """(salesorder_id, sku_upper) -> {return_date, rma_number}."""
    ids = sorted({str(s) for s in so_ids if s})
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not ids:
        return out
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT salesorder_id, raw_json FROM sales_orders WHERE salesorder_id IN ({placeholders})",
        ids,
    ).fetchall()
    for row in rows:
        soid = str(row["salesorder_id"])
        try:
            order = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            continue
        for sku, meta in parse_return_metadata_from_order(order).items():
            out[(soid, sku)] = meta
    return out
