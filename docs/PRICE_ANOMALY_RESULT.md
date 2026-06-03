# PRICE_ANOMALY — Result (Jan–May 2026)

_Documented as-is. Threshold not lowered._

## Conclusion

- **PRICE_ANOMALY found 0 lines** across **January–May 2026**, across **all sales teams**
  (B2B, Exe./Comp. Account, and all B2C variants).
- The detector is **valid**: the **highest observed price/MAP ratio was 1.82×**, far below
  the **5× threshold**. So the zero is genuine, not a broken filter.
- **Keep PRICE_ANOMALY as a high-confidence safety net only** — it should fire only on
  gross keying errors (e.g. a $3k item invoiced at $400k ≈ 133×), not on normal pricing.

## Supporting data (open scan: all teams, with MAP)

| Month | Product lines checked (w/ MAP) | Highest invoiced/MAP ratio |
|-------|-------------------------------|----------------------------|
| Jan 2026 | 340 | 1.82× (CNT101: MAP $164.99 → $299.99) |
| Feb 2026 | 350 | 1.00× |
| Mar 2026 | 424 | 1.22× |
| Apr 2026 | 431 | 1.22× |
| May 2026 | 407 | 1.29× |
| **Combined** | **1,952** | **1.82× (max)** |

- PRICE_ANOMALY count: **0**
- Sales Orders affected: **0**
- Invoices affected: **0**
- Commission amount affected: **$0.00**

## Rule definition (current)

- Scope: **B2B / Exe./Comp. Account** product lines only (B2C is already excluded by
  sales-team routing).
- Condition: `invoiced_amount > MAP × quantity × 5` (with MAP > 0).
- Behavior: **review flag only** — never auto-excludes, never auto-holds.

## Known limitation

The ratio test requires a **known MAP**. A ticket/custom line with **no MAP** (SKU not in
R_LP) cannot be ratio-checked and will **not** trigger PRICE_ANOMALY. Those cases are
covered by other layers (MISSING_MAP flag, $0-line rule, B2C non-commissionable routing)
and are the subject of the separate **MISSING_MAP / possible-ticket** diagnostic.

## Decision

- Keep the 5× threshold. Do not lower it yet.
- Re-evaluate only if Accounting reports a real anomaly that slipped through.

_Generated from `scripts/price_anomaly_report.py --year 2026 --months 1,2,3,4,5 --all-teams`._
