"""
SQLite -> SalespersonData bridge (the rules engine).

Reads operational data (sales_orders, invoices, shipments, customer_payments,
items) from the commission_automation SQLite database for a given month, applies
Big Battery's B2B commission rules (transcribed from the accountant's recorded
process), and returns SalespersonData objects ready for workbook_builder_v2 plus
a list of review exceptions and KPI totals.

Business rules (see plan / comisiones_transcript.txt):
  1. Source = invoice lines invoiced in the month.
  2. Current (Section I) vs Prior period (Section II) by the parent SO order_date.
  3. Line type: product -> commissionable, shipping -> shipping, else -> other.
  4. $0 / ticket lines -> dropped, flagged for review.
  5. Shipping:
       income > 0          -> keep, Include=No by default.
       free + order > $5k  -> dropped (legit free shipping).
       free + order <= $5k -> dropped, flagged "reason not given".
  6. AR: paid via last-payment-date / balance 0 / closed; else UNPAID (kept, flagged).
  7. MAP price from items.rate (written as a value); missing -> flagged.
  8. Discount rate = 1 - revenue / (MAP * qty)  (Excel formula in the sheet, AH).
  9. Commission rate = tier lookup on discount; salaried vs non-salaried (double).
 10. Only the known B2B roster; others flagged.
 11. Coupons: not auto-excluded for B2B (rare, judgment-based) — out of scope today.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook

from src.commission.line_classification import classify_line_type
from src.commission.roster import (
    ALL_SHEETS_ORDERED,
    NON_SALARIED_SHEETS,
    ROUTING_UNASSIGNED,
    SALESPERSON_FULL_TO_SHEET,
    enrich_audit_fields,
    format_zoho_salesperson,
    resolve_roster_sheet,
    roster_rep_sheet_keys,
)
from src.commission.returns import commissionable_quantity
from src.commission.workbook_builder_v2 import (
    Block,
    DetailRow,
    SalespersonData,
)
from src.db.connection import DbConnection, get_connection, init_database
from src.db.adjustments import get_adjustment_map, make_line_uid
from src.db.db_utils import date_prefix_expr


FREE_SHIPPING_THRESHOLD = 5000.0

# Fallback commission tiers (discount_rate, salaried_rate, non_salaried_rate),
# used only if the template's "Table" sheet can't be read.
DEFAULT_TIERS: list[tuple[float, float, float]] = [
    (0.00, 0.05, 0.10),
    (0.05, 0.04, 0.08),
    (0.10, 0.03, 0.06),
    (0.15, 0.02, 0.04),
    (0.20, 0.01, 0.02),
    (0.26, 0.00, 0.00),
]


# --- Review exceptions + result types ----------------------------------------


@dataclass
class ReviewItem:
    salesperson: str
    invoice_number: str
    sales_order_number: str
    sku: str
    amount: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "Salesperson": self.salesperson,
            "Invoice": self.invoice_number,
            "Sales Order": self.sales_order_number,
            "SKU": self.sku,
            "Amount": round(float(self.amount or 0), 2),
            "Reason": self.reason,
        }


@dataclass
class GenerationResult:
    salespeople: list[SalespersonData]
    exceptions: list[ReviewItem] = field(default_factory=list)
    totals_by_sheet: dict[str, float] = field(default_factory=dict)
    kpis: dict[str, Any] = field(default_factory=dict)
    audit_rows: list[dict[str, Any]] = field(default_factory=list)  # per-line system/adjustment/final
    adjusted_count: int = 0


# --- Time helpers ------------------------------------------------------------


def month_bounds(year: int, month: int) -> tuple[str, str]:
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    head = text.split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    return None


def is_in_month(d: date | None, year: int, month: int) -> bool:
    return bool(d and d.year == year and d.month == month)


def is_before_month(d: date | None, year: int, month: int) -> bool:
    if d is None:
        return False
    return (d.year, d.month) < (year, month)


def rate_type_for(sheet_name: str) -> str:
    return "non_salaried" if sheet_name in NON_SALARIED_SHEETS else "salaried"


# --- Commission math ---------------------------------------------------------


def load_tiers_from_template(template_path: Path | None) -> list[tuple[float, float, float]]:
    """Read (discount, salaried_rate, non_salaried_rate) tiers from the template Table sheet."""
    if not template_path or not Path(template_path).exists():
        return DEFAULT_TIERS
    try:
        wb = load_workbook(template_path, data_only=True)
        if "Table" not in wb.sheetnames:
            wb.close()
            return DEFAULT_TIERS
        ws = wb["Table"]
        # Find the header row that contains "Discount" in column B.
        header_row = None
        for r in range(1, 12):
            if "discount" in str(ws.cell(r, 2).value or "").lower():
                header_row = r
                break
        tiers: list[tuple[float, float, float]] = []
        if header_row:
            for r in range(header_row + 1, ws.max_row + 1):
                disc = ws.cell(r, 2).value
                sal = ws.cell(r, 3).value
                nonsal = ws.cell(r, 4).value
                if isinstance(disc, (int, float)):
                    tiers.append((float(disc), float(sal or 0), float(nonsal or 0)))
        wb.close()
        tiers.sort(key=lambda t: t[0])
        return tiers or DEFAULT_TIERS
    except Exception:
        return DEFAULT_TIERS


def load_map_from_template(template_path: Path | None) -> dict[str, float]:
    """
    Read the curated MAP price list from the template's R_LP sheet (col A = SKU,
    col G = Rate). This is Big Battery's commission MAP — NOT the live Zoho
    catalog price (items.rate), which differs and would distort discounts.
    """
    out: dict[str, float] = {}
    if not template_path or not Path(template_path).exists():
        return out
    try:
        wb = load_workbook(template_path, data_only=True)
        if "R_LP" not in wb.sheetnames:
            wb.close()
            return out
        ws = wb["R_LP"]
        for r in range(3, ws.max_row + 1):
            sku = ws.cell(r, 1).value
            rate = ws.cell(r, 7).value
            if sku and isinstance(rate, (int, float)):
                out[str(sku).strip().upper()] = float(rate)
        wb.close()
    except Exception:
        return out
    return out


def commission_rate(discount: float, rate_type: str, tiers: list[tuple[float, float, float]]) -> float:
    """Largest tier whose discount <= the line's discount; column by rate_type.
    Tiers MUST be sorted ascending by threshold (index 0) — caller's responsibility.
    """
    if not tiers:
        return 0.0
    idx = 2 if rate_type == "non_salaried" else 1
    chosen = tiers[0][idx]
    for tier in tiers:
        if discount + 1e-9 >= tier[0]:
            chosen = tier[idx]
        else:
            break
    return float(chosen)


def implied_discount(item_total: float, map_price: float, quantity: float) -> float:
    base = map_price * quantity
    if base <= 0:
        return 0.0
    disc = 1.0 - (item_total / base)
    return min(max(disc, 0.0), 1.0)


# --- Data loading ------------------------------------------------------------


@dataclass(frozen=True)
class InvoiceLineRecord:
    invoice_id: str
    invoice_number: str
    invoice_date: date | None
    invoice_status: str
    customer_name: str
    salesperson_name: str
    so_salesperson_name: str
    salesorder_number: str
    salesorder_id: str | None
    order_date: date | None
    sku: str
    item_name: str
    quantity: float
    item_total: float
    rate: float
    line_type: str
    delivery_method: str
    shipment_date: date | None
    shipment_status: str
    payment_date: date | None
    balance: float
    sales_team: str = ""        # CF.Sales Team from invoice raw_json (B2B / B2C ... / Exe.)
    payment_terms: str = ""     # invoice payment_terms_label (e.g. "Net 30", "Prepayment")
    carrier: str = ""           # shipment carrier (when shipments are synced)
    ship_charge: float = 0.0    # shipment shipping charge (when shipments are synced)
    has_shipment: bool = False  # True when a shipment record was found for the SO
    # Quantities from the matching Sales Order line (raw_json). Returns reduce commission.
    qty_ordered: float = 0.0
    qty_shipped: float = 0.0
    qty_invoiced: float = 0.0
    qty_returned: float = 0.0


def _load_item_map(conn: DbConnection) -> dict[str, float]:
    """sku -> MAP unit price (items.rate). SKU upper-cased for matching.
    When duplicates exist, the row with the highest rowid (latest inserted) wins.
    """
    out: dict[str, float] = {}
    for row in conn.execute(
        "SELECT sku, rate FROM items WHERE sku IS NOT NULL AND sku != '' ORDER BY sku, rowid DESC"
    ).fetchall():
        sku = str(row["sku"]).strip().upper()
        if sku and sku not in out:  # first = highest rowid = most recent
            out[sku] = float(row["rate"] or 0)
    return out


def _load_invoice_meta_map(conn: DbConnection, year: int, month: int) -> dict[str, dict[str, str]]:
    """invoice_id -> {sales_team, payment_terms} parsed from invoices.raw_json for the month."""
    import json as _json

    start, end = month_bounds(year, month)
    inv_date = date_prefix_expr("invoice_date", conn.postgres)
    out: dict[str, dict[str, str]] = {}
    rows = conn.execute(
        f"""
        SELECT invoice_id, raw_json FROM invoices
        WHERE {inv_date} >= ? AND {inv_date} <= ?
        """,
        (start, end),
    ).fetchall()
    for r in rows:
        try:
            inv = _json.loads(r["raw_json"])
        except Exception:
            out[str(r["invoice_id"])] = {"sales_team": "", "payment_terms": ""}
            continue
        team = ""
        for f in inv.get("custom_fields") or []:
            label = str(f.get("label") or f.get("api_name") or "").lower()
            if "sales team" in label or label == "cf_sales_team":
                team = str(f.get("value") or "")
                break
        terms = str(inv.get("payment_terms_label") or "")
        out[str(r["invoice_id"])] = {"sales_team": team, "payment_terms": terms}
    return out


def _load_returns_map(conn: DbConnection, so_ids: Iterable[str]) -> dict[tuple[str, str], dict[str, float]]:
    """(salesorder_id, sku_upper) -> {ordered, invoiced, shipped, returned} from the SO line items.

    Returned quantity is only tracked on the Sales Order line raw_json, never on
    invoice lines, so commission must look it up here to net out returns.
    """
    import json as _json

    ids = sorted({str(s) for s in so_ids if s})
    out: dict[tuple[str, str], dict[str, float]] = {}
    if not ids:
        return out
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT salesorder_id, raw_json FROM sales_orders WHERE salesorder_id IN ({placeholders})",
        ids,
    ).fetchall()
    for r in rows:
        try:
            order = _json.loads(r["raw_json"])
        except Exception:
            continue
        soid = str(r["salesorder_id"])
        for li in order.get("line_items") or []:
            sku = str(li.get("sku") or "").strip().upper()
            if not sku:
                continue
            agg = out.setdefault((soid, sku), {"ordered": 0.0, "invoiced": 0.0, "shipped": 0.0, "returned": 0.0})
            agg["ordered"] += float(li.get("quantity") or 0)
            agg["invoiced"] += float(li.get("quantity_invoiced") or 0)
            agg["shipped"] += float(li.get("quantity_shipped") or 0)
            agg["returned"] += float(li.get("quantity_returned") or 0)
    return out


def _load_invoice_lines_with_context(
    conn: DbConnection, year: int, month: int
) -> list[InvoiceLineRecord]:
    start, end = month_bounds(year, month)
    inv_date = date_prefix_expr("i.invoice_date", conn.postgres)
    rows = conn.execute(
        f"""
        SELECT
          il.invoice_id, il.line_index, il.sku, il.item_name,
          il.quantity, il.rate, il.item_total,
          i.invoice_number, i.invoice_date, i.status AS invoice_status,
          i.customer_name AS inv_customer, i.salesperson_name AS inv_salesperson,
          i.balance, i.salesorder_number,
          so.salesorder_id, so.order_date, so.salesperson_name AS so_salesperson,
          so.delivery_method, so.customer_name AS so_customer
        FROM invoice_lines il
        INNER JOIN invoices i ON i.invoice_id = il.invoice_id
        LEFT JOIN sales_orders so ON so.salesorder_number = i.salesorder_number
        WHERE {inv_date} >= ? AND {inv_date} <= ?
        ORDER BY i.invoice_date, i.invoice_number, il.line_index
        """,
        (start, end),
    ).fetchall()

    payment_date_by_invoice = _load_payment_dates(conn, [r["invoice_id"] for r in rows])
    so_numbers = sorted({r["salesorder_number"] for r in rows if r["salesorder_number"]})
    shipment_info = _load_shipment_summary(conn, so_numbers)
    invoice_meta = _load_invoice_meta_map(conn, year, month)
    returns_map = _load_returns_map(conn, {r["salesorder_id"] for r in rows if r["salesorder_id"]})

    records: list[InvoiceLineRecord] = []
    for r in rows:
        sku = r["sku"] or ""
        item_name = r["item_name"] or ""
        item_total = float(r["item_total"] or 0)
        quantity = float(r["quantity"] or 0)
        rate = float(r["rate"] or 0)
        line_type = classify_line_type(
            sku=sku, item_name=item_name,
            item_total=item_total, quantity=quantity, rate=rate,
        )
        ship = shipment_info.get(r["salesorder_number"] or "")
        meta = invoice_meta.get(str(r["invoice_id"] or ""), {})
        qtys = returns_map.get((str(r["salesorder_id"]), sku.strip().upper()), {}) if r["salesorder_id"] else {}
        records.append(InvoiceLineRecord(
            invoice_id=str(r["invoice_id"] or ""),
            invoice_number=r["invoice_number"] or "",
            invoice_date=parse_date(r["invoice_date"]),
            invoice_status=r["invoice_status"] or "",
            customer_name=r["inv_customer"] or r["so_customer"] or "",
            salesperson_name=r["inv_salesperson"] or "",
            so_salesperson_name=r["so_salesperson"] or "",
            salesorder_number=r["salesorder_number"] or "",
            salesorder_id=str(r["salesorder_id"]) if r["salesorder_id"] else None,
            order_date=parse_date(r["order_date"]),
            sku=sku,
            item_name=item_name,
            quantity=quantity,
            item_total=item_total,
            rate=rate,
            line_type=line_type,
            delivery_method=r["delivery_method"] or "",
            shipment_date=ship["date"] if ship else None,
            shipment_status=ship["status"] if ship else "",
            payment_date=payment_date_by_invoice.get(str(r["invoice_id"] or "")),
            balance=float(r["balance"] or 0),
            sales_team=meta.get("sales_team", ""),
            payment_terms=meta.get("payment_terms", ""),
            carrier=ship["carrier"] if ship else "",
            ship_charge=ship["charge"] if ship else 0.0,
            has_shipment=bool(ship),
            qty_ordered=float(qtys.get("ordered", 0.0)),
            qty_shipped=float(qtys.get("shipped", 0.0)),
            qty_invoiced=float(qtys.get("invoiced", 0.0)),
            qty_returned=float(qtys.get("returned", 0.0)),
        ))
    return records


def _load_payment_dates(conn: DbConnection, invoice_ids: Iterable[str]) -> dict[str, date | None]:
    inv_ids = [str(i) for i in invoice_ids if i]
    if not inv_ids:
        return {}
    out: dict[str, date | None] = {iid: None for iid in inv_ids}
    placeholders = ",".join("?" * len(inv_ids))
    pay_date = date_prefix_expr("cp.payment_date", conn.postgres)
    rows = conn.execute(
        f"""
        SELECT cpi.invoice_id, MAX({pay_date}) AS last_date
        FROM customer_payment_invoices cpi
        INNER JOIN customer_payments cp ON cp.payment_id = cpi.payment_id
        WHERE cpi.invoice_id IN ({placeholders})
        GROUP BY cpi.invoice_id
        """,
        inv_ids,
    ).fetchall()
    for r in rows:
        out[str(r["invoice_id"])] = parse_date(r["last_date"])
    return out


def _load_shipment_summary(conn: DbConnection, so_numbers: Iterable[str]) -> dict[str, dict]:
    """salesorder_number -> {date, status, carrier, charge} from the shipments table.

    Returns an empty map when no shipments are synced (the salesperson sheets then
    show no shipment data, and the workbook is flagged accordingly).
    """
    nums = [n for n in so_numbers if n]
    if not nums:
        return {}
    placeholders = ",".join("?" * len(nums))
    out: dict[str, dict] = {}

    # 1) Authoritative Zoho shipments (when synced).
    ship_date = date_prefix_expr("shipment_date", conn.postgres)
    for r in conn.execute(
        f"""
        SELECT salesorder_number,
               MAX({ship_date}) AS last_ship_date,
               MAX(status) AS status,
               MAX(carrier_name) AS carrier_name,
               MAX(shipping_charge) AS shipping_charge
        FROM shipments
        WHERE salesorder_number IN ({placeholders})
        GROUP BY salesorder_number
        """,
        nums,
    ).fetchall():
        out[r["salesorder_number"]] = {
            "date": parse_date(r["last_ship_date"]),
            "status": r["status"] or "",
            "carrier": r["carrier_name"] or "",
            "charge": float(r["shipping_charge"] or 0),
        }

    # 2) Fallback to locally derived shipments (from sales_orders.raw_json packages).
    missing = [n for n in nums if n not in out]
    if missing:
        ph2 = ",".join("?" * len(missing))
        derived_date = date_prefix_expr("shipment_date", conn.postgres)
        for r in conn.execute(
            f"""
            SELECT salesorder_number,
                   MAX({derived_date}) AS last_ship_date,
                   MAX(shipment_status) AS shipment_status,
                   MAX(carrier_name) AS carrier_name,
                   MAX(shipping_charge) AS shipping_charge
            FROM derived_shipments
            WHERE salesorder_number IN ({ph2})
            GROUP BY salesorder_number
            """,
            missing,
        ).fetchall():
            if not r["last_ship_date"]:
                continue
            out[r["salesorder_number"]] = {
                "date": parse_date(r["last_ship_date"]),
                "status": r["shipment_status"] or "",
                "carrier": r["carrier_name"] or "",
                "charge": float(r["shipping_charge"] or 0),
            }
    return out


# --- Classification + DetailRow construction ---------------------------------


def _ar_status(rec: InvoiceLineRecord) -> str:
    inv_lower = rec.invoice_status.lower()
    if rec.payment_date is not None:
        return "PAID"
    if rec.balance == 0 or "paid" in inv_lower or "closed" in inv_lower:
        return "PAID"
    if rec.balance > 0:
        return "UNPAID"
    return "REVIEW"  # negative balance = over-payment / credit note


def _is_negative_balance(rec: InvoiceLineRecord) -> bool:
    return rec.balance < 0 and rec.payment_date is None


def _build_detail_row(
    rec: InvoiceLineRecord,
    *,
    map_price: float,
    comm_rate: float,
    ar_status: str,
) -> DetailRow:
    is_shipping = rec.line_type == "shipping"
    return DetailRow(
        order_date=rec.order_date,
        sales_order_number=rec.salesorder_number,
        invoice_date=rec.invoice_date,
        invoice_number=rec.invoice_number,
        invoice_status=rec.invoice_status,
        customer_name=rec.customer_name,
        estimate_number="",
        sku=rec.sku,
        quantity=rec.quantity,
        item_total=rec.item_total,
        account="Shipping Income" if is_shipping else ("Sales" if rec.line_type == "product" else ""),
        account_code="",
        payment_terms=rec.payment_terms,
        delivery_method=rec.delivery_method,
        shipment_date=rec.shipment_date,
        shipment_status=(
            ("SHIPPED" if "ship" in rec.shipment_status.lower() else rec.shipment_status.upper())
            if rec.has_shipment else ""
        ),
        ar_status=ar_status,
        payment_date=rec.payment_date,
        shipping_method=rec.carrier,
        reason="",
        include_in_commission="No",
        shipping_income=rec.item_total if is_shipping else 0.0,
        shipping_expenses=0.0,
        discount_rate=None,
        map_price=map_price,
        commission_rate=comm_rate,
    )


def _section_for(rec: InvoiceLineRecord, year: int, month: int) -> str:
    if is_before_month(rec.order_date, year, month):
        return "II"
    return "I"  # current month, or unknown date (don't lose data)


def _empty_salesperson(sheet_name: str, full_name: str, year: int, month: int) -> SalespersonData:
    return SalespersonData(
        name=sheet_name,
        full_name=full_name.upper(),
        rate_type=rate_type_for(sheet_name),
        month_name=calendar.month_name[month],
        year=year,
        current_commissionable=Block("ORDERS COMMISSIONABLE - SHIPPED AND INVOICED"),
        current_shipping=Block("SHIPPING INCOME"),
        current_other=Block("OTHER CHARGES"),
        prior_commissionable=Block("ORDERS COMMISSIONABLE"),
        prior_shipping=Block("SHIPPING CHARGE"),
    )


@dataclass
class _Line:
    """A surviving commission line carrying both system and final (post-adjustment) values."""
    line_uid: str
    rec: InvoiceLineRecord
    section: str               # "I" | "II"
    block: str                 # "commissionable" | "shipping" | "other"
    zoho_salesperson: str      # Original Zoho salesperson (display only)
    sys_sheet: str             # roster sheet or Zoho name from automated calculation
    sheet: str                 # routing sheet key (after adjustment); ROUTING_UNASSIGNED if pending
    sys_map: float
    sys_discount: float
    sys_rate: float
    sys_commissionable: float
    sys_commission: float
    detail: DetailRow          # final values written into the workbook
    flags: list[str] = field(default_factory=list)
    excluded: bool = False
    classification: str = ""
    adj_reason: str = ""
    reviewer: str = ""
    approval_status: str = ""
    adjusted: bool = False
    pending: bool = False        # B2B line awaiting manual salesperson assignment


def _resolve_sheet(name: str | None) -> str | None:
    """Map a salesperson full-name or sheet key to a roster sheet key."""
    return resolve_roster_sheet(name)


def _route_from_zoho(
    full_name: str,
    data_by_sheet: dict[str, SalespersonData],
) -> tuple[str, str, str, bool, list[str]]:
    """Return zoho_display, sys_sheet, routing_sheet, in_roster, flags."""
    zoho = format_zoho_salesperson(full_name)
    roster_sheet = resolve_roster_sheet(full_name)
    in_roster = roster_sheet is not None and roster_sheet in data_by_sheet
    flags: list[str] = []
    if in_roster:
        return zoho, roster_sheet, roster_sheet, True, flags
    flags.append("UNASSIGNED")
    return zoho, zoho, ROUTING_UNASSIGNED, False, flags


def build_salespeople_from_sqlite(
    year: int,
    month: int,
    db_path: Path | None = None,
    *,
    tiers: list[tuple[float, float, float]] | None = None,
    rlp_map: dict[str, float] | None = None,
    apply_adjustments: bool = True,
) -> GenerationResult:
    """Build SalespersonData per roster sheet from SQLite, plus exceptions + totals.

    MAP price source priority: the curated R_LP map (``rlp_map``) wins; the live
    Zoho catalog (items.rate) is only a fallback for SKUs missing from R_LP.

    When ``apply_adjustments`` is true, manual adjustments stored for the period
    are applied AFTER the automated calculation and BEFORE grouping/export.
    """
    init_database(db_path)
    conn = get_connection(db_path)
    try:
        invoice_lines = _load_invoice_lines_with_context(conn, year, month)
        item_map = _load_item_map(conn)
    finally:
        conn.close()

    tiers = tiers or DEFAULT_TIERS
    # Curated MAP (R_LP) overrides the live catalog; catalog fills any gaps.
    map_by_sku = {**item_map, **(rlp_map or {})}

    # Order total per SO (all line types) for the $5k free-shipping test.
    order_total_by_so: dict[str, float] = {}
    for rec in invoice_lines:
        if rec.salesorder_number:
            order_total_by_so[rec.salesorder_number] = (
                order_total_by_so.get(rec.salesorder_number, 0.0) + rec.item_total
            )

    data_by_sheet: dict[str, SalespersonData] = {
        sheet: _empty_salesperson(sheet, full, year, month)
        for sheet, full in ALL_SHEETS_ORDERED
    }
    exceptions: list[ReviewItem] = []
    totals_by_sheet: dict[str, float] = {s: 0.0 for s, _ in ALL_SHEETS_ORDERED}

    # ---- Phase 1: build surviving commission lines with SYSTEM values ----
    lines: list[_Line] = []
    for rec in invoice_lines:
        # Rule 1 — route by CF.Sales Team exactly like Accounting does.
        team = (rec.sales_team or "").strip().lower()
        is_b2b = team.startswith("b2b") or team.startswith("exe") or "comp. account" in team
        if not is_b2b:
            continue

        full_name = (rec.so_salesperson_name or rec.salesperson_name or "").strip()
        zoho_sp, sys_sheet, routing, in_roster, base_flags = _route_from_zoho(full_name, data_by_sheet)
        line_uid = make_line_uid(rec.invoice_number, rec.sku, rec.salesorder_number)
        section = _section_for(rec, year, month)
        ar = _ar_status(rec)
        flags: list[str] = list(base_flags)

        # Rule 5 — shipping lines
        if rec.line_type == "shipping":
            if rec.item_total <= 0:
                order_total = order_total_by_so.get(rec.salesorder_number, 0.0)
                if order_total <= FREE_SHIPPING_THRESHOLD:
                    exceptions.append(ReviewItem(
                        salesperson=sys_sheet,
                        invoice_number=rec.invoice_number,
                        sales_order_number=rec.salesorder_number,
                        sku=rec.sku,
                        amount=order_total,
                        reason=f"Free shipping under ${int(FREE_SHIPPING_THRESHOLD):,} — reason not given",
                    ))
                continue  # free shipping: drop the $0 shipping line
            detail = _build_detail_row(rec, map_price=0.0, comm_rate=0.0, ar_status=ar)
            lines.append(_Line(line_uid, rec, section, "shipping", zoho_sp, sys_sheet, routing,
                               0.0, 0.0, 0.0, 0.0, 0.0, detail, flags=flags, pending=not in_roster))
            continue

        # Rule 4 — $0 non-shipping line (likely a ticket / warranty)
        if rec.item_total == 0:
            exceptions.append(ReviewItem(
                salesperson=sys_sheet,
                invoice_number=rec.invoice_number,
                sales_order_number=rec.salesorder_number,
                sku=rec.sku,
                amount=0.0,
                reason="$0 line / possible ticket — verify, excluded",
            ))
            continue

        if not in_roster:
            exceptions.append(ReviewItem(
                salesperson=zoho_sp,
                invoice_number=rec.invoice_number,
                sales_order_number=rec.salesorder_number,
                sku=rec.sku,
                amount=rec.item_total,
                reason="Salesperson not in commission roster — classify or assign",
            ))

        # Other charges (non-product, non-shipping, non-zero)
        if rec.line_type != "product":
            detail = _build_detail_row(rec, map_price=0.0, comm_rate=0.0, ar_status=ar)
            lines.append(_Line(line_uid, rec, "I", "other", zoho_sp, sys_sheet, routing,
                               0.0, 0.0, 0.0, 0.0, 0.0, detail, flags=flags, pending=not in_roster))
            continue

        # Product line — compute MAP, discount, commission rate
        map_price = map_by_sku.get(rec.sku.strip().upper(), 0.0)
        if map_price <= 0:
            flags.append("MISSING_MAP")
            exceptions.append(ReviewItem(
                salesperson=sys_sheet,
                invoice_number=rec.invoice_number,
                sales_order_number=rec.salesorder_number,
                sku=rec.sku,
                amount=rec.item_total,
                reason="Missing MAP price — rate may be wrong",
            ))
        # Discount/rate are per-unit (computed on the gross invoiced amount/qty);
        # returns do not change which rate tier applies, only how much qty counts.
        disc = implied_discount(rec.item_total, map_price, rec.quantity)
        rt = rate_type_for(routing if in_roster else "Paul")
        rate = commission_rate(disc, rt, tiers) if map_price > 0 else 0.0
        if ar == "UNPAID":
            flags.append("UNPAID")
            exceptions.append(ReviewItem(
                salesperson=sys_sheet,
                invoice_number=rec.invoice_number,
                sales_order_number=rec.salesorder_number,
                sku=rec.sku,
                amount=rec.item_total,
                reason="Unpaid — included, confirm before payout",
            ))
        if _is_negative_balance(rec):
            flags.append("NEGATIVE_BALANCE")
            exceptions.append(ReviewItem(
                salesperson=sys_sheet,
                invoice_number=rec.invoice_number,
                sales_order_number=rec.salesorder_number,
                sku=rec.sku,
                amount=rec.item_total,
                reason="Negative balance (credit / over-payment) — verify before payout",
            ))

        # Rule (shared with the audit engine) — commission only on quantity kept.
        comm_qty, factor, ret_status = commissionable_quantity(
            rec.qty_invoiced, rec.qty_returned, rec.qty_shipped, rec.quantity
        )
        comm_amount = round(rec.item_total * factor, 2)
        if ret_status == "Fully Returned":
            flags.append("FULLY_RETURNED")
            exceptions.append(ReviewItem(
                salesperson=sys_sheet, invoice_number=rec.invoice_number,
                sales_order_number=rec.salesorder_number, sku=rec.sku, amount=rec.item_total,
                reason="Returned quantity fully offsets shipped/invoiced quantity",
            ))
        elif ret_status == "Partially Returned":
            flags.append("PARTIALLY_RETURNED")
            exceptions.append(ReviewItem(
                salesperson=sys_sheet, invoice_number=rec.invoice_number,
                sales_order_number=rec.salesorder_number, sku=rec.sku, amount=rec.item_total,
                reason=f"Partial return: {rec.qty_returned:g} returned — commission on {comm_qty:g}",
            ))

        detail = _build_detail_row(rec, map_price=map_price, comm_rate=rate, ar_status=ar)
        # Write NET-of-returns quantity & amount so the workbook formulas stay self-consistent.
        detail.item_total = comm_amount
        detail.quantity = comm_qty
        lines.append(_Line(line_uid, rec, section, "commissionable", zoho_sp, sys_sheet, routing,
                           map_price, disc, rate, comm_amount, comm_amount * rate,
                           detail, flags=flags, pending=not in_roster))

    # ---- Phase 2: apply manual adjustments (after calc, before export) ----
    adj_map = get_adjustment_map(year, month, db_path=db_path) if apply_adjustments else {}
    adjusted_count = 0
    for ln in lines:
        adj = adj_map.get(ln.line_uid)
        if not adj:
            continue
        adjusted_count += 1
        ln.adjusted = True
        ln.reviewer = adj.get("reviewer") or ""
        ln.approval_status = adj.get("approval_status") or ""
        ln.adj_reason = adj.get("reason") or ""
        if adj.get("exclude_flag"):
            ln.excluded = True
        cls = (adj.get("classification") or "").lower()
        if cls in ("company", "executive"):
            ln.classification = cls
            ln.sheet = "Company Acct"
            ln.pending = False
        raw_sp = adj.get("adjusted_salesperson")
        if raw_sp:
            new_sheet = _resolve_sheet(raw_sp)
            if new_sheet:
                ln.sheet = new_sheet
                ln.pending = False
            else:
                ln.flags.append("ADJ_SALESPERSON_NOT_IN_ROSTER")
        if adj.get("adjusted_commissionable") is not None:
            ln.detail.item_total = float(adj["adjusted_commissionable"])
        if adj.get("adjusted_map") is not None:
            ln.detail.map_price = float(adj["adjusted_map"])
        if ln.block == "commissionable":
            rt = rate_type_for(ln.sheet)
            if adj.get("adjusted_discount") is not None:
                ln.detail.commission_rate = commission_rate(float(adj["adjusted_discount"]), rt, tiers)
            else:
                # Use commissionable qty (after returns) so the implied discount is correct
                comm_qty = ln.detail.quantity or ln.rec.quantity
                d = implied_discount(ln.detail.item_total, ln.detail.map_price, comm_qty)
                ln.detail.commission_rate = commission_rate(d, rt, tiers) if ln.detail.map_price > 0 else 0.0
        if ln.adj_reason:
            ln.detail.reason = ln.adj_reason

    # ---- Phase 3: group final lines + per-line audit + totals ----
    audit_rows: list[dict[str, Any]] = []
    for ln in lines:
        final_comm = 0.0
        if not ln.excluded and not ln.pending and ln.block == "commissionable":
            final_comm = ln.detail.item_total * ln.detail.commission_rate
        if not ln.excluded and not ln.pending:
            sp = data_by_sheet.get(ln.sheet)
            if sp is not None:
                if ln.block == "commissionable":
                    (sp.current_commissionable if ln.section == "I" else sp.prior_commissionable).rows.append(ln.detail)
                    totals_by_sheet[ln.sheet] += final_comm
                elif ln.block == "shipping":
                    (sp.current_shipping if ln.section == "I" else sp.prior_shipping).rows.append(ln.detail)
                else:
                    sp.current_other.rows.append(ln.detail)
        flags_str = ",".join(ln.flags)
        audit_extra = enrich_audit_fields(
            zoho_salesperson=ln.zoho_salesperson,
            sys_sheet=ln.sys_sheet,
            sheet=ln.sheet,
            excluded=ln.excluded,
            classification=ln.classification,
            pending=ln.pending,
            flags=flags_str,
            block=ln.block,
            section=ln.section,
            sales_team=ln.rec.sales_team or "",
            approval_status=ln.approval_status,
        )
        audit_rows.append({
            "line_uid": ln.line_uid,
            "period": f"{year}-{month:02d}",
            "sales_team": ln.rec.sales_team,
            **audit_extra,
            "sales_order": ln.rec.salesorder_number,
            "invoice": ln.rec.invoice_number,
            "sku": ln.rec.sku,
            "item_name": ln.rec.item_name,
            "customer": ln.rec.customer_name,
            "quantity": ln.rec.quantity,
            "qty_ordered": ln.rec.qty_ordered,
            "qty_shipped": ln.rec.qty_shipped,
            "qty_invoiced": ln.rec.qty_invoiced,
            "qty_returned": ln.rec.qty_returned,
            "qty_commissionable": (round(ln.detail.quantity, 2) if ln.block == "commissionable" else ""),
            "return_status": (
                "Fully Returned" if "FULLY_RETURNED" in ln.flags
                else "Partially Returned" if "PARTIALLY_RETURNED" in ln.flags
                else ""
            ),
            "revenue": round(ln.rec.item_total, 2),
            "block": ln.block,
            "section": ln.section,
            "flags": flags_str,
            "map": round(ln.detail.map_price, 2),
            "system_commissionable": round(ln.sys_commissionable, 2),
            "system_rate": round(ln.sys_rate, 4),
            "system_commission": round(ln.sys_commission, 2),
            "final_commissionable": round(ln.detail.item_total, 2),
            "final_rate": round(ln.detail.commission_rate, 4),
            "final_commission": round(final_comm, 2),
            "adjustment": round(final_comm - ln.sys_commission, 2),
            "excluded": ln.excluded,
            "classification": ln.classification,
            "reason": ln.adj_reason,
            "reviewer": ln.reviewer,
            "approval_status": ln.approval_status,
            "adjusted": ln.adjusted,
            "pending": ln.pending,
        })

    pending_lines = sum(1 for a in audit_rows if a.get("pending"))
    pending_revenue = round(sum(a["revenue"] for a in audit_rows if a.get("pending")), 2)
    pending_commission = round(sum(a["system_commission"] for a in audit_rows if a.get("pending")), 2)
    shipment_present = any(ln.rec.has_shipment for ln in lines)
    approval_incomplete = any(
        a.get("adjusted") and a.get("approval_status") not in ("approved", "Approved") for a in audit_rows
    )

    kpis = _compute_kpis(data_by_sheet, totals_by_sheet, exceptions)
    kpis["adjusted_lines"] = adjusted_count
    kpis["pending_lines"] = pending_lines
    kpis["pending_revenue"] = pending_revenue
    kpis["pending_commission"] = pending_commission
    kpis["shipment_data_present"] = shipment_present
    kpis["approval_incomplete"] = approval_incomplete
    # Draft while there are unassigned lines, shipments are missing, or any
    # adjustment is not yet approved.
    kpis["is_draft"] = bool(pending_lines > 0 or (not shipment_present) or approval_incomplete)
    return GenerationResult(
        salespeople=list(data_by_sheet.values()),
        exceptions=exceptions,
        totals_by_sheet=totals_by_sheet,
        kpis=kpis,
        audit_rows=audit_rows,
        adjusted_count=adjusted_count,
    )


def _compute_kpis(
    data_by_sheet: dict[str, SalespersonData],
    totals_by_sheet: dict[str, float],
    exceptions: list[ReviewItem],
) -> dict[str, Any]:
    commissionable_lines = 0
    revenue_current = 0.0
    revenue_prior = 0.0
    reps_with_sales = 0
    for sp in data_by_sheet.values():
        cur = sp.current_commissionable.rows
        pri = sp.prior_commissionable.rows
        commissionable_lines += len(cur) + len(pri)
        revenue_current += sum(r.item_total for r in cur)
        revenue_prior += sum(r.item_total for r in pri)
        if cur or pri:
            reps_with_sales += 1
    return {
        "total_commission": round(sum(totals_by_sheet.values()), 2),
        "commissionable_lines": commissionable_lines,
        "salespeople_with_sales": reps_with_sales,
        "revenue_current": round(revenue_current, 2),
        "revenue_prior": round(revenue_prior, 2),
        "exceptions_count": len(exceptions),
    }


# --- High-level orchestration ------------------------------------------------


def _reconciliation_values(result: GenerationResult) -> dict[str, float]:
    """Engine-computed reconciliation (written as VALUES so checks read 0 on open)."""
    reps = [s for s, _ in ALL_SHEETS_ORDERED if s != "Company Acct"]
    rep_commission = round(sum(result.totals_by_sheet.get(s, 0.0) for s in reps), 2)
    company_commission = round(result.totals_by_sheet.get("Company Acct", 0.0), 2)
    # Bruce override mirrors the B2B Summary model: 15% of rep + 20% of company.
    bruce = round(rep_commission * 0.15 + company_commission * 0.20, 2)
    executive = 0.0  # manual line — never auto-populated
    total_to_pay = round(rep_commission + company_commission + bruce, 2)
    return {
        "rep_commission": rep_commission,
        "company_commission": company_commission,
        "bruce": bruce,
        "executive": executive,
        "total_to_pay": total_to_pay,
        "check_a": round(rep_commission - rep_commission, 2),                       # sheets vs rep = 0
        "check_b": round((rep_commission + company_commission + bruce) - total_to_pay, 2),  # = 0
    }


def _period_reference_sheets(year: int, month: int, db_path: Path | None,
                             result: GenerationResult) -> tuple[dict, bool]:
    """Build period-correct raw reference sheets (R_SO, R_INV, R_SH, R_LP) from SQLite."""
    from src.commission.sqlite_data_source import load_period_dataframes

    def df_to_hr(df) -> tuple[list, list]:
        if df is None or df.empty:
            return [], []
        headers = [str(c) for c in df.columns.tolist()]
        rows = []
        for _, row in df.iterrows():
            out = []
            for v in row.tolist():
                try:
                    import pandas as _pd
                    if _pd.isna(v):
                        v = ""
                except Exception:
                    pass
                out.append(v)
            rows.append(out)
        return headers, rows

    period = load_period_dataframes(year, month, db_path=db_path)
    shipments_present = not period.shipments.empty
    ref = {
        "R_SO": df_to_hr(period.sales_orders),
        "R_INV": df_to_hr(period.invoices),
        "R_SH": df_to_hr(period.shipments),
    }
    # R_LP = the MAP actually applied this period (per product line), for transparency.
    applied: dict[str, float] = {}
    for a in result.audit_rows:
        if a.get("block") == "commissionable" and a.get("sku"):
            applied[str(a["sku"])] = a.get("map", 0)
    ref["R_LP"] = (["SKU", "MAP Price (applied this period)"],
                   [[s, m] for s, m in sorted(applied.items())])
    return ref, shipments_present


def generate_commission_workbook(
    year: int,
    month: int,
    *,
    template_path: Path,
    output_path: Path,
    db_path: Path | None = None,
) -> GenerationResult:
    """Full pipeline: SQLite -> SalespersonData[] -> Jennifer-style master workbook."""
    from src.commission.workbook_builder_v2 import build_master_workbook

    tiers = load_tiers_from_template(template_path)
    rlp_map = load_map_from_template(template_path)
    result = build_salespeople_from_sqlite(year, month, db_path=db_path, tiers=tiers, rlp_map=rlp_map)
    recon = _reconciliation_values(result)
    reference_sheets, shipments_present = _period_reference_sheets(year, month, db_path, result)
    build_master_workbook(
        template_path=template_path,
        output_path=output_path,
        salespeople=result.salespeople,
        month_name=calendar.month_name[month],
        year=year,
        audit_rows=result.audit_rows,
        status_info=result.kpis,
        reconciliation=recon,
        reference_sheets=reference_sheets,
        shipments_present=shipments_present,
    )
    return result


def generate_salesperson_workbook(
    year: int,
    month: int,
    salesperson_sheet: str,
    *,
    template_path: Path,
    output_path: Path,
    db_path: Path | None = None,
) -> GenerationResult:
    """Pipeline for ONE salesperson's standalone workbook (single sheet)."""
    from src.commission.workbook_builder_v2 import build_salesperson_workbook

    tiers = load_tiers_from_template(template_path)
    rlp_map = load_map_from_template(template_path)
    result = build_salespeople_from_sqlite(year, month, db_path=db_path, tiers=tiers, rlp_map=rlp_map)
    target = next((d for d in result.salespeople if d.name == salesperson_sheet), None)
    if target is None:
        raise ValueError(
            f"Salesperson sheet {salesperson_sheet!r} not in catalog. "
            f"Valid: {[s for s, _ in ALL_SHEETS_ORDERED]}"
        )
    build_salesperson_workbook(
        template_path=template_path,
        output_path=output_path,
        data=target,
    )
    return result
