# Big Battery Commission Automation — Project Context

## Purpose

This is a **Big Battery internal Commission Automation** tool.

The system replaces the manual monthly commission preparation process currently performed by Accounting. It calculates commissions from **Zoho Books operational data** and **company commission rules**, then produces a reviewable **Commission Audit Report** for Accounting approval.

This application does **not** pay commissions automatically. It is an **audit and review** workflow.

---

## Production Workflow (Target)

The future production workflow is:

1. **Select period** — Choose year and month for the commission cycle.
2. **Fetch Zoho data** — Pull Sales Orders, Invoices, Shipments/Packages, Items, and Customer Payments from Zoho Books.
3. **Validate data quality** — Confirm required data is present and consistent before calculation.
4. **Generate commission audit** — Run the commission calculation engine against Zoho-sourced data.
5. **Review exceptions** — Prioritize lines that need Accounting review (missing shipment, missing invoice, MAP issues, AR, discounts, etc.).
6. **Review salesperson summary** — Review line-level results aggregated by salesperson.
7. **Download report** — Export the Commission Audit Report (Excel) for records and approval.

---

## Source of Truth

| Source | Role |
|--------|------|
| **Zoho Books** | Primary operational data for production commissions |
| **Commission rules** | Business logic (rates, periods, eligibility) defined by the company |
| **Historical Jennifer workbooks** | Validation references only — used to verify automation during development |

---

## Important Principles

- **The system should be Zoho-driven.** Normal users should not depend on manual Excel exports from Zoho.
- **Historical Jennifer workbooks are validation references only.** They exist to compare automation output against past Accounting work while the system is being built and verified.
- **Jennifer is an accounting reviewer, not the source of truth.** Her historical process reflects how Accounting reviewed commissions; it does not define how future commissions are calculated.
- **Do not design the main workflow around uploading Excel files.** Uploads are optional and reserved for edge cases (historical testing, incomplete Zoho data, replay validation).
- **Historical workbook comparison should be optional.** It is a separate validation step, not part of standard commission generation.

---

## Commission Philosophy

- Commissions are **line-based**, not summary-based. Every commission dollar should trace to transaction detail.
- The engine calculates at **line level first**, then generates salesperson summaries.
- **Exception visibility** is a priority — most Accounting effort goes to reviewing exceptions before approval.

---

## Architecture Overview (Conceptual)

| Phase | Description |
|-------|-------------|
| Zoho data acquisition | Fetch and map Zoho Books data for the selected period |
| Data quality validation | Verify completeness and linkage (SO, invoice, shipment, MAP) |
| Commission calculation engine | Apply commission rules to Zoho/current data |
| Exception engine | Flag lines requiring review |
| Salesperson summary generation | Aggregate audit results by salesperson |
| Historical replay validation | Optional comparison against historical workbooks |
| Accounting review dashboard | UI for audit, exceptions, and download |

The **calculation engine** and **validation engine** remain separate: calculation uses Zoho/current data; validation may read historical workbooks only for comparison.

---

## What This Document Is For

Use this file as the canonical context when making product, UI, or architecture decisions. When in doubt:

- Default to **Zoho + commission rules** for production behavior.
- Treat **historical workbooks** as optional validation, not the main path.
- Keep the user experience aligned with **Audit / Review**, not final payout automation.
