import React from "react";

function formatCell(cell) {
  const value = cell?.v;
  if (value === null || value === undefined || value === "") return "";
  if (cell?.is_date && typeof value === "string") {
    const dt = new Date(value);
    if (!Number.isNaN(dt.getTime())) {
      return dt.toLocaleDateString();
    }
  }
  if (typeof value === "number" && typeof cell?.num_fmt === "string") {
    const fmt = cell.num_fmt.toLowerCase();
    if (fmt.includes("%")) {
      return `${(value * 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
    }
    if (fmt.includes("$")) {
      return value.toLocaleString(undefined, { style: "currency", currency: "USD" });
    }
  }
  if (typeof value === "number") {
    return Number.isInteger(value)
      ? value.toLocaleString()
      : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  return String(value);
}

function cellClass(cell) {
  const classes = ["excel-cell"];
  if (cell.bold) classes.push("excel-cell-bold");
  if (typeof cell.v === "number") classes.push("excel-cell-number");
  if (cell.has_border) classes.push("excel-cell-bordered");
  return classes.join(" ");
}

function cellStyle(cell) {
  const style = {};
  if (cell.bg) style.backgroundColor = cell.bg;
  if (cell.color) style.color = cell.color;
  if (cell.align && cell.align !== "general") style.textAlign = cell.align;
  if (cell.italic) style.fontStyle = "italic";
  if (cell.underline) style.textDecoration = "underline";
  return style;
}

export default function ExcelGrid({ grid, loading, title }) {
  if (loading && !grid?.rows?.length) {
    return <div className="sheet-empty">Loading sheet...</div>;
  }

  if (!grid?.rows?.length) {
    return <div className="sheet-empty">Select a month and sheet to view commissions.</div>;
  }

  const { columns, rows } = grid;

  return (
    <div className="excel-workbook">
      <div className="excel-formula-bar">
        <span className="formula-label">Sheet</span>
        <span className="formula-value">{title || grid.sheet}</span>
        <span className="formula-meta">{rows.length} rows × {columns.length} columns</span>
      </div>
      <div className="excel-scroll">
        <table className="excel-grid">
          <thead>
            <tr>
              <th className="excel-corner" />
              {columns.map((col) => (
                <th key={col} className="excel-col-head">
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIdx) => (
              <tr key={rowIdx} className={rowIdx % 2 === 1 ? "excel-row-alt" : ""}>
                <td className="excel-row-head">{rowIdx + 1}</td>
                {row.map((cell, colIdx) => (
                  <td
                    key={`${rowIdx}-${colIdx}`}
                    className={cellClass(cell)}
                    style={cellStyle(cell)}
                  >
                    {formatCell(cell)}
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
