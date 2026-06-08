import React, { useEffect, useMemo, useState } from "react";
import { KpiCard, Banner, ErrorBanner, money, num, LoadingNotice } from "./ui.jsx";
import { IconSearch, IconDollar, IconChart, IconAlert, IconCheck, IconInfo, IconSparkle, IconX } from "./Icons.jsx";

import { API, apiFetch, readJson } from "../lib/api.js";

const MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];

function prevMonth() {
  const d = new Date(); d.setMonth(d.getMonth() - 1);
  return { year: d.getFullYear(), month: d.getMonth() + 1 };
}
const numOrNull = (v) => (v === "" || v === null || v === undefined ? null : Number(v));
const round2 = (x) => Math.round((Number(x) || 0) * 100) / 100;

// ---- Field help text (tooltips) ------------------------------------------
const TIP = {
  calc: "Total to Pay (calculated) — engine payable total (rep + Bruce), matching B2B Summary / Check A+B. Not the sum of every audit-line system amount.",
  final: "Total to Pay (final) — what will actually be paid after accounting decisions (same as B2B Summary when no adjustments).",
  change: "Change — difference between calculated and final Total to Pay (non-zero only after accounting adjustments).",
  needsReview: "Needs Review — this line cannot be finalized until Accounting makes a decision (e.g. assign a salesperson or classify the account).",
  exclude: "Exclude from Commission — remove this line from commission entirely. Its Final Commission becomes $0.",
  category: "Accounting Category — move this line to the Company Account or Executive Account instead of a salesperson.",
  overrideComm: "Override Commissionable Amount — replace the dollar base used to calculate commission for this line.",
  overrideMap: "Override MAP — replace the list (MAP) price used to compute the discount for this line.",
  overrideDisc: "Override Discount / Rate — set the discount (0–1) directly; the commission rate tier is recalculated from it on Save.",
  reviewStatus: "Review Status — Pending (not decided), Approved (ready to pay), or Rejected.",
  reason: "Reason / Notes — required. Explains why this adjustment was made, for the audit trail.",
  assign: "Assign to Salesperson — credit this line to a specific salesperson.",
  zohoSp: "Original Zoho Salesperson — exactly as Zoho recorded on the order (never replaced with 'unassigned').",
  finalAssign: "Final Commission Assignment — who/what receives commission after Accounting review. 'Pending' until classified or assigned.",
  acctCat: "Accounting Category — Company Account or Executive Account when the line was moved off a rep.",
};

// ---- Management review categories (Phase B, surfaced from backend Phase A) ----
// Single source of truth for the chip / badge / filter labels and ordering.
// `id` matches the backend category_tags value (or 'over_5000_review' which is the
// virtual API-time tag from /api/adjustments/lines). Ordering here defines:
//   (a) chip rendering order on the toolbar
//   (b) "primary category" tiebreak when a line has multiple tags (first match wins).
// Excluding ones come first (red), then held (yellow), then informational (gray/blue).
const CATEGORY_DEFS = [
  { id: "ticket",             label: "Tickets",            short: "Ticket",        color: "red"    },
  { id: "ticket_review",      label: "Ticket Review",      short: "Ticket?",       color: "yellow" },
  { id: "quote_reference",    label: "Quote Ref",          short: "Quote",         color: "gray"   },
  { id: "return",             label: "Returns",            short: "Return",        color: "red"    },
  { id: "discount_excluded",  label: "Discount >60%",      short: ">60% Excluded", color: "red"    },
  { id: "executive_account",  label: "Executive Account",  short: "Executive",     color: "blue"   },
  { id: "discount_review",    label: "Discount 30-60%",    short: "30-60% Hold",   color: "yellow" },
  { id: "inactive_unmatched", label: "Inactive/Unassigned",short: "Inactive",      color: "yellow" },
  { id: "price_map_issue",    label: "Price/MAP Issue",    short: "MAP Issue",     color: "yellow" },
  { id: "over_5000_review",   label: "Over $5K Review",    short: ">$5K",          color: "gray"   },
  { id: "unpaid_info",        label: "Unpaid (info)",      short: "Unpaid",        color: "gray"   },
  { id: "company_account",    label: "Company Account",    short: "Company",       color: "blue"   },
];
const CATEGORY_DEF_BY_ID = Object.fromEntries(CATEGORY_DEFS.map((d) => [d.id, d]));

// Mirrors backend `_CATEGORY_TAG_MAP` in sqlite_to_workbook.py — used when the API
// response predates Phase A (category_tags null) or the backend was not restarted.
const FLAG_TO_CATEGORY_TAG = [
  ["REAL_TICKET", "ticket"],
  ["OTHER_TICKET_REFERENCE", "ticket_review"],
  ["QUOTE_REFERENCE_IN_TICKET_FIELD", "quote_reference"],
  ["FULLY_RETURNED", "return"],
  ["PARTIALLY_RETURNED", "return"],
  ["KNOWN_INACTIVE", "inactive_unmatched"],
  ["UNASSIGNED", "inactive_unmatched"],
  ["DISCOUNT_OVER_30", "discount_review"],
  ["DISCOUNT_OVER_60", "discount_excluded"],
  ["UNPAID", "unpaid_info"],
  ["COMPANY_ACCOUNT", "company_account"],
  ["EXECUTIVE_ACCOUNT", "executive_account"],
  ["PRICE_HISTORY_NO_WINDOW", "price_map_issue"],
  ["MISSING_MAP", "price_map_issue"],
  ["MAP_ANOMALY_LOW", "price_map_issue"],
];

function categoryTagsFromFlags(flagsStr) {
  if (!flagsStr) return [];
  const flagSet = new Set(String(flagsStr).split(",").map((s) => s.trim()).filter(Boolean));
  const tags = [];
  const seen = new Set();
  for (const [flag, tag] of FLAG_TO_CATEGORY_TAG) {
    if (flagSet.has(flag) && !seen.has(tag)) {
      tags.push(tag);
      seen.add(tag);
    }
  }
  return tags;
}

function rowCategories(r, soRevenueByOrder = null) {
  const tags = Array.isArray(r.category_tags) && r.category_tags.length
    ? r.category_tags.slice()
    : categoryTagsFromFlags(r.flags);
  const over5000 = r.over_5000_review ?? (
    r.sales_order && soRevenueByOrder && (soRevenueByOrder[r.sales_order] || 0) > 5000
  );
  if (over5000 && !tags.includes("over_5000_review")) tags.push("over_5000_review");
  return tags;
}

function primaryCategoryDef(r, soRevenueByOrder = null) {
  const tags = rowCategories(r, soRevenueByOrder);
  // CATEGORY_DEFS ordering encodes priority -- first hit wins.
  for (const def of CATEGORY_DEFS) {
    if (tags.includes(def.id)) return def;
  }
  return null;
}

// Per-line implied discount = 1 - revenue/(map*qty). Returns null if not computable.
function discountPct(r) {
  const map = Number(r.map) || 0;
  const qty = Number(r.qty_commissionable || r.qty_invoiced || r.quantity) || 0;
  const rev = Number(r.revenue) || 0;
  if (map <= 0 || qty <= 0) return null;
  return 1 - rev / (map * qty);
}

// ---- Derived line state / issue / action ---------------------------------
function lineState(r) {
  // Excluded -- preserve category context in the label (Ticket / Return / >60% / Exec).
  if (r.excluded) {
    const def = primaryCategoryDef(r);
    const label = def && def.color === "red" ? `Excluded — ${def.short}`
      : def && def.id === "executive_account" ? "Executive Account"
      : "Excluded";
    const color = def && def.id === "executive_account" ? "blue" : "red";
    return { key: "excluded", label, color };
  }
  const cls = (r.classification || "").toLowerCase();
  if (cls === "company") return { key: "company", label: "Company Account", color: "blue" };
  if (cls === "executive") return { key: "executive", label: "Executive Account", color: "blue" };
  const appr = (r.approval_status || "").toLowerCase();
  if (appr === "approved") return { key: "approved", label: "Approved", color: "green" };
  if (r.pending) {
    const def = primaryCategoryDef(r);
    const label = def && def.color === "yellow" ? `Needs Review — ${def.short}` : "Needs Review";
    return { key: "needs", label, color: "yellow" };
  }
  const flags = String(r.flags || "");
  // UNPAID is informational ONLY -- engine still pays. Show a gray pill, NOT yellow.
  // Removing it from the yellow override list per Phase B spec #5.
  if (flags.includes("MISSING_MAP") || flags.includes("PRICE_ANOMALY") || flags.includes("NEGATIVE_BALANCE")) {
    return { key: "needs", label: "Needs Review", color: "yellow" };
  }
  return { key: "ready", label: "Ready", color: "gray" };
}

function issueFound(r) {
  if (r.issue_found) return r.issue_found;
  const flags = String(r.flags || "");
  const team = String(r.sales_team || "").toLowerCase();
  if (flags.includes("REAL_TICKET")) return "Real support ticket present — non-commissionable";
  if (flags.includes("QUOTE_REFERENCE_IN_TICKET_FIELD")) return "Quote reference in Ticket# field — not automatically excluded";
  if (flags.includes("OTHER_TICKET_REFERENCE")) return "Unrecognized Ticket# format — review required";
  if (flags.includes("FULLY_RETURNED")) return "Fully returned — not commissionable";
  if (flags.includes("PARTIALLY_RETURNED")) return "Partially returned";
  if (flags.includes("DISCOUNT_OVER_60")) return "Discount over 60% — non-commissionable";
  if (flags.includes("DISCOUNT_OVER_30")) return "Discount 30-60% — held for review";
  if (flags.includes("KNOWN_INACTIVE")) return "Salesperson inactive — held for review";
  if (flags.includes("EXECUTIVE_ACCOUNT")) return "Executive account — $0 commission";
  if (flags.includes("COMPANY_ACCOUNT")) return "Company Account / Bruce";
  if (flags.includes("PRICE_HISTORY_NO_WINDOW")) return "Snapshot exists but does not cover sale date";
  if (flags.includes("MAP_ANOMALY_LOW")) return "MAP unusually low vs items.rate — verify";
  if (r.pending && (team.includes("exe") || team.includes("comp")))
    return "Company / Executive account needs classification";
  if (r.pending && (r.original_zoho_salesperson === "(missing in Zoho)"))
    return "Missing salesperson in Zoho";
  if (r.pending && flags.includes("UNASSIGNED"))
    return "Salesperson not in commission roster";
  if (r.pending) return "Missing salesperson assignment";
  if (flags.includes("MISSING_MAP")) return "MAP / discount difference";
  // UNPAID is informational ONLY (engine pays). Show as info, not issue.
  if (flags.includes("UNPAID")) return "Unpaid (informational — still paid by system)";
  if (r.block === "shipping") return "Shipping line";
  if (r.section === "II") return "Prior-period order";
  return "";
}

function suggestedAction(r) {
  if (r.suggested_action) return r.suggested_action;
  const team = String(r.sales_team || "").toLowerCase();
  const flags = String(r.flags || "");
  if (flags.includes("REAL_TICKET")) return "Exclude — real support/warranty ticket (numeric 1–4 digits)";
  if (flags.includes("QUOTE_REFERENCE_IN_TICKET_FIELD")) return "No action required — quote reference is not a support ticket";
  if (flags.includes("OTHER_TICKET_REFERENCE")) return "Review Ticket# format — classify, exclude, or approve manually";
  if (flags.includes("FULLY_RETURNED")) return "Returned — verify $0 commission";
  if (flags.includes("PARTIALLY_RETURNED")) return "Partial return — verify kept qty";
  if (flags.includes("DISCOUNT_OVER_60")) return "Non-commissionable — do not pay unless management approves";
  if (flags.includes("DISCOUNT_OVER_30")) return "Confirm written approval and applicable rate";
  if (flags.includes("KNOWN_INACTIVE")) return "Assign to active rep, classify, or exclude";
  if (flags.includes("EXECUTIVE_ACCOUNT")) return "Track revenue; no commission unless exception approved";
  if (flags.includes("COMPANY_ACCOUNT")) return "Review only if exception";
  if (flags.includes("PRICE_HISTORY_NO_WINDOW")) return "Verify fallback MAP; consider loading a covering snapshot";
  if (flags.includes("MAP_ANOMALY_LOW")) return "Verify snapshot price for this SKU";
  if (r.pending && (team.includes("exe") || team.includes("comp"))) return "Classify as Company / Executive";
  if (r.pending && (r.original_zoho_salesperson === "(missing in Zoho)")) return "Assign salesperson";
  if (r.pending && flags.includes("UNASSIGNED"))
    return "Classify as Company Account, Executive Account, Bruce Commission, assign to a salesperson, or add to roster";
  if (r.pending) return "Assign salesperson";
  if (flags.includes("MISSING_MAP")) return "Review MAP / discount";
  if (flags.includes("UNPAID")) return "Will pay when collected — no action required";
  if (r.excluded) return "Review exclusion";
  if ((r.approval_status || "").toLowerCase() === "approved") return "—";
  return "Approve if correct";
}

function finalAssignment(r) {
  return r.final_commission_assignment || r.salesperson || "—";
}

function zohoSalesperson(r) {
  if (r.original_zoho_salesperson) return r.original_zoho_salesperson;
  const sys = r.system_salesperson || "";
  if (sys === "(unassigned)") return "— (update backend — redeploy required)";
  return sys || "—";
}

// State-level chips (4) + 10 management-category chips appended from CATEGORY_DEFS.
// Each entry is [chipKey, displayLabel, optional helper to test row membership].
const STATE_VIEWS = [
  ["all",      "All lines"],
  ["needs",    "Needs Review"],
  ["approved", "Approved"],
  ["excluded", "Excluded"],
];
const VIEWS = [
  ...STATE_VIEWS,
  // Category chips: keyed as `cat:<id>` so filter logic can dispatch on prefix.
  ...CATEGORY_DEFS.map((d) => [`cat:${d.id}`, d.label]),
];

const BLANK_FILTERS = { salesperson: "", sales_team: "", issue: "", action: "", sales_order: "", invoice: "", sku: "", category: "" };

function Tip({ text, children }) {
  return (
    <span className="tip" title={text}>
      {children}<sup className="tip-mark">?</sup>
    </span>
  );
}

// ---- Quick-confirm modal (Approve / Exclude) --------------------------------
function QuickConfirmModal({ action, row, onConfirm, onCancel }) {
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const isApprove = action === "approve";
  const color = isApprove ? "var(--bb-emerald)" : "var(--bb-rose)";
  const title = isApprove ? "Approve Commission Line" : "Exclude Commission Line";
  const btnLabel = isApprove ? "Confirm Approve" : "Confirm Exclude";

  function handleConfirm() {
    if (!reason.trim()) { setErr("Please add a reason before continuing."); return; }
    onConfirm(reason.trim());
  }

  return (
    <div className="adj-drawer-backdrop" style={{ alignItems: "center", justifyContent: "center" }}
         onClick={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="confirm-modal-card" role="dialog" aria-modal="true">
        {/* Header */}
        <div className="confirm-modal-head" style={{ borderLeftColor: color }}>
          <span className="confirm-modal-title" style={{ color }}>
            {isApprove ? <IconCheck style={{ width: 18, height: 18 }} /> : <IconAlert style={{ width: 18, height: 18 }} />}
            {title}
          </span>
          <button type="button" className="btn btn-ghost btn-icon" onClick={onCancel}><IconX /></button>
        </div>

        {/* Line details */}
        <div className="confirm-modal-body">
          <div className="confirm-modal-grid">
            <span>Customer</span>       <strong>{row.customer || "—"}</strong>
            <span>Sales Order</span>    <strong>{row.sales_order || "—"}</strong>
            <span>Invoice</span>        <strong>{row.invoice || "—"}</strong>
            <span>SKU</span>            <strong>{row.sku || "—"}</strong>
            <span>Item</span>           <strong>{row.item_name || "—"}</strong>
            <span>Salesperson</span>    <strong>{row.final_commission_assignment || row.system_salesperson || "—"}</strong>
            <span>Sales Team</span>     <strong>{row.sales_team || "—"}</strong>
            <span>Calculated Commission</span>
            <strong style={{ color: "var(--bb-navy-700)" }}>{money(row.system_commission)}</strong>
            {isApprove ? (
              <>
                <span>Final Commission</span>
                <strong style={{ color: "var(--bb-emerald)" }}>{money(row.final_commission)}</strong>
              </>
            ) : (
              <>
                <span>Commission after exclusion</span>
                <strong style={{ color: "var(--bb-rose)" }}>$0.00</strong>
              </>
            )}
          </div>

          {!isApprove && (
            <div className="confirm-modal-warning">
              <IconAlert style={{ width: 15, height: 15, flexShrink: 0 }} />
              This line will be removed from the payable. The workbook must be regenerated to reflect the change.
            </div>
          )}

          {/* Reason */}
          <div className="field" style={{ marginTop: "0.85rem" }}>
            <label className="field-label">Reason / Notes <span style={{ color: "var(--bb-rose)" }}>*</span></label>
            <textarea
              className="input"
              rows={2}
              placeholder={isApprove ? "e.g. Verified against Zoho — correct salesperson and amount" : "e.g. Internal account — not commissionable"}
              value={reason}
              onChange={(e) => { setReason(e.target.value); setErr(""); }}
              style={{ resize: "vertical" }}
            />
            {err && <span style={{ color: "var(--bb-rose)", fontSize: 12 }}>{err}</span>}
          </div>
        </div>

        {/* Footer */}
        <div className="confirm-modal-foot">
          <button type="button" className="btn" onClick={onCancel}>Cancel</button>
          <button
            type="button"
            className={`btn ${isApprove ? "btn-success" : "btn-danger"}`}
            onClick={handleConfirm}
          >
            {isApprove ? <IconCheck style={{ width: 14, height: 14 }} /> : <IconAlert style={{ width: 14, height: 14 }} />}
            {btnLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function AdjustmentsView() {
  const p = prevMonth();
  const [year, setYear] = useState(p.year);
  const [month, setMonth] = useState(p.month);
  const [view, setView] = useState("needs");
  const [filters, setFilters] = useState(BLANK_FILTERS);
  const [rows, setRows] = useState([]);
  const [kpis, setKpis] = useState({});
  const [roster, setRoster] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadingPeriod, setLoadingPeriod] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(null);
  const [showHelp, setShowHelp] = useState(false);
  const [confirmSave, setConfirmSave] = useState(false);
  const [quickConfirm, setQuickConfirm] = useState(null); // { action: "approve"|"exclude", row }

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [year, month]);

  async function load() {
    setLoading(true);
    setLoadingPeriod(true);
    setError("");
    try {
      const data = await readJson(await apiFetch(`${API}/adjustments/lines?year=${year}&month=${month}`));
      setRows(data.rows || []);
      setKpis(data.kpis || {});
      setRoster(data.roster || []);
    } catch (err) { setError(err); setRows([]); setKpis({}); }
    finally { setLoading(false); setLoadingPeriod(false); }
  }

  const salespeopleOptions = useMemo(() => {
    const set = new Set(roster);
    rows.forEach((r) => {
      if (finalAssignment(r)) set.add(finalAssignment(r));
      if (zohoSalesperson(r)) set.add(zohoSalesperson(r));
    });
    return [...set].sort();
  }, [rows, roster]);
  const teamOptions = useMemo(() => [...new Set(rows.map((r) => r.sales_team).filter(Boolean))].sort(), [rows]);
  const issueOptions = useMemo(() => [...new Set(rows.map(issueFound).filter(Boolean))].sort(), [rows]);
  const actionOptions = useMemo(() => [...new Set(rows.map(suggestedAction).filter((a) => a && a !== "—"))].sort(), [rows]);

  const soRevenueByOrder = useMemo(() => {
    const m = {};
    for (const r of rows) {
      const so = r.sales_order;
      if (so) m[so] = (m[so] || 0) + (Number(r.revenue) || 0);
    }
    return m;
  }, [rows]);

  const filtered = useMemo(() => {
    const inc = (hay, needle) => String(hay || "").toLowerCase().includes(needle.toLowerCase());
    let list = rows.filter((r) => {
      const st = lineState(r).key;
      // State-level chips
      if (view === "needs" && st !== "needs") return false;
      if (view === "approved" && st !== "approved") return false;
      if (view === "excluded" && st !== "excluded") return false;
      // Category-level chips: 'cat:<tag-id>' filters by category_tags / over_5000_review membership
      if (typeof view === "string" && view.startsWith("cat:")) {
        const catId = view.slice(4);
        if (!rowCategories(r, soRevenueByOrder).includes(catId)) return false;
      }
      if (filters.category && !rowCategories(r, soRevenueByOrder).includes(filters.category)) return false;
      if (filters.salesperson && finalAssignment(r) !== filters.salesperson && zohoSalesperson(r) !== filters.salesperson) return false;
      if (filters.sales_team && r.sales_team !== filters.sales_team) return false;
      if (filters.issue && issueFound(r) !== filters.issue) return false;
      if (filters.action && suggestedAction(r) !== filters.action) return false;
      if (filters.sales_order && !inc(r.sales_order, filters.sales_order)) return false;
      if (filters.invoice && !inc(r.invoice, filters.invoice)) return false;
      if (filters.sku && !inc(r.sku, filters.sku)) return false;
      return true;
    });
    // Needs Review first, then by salesperson.
    const rank = (r) => (lineState(r).key === "needs" ? 0 : r.pending ? 1 : 2);
    return list.sort((a, b) => rank(a) - rank(b) || String(a.salesperson).localeCompare(String(b.salesperson)));
  }, [rows, view, filters, soRevenueByOrder]);

  const totals = useMemo(() => {
    let adj = 0, needs = 0, exc = 0;
    for (const r of rows) {
      if (r.adjusted) adj += 1;
      if (lineState(r).key === "needs") needs += 1;
      if (r.excluded) exc += 1;
    }
    const finPayable = Number(kpis.total_commission);
    const pendingLines = Number(kpis.pending_lines);
    if (Number.isFinite(finPayable)) {
      const hasAdj = adj > 0 || (Number(kpis.adjusted_lines) || 0) > 0;
      const fin = round2(finPayable);
      const sys = hasAdj ? round2(finPayable + (Number(kpis.pending_commission) || 0)) : fin;
      return {
        sys,
        fin,
        delta: hasAdj ? round2(fin - sys) : 0,
        adj,
        needs: Number.isFinite(pendingLines) ? pendingLines : needs,
        exc,
      };
    }
    let sys = 0, fin = 0;
    for (const r of rows) {
      sys += r.system_commission || 0;
      fin += r.final_commission || 0;
    }
    return { sys, fin, delta: fin - sys, adj, needs, exc };
  }, [rows, kpis]);

  // ---- drawer ----
  function openEdit(row, preset = {}) {
    const a = row.adjustment_record || {};
    setConfirmSave(false);
    setEditing(row);
    setForm({
      adjusted_salesperson: a.adjusted_salesperson || "",
      exclude_flag: !!a.exclude_flag,
      classification: a.classification || "",
      adjusted_commissionable: a.adjusted_commissionable ?? "",
      adjusted_map: a.adjusted_map ?? "",
      adjusted_discount: a.adjusted_discount ?? "",
      reason: a.reason || "",
      reviewer: a.reviewer || "",
      approval_status: a.approval_status || "pending",
      ...preset,
    });
  }
  function closeDrawer() { setEditing(null); setForm(null); setConfirmSave(false); }

  const highImpact = (f) =>
    !!f && (f.exclude_flag || numOrNull(f.adjusted_commissionable) != null ||
            numOrNull(f.adjusted_map) != null || numOrNull(f.adjusted_discount) != null);

  function requestSave() {
    if (!form.reason.trim()) { setError("Please add a Reason / Notes before saving."); return; }
    if (highImpact(form)) { setConfirmSave(true); return; }
    doSave();
  }

  async function doSave() {
    if (!editing) return;
    setConfirmSave(false); setLoading(true); setError("");
    try {
      await readJson(await apiFetch(`${API}/adjustments`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          period_year: year, period_month: month,
          line_uid: editing.line_uid,
          sales_order_number: editing.sales_order, invoice_number: editing.invoice, sku: editing.sku,
          original_salesperson: editing.system_salesperson,
          original_commissionable: editing.system_commissionable,
          original_map: editing.map,
          adjusted_salesperson: form.adjusted_salesperson || null,
          adjusted_commissionable: numOrNull(form.adjusted_commissionable),
          adjusted_map: numOrNull(form.adjusted_map),
          adjusted_discount: numOrNull(form.adjusted_discount),
          exclude_flag: form.exclude_flag,
          classification: form.classification || null,
          reason: form.reason, reviewer: form.reviewer, approval_status: form.approval_status,
        }),
      }));
      setStatus(`Saved adjustment for ${editing.invoice} · ${editing.sku}`);
      closeDrawer();
      await load();
    } catch (err) { setError(err); }
    finally { setLoading(false); }
  }

  async function removeAdjustment() {
    const id = editing?.adjustment_record?.id;
    if (!id) { closeDrawer(); return; }
    setLoading(true); setError("");
    try {
      await readJson(await apiFetch(`${API}/adjustments/${id}`, { method: "DELETE" }));
      setStatus("Adjustment removed.");
      closeDrawer();
      await load();
    } catch (err) { setError(err); }
    finally { setLoading(false); }
  }

  async function handleQuickConfirm(reason) {
    if (!quickConfirm) return;
    const { action, row } = quickConfirm;
    setQuickConfirm(null);
    setLoading(true); setError("");
    try {
      await readJson(await apiFetch(`${API}/adjustments`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          period_year: year, period_month: month,
          line_uid: row.line_uid,
          sales_order_number: row.sales_order,
          invoice_number: row.invoice,
          sku: row.sku,
          original_salesperson: row.system_salesperson,
          original_commissionable: row.system_commissionable,
          original_map: row.map,
          exclude_flag: action === "exclude",
          approval_status: action === "approve" ? "approved" : "pending",
          reason,
          reviewer: "",
        }),
      }));
      setStatus(action === "approve"
        ? `Approved: ${row.invoice} · ${row.sku}`
        : `Excluded: ${row.invoice} · ${row.sku}`);
      await load();
    } catch (err) { setError(err); }
    finally { setLoading(false); }
  }

  async function regenerate() {
    setLoading(true); setError(""); setStatus("Regenerating the workbook with adjustments…");
    try {
      const d = await readJson(await apiFetch(`${API}/commission/generate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ year, month }),
      }));
      setStatus(`Workbook regenerated: ${d.report_id} (includes the "Adjustments Audit" sheet).`);
    } catch (err) { setError(err); }
    finally { setLoading(false); }
  }

  // ---- impact preview ----
  function estimateFinal() {
    if (!editing || !form) return null;
    if (form.exclude_flag) return 0;
    const willAssign = !!form.adjusted_salesperson || form.classification === "company" || form.classification === "executive";
    if (editing.pending && !willAssign) return 0; // still unassigned -> not counted
    if (numOrNull(form.adjusted_discount) != null) return null; // rate tier recalculated server-side
    const base = numOrNull(form.adjusted_commissionable) ?? (editing.final_commissionable ?? editing.system_commissionable ?? 0);
    const rate = editing.final_rate || editing.system_rate || 0;
    return round2(base * rate);
  }

  function salespersonAfter() {
    if (!form) return "";
    if (form.exclude_flag) return "Excluded";
    if (form.classification === "company") return "Company Account";
    if (form.classification === "executive") return "Executive Account";
    if (form.adjusted_salesperson) return form.adjusted_salesperson;
    return finalAssignment(editing);
  }

  const estFinal = estimateFinal();
  const estChange = estFinal == null ? null : round2(estFinal - (editing?.system_commission || 0));

  const badge = (r) => { const s = lineState(r); return <span className={`badge badge-${s.color}`}>{s.label}</span>; };

  const periodLabel = `${MONTHS[month - 1]} ${year}`;
  const periodBusy = loadingPeriod || loading;

  return (
    <div className="page">
      {/* Header + workflow steps */}
      <section className="workflow-bar">
        <div className="workflow-period">
          <div className="field">
            <label className="field-label">Year</label>
            <input className="input" type="number" min="2020" max="2100" value={year} onChange={(e) => setYear(Number(e.target.value))} disabled={periodBusy} />
          </div>
          <div className="field">
            <label className="field-label">Month</label>
            <select className="select" value={month} onChange={(e) => setMonth(Number(e.target.value))} disabled={periodBusy}>
              {MONTHS.map((n, i) => <option key={n} value={i + 1}>{n}</option>)}
            </select>
          </div>
        </div>
        <div className="workflow-step-divider" />
        <div className="workflow-actions">
          <button type="button" className="btn" onClick={load} disabled={loading}>
            {loading ? <span className="spinner" /> : <IconSearch />} Reload
          </button>
          <button type="button" className="btn btn-primary" onClick={regenerate} disabled={loading}>
            <IconSparkle /> Regenerate workbook
          </button>
        </div>
        <div className="row" style={{ marginLeft: "auto", gap: "0.5rem", flexWrap: "wrap" }}>
          {loadingPeriod && <LoadingNotice>Loading {periodLabel}…</LoadingNotice>}
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setShowHelp(true)}>
            <IconInfo /> Help: How Accounting Adjustments Work
          </button>
        </div>
      </section>

      {/* Workflow steps */}
      <section className="adj-steps">
        {[
          "Review lines marked Needs Review",
          "Choose an accounting action",
          "Add a reason / note",
          "Mark as Approved",
          "Regenerate the workbook",
        ].map((t, i) => (
          <div className="adj-step" key={i}>
            <span className="adj-step-num">{i + 1}</span>
            <span>{t}</span>
          </div>
        ))}
      </section>

      {status && <Banner type="success" icon={IconCheck}>{status}</Banner>}
      {error && <ErrorBanner error={error} onRetry={load} />}

      <div className={loadingPeriod ? "content-loading" : ""}>
      {/* KPIs */}
      <section className="kpi-grid">
        <KpiCard variant="money" label="Calculated Commission" value={money(totals.sys)} icon={IconDollar} />
        <KpiCard variant="money" label="Net Change" value={money(totals.delta)} icon={IconChart} />
        <KpiCard variant="money" label="Final Commission" value={money(totals.fin)} icon={IconDollar} />
        <KpiCard variant="warning" label="Needs Review" value={num(totals.needs)} icon={IconAlert} />
        <KpiCard variant="info" label="Adjusted lines" value={num(totals.adj)} icon={IconSparkle} />
        <KpiCard variant="danger" label="Excluded" value={num(totals.exc)} icon={IconX} />
      </section>

      {/* View chips + legend */}
      <section className="card">
        <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          <div className="adj-chips" style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", alignItems: "center" }}>
            {VIEWS.map(([k, lbl], idx) => {
              let count;
              if (k === "all") count = rows.length;
              else if (typeof k === "string" && k.startsWith("cat:")) {
                const catId = k.slice(4);
                count = rows.filter((r) => rowCategories(r, soRevenueByOrder).includes(catId)).length;
              } else {
                count = rows.filter((r) => lineState(r).key === k).length;
              }
              const isCategory = typeof k === "string" && k.startsWith("cat:");
              const def = isCategory ? CATEGORY_DEF_BY_ID[k.slice(4)] : null;
              const colorClass = def ? `chip-${def.color}` : "";
              // Insert a small visual divider before the first category chip.
              const isFirstCategory = isCategory && VIEWS.findIndex((v) => typeof v[0] === "string" && v[0].startsWith("cat:")) === idx;
              return (
                <React.Fragment key={k}>
                  {isFirstCategory && (
                    <span aria-hidden="true" style={{ color: "var(--bb-muted, #999)", padding: "0 0.25rem", fontSize: 12 }}>·</span>
                  )}
                  <button
                    type="button"
                    className={`chip ${colorClass} ${view === k ? "chip-active" : ""}`}
                    title={isCategory ? `Filter to: ${lbl}` : lbl}
                    onClick={() => setView(k)}
                  >
                    {lbl} <span className="chip-count">{count}</span>
                  </button>
                </React.Fragment>
              );
            })}
          </div>

          <div className="row" style={{ flexWrap: "wrap", gap: "0.6rem", alignItems: "flex-end" }}>
            <div className="field" style={{ minWidth: 150, flex: "1 1 150px" }}>
              <label className="field-label">Assignment / Zoho name</label>
              <select className="select" value={filters.salesperson} onChange={(e) => setFilters({ ...filters, salesperson: e.target.value })}>
                <option value="">All</option>
                {salespeopleOptions.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div className="field" style={{ minWidth: 170, flex: "1 1 170px" }}>
              <label className="field-label">Sales Team</label>
              <select className="select" value={filters.sales_team} onChange={(e) => setFilters({ ...filters, sales_team: e.target.value })}>
                <option value="">All</option>
                {teamOptions.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div className="field" style={{ minWidth: 200, flex: "1 1 200px" }}>
              <label className="field-label">Issue Found</label>
              <select className="select" value={filters.issue} onChange={(e) => setFilters({ ...filters, issue: e.target.value })}>
                <option value="">All issues</option>
                {issueOptions.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div className="field" style={{ minWidth: 200, flex: "1 1 200px" }}>
              <label className="field-label">Suggested Action</label>
              <select className="select" value={filters.action} onChange={(e) => setFilters({ ...filters, action: e.target.value })}>
                <option value="">All actions</option>
                {actionOptions.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div className="field" style={{ minWidth: 150, flex: "1 1 150px" }}>
              <label className="field-label">Category</label>
              <select className="select" value={filters.category} onChange={(e) => setFilters({ ...filters, category: e.target.value })}>
                <option value="">All categories</option>
                {CATEGORY_DEFS.map((d) => {
                  const n = rows.filter((r) => rowCategories(r, soRevenueByOrder).includes(d.id)).length;
                  return <option key={d.id} value={d.id}>{d.label} ({n})</option>;
                })}
              </select>
            </div>
            <div className="field" style={{ minWidth: 110, flex: "1 1 110px" }}>
              <label className="field-label">Invoice</label>
              <input className="input" value={filters.invoice} onChange={(e) => setFilters({ ...filters, invoice: e.target.value })} placeholder="search…" />
            </div>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => { setFilters(BLANK_FILTERS); setView("all"); }}>Clear</button>
          </div>

          <div className="adj-legend">
            <span><i className="dot dot-green" /> Approved</span>
            <span><i className="dot dot-yellow" /> Needs Review (held)</span>
            <span><i className="dot dot-red" /> Excluded (non-commissionable)</span>
            <span><i className="dot dot-blue" /> Company / Executive</span>
            <span><i className="dot dot-gray" /> Informational (still paid)</span>
          </div>
        </div>
      </section>

      {/* Lines table */}
      <section className="card">
        <div className="table-toolbar" style={{ borderRadius: "14px 14px 0 0" }}>
          <strong>Commission lines — review &amp; adjust</strong>
          <span className="pill">{filtered.length} shown</span>
        </div>
        <div className={`spreadsheet-wrap ${loadingPeriod ? "is-data-loading" : ""}`} style={{ borderRadius: "0 0 14px 14px", minHeight: 120 }}>
          {loadingPeriod && (
            <div className="data-loading-overlay" role="status" aria-live="polite">
              <span className="spinner" style={{ width: 26, height: 26, borderWidth: 3 }} />
              <span>Loading commission lines…</span>
            </div>
          )}
          <div className="spreadsheet-scroll">
            <table className="spreadsheet adj-table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th title="Management review categories derived from engine flags + over_5000_review">Categories</th>
                  <th><Tip text={TIP.zohoSp}>Original Zoho</Tip></th>
                  <th><Tip text={TIP.finalAssign}>Final Assignment</Tip></th>
                  <th>Customer</th>
                  <th>SO / Invoice</th>
                  <th>Item</th>
                  <th className="cell-number"><Tip text={TIP.calc}>Calculated</Tip></th>
                  <th className="cell-number"><Tip text={TIP.change}>Change</Tip></th>
                  <th className="cell-number"><Tip text={TIP.final}>Final</Tip></th>
                  <th className="cell-number" title="Per-line implied discount = 1 − revenue / (MAP × qty)">Disc %</th>
                  <th>Issue Found</th>
                  <th>Suggested Action</th>
                  <th>Quick Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, i) => {
                  const issue = issueFound(r);
                  const cats = rowCategories(r, soRevenueByOrder);
                  const disc = discountPct(r);
                  const retStatus = r.return_status || "";
                  return (
                    <tr key={r.line_uid + i} className={lineState(r).key === "needs" ? "row-needs" : ""}>
                      <td title={String(r.flags || "")}>{badge(r)}</td>
                      <td>
                        {cats.length === 0 ? (
                          <span className="text-faint">—</span>
                        ) : (
                          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                            {cats.map((id) => {
                              const def = CATEGORY_DEF_BY_ID[id];
                              if (!def) return null;
                              return (
                                <span key={id} className={`badge badge-${def.color}`} style={{ fontSize: 11, padding: "1px 6px" }} title={def.label}>
                                  {def.short}
                                </span>
                              );
                            })}
                          </div>
                        )}
                      </td>
                      <td className="cell-trunc" title={zohoSalesperson(r)}>{zohoSalesperson(r)}</td>
                      <td>{finalAssignment(r)}</td>
                      <td className="cell-trunc" title={r.customer}>{r.customer}</td>
                      <td>
                        <div>{r.sales_order}</div>
                        <div className="text-faint" style={{ fontSize: 11 }}>{r.invoice}</div>
                        {(r.over_5000_review || (r.sales_order && (soRevenueByOrder[r.sales_order] || 0) > 5000)) && (
                          <div style={{ marginTop: 2 }}>
                            <span className="badge badge-gray" style={{ fontSize: 10, padding: "0 5px" }} title="Sales order revenue total exceeds $5,000 (informational annotation; does not affect commission)">
                              &gt;$5K
                            </span>
                          </div>
                        )}
                      </td>
                      <td className="cell-trunc" title={r.item_name}>
                        <div>{r.sku}</div>
                        {retStatus && (
                          <div style={{ marginTop: 2 }}>
                            <span className="badge badge-red" style={{ fontSize: 10, padding: "0 5px" }} title="Return status">
                              {retStatus}
                            </span>
                          </div>
                        )}
                      </td>
                      <td className="cell-number">{money(r.system_commission)}</td>
                      <td className="cell-number" style={{ color: r.adjustment < 0 ? "var(--bb-rose)" : r.adjustment > 0 ? "var(--bb-emerald)" : "inherit" }}>
                        {r.adjustment ? money(r.adjustment) : "—"}
                      </td>
                      <td className="cell-number text-bold">{money(r.final_commission)}</td>
                      <td className="cell-number" title={disc == null ? "Not computable (MAP or qty is 0)" : `${(disc*100).toFixed(2)}%`}>
                        {disc == null ? <span className="text-faint">—</span> : `${(disc*100).toFixed(1)}%`}
                      </td>
                      <td>{issue ? <span className="issue-text">{issue}</span> : <span className="text-faint">—</span>}</td>
                      <td className="text-faint">{suggestedAction(r)}</td>
                      <td>
                        <div className="quick-actions">
                          <button type="button" className="btn btn-xs" title="Assign to a salesperson" onClick={() => openEdit(r, { approval_status: "pending" })}>Assign</button>
                          <button type="button" className="btn btn-xs" title="Move to Company Account" onClick={() => openEdit(r, { classification: "company" })}>Company</button>
                          <button type="button" className="btn btn-xs" title="Move to Executive Account" onClick={() => openEdit(r, { classification: "executive" })}>Exec</button>
                          <button type="button" className="btn btn-xs btn-danger-ghost" title="Exclude from commission" onClick={() => setQuickConfirm({ action: "exclude", row: r })}>Exclude</button>
                          <button type="button" className="btn btn-xs" title="Override MAP / discount / amount" onClick={() => openEdit(r)}>Override</button>
                          <button type="button" className="btn btn-xs btn-success-ghost" title="Approve this line" onClick={() => setQuickConfirm({ action: "approve", row: r })}>Approve</button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
                {filtered.length === 0 && !loading && (
                  <tr><td colSpan={14}><div className="empty-state"><div className="empty-state-icon"><IconCheck /></div>
                    <p className="empty-state-title">Nothing to review here</p>
                    <p className="empty-state-desc">Try the "All lines" chip or change the filters.</p></div></td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>
      </div>

      {/* ---- Edit drawer ---- */}
      {editing && form && (
        <div className="adj-drawer-backdrop" onClick={closeDrawer}>
          <div className="adj-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="adj-drawer-head">
              <div>
                <strong>Accounting Decision</strong>
                <div className="text-faint" style={{ fontSize: 12 }}>{editing.invoice} · {editing.sku}</div>
              </div>
              <button type="button" className="btn btn-icon btn-ghost" onClick={closeDrawer}><IconX /></button>
            </div>

            <div className="adj-drawer-body">
              {/* A. Line Information */}
              <div className="drawer-section">
                <div className="drawer-section-title">Line Information</div>
                <div className="info-grid">
                  <div><span>Customer</span><strong>{editing.customer || "—"}</strong></div>
                  <div><span>Sales Order</span><strong>{editing.sales_order || "—"}</strong></div>
                  <div><span>Invoice</span><strong>{editing.invoice || "—"}</strong></div>
                  <div><span>Item</span><strong title={editing.item_name}>{editing.sku || "—"}</strong></div>
                  <div><span>Sales Team</span><strong>{editing.sales_team || "—"}</strong></div>
                  <div><span><Tip text={TIP.zohoSp}>Original Zoho Salesperson</Tip></span><strong>{zohoSalesperson(editing)}</strong></div>
                  <div><span><Tip text={TIP.finalAssign}>Final Commission Assignment</Tip></span><strong>{finalAssignment(editing)}</strong></div>
                  {editing.accounting_category && (
                    <div><span><Tip text={TIP.acctCat}>Accounting Category</Tip></span><strong>{editing.accounting_category}</strong></div>
                  )}
                  <div><span><Tip text={TIP.calc}>Calculated Commission</Tip></span><strong>{money(editing.system_commission)}</strong></div>
                  <div><span><Tip text={TIP.final}>Final Commission</Tip></span><strong>{money(editing.final_commission)}</strong></div>
                </div>
                {issueFound(editing) && <div className="drawer-issue"><IconAlert /> {issueFound(editing)} — <em>{suggestedAction(editing)}</em></div>}
              </div>

              {/* B. Accounting Decision */}
              <div className="drawer-section">
                <div className="drawer-section-title">Accounting Decision</div>

                <div className="field">
                  <label className="field-label"><Tip text={TIP.assign}>Assign to Salesperson</Tip></label>
                  <select className="select" value={form.adjusted_salesperson} onChange={(e) => setForm({ ...form, adjusted_salesperson: e.target.value })}>
                    <option value="">— no change —</option>
                    {roster.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>

                <div className="row" style={{ gap: "0.6rem" }}>
                  <div className="field" style={{ flex: 1 }}>
                    <label className="field-label"><Tip text={TIP.category}>Accounting Category</Tip></label>
                    <select className="select" value={form.classification} onChange={(e) => setForm({ ...form, classification: e.target.value })}>
                      <option value="">— none —</option>
                      <option value="company">Company Account</option>
                      <option value="executive">Executive Account</option>
                    </select>
                  </div>
                  <label className="field exclude-toggle">
                    <span className="field-label"><Tip text={TIP.exclude}>Exclude from Commission</Tip></span>
                    <input type="checkbox" checked={form.exclude_flag} onChange={(e) => setForm({ ...form, exclude_flag: e.target.checked })} />
                  </label>
                </div>

                <details className="drawer-advanced">
                  <summary>Advanced overrides (changes the amount)</summary>
                  <div className="row" style={{ gap: "0.6rem", marginTop: "0.5rem" }}>
                    <div className="field" style={{ flex: 1 }}>
                      <label className="field-label"><Tip text={TIP.overrideComm}>Override Commissionable</Tip></label>
                      <input className="input" type="number" step="0.01" value={form.adjusted_commissionable} onChange={(e) => setForm({ ...form, adjusted_commissionable: e.target.value })} placeholder="no change" />
                    </div>
                    <div className="field" style={{ flex: 1 }}>
                      <label className="field-label"><Tip text={TIP.overrideMap}>Override MAP</Tip></label>
                      <input className="input" type="number" step="0.01" value={form.adjusted_map} onChange={(e) => setForm({ ...form, adjusted_map: e.target.value })} placeholder="no change" />
                    </div>
                    <div className="field" style={{ flex: 1 }}>
                      <label className="field-label"><Tip text={TIP.overrideDisc}>Override Discount (0–1)</Tip></label>
                      <input className="input" type="number" step="0.01" min="0" max="1" value={form.adjusted_discount} onChange={(e) => setForm({ ...form, adjusted_discount: e.target.value })} placeholder="no change" />
                    </div>
                  </div>
                </details>

                <div className="field">
                  <label className="field-label"><Tip text={TIP.reason}>Reason / Notes</Tip> <span className="req">required</span></label>
                  <input className="input" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="Why is this adjustment being made?" />
                </div>
                <div className="row" style={{ gap: "0.6rem" }}>
                  <div className="field" style={{ flex: 1 }}>
                    <label className="field-label">Reviewer</label>
                    <input className="input" value={form.reviewer} onChange={(e) => setForm({ ...form, reviewer: e.target.value })} placeholder="your name" />
                  </div>
                  <div className="field" style={{ flex: 1 }}>
                    <label className="field-label"><Tip text={TIP.reviewStatus}>Review Status</Tip></label>
                    <select className="select" value={form.approval_status} onChange={(e) => setForm({ ...form, approval_status: e.target.value })}>
                      <option value="pending">Pending</option>
                      <option value="approved">Approved</option>
                      <option value="rejected">Rejected</option>
                    </select>
                  </div>
                </div>
              </div>

              {/* C. Impact Preview */}
              <div className="drawer-section impact">
                <div className="drawer-section-title">Impact Preview</div>
                <div className="impact-grid">
                  <div><span>Calculated Commission</span><strong>{money(editing.system_commission)}</strong></div>
                  <div><span>Change</span><strong style={{ color: estChange == null ? "inherit" : estChange < 0 ? "var(--bb-rose)" : estChange > 0 ? "var(--bb-emerald)" : "inherit" }}>{estChange == null ? "recalculated on Save" : money(estChange)}</strong></div>
                  <div><span>Final Commission</span><strong>{estFinal == null ? "recalculated on Save" : money(estFinal)}</strong></div>
                  <div><span>Final Assignment</span><strong>{finalAssignment(editing)} → {salespersonAfter()}</strong></div>
                </div>
              </div>
            </div>

            <div className="adj-drawer-foot">
              {editing.adjustment_record?.id ? (
                <button type="button" className="btn btn-danger" onClick={removeAdjustment} disabled={loading}>Remove adjustment</button>
              ) : <span />}
              <div className="row" style={{ gap: "0.5rem" }}>
                <button type="button" className="btn btn-ghost" onClick={closeDrawer}>Cancel</button>
                <button type="button" className="btn btn-primary" onClick={requestSave} disabled={loading || !form.reason.trim()} title={!form.reason.trim() ? "Add a Reason / Notes first" : ""}>
                  Save Decision
                </button>
              </div>
            </div>

            {/* high-impact confirmation */}
            {confirmSave && (
              <div className="confirm-overlay" onClick={() => setConfirmSave(false)}>
                <div className="confirm-box" onClick={(e) => e.stopPropagation()}>
                  <div className="confirm-title"><IconAlert /> Confirm accounting adjustment</div>
                  <p>This will change the final commission amount. Please confirm this accounting adjustment.</p>
                  <div className="row" style={{ justifyContent: "flex-end", gap: "0.5rem" }}>
                    <button type="button" className="btn btn-ghost" onClick={() => setConfirmSave(false)}>Go back</button>
                    <button type="button" className="btn btn-primary" onClick={doSave} disabled={loading}>Confirm &amp; Save</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ---- Quick confirm modal (Approve / Exclude) ---- */}
      {quickConfirm && (
        <QuickConfirmModal
          action={quickConfirm.action}
          row={quickConfirm.row}
          onConfirm={handleQuickConfirm}
          onCancel={() => setQuickConfirm(null)}
        />
      )}

      {/* ---- Help modal ---- */}
      {showHelp && (
        <div className="adj-drawer-backdrop" onClick={() => setShowHelp(false)}>
          <div className="help-modal" onClick={(e) => e.stopPropagation()}>
            <div className="adj-drawer-head">
              <strong>Help: How Accounting Adjustments Work</strong>
              <button type="button" className="btn btn-icon btn-ghost" onClick={() => setShowHelp(false)}><IconX /></button>
            </div>
            <div className="help-body">
              <p><strong>What this screen is for.</strong> It lets Accounting review each commission line the system calculated automatically and make the final decisions before the workbook is paid out.</p>
              <p><strong>Calculated Commission</strong> is what the system computed from Zoho data. <strong>Final Commission</strong> is what will actually be paid after your decisions. <strong>Change</strong> is the difference between them.</p>
              <p><strong>Original Zoho Salesperson</strong> always shows who Zoho says owns the sale (e.g. Bruce Taylor, Marshall Neipert). It is never replaced with "unassigned."</p>
              <p><strong>Final Commission Assignment</strong> is who/what will be paid after your review. Lines for salespeople <em>not on the approved B2B roster</em> stay <strong>Pending</strong> with $0 final commission until you classify or assign them.</p>
              <p><strong>Needs Review</strong> means a line can't be finalized until you decide something — missing Zoho salesperson, non-roster salesperson, or Company/Executive classification.</p>
              <ul>
                <li><strong>Assign a salesperson</strong> when Zoho has no salesperson or you want to credit a roster rep.</li>
                <li><strong>Move to Company Account</strong> when the sale belongs to the house account, not an individual rep.</li>
                <li><strong>Move to Executive Account</strong> when it belongs to an executive/owner account.</li>
                <li><strong>Exclude a line</strong> when it should not earn commission at all (its Final becomes $0).</li>
                <li><strong>Override MAP / Discount / Amount</strong> only when the calculated price or discount is wrong; the rate is recalculated from your input.</li>
              </ul>
              <p><strong>Reason / Notes are required</strong> so every change has a documented explanation in the audit trail.</p>
              <p><strong>When an adjustment is Approved</strong>, it's marked ready to pay. <strong>Regenerating the workbook</strong> applies all approved/pending decisions: the salesperson sheets, B2B Summary, and the <em>Adjustments Audit</em> sheet all reflect the Final values, while raw Zoho data is never changed.</p>
              <p className="text-faint">The workbook stays a <strong>Draft</strong> until there are no unresolved Needs-Review lines.</p>
            </div>
            <div className="adj-drawer-foot" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn btn-primary" onClick={() => setShowHelp(false)}>Got it</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
