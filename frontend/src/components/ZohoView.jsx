import React, { useEffect, useState } from "react";
import SpreadsheetView from "./SpreadsheetView.jsx";

const API = "/api";

async function readJson(res) {
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || `Request failed (${res.status})`);
  }
  return data;
}

export default function ZohoView() {
  const [workbooks, setWorkbooks] = useState([]);
  const [selectedWorkbook, setSelectedWorkbook] = useState("");
  const [sheets, setSheets] = useState([]);
  const [activeSheet, setActiveSheet] = useState("");
  const [columns, setColumns] = useState([]);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [fetchDate, setFetchDate] = useState(new Date().toISOString().slice(0, 10));
  const [status, setStatus] = useState("");

  useEffect(() => {
    loadWorkbooks();
  }, []);

  useEffect(() => {
    if (!selectedWorkbook) return;
    loadSheets(selectedWorkbook);
  }, [selectedWorkbook]);

  useEffect(() => {
    if (!selectedWorkbook || !activeSheet) return;
    loadSheet(selectedWorkbook, activeSheet);
  }, [selectedWorkbook, activeSheet]);

  async function loadWorkbooks() {
    const data = await readJson(await fetch(`${API}/zoho/workbooks`));
    setWorkbooks(data.workbooks || []);
    if (data.workbooks?.[0]) setSelectedWorkbook(data.workbooks[0].id);
  }

  async function loadSheets(workbookId) {
    setLoading(true);
    try {
      const data = await readJson(
        await fetch(
        `${API}/workbooks/${encodeURIComponent(workbookId)}/sheets?source=zoho`
        )
      );
      setSheets(data.sheets || []);
      setActiveSheet(data.sheets?.[0] || "");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadSheet(workbookId, sheetName) {
    setLoading(true);
    try {
      const data = await readJson(
        await fetch(
        `${API}/workbooks/${encodeURIComponent(workbookId)}/sheets/${encodeURIComponent(sheetName)}?source=zoho`
        )
      );
      setColumns(data.columns || []);
      setRows(data.rows || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function runFetch() {
    setLoading(true);
    setError("");
    setStatus("");
    try {
      const data = await readJson(await fetch(`${API}/fetch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date: fetchDate }),
      }));
      setStatus(`Data downloaded: ${data.workbook}`);
      await loadWorkbooks();
      setSelectedWorkbook(data.workbook);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <section className="page-header">
        <div>
          <h2 className="page-title">Zoho Books</h2>
          <p className="page-subtitle">View exports and run daily Zoho fetch.</p>
        </div>
      </section>

      {status && <div className="banner banner-success">{status}</div>}
      {error && <div className="banner banner-danger">{error}</div>}

      <section className="card">
        <div className="card-body row" style={{ flexWrap: "wrap", gap: "0.9rem" }}>
          <div className="field" style={{ minWidth: "260px", flex: 1 }}>
            <label className="field-label" htmlFor="zoho-workbook">
              Zoho Export
            </label>
            <select
              className="select"
              id="zoho-workbook"
              value={selectedWorkbook}
              onChange={(e) => setSelectedWorkbook(e.target.value)}
            >
              {workbooks.map((wb) => (
                <option key={wb.id} value={wb.id}>
                  {wb.label}
                </option>
              ))}
            </select>
          </div>

          <div className="field" style={{ minWidth: "180px" }}>
            <label className="field-label" htmlFor="fetch-date">
              Day
            </label>
            <input
              className="input"
              id="fetch-date"
              type="date"
              value={fetchDate}
              onChange={(e) => setFetchDate(e.target.value)}
            />
          </div>

          <button type="button" className="btn btn-primary" onClick={runFetch} disabled={loading}>
            {loading ? "Downloading..." : "Fetch Zoho"}
          </button>
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
