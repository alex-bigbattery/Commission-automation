import React, { useEffect, useMemo, useState } from "react";
import SpreadsheetView from "./SpreadsheetView.jsx";
import { apiFetch, downloadApi, readJson } from "../lib/api.js";
import { LoadingNotice } from "./ui.jsx";

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

const HISTORICAL_UPLOAD = { key: "b2b", label: "Historical Commission Workbook (optional)" };

function toBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || "");
      resolve(text.includes(",") ? text.split(",")[1] : text);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function currentMonthPeriod() {
  const now = new Date();
  return { year: now.getFullYear(), month: now.getMonth() + 1 };
}

function formatSyncTime(value) {
  if (!value) return "Never";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export default function AuditReviewView() {
  const period = currentMonthPeriod();
  const [year, setYear] = useState(period.year);
  const [month, setMonth] = useState(period.month);
  const [historicalUpload, setHistoricalUpload] = useState(null);
  const [statusCards, setStatusCards] = useState({
    sales_orders_count: 0,
    invoice_count: 0,
    shipment_count: 0,
    items_count: 0,
    commissionable_lines: 0,
    exceptions_count: 0,
    estimated_commission: 0,
    amount_under_review: 0,
    matched_lines: 0,
    historical_workbook_only_lines: 0,
    system_only_lines: 0,
    amount_difference_total: 0,
  });
  const [reportId, setReportId] = useState("");
  const [validation, setValidation] = useState({ columns: [], rows: [] });
  const [lineMatch, setLineMatch] = useState({ columns: [], rows: [] });
  const [exceptions, setExceptions] = useState({ columns: [], rows: [] });
  const [tableView, setTableView] = useState("exceptions");
  const [loading, setLoading] = useState(false);
  const [loadingPeriod, setLoadingPeriod] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [dataSource, setDataSource] = useState("Zoho SQLite");
  const [inputStatus, setInputStatus] = useState(null);
  const [hasHistoricalValidation, setHasHistoricalValidation] = useState(false);
  const [historicalOpen, setHistoricalOpen] = useState(false);
  const [dbStatus, setDbStatus] = useState(null);

  useEffect(() => {
    refreshStatus();
    loadDbStatus();
  }, [year, month]);

  const reportRows = useMemo(() => {
    const sourceRows =
      tableView === "validation"
        ? validation.rows
        : tableView === "line_match"
          ? lineMatch.rows
          : exceptions.rows;
    if (!search.trim()) return sourceRows;
    const q = search.toLowerCase();
    return sourceRows.filter((row) =>
      Object.values(row).some((value) => String(value ?? "").toLowerCase().includes(q))
    );
  }, [search, tableView, validation.rows, lineMatch.rows, exceptions.rows]);

  const reportColumns =
    tableView === "validation"
      ? validation.columns
      : tableView === "line_match"
        ? lineMatch.columns
        : exceptions.columns;
  const reportTitle =
    tableView === "validation"
      ? "Historical Validation"
      : tableView === "line_match"
        ? "Line Match Audit"
        : "Exceptions Review";

  const periodReady = Boolean(inputStatus?.sqlite_period?.ready);
  const hasHistoricalWorkbook = Boolean(inputStatus?.files?.b2b?.exists);

  async function loadDbStatus() {
    try {
      const data = await readJson(await apiFetch(`db/status`));
      setDbStatus(data);
    } catch {
      setDbStatus(null);
    }
  }

  async function refreshStatus(overrideReportId = "") {
    setLoadingPeriod(true);
    setError("");
    try {
      const statusData = await readJson(
        await apiFetch(`input/status?year=${encodeURIComponent(year)}&month=${encodeURIComponent(month)}`)
      );
      setInputStatus(statusData);

      const sqliteCounts = statusData?.sqlite_period?.counts || {};
      const baseCards = {
        sales_orders_count: sqliteCounts.sales_orders ?? 0,
        invoice_count: sqliteCounts.invoices ?? 0,
        shipment_count: sqliteCounts.shipments ?? 0,
        items_count: sqliteCounts.items ?? 0,
        payments_count: sqliteCounts.payments ?? 0,
        commissionable_lines: 0,
        exceptions_count: 0,
        estimated_commission: 0,
        amount_under_review: 0,
        matched_lines: 0,
        historical_workbook_only_lines: 0,
        system_only_lines: 0,
        amount_difference_total: 0,
      };
      setStatusCards((prev) => ({ ...prev, ...baseCards }));

      setDataSource(statusData?.source?.source || "Zoho SQLite");

      const query = new URLSearchParams({ year: String(year), month: String(month) });
      if (overrideReportId) query.set("report_id", overrideReportId);

      try {
        const summary = await readJson(await apiFetch(`audit/summary?${query.toString()}`));
        setStatusCards((prev) => ({ ...prev, ...(summary.cards || {}) }));
        setReportId(summary.report_id || "");
        setHasHistoricalValidation(Boolean(summary?.has_historical_workbook || statusData?.files?.b2b?.exists));
        const summarySource = summary?.source?.source;
        if (summarySource && summarySource !== "Unknown") {
          setDataSource(summarySource);
        }
      } catch {
        setReportId("");
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingPeriod(false);
    }
  }

  async function runFullSync() {
    if (
      !window.confirm(
        "Initial Historical Sync pulls all Zoho data from 2021 onward into SQLite. This may take a long time. Continue?"
      )
    ) {
      return;
    }
    setLoading(true);
    setError("");
    setStatus("Running initial historical sync…");
    try {
      const data = await readJson(
        await apiFetch(`sync/full`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ date_start: "2021-01-01", date_end: "today" }),
        })
      );
      setDbStatus(data.db || null);
      await refreshStatus();
      setStatus("Initial historical sync completed. Data is stored in SQLite.");
    } catch (err) {
      setError(err.message);
      setStatus("");
    } finally {
      setLoading(false);
    }
  }

  async function runIncrementalSync() {
    setLoading(true);
    setError("");
    setStatus("Syncing latest Zoho data into SQLite…");
    try {
      const data = await readJson(
        await apiFetch(`sync/incremental`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        })
      );
      setDbStatus(data.db || null);
      await refreshStatus();
      setStatus("Latest Zoho data synced into SQLite.");
    } catch (err) {
      setError(err.message);
      setStatus("");
    } finally {
      setLoading(false);
    }
  }

  async function uploadHistoricalWorkbook() {
    if (!historicalUpload) {
      setError("Select a historical commission workbook first.");
      return;
    }
    setLoading(true);
    setError("");
    setStatus("");
    try {
      await readJson(
        await apiFetch(`uploads`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            year,
            month,
            files: [
              {
                kind: HISTORICAL_UPLOAD.key,
                filename: historicalUpload.name,
                content_base64: await toBase64(historicalUpload),
              },
            ],
          }),
        })
      );
      setStatus("Historical workbook uploaded (optional validation only).");
      await refreshStatus(reportId);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function runAudit() {
    setLoading(true);
    setError("");
    setStatus("Generating commission audit from SQLite…");
    try {
      const data = await readJson(
        await apiFetch(`audit/run`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ year, month }),
        })
      );
      setReportId(data.report_id || "");
      setStatus(`Commission audit generated: ${data.report_id}`);
      await refreshStatus(data.report_id || "");
      setTableView("exceptions");
      await loadExceptionsTables(data.report_id);
    } catch (err) {
      setError(err.message);
      setStatus("");
    } finally {
      setLoading(false);
    }
  }

  async function loadExceptionsTables(forReportId = reportId) {
    const id = forReportId || reportId;
    if (!id) return;
    const validationData = await readJson(
      await apiFetch(
        `workbooks/${encodeURIComponent(id)}/sheets/${encodeURIComponent("Legacy Validation vs Jennifer")}?source=report`
      )
    );
    const lineMatchData = await readJson(
      await apiFetch(
        `workbooks/${encodeURIComponent(id)}/sheets/${encodeURIComponent("Legacy Line Match vs Jennifer")}?source=report`
      )
    );
    const exceptionsData = await readJson(
      await apiFetch(`workbooks/${encodeURIComponent(id)}/sheets/${encodeURIComponent("Legacy Exceptions")}?source=report`)
    );
    setValidation({ columns: validationData.columns || [], rows: validationData.rows || [] });
    setLineMatch({ columns: lineMatchData.columns || [], rows: lineMatchData.rows || [] });
    setExceptions({ columns: exceptionsData.columns || [], rows: exceptionsData.rows || [] });
  }

  async function loadValidation() {
    if (!reportId) {
      setError("Generate a commission audit first.");
      return;
    }
    setLoading(true);
    setError("");
    setStatus("");
    try {
      await loadExceptionsTables();
      setStatus("Historical validation tables loaded.");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleDownloadReport() {
    if (!reportId) return;
    try {
      await downloadApi(`downloads/reports/${encodeURIComponent(reportId)}`, reportId);
    } catch (err) {
      setError(err.message);
    }
  }

  const latestRuns = Array.isArray(dbStatus?.latest_runs) ? dbStatus.latest_runs.slice(0, 6) : [];
  const tabCounts = {
    exceptions: exceptions.rows.length,
    line_match: lineMatch.rows.length,
    validation: validation.rows.length,
  };
  const currency = (value) =>
    Number(value ?? 0).toLocaleString(undefined, {
      style: "currency",
      currency: "USD",
    });

  const periodLabel = `${MONTHS[month - 1]} ${year}`;
  const periodBusy = loadingPeriod || loading;

  return (
    <div className="page">
      <section className="page-header">
        <div>
          <h2 className="page-title">Commission Audit / Review</h2>
          <p className="page-subtitle">Run the full monthly workflow from SQLite and review line-level exceptions.</p>
        </div>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <span className="pill pill-info">Source: {dataSource}</span>
          {reportId && <span className="pill">Report: {reportId}</span>}
        </div>
      </section>

      <section className="workflow-bar">
        <div className="workflow-period">
          <div className="field">
            <label className="field-label" htmlFor="audit-year">
              Year
            </label>
            <input
              className="input"
              id="audit-year"
              type="number"
              min="2020"
              max="2100"
              value={year}
              onChange={(e) => setYear(Number(e.target.value))}
              disabled={periodBusy}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="audit-month">
              Month
            </label>
            <select className="select" id="audit-month" value={month} onChange={(e) => setMonth(Number(e.target.value))} disabled={periodBusy}>
              {MONTHS.map((name, idx) => (
                <option key={name} value={idx + 1}>
                  {name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="workflow-actions">
          <div className="workflow-step">
            <span className="workflow-step-label">
              <span className="workflow-step-num">1</span>
              Sync
            </span>
            <div className="row">
              <button type="button" className="btn" onClick={runFullSync} disabled={loading}>
                Initial Historical Sync
              </button>
              <button type="button" className="btn" onClick={runIncrementalSync} disabled={loading}>
                Sync Latest Zoho Data
              </button>
            </div>
          </div>
          <div className="workflow-step-divider" />
          <div className="workflow-step">
            <span className="workflow-step-label">
              <span className="workflow-step-num">2</span>
              Generate
            </span>
            <div className="row">
              <button
                type="button"
                className="btn btn-primary"
                onClick={runAudit}
                disabled={loading || !periodReady}
                title={
                  periodReady
                    ? "Generate audit from SQLite for selected month"
                    : "Sync Zoho data first — need sales orders and invoices for this month in SQLite"
                }
              >
                Generate Commission Audit
              </button>
              <button type="button" className="btn" disabled={!reportId} onClick={handleDownloadReport}>
                Download Report
              </button>
            </div>
          </div>
        </div>

        {loadingPeriod && (
          <LoadingNotice>Loading {periodLabel}…</LoadingNotice>
        )}
      </section>

      {status && <div className="banner banner-success">{status}</div>}
      {error && <div className="banner banner-danger">{error}</div>}
      {!periodReady && !loadingPeriod && (
        <div className="banner banner-warning">
          No SQLite data for {periodLabel}. Run <strong>Sync Latest Zoho Data</strong> first.
        </div>
      )}

      <div className={loadingPeriod ? "content-loading" : ""}>
      <section className="card">
        <div className="card-header">
          <h3 className="card-title">SQLite Database Status</h3>
          <button type="button" className="btn btn-sm" onClick={loadDbStatus} disabled={loading}>
            Refresh
          </button>
        </div>
        <div className="card-body">
          <p className="text-muted" style={{ marginTop: 0 }}>
            Last successful sync: <strong>{formatSyncTime(dbStatus?.last_sync_time)}</strong>
            {dbStatus?.last_sync_status ? ` (${dbStatus.last_sync_status})` : ""}
          </p>
          <div className="status-bar">
            <span className="pill">Sales Orders: {dbStatus?.counts?.sales_orders ?? 0}</span>
            <span className="pill">Invoices: {dbStatus?.counts?.invoices ?? 0}</span>
            <span className="pill">Shipments: {dbStatus?.counts?.shipments ?? 0}</span>
            <span className="pill">Items: {dbStatus?.counts?.items ?? 0}</span>
            <span className="pill">Payments: {dbStatus?.counts?.customer_payments ?? 0}</span>
          </div>
          {latestRuns.length > 0 && (
            <ul className="sync-run-list">
              {latestRuns.map((run) => (
                <li key={`${run.sync_id}-${run.module}-${run.started_at}`}>
                  {run.module}: {run.status} ({run.records_fetched ?? 0} records)
                  {run.error_message ? ` — ${run.error_message}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="kpi-grid">
        <div className="kpi-section-label">Selected period in SQLite ({periodLabel})</div>
        <article className="kpi-card">
          <div className="kpi-card-label">Sales Orders</div>
          <div className="kpi-card-value">{statusCards.sales_orders_count ?? 0}</div>
        </article>
        <article className="kpi-card">
          <div className="kpi-card-label">Invoices</div>
          <div className="kpi-card-value">{statusCards.invoice_count ?? 0}</div>
        </article>
        <article className="kpi-card">
          <div className="kpi-card-label">Shipments</div>
          <div className="kpi-card-value">{statusCards.shipment_count ?? 0}</div>
        </article>
        <article className="kpi-card">
          <div className="kpi-card-label">Items</div>
          <div className="kpi-card-value">{statusCards.items_count ?? 0}</div>
        </article>
        <article className="kpi-card">
          <div className="kpi-card-label">Commissionable Lines</div>
          <div className="kpi-card-value">{statusCards.commissionable_lines ?? 0}</div>
        </article>
        <article className="kpi-card warning">
          <div className="kpi-card-label">Exceptions</div>
          <div className="kpi-card-value">{statusCards.exceptions_count ?? 0}</div>
        </article>
        <article className="kpi-card money">
          <div className="kpi-card-label">Estimated Commission</div>
          <div className="kpi-card-value">{currency(statusCards.estimated_commission)}</div>
        </article>
        <article className="kpi-card money">
          <div className="kpi-card-label">Amount Under Review</div>
          <div className="kpi-card-value">{currency(statusCards.amount_under_review)}</div>
        </article>
      </section>

      <section className="card">
        <div className="card-header">
          <h3 className="card-title">Optional Historical Validation</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setHistoricalOpen((prev) => !prev)}>
            {historicalOpen ? "Hide" : "Show"}
          </button>
        </div>
        {historicalOpen && (
          <div className="card-body">
            <p className="manual-help">
              Upload Jennifer&apos;s historical workbook only for side-by-side validation. Zoho operational data always
              comes from SQLite.
            </p>
            <div className="upload-grid upload-grid-single">
              <div className="upload-card">
                <label>{HISTORICAL_UPLOAD.label}</label>
                <input
                  type="file"
                  accept=".xlsx,.xls"
                  onChange={(e) => setHistoricalUpload(e.target.files?.[0] || null)}
                />
                <div className="upload-filename">{historicalUpload?.name || "No file selected"}</div>
              </div>
            </div>
            <div className="manual-actions">
              <button type="button" className="btn" onClick={uploadHistoricalWorkbook} disabled={loading}>
                Upload Historical Workbook
              </button>
              <button
                type="button"
                className="btn"
                onClick={loadValidation}
                disabled={loading || !reportId || !hasHistoricalWorkbook}
              >
                Compare Historical Workbook
              </button>
            </div>
          </div>
        )}
      </section>

      {hasHistoricalValidation && (
        <section className="kpi-grid">
          <div className="kpi-section-label">Historical validation snapshot</div>
          <article className="kpi-card success">
            <div className="kpi-card-label">Matched Lines</div>
            <div className="kpi-card-value">{statusCards.matched_lines ?? 0}</div>
          </article>
          <article className="kpi-card warning">
            <div className="kpi-card-label">Historical Workbook Only</div>
            <div className="kpi-card-value">{statusCards.historical_workbook_only_lines ?? 0}</div>
          </article>
          <article className="kpi-card warning">
            <div className="kpi-card-label">System Only</div>
            <div className="kpi-card-value">{statusCards.system_only_lines ?? 0}</div>
          </article>
          <article className="kpi-card money">
            <div className="kpi-card-label">Amount Difference</div>
            <div className="kpi-card-value">{currency(statusCards.amount_difference_total)}</div>
          </article>
        </section>
      )}

      <section className="card">
        <div className="card-header">
          <div className="tabs">
            <button
              type="button"
              className={`tab ${tableView === "exceptions" ? "active" : ""}`}
              onClick={() => setTableView("exceptions")}
            >
              Review Exceptions
              <span className="tab-count">{tabCounts.exceptions}</span>
            </button>
            <button
              type="button"
              className={`tab ${tableView === "line_match" ? "active" : ""}`}
              onClick={() => setTableView("line_match")}
            >
              Line Match Audit
              <span className="tab-count">{tabCounts.line_match}</span>
            </button>
            <button
              type="button"
              className={`tab ${tableView === "validation" ? "active" : ""}`}
              onClick={() => setTableView("validation")}
            >
              Historical Validation
              <span className="tab-count">{tabCounts.validation}</span>
            </button>
          </div>
          <div className="search-input">
            <input
              type="search"
              placeholder="Filter by salesperson, SO, SKU, invoice..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
        <div className="card-body" style={{ padding: 0 }}>
          <div className="sheet-container">
            <SpreadsheetView
              columns={reportColumns}
              rows={reportRows}
              loading={loading || loadingPeriod}
              sheetName={`${reportTitle}${reportId ? ` (${reportId})` : ""}`}
            />
          </div>
        </div>
      </section>
      </div>
    </div>
  );
}
