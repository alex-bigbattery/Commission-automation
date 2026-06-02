import React, { useEffect, useMemo, useState } from "react";
import ExcelGrid from "./ExcelGrid.jsx";
import SpreadsheetView from "./SpreadsheetView.jsx";
import { API, apiFetch, downloadApi, readJson } from "../lib/api.js";

const GROUP_LABELS = {
  summary: "Summary",
  salespeople: "Salespeople",
  reference: "Reference",
  other: "Other",
};

const SHEET_LABELS = {
  Table: "Reference Table",
  R_LP: "List Price Ref",
  R_SO: "Sales Order Ref",
  R_INV: "Invoice Ref",
  R_SH: "Shipment Ref",
};

const MONTH_NAMES = {
  january: "January",
  february: "February",
  march: "March",
  april: "April",
  may: "May",
  june: "June",
  july: "July",
  august: "August",
  september: "September",
  october: "October",
  november: "November",
  december: "December",
};

const SQLITE_TABLES = [
  { id: "sales_orders", label: "Sales Orders" },
  { id: "sales_order_lines", label: "Sales Order Lines" },
  { id: "invoices", label: "Invoices" },
  { id: "invoice_lines", label: "Invoice Lines" },
  { id: "items", label: "Items" },
  { id: "customer_payments", label: "Customer Payments" },
  { id: "customer_payment_invoices", label: "Payment Applications" },
];

function parseMonthLabel(raw) {
  if (!raw) return "";
  const lower = raw.toLowerCase();
  const monthKey = Object.keys(MONTH_NAMES).find((m) => lower.includes(m));
  const month = monthKey ? MONTH_NAMES[monthKey] : raw.replace(/^\d{4}-\d+_?/i, "").replace(/_/g, " ");
  const status = lower.includes("completed")
    ? "Completed"
    : lower.includes("in progress")
      ? "In progress"
      : null;
  return { month, status };
}

function cleanWorkbookName(name) {
  if (!name) return "";
  return String(name).replace(/^UNPAID\s*-\s*/i, "").replace(/^DO NOT PAY\s*-\s*/i, "");
}

function sheetDisplayName(sheet) {
  return SHEET_LABELS[sheet] || sheet;
}

function workbookTypeLabel(kind) {
  if (kind === "full") return "Full B2B";
  if (kind === "individual") return "Individual";
  return "Workbook";
}

function defaultFilterForMonth(month) {
  const workbooks = Array.isArray(month?.workbooks) ? month.workbooks : [];
  if (workbooks.some((wb) => wb.kind === "full")) return "full";
  if (workbooks.some((wb) => wb.kind === "individual")) return "individual";
  return "all";
}

function pickDefaultSheet(meta, workbookKind) {
  const groups = meta?.groups || {};
  const sheetList = meta?.sheets || [];
  if (workbookKind === "full" && (groups.summary || []).length > 0) {
    return groups.summary[0];
  }
  if (workbookKind === "individual" && (groups.salespeople || []).length > 0) {
    return groups.salespeople[0];
  }
  if ((groups.summary || []).length > 0) return groups.summary[0];
  if ((groups.salespeople || []).length > 0) return groups.salespeople[0];
  return sheetList[0] || "";
}

export default function CommissionsView() {
  const period = new Date();
  const [tree, setTree] = useState({ years: [] });
  const [selectedYear, setSelectedYear] = useState("");
  const [selectedMonth, setSelectedMonth] = useState(null);
  const [workbookId, setWorkbookId] = useState("");
  const [sheetGroups, setSheetGroups] = useState({});
  const [sheets, setSheets] = useState([]);
  const [activeSheet, setActiveSheet] = useState("");
  const [grid, setGrid] = useState(null);
  const [loadingMeta, setLoadingMeta] = useState(false);
  const [loadingGrid, setLoadingGrid] = useState(false);
  const [error, setError] = useState("");
  const [sqliteStatus, setSqliteStatus] = useState(null);
  const [workbookTypeFilter, setWorkbookTypeFilter] = useState("all");
  const [sourceMode, setSourceMode] = useState("sqlite");
  const [sqliteYear, setSqliteYear] = useState(period.getFullYear());
  const [sqliteMonth, setSqliteMonth] = useState(period.getMonth() + 1);
  const [sqliteSummary, setSqliteSummary] = useState(null);
  const [sqliteTable, setSqliteTable] = useState("invoice_lines");
  const [sqliteGrid, setSqliteGrid] = useState({ columns: [], rows: [], total_rows: 0 });
  const [sqliteLoadingSummary, setSqliteLoadingSummary] = useState(false);
  const [sqliteLoadingTable, setSqliteLoadingTable] = useState(false);

  useEffect(() => {
    loadTree();
    loadSqliteStatus();
  }, []);

  useEffect(() => {
    if (sourceMode !== "sqlite") return;
    loadSqliteSummary();
  }, [sourceMode, sqliteYear, sqliteMonth]);

  useEffect(() => {
    if (sourceMode !== "sqlite") return;
    loadSqliteTable();
  }, [sourceMode, sqliteYear, sqliteMonth, sqliteTable]);

  async function loadTree() {
    try {
      const data = await readJson(await apiFetch(`${API}/commissions/tree`));
      setTree(data);
      const firstYear = data.years?.[0];
      if (firstYear) {
        setSelectedYear(firstYear.year);
        const firstMonth = firstYear.months?.[firstYear.months.length - 1];
        if (firstMonth) {
          selectMonth(firstMonth);
        }
      }
    } catch (err) {
      setError(`Unable to load commissions: ${err.message}`);
    }
  }

  async function loadSqliteStatus() {
    try {
      const data = await readJson(await apiFetch(`${API}/db/status`));
      setSqliteStatus(data);
    } catch {
      setSqliteStatus(null);
    }
  }

  async function loadSqliteSummary() {
    setSqliteLoadingSummary(true);
    setError("");
    try {
      const data = await readJson(
        await apiFetch(
          `${API}/commissions/sqlite/summary?year=${encodeURIComponent(sqliteYear)}&month=${encodeURIComponent(sqliteMonth)}`
        )
      );
      setSqliteSummary(data);
    } catch (err) {
      setError(err.message);
      setSqliteSummary(null);
    } finally {
      setSqliteLoadingSummary(false);
    }
  }

  async function loadSqliteTable() {
    setSqliteLoadingTable(true);
    setError("");
    try {
      const data = await readJson(
        await apiFetch(
          `${API}/commissions/sqlite/table?year=${encodeURIComponent(sqliteYear)}&month=${encodeURIComponent(
            sqliteMonth
          )}&table=${encodeURIComponent(sqliteTable)}&limit=1500`
        )
      );
      setSqliteGrid({
        columns: data.columns || [],
        rows: data.rows || [],
        total_rows: data.total_rows || 0,
      });
    } catch (err) {
      setError(err.message);
      setSqliteGrid({ columns: [], rows: [], total_rows: 0 });
    } finally {
      setSqliteLoadingTable(false);
    }
  }

  async function selectMonth(month) {
    setSelectedMonth(month);
    setWorkbookTypeFilter(defaultFilterForMonth(month));
    const defaultId = month.default_workbook_id || month.workbook_id || "";
    setWorkbookId(defaultId);
    setSheetGroups({});
    setSheets([]);
    setGrid(null);
    setActiveSheet("");
    setError("");
  }

  useEffect(() => {
    if (!workbookId) return;
    loadWorkbookMeta(workbookId);
  }, [workbookId]);

  async function loadWorkbookMeta(id) {
    setLoadingMeta(true);
    setError("");
    try {
      const data = await readJson(
        await apiFetch(`${API}/commissions/workbooks/${encodeURIComponent(id)}/meta`)
      );
      setSheetGroups(data.groups || {});
      setSheets(data.sheets || []);
      const wb = selectedMonthWorkbooks.find((entry) => entry.id === id);
      setActiveSheet(pickDefaultSheet(data, wb?.kind));
    } catch (err) {
      setError(err.message);
      setSheetGroups({});
      setSheets([]);
      setActiveSheet("");
    } finally {
      setLoadingMeta(false);
    }
  }

  useEffect(() => {
    if (!workbookId || !activeSheet) return;
    loadGrid(workbookId, activeSheet);
  }, [workbookId, activeSheet]);

  async function loadGrid(id, sheet) {
    setLoadingGrid(true);
    setError("");
    try {
      const data = await readJson(
        await apiFetch(
          `${API}/commissions/workbooks/${encodeURIComponent(id)}/sheets/${encodeURIComponent(sheet)}`
        )
      );
      setGrid(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingGrid(false);
    }
  }

  const monthsForYear = useMemo(() => {
    return tree.years?.find((y) => y.year === selectedYear)?.months || [];
  }, [tree, selectedYear]);

  const groupedTabs = useMemo(() => {
    const order = ["summary", "salespeople", "reference", "other"];
    return order.flatMap((group) =>
      (sheetGroups[group] || []).map((sheet) => ({ group, sheet }))
    );
  }, [sheetGroups]);

  const selectedMonthWorkbooks = useMemo(() => {
    if (!selectedMonth) return [];
    if (Array.isArray(selectedMonth.workbooks) && selectedMonth.workbooks.length > 0) {
      return selectedMonth.workbooks;
    }
    if (selectedMonth.workbook_id) {
      return [
        {
          id: selectedMonth.workbook_id,
          label: selectedMonth.workbook_id,
          kind: "full",
        },
      ];
    }
    return [];
  }, [selectedMonth]);

  const filteredWorkbooks = useMemo(() => {
    if (workbookTypeFilter === "all") return selectedMonthWorkbooks;
    return selectedMonthWorkbooks.filter((wb) => wb.kind === workbookTypeFilter);
  }, [selectedMonthWorkbooks, workbookTypeFilter]);

  const activeWorkbook = useMemo(
    () => selectedMonthWorkbooks.find((wb) => wb.id === workbookId) || null,
    [selectedMonthWorkbooks, workbookId]
  );
  const fullWorkbook = useMemo(
    () => selectedMonthWorkbooks.find((wb) => wb.kind === "full") || null,
    [selectedMonthWorkbooks]
  );
  async function exportWorkbook(workbook) {
    if (!workbook?.id) return;
    try {
      const name = cleanWorkbookName(workbook.label) || "workbook.xlsx";
      await downloadApi(`${API}/commissions/workbooks/${encodeURIComponent(workbook.id)}/download`, name);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    if (!filteredWorkbooks.length) {
      if (workbookId !== "") setWorkbookId("");
      return;
    }
    if (!filteredWorkbooks.some((wb) => wb.id === workbookId)) {
      setWorkbookId(filteredWorkbooks[0].id);
    }
  }, [filteredWorkbooks, workbookId]);

  if (sourceMode === "sqlite") {
    const tableCounts = sqliteSummary?.table_counts || {};
    const lineTypeCounts = sqliteSummary?.line_type_counts || {};
    const monthOptions = Object.entries(MONTH_NAMES).map(([key, label], idx) => ({
      key,
      label,
      value: idx + 1,
    }));

    return (
      <div className="page">
        <section className="page-header">
          <div>
            <h2 className="page-title">Commissions from SQLite</h2>
            <p className="page-subtitle">Operational source of truth for commission calculations.</p>
          </div>
          <div className="row">
            <button type="button" className="btn btn-primary btn-sm" onClick={() => setSourceMode("sqlite")}>
              SQLite Data
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setSourceMode("historical")}>
              Historical Excel
            </button>
          </div>
        </section>

        {error && <div className="banner banner-danger">{error}</div>}

        <section className="card">
          <div className="card-body row" style={{ flexWrap: "wrap", gap: "0.75rem" }}>
            <div className="field" style={{ minWidth: "120px" }}>
              <label className="field-label">Year</label>
              <input className="input" type="number" value={sqliteYear} onChange={(e) => setSqliteYear(Number(e.target.value))} />
            </div>
            <div className="field" style={{ minWidth: "180px" }}>
              <label className="field-label">Month</label>
              <select className="select" value={sqliteMonth} onChange={(e) => setSqliteMonth(Number(e.target.value))}>
                {monthOptions.map((m) => (
                  <option key={m.key} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field" style={{ minWidth: "280px", flex: 1 }}>
              <label className="field-label">Table</label>
              <select className="select" value={sqliteTable} onChange={(e) => setSqliteTable(e.target.value)}>
                {SQLITE_TABLES.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label} ({tableCounts[t.id] ?? 0})
                  </option>
                ))}
              </select>
            </div>
            <button type="button" className="btn btn-sm" onClick={() => { loadSqliteSummary(); loadSqliteTable(); }}>
              Refresh
            </button>
          </div>
        </section>

        <section className="kpi-grid">
          <div className="kpi-section-label">SQLite period snapshot</div>
          <article className="kpi-card"><div className="kpi-card-label">Sales Orders</div><div className="kpi-card-value">{tableCounts.sales_orders ?? 0}</div></article>
          <article className="kpi-card"><div className="kpi-card-label">SO Lines</div><div className="kpi-card-value">{tableCounts.sales_order_lines ?? 0}</div></article>
          <article className="kpi-card"><div className="kpi-card-label">Invoices</div><div className="kpi-card-value">{tableCounts.invoices ?? 0}</div></article>
          <article className="kpi-card"><div className="kpi-card-label">Invoice Lines</div><div className="kpi-card-value">{tableCounts.invoice_lines ?? 0}</div></article>
          <article className="kpi-card"><div className="kpi-card-label">Product Lines</div><div className="kpi-card-value">{lineTypeCounts.product ?? 0}</div></article>
          <article className="kpi-card warning"><div className="kpi-card-label">Shipping Lines</div><div className="kpi-card-value">{lineTypeCounts.shipping ?? 0}</div></article>
          <article className="kpi-card warning"><div className="kpi-card-label">Card Fee Lines</div><div className="kpi-card-value">{lineTypeCounts.credit_card_fee ?? 0}</div></article>
          <article className="kpi-card success"><div className="kpi-card-label">Commission Candidates</div><div className="kpi-card-value">{lineTypeCounts.commission_candidate ?? 0}</div></article>
        </section>

        <section className="card">
          <div className="card-header">
            <h3 className="card-title">
              {SQLITE_TABLES.find((t) => t.id === sqliteTable)?.label || sqliteTable}
            </h3>
            <span className="pill">{sqliteGrid.total_rows ?? 0} total rows</span>
          </div>
          <div className="card-body" style={{ padding: 0 }}>
            <SpreadsheetView
              columns={sqliteGrid.columns}
              rows={sqliteGrid.rows}
              loading={sqliteLoadingSummary || sqliteLoadingTable}
              sheetName={SQLITE_TABLES.find((t) => t.id === sqliteTable)?.label || sqliteTable}
            />
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="commissions-layout">
      <aside className="commissions-aside">
        <div className="commissions-aside-head">
          <h3>Historical workbooks</h3>
          <p>Year -&gt; month -&gt; B2B workbooks (reference only)</p>
          {tree.root && (
            <p className="month-item-file" title={tree.root}>
              {tree.root.split("\\").slice(-1).join("\\")}
            </p>
          )}
        </div>

        <div className="commissions-aside-content">
          <div className="year-selector">
            {(tree.years || []).map((y) => (
              <button
                key={y.year}
                type="button"
                className={`year-chip ${selectedYear === y.year ? "active" : ""}`}
                onClick={() => {
                  const year = y.year;
                  setSelectedYear(year);
                  const months = tree.years.find((entry) => entry.year === year)?.months || [];
                  const month = months[months.length - 1];
                  if (month) selectMonth(month);
                }}
              >
                {y.year}
              </button>
            ))}
          </div>

          <ul className="month-list">
            {monthsForYear.map((month) => (
              <li key={month.id}>
                {(() => {
                  const info = parseMonthLabel(month.label);
                  const workbookCount = Array.isArray(month.workbooks) ? month.workbooks.length : month.workbook_id ? 1 : 0;
                  const fullCount = Array.isArray(month.workbooks)
                    ? month.workbooks.filter((wb) => wb.kind === "full").length
                    : 0;
                  const individualCount = Array.isArray(month.workbooks)
                    ? month.workbooks.filter((wb) => wb.kind === "individual").length
                    : 0;
                  return (
                <button
                  type="button"
                  className={`month-item ${selectedMonth?.id === month.id ? "active" : ""}`}
                  onClick={() => selectMonth(month)}
                >
                  <span className="month-item-name">{info.month}</span>
                  <span className="month-item-file">
                    {workbookCount} workbook{workbookCount === 1 ? "" : "s"} ({fullCount} full / {individualCount} individual)
                    {info.status ? ` · ${info.status}` : ""}
                  </span>
                </button>
                  );
                })()}
              </li>
            ))}
          </ul>

          {selectedMonth && (
            <div className="sidebar-status-block" style={{ marginTop: "0.85rem", marginBottom: 0 }}>
              <div className="sidebar-status-row">
                <span className="sidebar-status-label">File</span>
                <span className="sidebar-status-value">{cleanWorkbookName(activeWorkbook?.label || "")}</span>
              </div>
              <div className="sidebar-status-row">
                <span className="sidebar-status-label">Sheets</span>
                <span className="sidebar-status-value">{sheets.length}</span>
              </div>
              <div className="sidebar-status-row">
                <span className="sidebar-status-label">Type</span>
                <span className="sidebar-status-value">{workbookTypeLabel(activeWorkbook?.kind)}</span>
              </div>
              <div className="sidebar-status-row">
                <span className="sidebar-status-label">SQLite in this tab</span>
                <span className="sidebar-status-value">No (reference mode)</span>
              </div>
            </div>
          )}
        </div>
      </aside>

      <section className="commissions-main">
        {error && <div className="error-banner">{error}</div>}

        <div className="banner banner-info" style={{ margin: "0.75rem 1rem 0" }}>
          This tab is for browsing historical Accounting workbooks. The production commission run uses SQLite in the
          <strong> Audit / Review</strong> tab.
        </div>

        {sqliteStatus && (
          <div className="banner banner-success" style={{ margin: "0.75rem 1rem 0" }}>
            SQLite connected: Sales Orders {sqliteStatus?.counts?.sales_orders ?? 0}, Invoices{" "}
            {sqliteStatus?.counts?.invoices ?? 0}, Items {sqliteStatus?.counts?.items ?? 0}.
          </div>
        )}

        <div className="commissions-toolbar">
          <div className="workbook-title">
            <span className="pill pill-info">Historical workbooks</span>
            <h3>
              {selectedMonth
                ? `${selectedYear} / ${parseMonthLabel(selectedMonth.label).month}`
                : "Select a month"}
            </h3>
          </div>
          {selectedMonth && (
            <div className="row" style={{ flexWrap: "wrap", gap: "0.5rem" }}>
              <select
                className="select"
                value={workbookTypeFilter}
                onChange={(e) => setWorkbookTypeFilter(e.target.value)}
                style={{ minWidth: "160px" }}
              >
                <option value="all">All workbook types</option>
                <option value="full">Full B2B only</option>
                <option value="individual">Individual only</option>
              </select>
              <select
                className="select"
                value={workbookId}
                onChange={(e) => setWorkbookId(e.target.value)}
                style={{ minWidth: "380px", maxWidth: "100%" }}
              >
                {filteredWorkbooks.length === 0 && <option value="">No workbook for this filter</option>}
                {filteredWorkbooks.map((wb) => (
                  <option key={wb.id} value={wb.id}>
                    [{workbookTypeLabel(wb.kind)}] {cleanWorkbookName(wb.label)}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn-sm"
                disabled={!fullWorkbook}
                onClick={() => exportWorkbook(fullWorkbook)}
                title="Download full B2B workbook for this month"
              >
                Export Full B2B
              </button>
              <button
                type="button"
                className="btn btn-sm"
                disabled={!activeWorkbook}
                onClick={() => exportWorkbook(activeWorkbook)}
                title="Download currently selected workbook"
              >
                {activeWorkbook?.kind === "individual" ? "Export Selected Individual" : "Export Selected Workbook"}
              </button>
              <button type="button" className="btn btn-sm" onClick={() => setSourceMode("sqlite")}>
                Switch to SQLite Data
              </button>
            </div>
          )}
          {loadingMeta && <span className="loading-pill">Loading workbook…</span>}
          {loadingGrid && <span className="loading-pill">Loading sheet…</span>}
        </div>

        <div className="excel-workbook">
          <ExcelGrid grid={grid} loading={loadingGrid} title={sheetDisplayName(activeSheet)} />
        </div>

        <footer className="sheet-tabs grouped-tabs">
          {groupedTabs.map(({ group, sheet }) => (
            <button
              key={sheet}
              type="button"
              title={`${GROUP_LABELS[group]} - ${sheet}`}
              className={`sheet-tab ${sheet === activeSheet ? "active" : ""} tab-${group}`}
              onClick={() => setActiveSheet(sheet)}
            >
              {sheetDisplayName(sheet)}
            </button>
          ))}
        </footer>
      </section>
    </div>
  );
}
