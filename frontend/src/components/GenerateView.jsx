import React, { useEffect, useMemo, useState } from "react";
import SpreadsheetView from "./SpreadsheetView.jsx";
import { KpiCard, Banner, ErrorBanner, money, num, LoadingNotice } from "./ui.jsx";
import {
  IconSparkle,
  IconSync,
  IconDownload,
  IconDollar,
  IconChart,
  IconList,
  IconAlert,
  IconCheck,
  IconInfo,
  IconHistory,
} from "./Icons.jsx";

import { API, apiFetch, downloadApi, readJson } from "../lib/api.js";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function previousMonthPeriod() {
  // Commissions are run after the month closes, so default to the previous month.
  const now = new Date();
  const d = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return { year: d.getFullYear(), month: d.getMonth() + 1 };
}

export default function GenerateView() {
  const period = previousMonthPeriod();
  const [year, setYear] = useState(period.year);
  const [month, setMonth] = useState(period.month);

  const [periodReady, setPeriodReady] = useState(false);
  const [periodCounts, setPeriodCounts] = useState({});
  const [summary, setSummary] = useState(null); // {generated, report_id, kpis, ...}
  const [exceptions, setExceptions] = useState({ columns: [], rows: [] });
  const [sheets, setSheets] = useState([]);
  const [activeSheet, setActiveSheet] = useState("");
  const [preview, setPreview] = useState({ columns: [], rows: [] });

  const [loading, setLoading] = useState(false);
  const [loadingPeriod, setLoadingPeriod] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [tab, setTab] = useState("exceptions"); // exceptions | preview

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, month]);

  async function refresh() {
    setLoadingPeriod(true);
    setError("");
    try {
      const input = await readJson(
        await apiFetch(`${API}/input/status?year=${year}&month=${month}`)
      );
      setPeriodReady(Boolean(input?.sqlite_period?.ready));
      setPeriodCounts(input?.sqlite_period?.counts || {});

      const sum = await readJson(await apiFetch(`${API}/commission/summary?year=${year}&month=${month}`));
      if (sum.generated) {
        setSummary(sum);
        await loadExceptions(sum.report_id);
        await loadSheets(sum.report_id);
      } else {
        setSummary(null);
        setExceptions({ columns: [], rows: [] });
        setSheets([]);
        setActiveSheet("");
        setPreview({ columns: [], rows: [] });
      }
    } catch (err) {
      setError(err);
    } finally {
      setLoadingPeriod(false);
    }
  }

  async function loadExceptions() {
    const data = await readJson(
      await apiFetch(`${API}/commission/exceptions?year=${year}&month=${month}`)
    );
    setExceptions({ columns: data.columns || [], rows: data.rows || [] });
  }

  async function loadSheets(reportId) {
    try {
      const data = await readJson(
        await apiFetch(`${API}/workbooks/${encodeURIComponent(reportId)}/sheets?source=report`)
      );
      setSheets(data.sheets || []);
      const first = (data.sheets || [])[0] || "";
      setActiveSheet(first);
      if (first) loadSheet(reportId, first);
    } catch {
      setSheets([]);
    }
  }

  async function loadSheet(reportId, sheet) {
    try {
      const data = await readJson(
        await apiFetch(
          `${API}/workbooks/${encodeURIComponent(reportId)}/sheets/${encodeURIComponent(sheet)}?source=report`
        )
      );
      setPreview({ columns: data.columns || [], rows: data.rows || [] });
    } catch (err) {
      setError(err);
    }
  }

  async function generate() {
    setLoading(true);
    setError("");
    setStatus("Generating the commission workbook from SQLite…");
    try {
      const data = await readJson(
        await apiFetch(`${API}/commission/generate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ year, month }),
        })
      );
      setStatus(`Workbook generated: ${data.report_id}`);
      await refresh();
      setTab(data.exception_count > 0 ? "exceptions" : "preview");
    } catch (err) {
      setError(err);
      setStatus("");
    } finally {
      setLoading(false);
    }
  }

  async function syncLatest() {
    setSyncing(true);
    setError("");
    setStatus("Syncing latest Zoho data… this runs in the background and can take a few minutes.");
    try {
      // Kick off the sync (returns immediately — no gateway timeout).
      await readJson(
        await apiFetch(`${API}/sync/incremental`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        })
      );
      // Poll the background job until it finishes (keeps the server awake too).
      let finished = false;
      for (let i = 0; i < 240 && !finished; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const st = await readJson(await apiFetch(`${API}/sync/incremental/status`));
        if (st.status && st.status !== "running") {
          finished = true;
          if (st.status === "failed") {
            throw new Error((st.errors && st.errors.length ? st.errors.join("; ") : "Sync failed."));
          }
          setStatus("Zoho data synced.");
          await refresh();
        }
      }
      if (!finished) {
        setStatus("Sync is still running in the background — check back in a moment.");
      }
    } catch (err) {
      setError(err);
      setStatus("");
    } finally {
      setSyncing(false);
    }
  }

  async function handleDownload() {
    if (!summary?.report_id) return;
    try {
      await downloadApi(`${API}/downloads/reports/${encodeURIComponent(summary.report_id)}`, summary.report_id);
    } catch (err) {
      setError(err);
    }
  }

  const kpis = summary?.kpis || {};
  const canDownload = Boolean(summary?.report_id);

  const topReps = useMemo(() => {
    const totals = summary?.totals_by_sheet || {};
    return Object.entries(totals)
      .filter(([, v]) => Number(v) > 0)
      .sort((a, b) => b[1] - a[1]);
  }, [summary]);

  const periodBusy = loadingPeriod || loading || syncing;
  const periodLabel = `${MONTHS[month - 1]} ${year}`;

  return (
    <div className="page">
      {/* Step 1 — period + actions */}
      <section className="workflow-bar">
        <div className="workflow-period">
          <div className="field">
            <label className="field-label" htmlFor="gen-year">Year</label>
            <input
              id="gen-year"
              className="input"
              type="number"
              min="2020"
              max="2100"
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
            disabled={periodBusy}
          />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="gen-month">Month</label>
            <select id="gen-month" className="select" value={month} onChange={(e) => setMonth(Number(e.target.value))} disabled={periodBusy}>
              {MONTHS.map((name, idx) => (
                <option key={name} value={idx + 1}>{name}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="workflow-step-divider" />

        <div className="workflow-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={generate}
            disabled={loading || syncing || !periodReady}
            title={periodReady ? "Generate the month's commission workbook" : "Sync this month's Zoho data first"}
          >
            {loading ? <span className="spinner" /> : <IconSparkle />}
            {loading ? "Generating…" : "Generate Commissions"}
          </button>
          <button type="button" className="btn" disabled={!canDownload} onClick={handleDownload}>
            <IconDownload /> Download workbook
          </button>
        </div>

        <div className="row" style={{ marginLeft: "auto", gap: "0.5rem", flexWrap: "wrap" }}>
          {loadingPeriod && <LoadingNotice>Loading {periodLabel}…</LoadingNotice>}
          <span className={`pill ${periodReady ? "pill-success" : "pill-warning"}`}>
            <span className="pill-dot" />
            {periodReady ? "Data ready in SQLite" : "No data for this month"}
          </span>
          <button type="button" className="btn btn-sm" onClick={syncLatest} disabled={syncing || loading}>
            {syncing ? <span className="spinner" /> : <IconSync />}
            Sync Zoho
          </button>
        </div>
      </section>

      {status && (
        <Banner type="success" icon={IconCheck}>{status}</Banner>
      )}
      {error && (
        <ErrorBanner error={error} onRetry={refresh} />
      )}
      {!periodReady && !loadingPeriod && (
        <Banner type="warning" icon={IconAlert}>
          No SQLite data for <strong>{periodLabel}</strong>. Use{" "}
          <strong>Sync Zoho</strong> to pull this month's sales orders, invoices, and shipments.
        </Banner>
      )}

      <div className={loadingPeriod ? "content-loading" : ""}>
      {/* SQLite period snapshot */}
      <section className="kpi-grid">
        <div className="kpi-section-label">Period data in SQLite ({periodLabel})</div>
        <KpiCard label="Sales Orders" value={num(periodCounts.sales_orders)} icon={IconList} />
        <KpiCard label="Invoices" value={num(periodCounts.invoices)} icon={IconList} />
        <KpiCard label="Shipments" value={num(periodCounts.shipments)} icon={IconList} />
        <KpiCard label="Items (catalog)" value={num(periodCounts.items)} icon={IconList} />
      </section>

      {/* Result KPIs */}
      {summary?.generated ? (
        <section className="kpi-grid">
          <div className="kpi-section-label">Calculation result</div>
          <KpiCard variant="money" label="Total commission" value={money(kpis.total_commission)} icon={IconDollar} />
          <KpiCard variant="success" label="Salespeople with sales" value={num(kpis.salespeople_with_sales)} icon={IconChart} />
          <KpiCard label="Commissionable lines" value={num(kpis.commissionable_lines)} icon={IconList} />
          <KpiCard variant="warning" label="Exceptions to review" value={num(kpis.exceptions_count)} icon={IconAlert} />
          <KpiCard variant="money" label="Current period revenue" value={money(kpis.revenue_current)} icon={IconDollar} />
          <KpiCard variant="money" label="Prior periods revenue" value={money(kpis.revenue_prior)} icon={IconHistory} />
        </section>
      ) : (
        <section className="card">
          <div className="empty-state">
            <div className="empty-state-icon"><IconSparkle /></div>
            <p className="empty-state-title">You haven't generated the {periodLabel} workbook yet</p>
            <p className="empty-state-desc">
              {periodReady
                ? "Click “Generate Commissions” to build the B2B-style workbook with every salesperson and the summary sheet."
                : "Sync this month's Zoho data, then click “Generate Commissions”."}
            </p>
          </div>
        </section>
      )}

      {/* Top reps */}
      {summary?.generated && topReps.length > 0 && (
        <section className="card">
          <div className="card-header">
            <h3 className="card-title"><IconChart /> Commission by salesperson (estimate)</h3>
            <span className="pill pill-info">{topReps.length} with sales</span>
          </div>
          <div className="card-body">
            <div className="status-bar">
              {topReps.map(([sheet, total]) => (
                <span key={sheet} className="pill">
                  <strong style={{ marginRight: 4 }}>{sheet}</strong> {money(total)}
                </span>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Exceptions + preview */}
      {summary?.generated && (
        <section className="card">
          <div className="table-toolbar" style={{ borderRadius: "14px 14px 0 0" }}>
            <div className="tabs" style={{ border: "none" }}>
              <button
                type="button"
                className={`tab ${tab === "exceptions" ? "active" : ""}`}
                onClick={() => setTab("exceptions")}
              >
                Exceptions to review
                <span className="tab-count">{exceptions.rows.length}</span>
              </button>
              <button
                type="button"
                className={`tab ${tab === "preview" ? "active" : ""}`}
                onClick={() => setTab("preview")}
              >
                Workbook preview
              </button>
            </div>
            {tab === "preview" && sheets.length > 0 && (
              <select
                className="select"
                style={{ maxWidth: 220, minWidth: 0, flex: "0 1 220px" }}
                value={activeSheet}
                onChange={(e) => {
                  setActiveSheet(e.target.value);
                  loadSheet(summary.report_id, e.target.value);
                }}
              >
                {sheets.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            )}
          </div>
          <div className="card-body" style={{ padding: 0 }}>
            {tab === "exceptions" ? (
              exceptions.rows.length === 0 ? (
                <div className="empty-state">
                  <div className="empty-state-icon"><IconCheck /></div>
                  <p className="empty-state-title">No exceptions</p>
                  <p className="empty-state-desc">No line needs manual review this month.</p>
                </div>
              ) : (
                <SpreadsheetView
                  columns={exceptions.columns}
                  rows={exceptions.rows}
                  loading={loadingPeriod}
                  sheetName="Exceptions"
                  emptyHint="No exceptions."
                />
              )
            ) : (
              <SpreadsheetView
                columns={preview.columns}
                rows={preview.rows}
                loading={loadingPeriod}
                sheetName={activeSheet}
                emptyHint="Select a sheet to preview."
              />
            )}
          </div>
        </section>
      )}

      </div>

      <Banner type="info" icon={IconInfo}>
        The workbook is built with the usual B2B structure (one sheet per salesperson + B2B Summary, with live formulas).
        Commissions are based on the month's invoices; orders from prior months invoiced now go under
        “prior periods”. Shipping decisions and edge cases appear in <strong>Exceptions to review</strong>.
      </Banner>
    </div>
  );
}
