import React, { useEffect, useState } from "react";
import SpreadsheetView from "./SpreadsheetView.jsx";
import { API, apiFetch, readJson } from "../lib/api.js";

export default function ReportsView() {
  const [reports, setReports] = useState([]);
  const [selected, setSelected] = useState("");
  const [sheets, setSheets] = useState([]);
  const [activeSheet, setActiveSheet] = useState("");
  const [columns, setColumns] = useState([]);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch(`${API}/reports`)
      .then(readJson)
      .then((d) => {
        setReports(d.workbooks || []);
        if (d.workbooks?.[0]) setSelected(d.workbooks[0].id);
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setLoading(true);
    setError("");
    apiFetch(`${API}/workbooks/${encodeURIComponent(selected)}/sheets?source=report`)
      .then(readJson)
      .then((d) => {
        setSheets(d.sheets || []);
        setActiveSheet(d.sheets?.[0] || "");
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [selected]);

  useEffect(() => {
    if (!selected || !activeSheet) return;
    setLoading(true);
    setError("");
    apiFetch(
      `${API}/workbooks/${encodeURIComponent(selected)}/sheets/${encodeURIComponent(activeSheet)}?source=report`
    )
      .then(readJson)
      .then((d) => {
        setColumns(d.columns || []);
        setRows(d.rows || []);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [selected, activeSheet]);

  return (
    <div className="page">
      <section className="page-header">
        <div>
          <h2 className="page-title">Reports</h2>
          <p className="page-subtitle">Browse generated audit reports.</p>
        </div>
      </section>

      {error && <div className="banner banner-danger">{error}</div>}

      <section className="card">
        <div className="card-body row" style={{ flexWrap: "wrap", gap: "0.9rem" }}>
          <div className="field" style={{ minWidth: "200px", flex: 1 }}>
            <label className="field-label" htmlFor="report-select">
              Generated report
            </label>
            <select className="select" id="report-select" value={selected} onChange={(e) => setSelected(e.target.value)}>
              <option value="">Select a report...</option>
            {reports.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
            </select>
          </div>
          {loading && <span className="pill">Loading...</span>}
        </div>
      </section>

      <main>
        <SpreadsheetView columns={columns} rows={rows} loading={loading} sheetName={activeSheet} />
      </main>

      <footer className="sheet-tabs">
        {sheets.map((sheet) => (
          <button
            key={sheet}
            type="button"
            className={`sheet-tab ${sheet === activeSheet ? "active" : ""}`}
            onClick={() => setActiveSheet(sheet)}
          >
            {sheet}
          </button>
        ))}
      </footer>
    </div>
  );
}
