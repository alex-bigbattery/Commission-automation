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
  calc: "Calculated Commission — what the system computed automatically from Zoho data, before any accounting decision.",
  final: "Final Commission — what will actually be paid after your accounting decisions are applied.",
  change: "Change — the difference between Calculated and Final commission caused by your decision.",
  needsReview: "Needs Review — this line cannot be finalized until Accounting makes a decision (e.g. assign a salesperson or classify the account).",
  exclude: "Exclude from Commission — remove this line from commission entirely. Its Final Commission becomes $0.",
  category: "Accounting Category — move this line to the Company Account or Executive Account instead of a salesperson.",
  overrideComm: "Override Commissionable Amount — replace the dollar base used to calculate commission for this line.",
  overrideMap: "Override MAP — replace the list (MAP) price used to compute the discount for this line.",
  overrideDisc: "Override Discount / Rate — set the discount (0–1) directly; the commission rate tier is recalculated from it on Save.",
  reviewStatus: "Review Status — Pending (not decided), Approved (ready to pay), or Rejected.",
  reason: "Reason / Notes — required. Explains why this adjustment was made, for the audit trail.",
  assign: "Assign to Salesperson — credit this line to a specific salesperson.",
};

// ---- Derived line state / issue / action ---------------------------------
function lineState(r) {
  if (r.excluded) return { key: "excluded", label: "Excluded", color: "red" };
  const cls = (r.classification || "").toLowerCase();
  if (cls === "company") return { key: "company", label: "Company Account", color: "blue" };
  if (cls === "executive") return { key: "executive", label: "Executive Account", color: "blue" };
  const appr = (r.approval_status || "").toLowerCase();
  if (appr === "approved") return { key: "approved", label: "Approved", color: "green" };
  if (r.pending) return { key: "needs", label: "Needs Review", color: "yellow" };
  const flags = String(r.flags || "");
  if (flags.includes("MISSING_MAP") || flags.includes("UNPAID")) return { key: "needs", label: "Needs Review", color: "yellow" };
  return { key: "ready", label: "Ready", color: "gray" };
}

function issueFound(r) {
  const flags = String(r.flags || "");
  const team = String(r.sales_team || "").toLowerCase();
  if (flags.includes("FULLY_RETURNED")) return "Fully returned — not commissionable";
  if (flags.includes("PARTIALLY_RETURNED")) return "Partially returned";
  if (r.pending && (team.includes("exe") || team.includes("comp")))
    return "Company / Executive account needs classification";
  if (r.pending) return "Missing salesperson assignment";
  if (flags.includes("MISSING_MAP")) return "MAP / discount difference";
  if (flags.includes("UNPAID")) return "Invoice not paid yet";
  if (r.block === "shipping") return "Shipping line";
  if (r.section === "II") return "Prior-period order";
  if (flags.includes("UNASSIGNED")) return "Manual review required";
  return "";
}

function suggestedAction(r) {
  const team = String(r.sales_team || "").toLowerCase();
  const flags = String(r.flags || "");
  if (flags.includes("FULLY_RETURNED")) return "Returned — verify $0 commission";
  if (flags.includes("PARTIALLY_RETURNED")) return "Partial return — verify kept qty";
  if (r.pending && (team.includes("exe") || team.includes("comp"))) return "Classify as Company / Executive";
  if (r.pending) return "Assign salesperson";
  if (String(r.flags || "").includes("MISSING_MAP")) return "Review MAP / discount";
  if (String(r.flags || "").includes("UNPAID")) return "Confirm payment, then approve";
  if (r.excluded) return "Review exclusion";
  if ((r.approval_status || "").toLowerCase() === "approved") return "—";
  return "Approve if correct";
}

const VIEWS = [
  ["needs", "Needs Review"],
  ["all", "All lines"],
  ["approved", "Approved"],
  ["excluded", "Excluded"],
  ["company_exec", "Company / Executive"],
];

const BLANK_FILTERS = { salesperson: "", sales_team: "", issue: "", action: "", sales_order: "", invoice: "", sku: "" };

function Tip({ text, children }) {
  return (
    <span className="tip" title={text}>
      {children}<sup className="tip-mark">?</sup>
    </span>
  );
}

export default function AdjustmentsView() {
  const p = prevMonth();
  const [year, setYear] = useState(p.year);
  const [month, setMonth] = useState(p.month);
  const [view, setView] = useState("needs");
  const [filters, setFilters] = useState(BLANK_FILTERS);
  const [rows, setRows] = useState([]);
  const [roster, setRoster] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadingPeriod, setLoadingPeriod] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(null);
  const [showHelp, setShowHelp] = useState(false);
  const [confirmSave, setConfirmSave] = useState(false);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [year, month]);

  async function load() {
    setLoading(true);
    setLoadingPeriod(true);
    setError("");
    try {
      const data = await readJson(await apiFetch(`${API}/adjustments/lines?year=${year}&month=${month}`));
      setRows(data.rows || []);
      setRoster(data.roster || []);
    } catch (err) { setError(err); setRows([]); }
    finally { setLoading(false); setLoadingPeriod(false); }
  }

  const salespeopleOptions = useMemo(() => {
    const set = new Set(roster);
    rows.forEach((r) => { if (r.salesperson) set.add(r.salesperson); });
    return [...set].sort();
  }, [rows, roster]);
  const teamOptions = useMemo(() => [...new Set(rows.map((r) => r.sales_team).filter(Boolean))].sort(), [rows]);
  const issueOptions = useMemo(() => [...new Set(rows.map(issueFound).filter(Boolean))].sort(), [rows]);
  const actionOptions = useMemo(() => [...new Set(rows.map(suggestedAction).filter((a) => a && a !== "—"))].sort(), [rows]);

  const filtered = useMemo(() => {
    const inc = (hay, needle) => String(hay || "").toLowerCase().includes(needle.toLowerCase());
    let list = rows.filter((r) => {
      const st = lineState(r).key;
      if (view === "needs" && st !== "needs") return false;
      if (view === "approved" && st !== "approved") return false;
      if (view === "excluded" && st !== "excluded") return false;
      if (view === "company_exec" && !(st === "company" || st === "executive")) return false;
      if (filters.salesperson && r.salesperson !== filters.salesperson) return false;
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
  }, [rows, view, filters]);

  const totals = useMemo(() => {
    let sys = 0, fin = 0, adj = 0, needs = 0, exc = 0;
    for (const r of rows) {
      if (!r.pending) sys += r.system_commission || 0;
      fin += r.final_commission || 0;
      if (r.adjusted) adj += 1;
      if (lineState(r).key === "needs") needs += 1;
      if (r.excluded) exc += 1;
    }
    return { sys, fin, delta: fin - sys, adj, needs, exc };
  }, [rows]);

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

  async function regenerate() {
    setLoading(true); setError(""); setStatus("Regenerating the workbook with adjustments…");
    try {
      const d = await readJson(await apiFetch(`${API}/commission/generate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ year, month }),
      }));
      setStatus(`Workbook regenerated: ${d.report_id} (includes the “Adjustments Audit” sheet).`);
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
    if (form.exclude_flag) return "— (excluded)";
    if (form.classification === "company") return "Company Acct";
    if (form.classification === "executive") return "Executive";
    return form.adjusted_salesperson || editing.salesperson;
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
          <div className="adj-chips">
            {VIEWS.map(([k, lbl]) => {
              const count = k === "all" ? rows.length : rows.filter((r) => {
                const st = lineState(r).key;
                if (k === "company_exec") return st === "company" || st === "executive";
                return st === k;
              }).length;
              return (
                <button key={k} type="button" className={`chip ${view === k ? "chip-active" : ""}`} onClick={() => setView(k)}>
                  {lbl} <span className="chip-count">{count}</span>
                </button>
              );
            })}
          </div>

          <div className="row" style={{ flexWrap: "wrap", gap: "0.6rem", alignItems: "flex-end" }}>
            <div className="field" style={{ minWidth: 150, flex: "1 1 150px" }}>
              <label className="field-label">Salesperson</label>
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
            <div className="field" style={{ minWidth: 110, flex: "1 1 110px" }}>
              <label className="field-label">Invoice</label>
              <input className="input" value={filters.invoice} onChange={(e) => setFilters({ ...filters, invoice: e.target.value })} placeholder="search…" />
            </div>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => { setFilters(BLANK_FILTERS); setView("all"); }}>Clear</button>
          </div>

          <div className="adj-legend">
            <span><i className="dot dot-green" /> Approved</span>
            <span><i className="dot dot-yellow" /> Needs Review</span>
            <span><i className="dot dot-red" /> Excluded</span>
            <span><i className="dot dot-blue" /> Company / Executive</span>
            <span><i className="dot dot-gray" /> Informational</span>
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
                  <th>Salesperson</th>
                  <th>Customer</th>
                  <th>SO / Invoice</th>
                  <th>Item</th>
                  <th className="cell-number"><Tip text={TIP.calc}>Calculated</Tip></th>
                  <th className="cell-number"><Tip text={TIP.change}>Change</Tip></th>
                  <th className="cell-number"><Tip text={TIP.final}>Final</Tip></th>
                  <th>Issue Found</th>
                  <th>Suggested Action</th>
                  <th>Quick Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, i) => {
                  const issue = issueFound(r);
                  return (
                    <tr key={r.line_uid + i} className={lineState(r).key === "needs" ? "row-needs" : ""}>
                      <td>{badge(r)}</td>
                      <td>{r.salesperson}</td>
                      <td className="cell-trunc" title={r.customer}>{r.customer}</td>
                      <td><div>{r.sales_order}</div><div className="text-faint" style={{ fontSize: 11 }}>{r.invoice}</div></td>
                      <td className="cell-trunc" title={r.item_name}>{r.sku}</td>
                      <td className="cell-number">{money(r.system_commission)}</td>
                      <td className="cell-number" style={{ color: r.adjustment < 0 ? "var(--bb-rose)" : r.adjustment > 0 ? "var(--bb-emerald)" : "inherit" }}>
                        {r.adjustment ? money(r.adjustment) : "—"}
                      </td>
                      <td className="cell-number text-bold">{money(r.final_commission)}</td>
                      <td>{issue ? <span className="issue-text">{issue}</span> : <span className="text-faint">—</span>}</td>
                      <td className="text-faint">{suggestedAction(r)}</td>
                      <td>
                        <div className="quick-actions">
                          <button type="button" className="btn btn-xs" title="Assign to a salesperson" onClick={() => openEdit(r, { approval_status: "pending" })}>Assign</button>
                          <button type="button" className="btn btn-xs" title="Move to Company Account" onClick={() => openEdit(r, { classification: "company" })}>Company</button>
                          <button type="button" className="btn btn-xs" title="Move to Executive Account" onClick={() => openEdit(r, { classification: "executive" })}>Exec</button>
                          <button type="button" className="btn btn-xs btn-danger-ghost" title="Exclude from commission" onClick={() => openEdit(r, { exclude_flag: true })}>Exclude</button>
                          <button type="button" className="btn btn-xs" title="Override MAP / discount / amount" onClick={() => openEdit(r)}>Override</button>
                          <button type="button" className="btn btn-xs btn-success-ghost" title="Approve this line" onClick={() => openEdit(r, { approval_status: "approved" })}>Approve</button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
                {filtered.length === 0 && !loading && (
                  <tr><td colSpan={11}><div className="empty-state"><div className="empty-state-icon"><IconCheck /></div>
                    <p className="empty-state-title">Nothing to review here</p>
                    <p className="empty-state-desc">Try the “All lines” chip or change the filters.</p></div></td></tr>
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
                  <div><span>Current Salesperson</span><strong>{editing.system_salesperson || "—"}</strong></div>
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
                  <div><span>Salesperson</span><strong>{editing.system_salesperson} → {salespersonAfter()}</strong></div>
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
              <p><strong>Needs Review</strong> means a line can't be finalized until you decide something — usually a missing salesperson or a Company/Executive account that needs classifying.</p>
              <ul>
                <li><strong>Assign a salesperson</strong> when a B2B line has no salesperson (or the wrong one).</li>
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
