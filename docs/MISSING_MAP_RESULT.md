# MISSING_MAP / Possible-Ticket — Diagnostic Result (Jan–May 2026)

_Documented as-is. No new Needs-Review flag implemented (deferred)._

## Conclusion

A read-only diagnostic looked for commission-relevant lines the engine **cannot price**
because MAP is missing (SKU not in R_LP) — potential tickets or custom lines.

- **52 missing-MAP lines** found across Jan–May 2026 (all sales teams).
- **Only 6 are potentially payable**; estimated exposure is **$78.83**.
- **Current real leakage is $0** — these lines do **not** earn commission without a
  MAP/rate (the engine sets `rate = 0` when MAP is missing).

## Numbers

| # | Metric | Value |
|---|--------|-------|
| 1 | MISSING_MAP count | **52** |
| 2 | Sales Orders affected | 19 |
| 3 | Invoices affected | 22 |
| 4 | Total revenue affected | $43,608.01 |
| 5 | Commission exposure (currently payable, est.) | **$78.83** (6 lines) |

### By sales team
| Team | Lines | Revenue | Payable | Exposure |
|------|------:|--------:|--------:|---------:|
| Exe./Comp. Account | 36 | $39,956.48 | 0 | $0.00 |
| **B2B** | 6 | $1,526.53 | **6** | **$78.83** |
| B2C non-commissionable | 6 | $1,440.00 | 0 | $0.00 |
| B2C - RC Team | 4 | $685.00 | 0 | $0.00 |

## Why exposure is effectively zero

Almost all missing-MAP revenue is **already held or excluded** by existing rules:
- The large Exe./Comp. "Miscellaneous" lines (incl. a **$30,136** line, INV-05807) are
  **held** (inactive name / Bruce / Marshall / not-in-roster).
- The B2C "Round Trip Repair" lines (Dylan Nava, several with a Ticket#) are **excluded**
  by sales-team routing.
- The only un-held lines are **6 B2B "Miscellaneous"** lines (High Voltage Auto, Michael
  Ayala) — and these still pay **$0** today because they have no MAP → `rate = 0`. The
  $78.83 is a hypothetical "if priced at full MAP" figure, not actual commission.

## Monitoring note (current policy)

- Missing-MAP lines are **monitored** as a possible custom/ticket risk.
- Most are **already excluded or held** by the B2C, Exe./Comp., $0-line, or roster rules.
- **No dedicated review flag** is enabled yet, to avoid adding Needs-Review noise for
  a near-zero financial exposure.
- If future missing-MAP **payable** exposure becomes material, Accounting can enable a
  dedicated review flag:
  - `Issue Found = Missing MAP / possible custom-ticket line`
  - `Suggested Action = Review MAP or classify as ticket/noncommissionable`

## How to re-check

```
python scripts/missing_map_report.py
```
Outputs a console summary and `data/output/missing_map_possible_ticket_report.xlsx`
(Summary + per-line Detail). Read-only; no Zoho calls; no historical workbooks changed.

## Decision

- Keep monitoring via the diagnostic script.
- Do **not** add a Needs-Review flag now.
- Re-evaluate if a future month shows material payable missing-MAP exposure.
