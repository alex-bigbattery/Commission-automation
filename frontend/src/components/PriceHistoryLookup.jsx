import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch, readJson } from "../lib/api.js";
import { Banner, ErrorBanner, LoadingNotice, Pill } from "./ui.jsx";
import { IconAlert, IconRefresh, IconSearch } from "./Icons.jsx";

const CATALOG_PAGE = 100;

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  });
}

function SourceBadge({ kind }) {
  const map = {
    accountant_snapshot: { label: "Accountant snapshot", variant: "info" },
    zoho_live_sync: { label: "Zoho live sync", variant: "success" },
    manual: { label: "Manual", variant: "warning" },
    other: { label: "Other", variant: "default" },
  };
  const def = map[kind] || map.other;
  return <Pill variant={def.variant}>{def.label}</Pill>;
}

function PriceStepChart({ rows }) {
  const chart = useMemo(() => {
    if (!rows?.length) return null;
    const parse = (d) => new Date(`${d}T00:00:00`).getTime();
    const points = rows.map((r) => ({
      from: parse(r.effective_from),
      to: r.effective_to_display === "Current" ? Date.now() : parse(r.effective_to),
      price: Number(r.map_price),
      kind: r.source_kind,
    })).filter((p) => !Number.isNaN(p.from) && !Number.isNaN(p.price));

    if (!points.length) return null;

    const minX = Math.min(...points.map((p) => p.from));
    const maxX = Math.max(...points.map((p) => p.to));
    const minY = Math.min(...points.map((p) => p.price));
    const maxY = Math.max(...points.map((p) => p.price));
    const pad = 24;
    const w = 640;
    const h = 180;
    const spanX = Math.max(maxX - minX, 86400000);
    const spanY = Math.max(maxY - minY, 1);

    const xScale = (t) => pad + ((t - minX) / spanX) * (w - pad * 2);
    const yScale = (v) => h - pad - ((v - minY) / spanY) * (h - pad * 2);

    const colors = {
      accountant_snapshot: "#2563eb",
      zoho_live_sync: "#16a34a",
      manual: "#d97706",
      other: "#64748b",
    };

    const segments = points.map((p, i) => {
      const x1 = xScale(p.from);
      const x2 = xScale(p.to);
      const y = yScale(p.price);
      return (
        <g key={`seg-${i}`}>
          <line
            x1={x1}
            y1={y}
            x2={x2}
            y2={y}
            stroke={colors[p.kind] || colors.other}
            strokeWidth={3}
            strokeLinecap="round"
          />
          <circle cx={x1} cy={y} r={4} fill={colors[p.kind] || colors.other} />
        </g>
      );
    });

    return { w, h, segments, minY, maxY };
  }, [rows]);

  if (!chart) return null;

  return (
    <div className="ph-chart-wrap">
      <div className="ph-chart-label">MAP price over time (step chart)</div>
      <svg className="ph-chart" viewBox={`0 0 ${chart.w} ${chart.h}`} role="img" aria-label="Price step chart">
        {chart.segments}
      </svg>
      <div className="ph-chart-legend text-faint">
        <span>Low {money(chart.minY)}</span>
        <span>High {money(chart.maxY)}</span>
      </div>
    </div>
  );
}

function CatalogTable({ rows, selectedSku, onSelect, loading }) {
  if (loading) return <LoadingNotice>Loading SKU catalog…</LoadingNotice>;
  if (!rows.length) {
    return <p className="text-faint ph-hint">No SKUs match the current filter.</p>;
  }
  return (
    <div className="table-wrap">
      <table className="data-table settings-table ph-catalog-table">
        <thead>
          <tr>
            <th>SKU</th>
            <th>Item ID</th>
            <th className="cell-number">Current MAP</th>
            <th>Latest from</th>
            <th>Latest source</th>
            <th>Snapshot</th>
            <th className="cell-number">Rows</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.sku}
              className={`ph-catalog-row ${selectedSku === r.sku ? "ph-catalog-row-active" : ""}`}
              onClick={() => onSelect(r.sku)}
              onKeyDown={(e) => { if (e.key === "Enter") onSelect(r.sku); }}
              tabIndex={0}
              role="button"
            >
              <td><code>{r.sku}</code></td>
              <td>{r.item_id || "—"}</td>
              <td className="cell-number">{money(r.current_price)}</td>
              <td>{r.latest_effective_from}</td>
              <td className="cell-trunc" title={r.latest_source}>{r.latest_source}</td>
              <td>{r.latest_snapshot_month}</td>
              <td className="cell-number">{r.row_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function PriceHistoryLookup() {
  const [pickerMode, setPickerMode] = useState("search"); // search | browse
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);

  const [catalog, setCatalog] = useState({ results: [], total: 0, offset: 0, limit: CATALOG_PAGE });
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogFilter, setCatalogFilter] = useState("");
  const [catalogPage, setCatalogPage] = useState(0);

  const [dropdownOptions, setDropdownOptions] = useState([]);
  const [dropdownLoading, setDropdownLoading] = useState(false);
  const [catalogError, setCatalogError] = useState(null);

  const [selectedSku, setSelectedSku] = useState("");
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [sourceFilter, setSourceFilter] = useState("");
  const [monthFilter, setMonthFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const loadCatalog = useCallback(async (q, page) => {
    setCatalogLoading(true);
    setCatalogError(null);
    try {
      const offset = page * CATALOG_PAGE;
      const params = new URLSearchParams({
        limit: String(CATALOG_PAGE),
        offset: String(offset),
      });
      if (q.trim()) params.set("q", q.trim());
      const res = await apiFetch(`/settings/price-history/catalog?${params.toString()}`);
      const payload = await readJson(res);
      setCatalog({
        results: Array.isArray(payload?.results) ? payload.results : [],
        total: payload?.total ?? 0,
        offset: payload?.offset ?? offset,
        limit: payload?.limit ?? CATALOG_PAGE,
      });
    } catch (e) {
      setCatalog({ results: [], total: 0, offset: 0, limit: CATALOG_PAGE });
      setCatalogError(e);
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  const loadDropdownOptions = useCallback(async () => {
    setDropdownLoading(true);
    setCatalogError(null);
    try {
      const res = await apiFetch("/settings/price-history/catalog?limit=500&offset=0");
      const payload = await readJson(res);
      setDropdownOptions(Array.isArray(payload?.results) ? payload.results : []);
      if ((payload?.total ?? 0) === 0) {
        setCatalogError({ title: "No price history data", message: "The price_history table is empty on the server." });
      }
    } catch (e) {
      setDropdownOptions([]);
      setCatalogError(e);
    } finally {
      setDropdownLoading(false);
    }
  }, []);

  const runSearch = useCallback(async (q) => {
    const trimmed = (q || "").trim();
    if (trimmed.length < 2) {
      setSearchResults([]);
      return;
    }
    setSearchLoading(true);
    try {
      const res = await apiFetch(`/settings/price-history/search?q=${encodeURIComponent(trimmed)}&limit=30`);
      const payload = await readJson(res);
      setSearchResults(Array.isArray(payload?.results) ? payload.results : []);
    } catch {
      setSearchResults([]);
    } finally {
      setSearchLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (sku) => {
    const trimmed = (sku || "").trim();
    if (!trimmed) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    try {
      const params = new URLSearchParams({ sku: trimmed });
      if (sourceFilter.trim()) params.set("source", sourceFilter.trim());
      if (monthFilter) params.set("snapshot_month", monthFilter);
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
      const res = await apiFetch(`/settings/price-history?${params.toString()}`);
      const payload = await readJson(res);
      setDetail(payload);
    } catch {
      setDetail({
        sku: trimmed,
        rows: [],
        warnings: ["Failed to load price history for this SKU."],
        current_price: null,
        r_lp_fallback: null,
      });
    } finally {
      setDetailLoading(false);
    }
  }, [sourceFilter, monthFilter, dateFrom, dateTo]);

  useEffect(() => {
    loadDropdownOptions();
    (async () => {
      try {
        const res = await apiFetch("/settings/price-history/catalog?limit=1&offset=0");
        const payload = await readJson(res);
        setCatalog((prev) => ({ ...prev, total: payload?.total ?? prev.total }));
      } catch {
        /* ignore — badge updates when Browse tab opens */
      }
    })();
  }, [loadDropdownOptions]);

  useEffect(() => {
    const t = setTimeout(() => runSearch(query), 250);
    return () => clearTimeout(t);
  }, [query, runSearch]);

  useEffect(() => {
    if (pickerMode !== "browse") return undefined;
    const t = setTimeout(() => loadCatalog(catalogFilter, catalogPage), 200);
    return () => clearTimeout(t);
  }, [pickerMode, catalogFilter, catalogPage, loadCatalog]);

  useEffect(() => {
    if (!selectedSku) return undefined;
    const t = setTimeout(() => loadDetail(selectedSku), 200);
    return () => clearTimeout(t);
  }, [selectedSku, loadDetail]);

  const selectSku = (sku) => {
    setSelectedSku(sku);
    setQuery(sku);
  };

  const clearDetailFilters = () => {
    setSourceFilter("");
    setMonthFilter("");
    setDateFrom("");
    setDateTo("");
  };

  const hasDetailFilters = Boolean(sourceFilter.trim() || monthFilter || dateFrom || dateTo);

  const dropdownFiltered = useMemo(() => {
    const needle = query.trim().toUpperCase();
    if (!needle) return dropdownOptions;
    return dropdownOptions.filter(
      (r) =>
        r.sku.toUpperCase().includes(needle)
        || String(r.item_id || "").toUpperCase().includes(needle),
    );
  }, [dropdownOptions, query]);

  const catalogPages = Math.max(1, Math.ceil((catalog.total || 0) / CATALOG_PAGE));

  const rows = detail?.rows ?? [];
  const warnings = detail?.warnings ?? [];
  const sources = detail?.sources ?? [];
  const months = detail?.snapshot_months ?? [];

  return (
    <div className="ph-lookup">
      <Banner type="info" icon={IconAlert}>
        Price history is read-only. Historical prices are used to calculate commissions based on sale date.
      </Banner>

      <ErrorBanner error={catalogError} onRetry={() => { loadDropdownOptions(); loadCatalog(catalogFilter, catalogPage); }} />

      <div className="ph-picker-tabs">
        <button
          type="button"
          className={`tab ${pickerMode === "search" ? "active" : ""}`}
          onClick={() => setPickerMode("search")}
        >
          Search / dropdown
        </button>
        <button
          type="button"
          className={`tab ${pickerMode === "browse" ? "active" : ""}`}
          onClick={() => setPickerMode("browse")}
        >
          Browse all SKUs
          {catalog.total ? <span className="tab-count">{catalog.total}</span> : null}
        </button>
      </div>

      {pickerMode === "search" ? (
        <div className="ph-search-block">
          <div className="ph-picker-row">
            <div className="search-input ph-search-input">
              <IconSearch />
              <input
                type="search"
                placeholder="Search by SKU or item_id…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && query.trim()) selectSku(query.trim());
                }}
              />
            </div>
            <label className="settings-filter-label ph-dropdown-label">
              Or select SKU
              <select
                value={selectedSku}
                onChange={(e) => {
                  if (e.target.value) selectSku(e.target.value);
                }}
                disabled={dropdownLoading}
              >
                <option value="">
                  {dropdownLoading ? "Loading SKUs…" : `Choose a SKU (${dropdownOptions.length} loaded)`}
                </option>
                {dropdownFiltered.map((r) => (
                  <option key={r.sku} value={r.sku}>
                    {r.sku} — {money(r.current_price)} ({r.row_count} rows)
                  </option>
                ))}
              </select>
            </label>
          </div>
          {dropdownOptions.length >= 500 ? (
            <p className="text-faint ph-hint">
              Dropdown shows first 500 SKUs. Use <strong>Browse all SKUs</strong> or search for the rest.
            </p>
          ) : null}
          {searchLoading ? <LoadingNotice>Searching…</LoadingNotice> : null}

          {searchResults.length > 0 && query.trim().length >= 2 ? (
            <ul className="ph-search-results">
              {searchResults.map((r) => (
                <li key={r.sku}>
                  <button type="button" className="ph-search-hit" onClick={() => selectSku(r.sku)}>
                    <strong><code>{r.sku}</code></strong>
                    <span>{money(r.current_price)}</span>
                    <span className="text-faint">{r.row_count} row(s) · {r.latest_source}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : (
        <section className="card ph-browse-card">
          <div className="card-header">
            <h3 className="card-title">All SKUs in price_history</h3>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => loadCatalog(catalogFilter, catalogPage)}
              disabled={catalogLoading}
            >
              <IconRefresh /> Refresh
            </button>
          </div>
          <div className="card-body">
            <div className="table-toolbar settings-filters">
              <div className="search-input">
                <IconSearch />
                <input
                  type="search"
                  placeholder="Filter table by SKU or item_id…"
                  value={catalogFilter}
                  onChange={(e) => {
                    setCatalogFilter(e.target.value);
                    setCatalogPage(0);
                  }}
                />
              </div>
              <span className="text-faint">
                {catalog.total.toLocaleString()} SKU(s) total
                {catalogFilter ? ` · filter: ${catalogFilter}` : ""}
              </span>
            </div>
            <CatalogTable
              rows={catalog.results}
              selectedSku={selectedSku}
              onSelect={selectSku}
              loading={catalogLoading}
            />
            {catalogPages > 1 ? (
              <div className="ph-pagination">
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={catalogPage <= 0 || catalogLoading}
                  onClick={() => setCatalogPage((p) => Math.max(0, p - 1))}
                >
                  Previous
                </button>
                <span className="text-faint">
                  Page {catalogPage + 1} of {catalogPages}
                </span>
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={catalogPage >= catalogPages - 1 || catalogLoading}
                  onClick={() => setCatalogPage((p) => p + 1)}
                >
                  Next
                </button>
              </div>
            ) : null}
            <p className="text-faint ph-hint">Click a row to view that SKU&apos;s full price timeline below.</p>
          </div>
        </section>
      )}

      {selectedSku ? (
        <section className="card ph-detail-card" id="ph-detail">
          <div className="card-header">
            <h3 className="card-title">
              <code>{selectedSku}</code>
              {detail?.item_id ? <span className="text-faint"> · item {detail.item_id}</span> : null}
            </h3>
            <button type="button" className="btn btn-sm" onClick={() => loadDetail(selectedSku)} disabled={detailLoading}>
              <IconRefresh /> Refresh
            </button>
          </div>
          <div className="card-body">
            <div className="ph-summary">
              <div>
                <span className="ph-summary-label">Current MAP</span>
                <strong className="ph-summary-value">{money(detail?.current_price)}</strong>
              </div>
              {detail?.r_lp_fallback != null ? (
                <div>
                  <span className="ph-summary-label">R_LP fallback (template)</span>
                  <strong className="ph-summary-value">{money(detail.r_lp_fallback)}</strong>
                </div>
              ) : null}
              <div>
                <span className="ph-summary-label">History rows</span>
                <strong className="ph-summary-value">{detail?.row_count ?? 0}</strong>
              </div>
            </div>

            {warnings.length > 0 ? (
              <div className="ph-warnings">
                {warnings.map((w, i) => (
                  <Banner key={`w-${i}`} type="warning" icon={IconAlert}>{w}</Banner>
                ))}
              </div>
            ) : null}

            <div className="table-toolbar settings-filters ph-filters">
              <label className="settings-filter-label">
                Source contains
                <input
                  type="text"
                  list="ph-sources"
                  value={sourceFilter}
                  onChange={(e) => setSourceFilter(e.target.value)}
                  placeholder="e.g. accountant_fvprice"
                />
                <datalist id="ph-sources">
                  {sources.map((s) => <option key={s} value={s} />)}
                </datalist>
              </label>
              <label className="settings-filter-label">
                Snapshot month
                <select value={monthFilter} onChange={(e) => setMonthFilter(e.target.value)}>
                  <option value="">All</option>
                  {months.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              <label className="settings-filter-label">
                From
                <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
              </label>
              <label className="settings-filter-label">
                To
                <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
              </label>
              {hasDetailFilters ? (
                <button type="button" className="btn btn-sm" onClick={clearDetailFilters}>
                  Clear filters
                </button>
              ) : null}
            </div>

            {detailLoading ? <LoadingNotice>Loading price timeline…</LoadingNotice> : null}

            {!detailLoading && rows.length > 0 ? <PriceStepChart rows={rows} /> : null}

            <div className="table-wrap">
              <table className="data-table settings-table">
                <thead>
                  <tr>
                    <th className="cell-number">MAP price</th>
                    <th>Effective from</th>
                    <th>Effective to</th>
                    <th>Source</th>
                    <th>Type</th>
                    <th>Snapshot month</th>
                    <th>Active today</th>
                    <th>Captured at</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, i) => (
                    <tr key={`${row.effective_from}-${row.source}-${i}`} className={row.is_active_for_today ? "ph-row-active" : ""}>
                      <td className="cell-number">{money(row.map_price)}</td>
                      <td>{row.effective_from}</td>
                      <td>{row.effective_to_display}</td>
                      <td className="cell-trunc" title={row.source}>{row.source}</td>
                      <td><SourceBadge kind={row.source_kind} /></td>
                      <td>{row.snapshot_month}</td>
                      <td>{row.is_active_for_today ? "Yes" : "—"}</td>
                      <td className="cell-trunc">{row.captured_at}</td>
                    </tr>
                  ))}
                  {!detailLoading && rows.length === 0 ? (
                    <tr>
                      <td colSpan={8}>
                        <p className="text-faint" style={{ padding: "1rem 0" }}>
                          No price history rows for this SKU{hasDetailFilters ? " with the current filters" : ""}.
                          {hasDetailFilters ? " Try clearing filters above." : " This SKU may not exist in price_history yet."}
                        </p>
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      ) : (
        <p className="text-faint ph-hint">
          Search, pick from the dropdown, or browse the full SKU table — then select a SKU to view its MAP trajectory.
        </p>
      )}
    </div>
  );
}
