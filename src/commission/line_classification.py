from __future__ import annotations

LINE_TYPES = (
    "product",
    "shipping",
    "credit_card_fee",
    "discount",
    "other_charge",
    "unknown",
)

COMMISSION_CANDIDATE_TYPES = frozenset({"product"})


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def line_amount(
    *,
    item_total: object = None,
    quantity: object = None,
    rate: object = None,
) -> float:
    if item_total is not None and str(item_total).strip() != "":
        try:
            return float(item_total)
        except (TypeError, ValueError):
            pass
    try:
        qty = float(quantity or 0)
        unit_rate = float(rate or 0)
        return qty * unit_rate
    except (TypeError, ValueError):
        return 0.0


def classify_line_type(
    *,
    sku: object = None,
    item_name: object = None,
    item_total: object = None,
    quantity: object = None,
    rate: object = None,
) -> str:
    """
    Classify a sales order or invoice line for commission processing.

    Rules (first match wins):
    - SKU or name contains "Shipping Charge" -> shipping
    - SKU or name contains "Credit Card Processing Fee" -> credit_card_fee
    - Negative amount or name contains "discount" -> discount
    - Blank SKU and non-zero amount -> other_charge
    - Otherwise -> product
    """
    sku_text = _text(sku)
    name_text = _text(item_name)
    combined = f"{sku_text} {name_text}".lower()
    amount = line_amount(
        item_total=item_total,
        quantity=quantity,
        rate=rate,
    )

    if "shipping charge" in combined:
        return "shipping"
    if "credit card processing fee" in combined:
        return "credit_card_fee"
    if amount < 0 or "discount" in name_text.lower():
        return "discount"
    if not sku_text and amount != 0:
        return "other_charge"
    if not sku_text and amount == 0 and not name_text:
        return "unknown"
    return "product"


def is_commission_candidate(line_type: str) -> bool:
    return line_type in COMMISSION_CANDIDATE_TYPES
