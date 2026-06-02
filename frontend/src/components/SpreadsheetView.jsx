import React, { useMemo } from "react";
import { IconSearch, IconList } from "./Icons.jsx";

function formatCell(value) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "number") {
    return Number.isInteger(value)
      ? value.toLocaleString()
      : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  return String(value);
}

function isNumericColumn(values) {
  const sample = values.filter((v) => v !== "" && v !== null && v !== undefined).slice(0, 20);
  if (!sample.length) return false;
  return sample.every((v) => typeof v === "number" || !Number.isNaN(Number(v)));
}

export default function SpreadsheetView({ columns, rows, loading, sheetName, emptyHint }) {
  const numericColumns = useMemo(() => {
    const map = {};
    columns.forEach((col) => {
      map[col] = isNumericColumn(rows.map((row) => row[col]));
    });
    return map;
  }, [columns, rows]);

  if (loading && rows.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">
          <span className="spinner" style={{ width: 26, height: 26, borderWidth: 3 }} />
        </div>
        <p className="empty-state-title">Loading {sheetName || "data"}…</p>
        <p className="empty-state-desc">One moment, reading the data.</p>
      </div>
    );
  }

  if (!columns.length) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">
          <IconList />
        </div>
        <p className="empty-state-title">No data to display</p>
        <p className="empty-state-desc">
          {emptyHint || "Select a period, workbook, or sheet to view data."}
        </p>
      </div>
    );
  }

  if (!rows.length) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">
          <IconSearch />
        </div>
        <p className="empty-state-title">No matching rows</p>
        <p className="empty-state-desc">
          {emptyHint || "This sheet has no records, or the filter found no matches."}
        </p>
      </div>
    );
  }

  return (
    <div className={`spreadsheet-wrap ${loading ? "is-data-loading" : ""}`}>
      {loading && (
        <div className="data-loading-overlay" role="status" aria-live="polite">
          <span className="spinner" style={{ width: 26, height: 26, borderWidth: 3 }} />
          <span>Updating data…</span>
        </div>
      )}
      <div className="excel-formula-bar">
        <span className="formula-label">{sheetName || "Sheet"}</span>
        <span className="formula-meta">
          {rows.length.toLocaleString()} {rows.length === 1 ? "row" : "rows"} ·{" "}
          {columns.length} {columns.length === 1 ? "column" : "columns"}
        </span>
      </div>
      <div className="spreadsheet-scroll">
        <table className="spreadsheet">
          <thead>
            <tr>
              <th className="row-number-head">#</th>
              {columns.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, idx) => (
              <tr key={idx} className={idx % 2 === 1 ? "row-odd" : ""}>
                <td className="row-number">{idx + 1}</td>
                {columns.map((col) => (
                  <td key={col} className={numericColumns[col] ? "cell-number" : "cell-text"}>
                    {formatCell(row[col])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
