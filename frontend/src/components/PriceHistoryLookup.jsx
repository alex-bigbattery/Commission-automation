import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch, downloadApi, readJson } from "../lib/api.js";
import { Banner, ErrorBanner, LoadingNotice, Pill } from "./ui.jsx";
import { IconAlert, IconRefresh, IconSearch } from "./Icons.jsx";

const CATALOG_PAGE = 100;
const MATRIX_PAGE_SIZE_MAX = 2000;
const MATRIX_PREVIEW_DEFAULT = MATRIX_PAGE_SIZE_MAX;
const MATRIX_PAGE_SIZE_OPTIONS = [100, 250, 500, 1000, MATRIX_PAGE_SIZE_MAX];

const PH_TABS = [
  { id: "search", label: "Search SKU", hint: "Inspect one SKU's MAP history." },
  { id: "browse", label: "Browse SKUs", hint: "Find a SKU and open its timeline." },
  { id: "matrix", label: "Timeline Matrix / Export", hint: "Create a SKU-by-date price matrix for a selected range." },
  { id: "detail", label: "Change Detail", hint: "Audit raw price_history rows." },
];

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function defaultGranularity(fromIso, toIso) {
  const a = new Date(`${fromIso}T00:00:00`);
  const b = new Date(`${toIso}T00:00:00`);
  const days = Math.round((b - a) / 86400000) + 1;
  return days <= 45 ? "daily" : "monthly";
}

function matrixCellClass(sourceType) {
  if (sourceType === "accountant_fvprice") return "ph-cell-accountant";
  if (sourceType === "imported_rlp") return "ph-cell-rlp";
  if (sourceType === "zoho_live_sync" || sourceType === "zoho_catalog_snapshot") return "ph-cell-live";
  if (sourceType === "rlp_template_fallback" || sourceType === "rlp_fallback") return "ph-cell-rlp-fallback";
  return "";
}

function MatrixPriceCell({ cell }) {
  if (!cell) return <span className="ph-cell-empty">—</span>;
  const title = [cell.source, cell.source_caution].filter(Boolean).join(" · ");
  return (
    <span className={`ph-matrix-price ${matrixCellClass(cell.source_type)}`} title={title}>
      {money(cell.map_price)}
    </span>
  );
}

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  });
}

function summarizePeriodPriceChange(rows) {
  if (!rows?.length) return { changed: false, label: "No coverage" };
  const sorted = [...rows].sort(
    (a, b) => String(a.effective_from).localeCompare(String(b.effective_from)),
  );
  const levels = [];
  for (const row of sorted) {
    const p = Number(row.map_price);
    if (Number.isNaN(p)) continue;
    if (!levels.length || levels[levels.length - 1] !== p) levels.push(p);
  }
  if (!levels.length) return { changed: false, label: "No coverage" };
  if (levels.length === 1) return { changed: false, label: "No change" };
  return { changed: true, label: `${money(levels[0])} → ${money(levels[levels.length - 1])}` };
}

const SOURCE_KIND_META = {
  accountant_fvprice: { label: "Accountant FV_PRICE snapshot", variant: "info" },
  imported_rlp: { label: "Imported R_LP snapshot", variant: "warning" },
  zoho_live_sync: { label: "Zoho live sync", variant: "success" },
  zoho_catalog_snapshot: { label: "Zoho catalog snapshot", variant: "success" },
  manual: { label: "Manual", variant: "warning" },
  other_snapshot: { label: "Other snapshot", variant: "default" },
  other: { label: "Other", variant: "default" },
};

function SourceBadge({ kind }) {
  const def = SOURCE_KIND_META[kind] || SOURCE_KIND_META.other;
  return <Pill variant={def.variant}>{def.label}</Pill>;
}

function SourceTypeCell({ row }) {
  return (
    <div className="ph-source-type">
      <SourceBadge kind={row.source_kind} />
      {row.source_caution ? (
        <span className="ph-source-caution" title={row.source_caution}>
          {row.source_caution}
        </span>
      ) : null}
    </div>
  );
}

function SourceLegend() {
  return (
    <div className="ph-source-legend">
      <SourceBadge kind="accountant_fvprice" />
      <SourceBadge kind="imported_rlp" />
      <SourceBadge kind="zoho_live_sync" />
      <span className="text-faint ph-source-legend-note">
        Imported R_LP rows are fallback/reference only — not confirmed accountant FV_PRICE.
      </span>
    </div>
  );
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
      accountant_fvprice: "#2563eb",
      imported_rlp: "#d97706",
      zoho_live_sync: "#16a34a",
      zoho_catalog_snapshot: "#059669",
      manual: "#d97706",
      other_snapshot: "#64748b",
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

function BrowseTable({ rows, onViewTimeline, loading }) {
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
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.sku} className="ph-catalog-row">
              <td><code>{r.sku}</code></td>
              <td>{r.item_id || "—"}</td>
              <td className="cell-number">{money(r.current_price)}</td>
              <td>{r.latest_effective_from}</td>
              <td className="cell-trunc" title={r.latest_source}>{r.latest_source}</td>
              <td>{r.latest_snapshot_month}</td>
              <td className="cell-number">{r.row_count}</td>
              <td>
                <button type="button" className="btn btn-sm" onClick={() => onViewTimeline(r.sku)}>
                  View timeline
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function PriceHistoryLookup() {
  const [activeTab, setActiveTab] = useState("search");

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
  const [dbDiag, setDbDiag] = useState(null);

  const [selectedSku, setSelectedSku] = useState("");
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [sourceFilter, setSourceFilter] = useState("");
  const [monthFilter, setMonthFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const [timelineFrom, setTimelineFrom] = useState("2026-04-01");
  const [timelineTo, setTimelineTo] = useState(todayIso());
  const [granularity, setGranularity] = useState("daily");
  const [granularityTouched, setGranularityTouched] = useState(false);
  const [includeFallback, setIncludeFallback] = useState(false);
  const [matrixSkuFilter, setMatrixSkuFilter] = useState("");
  const [matrixPreviewLimit, setMatrixPreviewLimit] = useState(MATRIX_PREVIEW_DEFAULT);
  const [matrixPage, setMatrixPage] = useState(0);
  const [showMatrixPreview, setShowMatrixPreview] = useState(false);
  const [matrixData, setMatrixData] = useState(null);
  const [matrixLoading, setMatrixLoading] = useState(false);
  const [matrixError, setMatrixError] = useState(null);

  const [detailSkuFilter, setDetailSkuFilter] = useState("");
  const [detailSourceFilter, setDetailSourceFilter] = useState("");
  const [detailSourceType, setDetailSourceType] = useState("");
  const [detailFrom, setDetailFrom] = useState("2026-04-01");
  const [detailTo, setDetailTo] = useState(todayIso());
  const [detailList, setDetailList] = useState(null);
  const [detailListLoading, setDetailListLoading] = useState(false);
  const [detailListError, setDetailListError] = useState(null);

  const activeTabMeta = PH_TABS.find((t) => t.id === activeTab) || PH_TABS[0];

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
      const res = await apiFetch(`settings/price-history/catalog?${params.toString()}`);
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
      const res = await apiFetch("settings/price-history/catalog?limit=500&offset=0");
      const payload = await readJson(res);
      setDropdownOptions(Array.isArray(payload?.results) ? payload.results : []);
      setDbDiag({
        backend: payload?.database_backend,
        rows: payload?.price_history_row_count,
        skus: payload?.price_history_sku_count,
      });
      if ((payload?.total ?? 0) === 0) {
        const backend = payload?.database_backend || "unknown";
        const rows = payload?.price_history_row_count;
        const skus = payload?.price_history_sku_count;
        setCatalogError({
          title: "No price history data on this backend",
          message: rows != null
            ? `Backend=${backend} · price_history rows=${rows} · SKUs=${skus ?? 0}. `
              + (backend === "sqlite"
                ? "Production likely has no DATABASE_URL — connect Render to Supabase Postgres."
                : "If you expected data, verify Render DATABASE_URL matches the Supabase project where snapshots were loaded.")
            : "The catalog returned zero SKUs. Check backend database connection.",
        });
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
      const res = await apiFetch(`settings/price-history/search?q=${encodeURIComponent(trimmed)}&limit=30`);
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
      const res = await apiFetch(`settings/price-history?${params.toString()}`);
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

  const buildMatrixParams = useCallback((extra = {}) => {
    const params = new URLSearchParams({
      from: timelineFrom,
      to: timelineTo,
      granularity,
      ...extra,
    });
    if (includeFallback) params.set("include_fallback", "true");
    if (matrixSkuFilter.trim()) params.set("q", matrixSkuFilter.trim());
    return params;
  }, [timelineFrom, timelineTo, granularity, includeFallback, matrixSkuFilter]);

  const buildDetailListParams = useCallback((extra = {}) => {
    const params = new URLSearchParams({
      from: detailFrom,
      to: detailTo,
      ...extra,
    });
    if (detailSkuFilter.trim()) params.set("q", detailSkuFilter.trim());
    return params;
  }, [detailFrom, detailTo, detailSkuFilter]);

  const loadMatrix = useCallback(async (page = matrixPage) => {
    setMatrixLoading(true);
    setMatrixError(null);
    try {
      const safePage = Math.max(0, page);
      const params = buildMatrixParams({
        limit: String(matrixPreviewLimit),
        offset: String(safePage * matrixPreviewLimit),
      });
      const res = await apiFetch(`settings/price-history/matrix?${params.toString()}`);
      const payload = await readJson(res);
      setMatrixData(payload);
      setMatrixPage(safePage);
      setShowMatrixPreview(true);
    } catch (e) {
      setMatrixError(e);
      setMatrixData(null);
      setShowMatrixPreview(false);
    } finally {
      setMatrixLoading(false);
    }
  }, [buildMatrixParams, matrixPreviewLimit, matrixPage]);

  const resetMatrixPreview = useCallback(() => {
    setMatrixPage(0);
    setShowMatrixPreview(false);
    setMatrixData(null);
  }, []);

  const viewMatrix = useCallback(() => {
    setMatrixPage(0);
    loadMatrix(0);
  }, [loadMatrix]);

  const loadDetailList = useCallback(async () => {
    setDetailListLoading(true);
    setDetailListError(null);
    try {
      const params = buildDetailListParams({ limit: "500", offset: "0" });
      const res = await apiFetch(`settings/price-history/detail-list?${params.toString()}`);
      setDetailList(await readJson(res));
    } catch (e) {
      setDetailListError(e);
      setDetailList(null);
    } finally {
      setDetailListLoading(false);
    }
  }, [buildDetailListParams]);

  const exportMatrix = useCallback((format) => {
    const params = buildMatrixParams({ mode: "matrix", format });
    const ext = format === "xlsx" ? "xlsx" : "csv";
    return downloadApi(`settings/price-history/export?${params.toString()}`, `price_timeline_matrix.${ext}`);
  }, [buildMatrixParams]);

  const exportDetail = useCallback((format) => {
    const params = buildDetailListParams({ mode: "detail", format });
    const ext = format === "xlsx" ? "xlsx" : "csv";
    return downloadApi(`settings/price-history/export?${params.toString()}`, `price_history_detail.${ext}`);
  }, [buildDetailListParams]);

  useEffect(() => {
    loadDropdownOptions();
  }, [loadDropdownOptions]);

  useEffect(() => {
    const t = setTimeout(() => runSearch(query), 250);
    return () => clearTimeout(t);
  }, [query, runSearch]);

  useEffect(() => {
    if (activeTab !== "browse") return undefined;
    const t = setTimeout(() => loadCatalog(catalogFilter, catalogPage), 200);
    return () => clearTimeout(t);
  }, [activeTab, catalogFilter, catalogPage, loadCatalog]);

  useEffect(() => {
    if (activeTab !== "search" || !selectedSku) return undefined;
    const t = setTimeout(() => loadDetail(selectedSku), 200);
    return () => clearTimeout(t);
  }, [activeTab, selectedSku, loadDetail]);

  useEffect(() => {
    if (!granularityTouched && timelineFrom && timelineTo) {
      setGranularity(defaultGranularity(timelineFrom, timelineTo));
    }
  }, [timelineFrom, timelineTo, granularityTouched]);

  const openSkuTimeline = (sku) => {
    setSelectedSku(sku);
    setQuery(sku);
    setActiveTab("search");
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

  const matrixDates = matrixData?.dates ?? [];
  const matrixRows = matrixData?.rows ?? [];
  const matrixWarnings = matrixData?.warnings ?? [];
  const matrixTotalSkus = matrixData?.total_skus ?? 0;
  const matrixOffset = matrixData?.offset ?? 0;
  const matrixPages = Math.max(1, Math.ceil(matrixTotalSkus / matrixPreviewLimit));
  const matrixRangeStart = matrixTotalSkus ? matrixOffset + 1 : 0;
  const matrixRangeEnd = matrixOffset + (matrixData?.count ?? 0);
  const manyDateColumns = showMatrixPreview && matrixDates.length > 45;

  const filteredDetailRows = useMemo(() => {
    const rows = detailList?.rows ?? [];
    return rows.filter((row) => {
      if (detailSourceFilter.trim()
        && !String(row.source || "").toLowerCase().includes(detailSourceFilter.trim().toLowerCase())) {
        return false;
      }
      if (detailSourceType && row.source_kind !== detailSourceType) return false;
      return true;
    });
  }, [detailList, detailSourceFilter, detailSourceType]);

  const skuRows = detail?.rows ?? [];
  const skuPeriodPriceChange = useMemo(
    () => summarizePeriodPriceChange(skuRows),
    [skuRows],
  );
  const skuWarnings = detail?.warnings ?? [];
  const sources = detail?.sources ?? [];
  const months = detail?.snapshot_months ?? [];

  return (
    <div className="ph-lookup">
      <Banner type="info" icon={IconAlert}>
        Price history is read-only. Historical prices are used to calculate commissions based on sale date.
      </Banner>

      <ErrorBanner error={catalogError} onRetry={loadDropdownOptions} />

      {dbDiag?.rows != null ? (
        <p className="text-faint ph-hint">
          Server DB: <strong>{dbDiag.backend}</strong>
          {" · "}price_history rows: <strong>{dbDiag.rows}</strong>
          {" · "}SKUs: <strong>{dbDiag.skus ?? 0}</strong>
        </p>
      ) : null}

      <div className="ph-subtabs">
        {PH_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`tab ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
            {tab.id === "browse" && catalog.total ? (
              <span className="tab-count">{catalog.total}</span>
            ) : null}
          </button>
        ))}
      </div>

      <p className="text-faint ph-tab-hint">{activeTabMeta.hint}</p>

      {activeTab === "search" ? (
        <div className="ph-tab-panel">
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
                    if (e.key === "Enter" && query.trim()) openSkuTimeline(query.trim());
                  }}
                />
              </div>
              <label className="settings-filter-label ph-dropdown-label">
                Or select SKU
                <select
                  value={selectedSku}
                  onChange={(e) => {
                    if (e.target.value) openSkuTimeline(e.target.value);
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
                Dropdown shows first 500 SKUs. Use <strong>Browse SKUs</strong> for the full list.
              </p>
            ) : null}
            {searchLoading ? <LoadingNotice>Searching…</LoadingNotice> : null}
            {searchResults.length > 0 && query.trim().length >= 2 ? (
              <ul className="ph-search-results">
                {searchResults.map((r) => (
                  <li key={r.sku}>
                    <button type="button" className="ph-search-hit" onClick={() => openSkuTimeline(r.sku)}>
                      <strong><code>{r.sku}</code></strong>
                      <span>{money(r.current_price)}</span>
                      <span className="text-faint">{r.row_count} row(s) · {r.latest_source}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          {!selectedSku ? (
            <p className="text-faint ph-hint">Search or select a SKU to view its MAP trajectory.</p>
          ) : (
            <section className="card ph-detail-card">
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

                {skuWarnings.length > 0 ? (
                  <div className="ph-warnings">
                    {skuWarnings.map((w, i) => (
                      <Banner key={`w-${i}`} type="warning" icon={IconAlert}>{w}</Banner>
                    ))}
                  </div>
                ) : null}

                <SourceLegend />

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
                {!detailLoading && skuRows.length > 0 ? <PriceStepChart rows={skuRows} /> : null}

                <div className="table-wrap">
                  <table className="data-table settings-table ph-sku-timeline-table">
                    <thead>
                      <tr>
                        <th className="cell-number">MAP price</th>
                        <th>Effective from</th>
                        <th>Effective to</th>
                        <th>Snapshot month</th>
                        <th>
                          Price change
                          {dateFrom || dateTo ? (
                            <span className="ph-col-period-hint">
                              {dateFrom && dateTo ? ` (${dateFrom} → ${dateTo})` : dateFrom ? ` (from ${dateFrom})` : ` (to ${dateTo})`}
                            </span>
                          ) : null}
                        </th>
                        <th>Active today</th>
                        <th>Captured at</th>
                      </tr>
                    </thead>
                    <tbody>
                      {skuRows.map((row, i) => (
                        <tr key={`${row.effective_from}-${row.source}-${i}`} className={row.is_active_for_today ? "ph-row-active" : ""}>
                          <td className="cell-number">{money(row.map_price)}</td>
                          <td>{row.effective_from}</td>
                          <td>{row.effective_to_display}</td>
                          <td>{row.snapshot_month}</td>
                          {i === 0 ? (
                            <td
                              rowSpan={skuRows.length}
                              className={skuPeriodPriceChange.changed ? "ph-price-changed" : "ph-price-unchanged"}
                            >
                              {skuPeriodPriceChange.label}
                            </td>
                          ) : null}
                          <td>{row.is_active_for_today ? "Yes" : "—"}</td>
                          <td className="cell-trunc">{row.captured_at}</td>
                        </tr>
                      ))}
                      {!detailLoading && skuRows.length === 0 ? (
                        <tr>
                          <td colSpan={7}>
                            <p className="text-faint" style={{ padding: "1rem 0" }}>
                              No price history rows for this SKU{hasDetailFilters ? " with the current filters" : ""}.
                            </p>
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>
          )}
        </div>
      ) : null}

      {activeTab === "browse" ? (
        <div className="ph-tab-panel">
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
                    placeholder="Filter by SKU or item_id…"
                    value={catalogFilter}
                    onChange={(e) => {
                      setCatalogFilter(e.target.value);
                      setCatalogPage(0);
                    }}
                  />
                </div>
                <span className="text-faint">
                  {catalog.total.toLocaleString()} SKU(s) total
                </span>
              </div>
              <BrowseTable
                rows={catalog.results}
                onViewTimeline={openSkuTimeline}
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
            </div>
          </section>
        </div>
      ) : null}

      {activeTab === "matrix" ? (
        <div className="ph-tab-panel">
          <div className="table-toolbar settings-filters ph-timeline-filters">
            <label className="settings-filter-label">
              From
              <input type="date" value={timelineFrom} onChange={(e) => { setTimelineFrom(e.target.value); resetMatrixPreview(); }} />
            </label>
            <label className="settings-filter-label">
              To
              <input type="date" value={timelineTo} onChange={(e) => { setTimelineTo(e.target.value); resetMatrixPreview(); }} />
            </label>
            <label className="settings-filter-label">
              Granularity
              <select
                value={granularity}
                onChange={(e) => {
                  setGranularity(e.target.value);
                  setGranularityTouched(true);
                  resetMatrixPreview();
                }}
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </label>
            <label className="settings-filter-label">
              SKU filter
              <input
                type="search"
                value={matrixSkuFilter}
                onChange={(e) => { setMatrixSkuFilter(e.target.value); resetMatrixPreview(); }}
                placeholder="Optional SKU filter…"
              />
            </label>
            <label className="settings-filter-label">
              Rows per page
              <select
                value={matrixPreviewLimit}
                onChange={(e) => {
                  setMatrixPreviewLimit(Number(e.target.value));
                  resetMatrixPreview();
                }}
              >
                {MATRIX_PAGE_SIZE_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n === MATRIX_PAGE_SIZE_MAX ? `${n} (max)` : n}
                  </option>
                ))}
              </select>
            </label>
            <label className="settings-filter-label ph-checkbox-label">
              <input
                type="checkbox"
                checked={includeFallback}
                onChange={(e) => {
                  setIncludeFallback(e.target.checked);
                  resetMatrixPreview();
                }}
              />
              Include fallback prices (R_LP template)
            </label>
            <button type="button" className="btn btn-sm btn-primary" onClick={viewMatrix} disabled={matrixLoading}>
              View Matrix
            </button>
          </div>

          <div className="ph-export-row">
            <span className="text-faint">Export matrix (up to 2,000 SKUs):</span>
            <button type="button" className="btn btn-sm" onClick={() => exportMatrix("csv")}>Matrix CSV</button>
            <button type="button" className="btn btn-sm" onClick={() => exportMatrix("xlsx")}>Matrix Excel</button>
          </div>

          <ErrorBanner error={matrixError} onRetry={viewMatrix} />
          {matrixLoading ? <LoadingNotice>Building matrix preview…</LoadingNotice> : null}

          {manyDateColumns ? (
            <Banner type="warning" icon={IconAlert}>
              This view has many date columns ({matrixDates.length}).
              Use weekly/monthly granularity or export to Excel for full review.
            </Banner>
          ) : null}

          {showMatrixPreview && !matrixLoading && matrixData ? (
            <>
              <p className="text-faint ph-hint">
                Showing {matrixRangeStart}–{matrixRangeEnd} of {matrixTotalSkus} SKU(s)
                · page {matrixPage + 1} of {matrixPages}
                · {matrixData.date_count} {matrixData.granularity} column(s)
                · {timelineFrom} → {timelineTo}
                · fallback {matrixData.include_fallback ? "on" : "off"}
              </p>
              {matrixWarnings.map((w, i) => (
                <Banner key={`mw-${i}`} type="warning" icon={IconAlert}>{w}</Banner>
              ))}
              <div className="ph-matrix-scroll">
                <table className="data-table settings-table ph-matrix-table">
                  <thead>
                    <tr>
                      <th className="ph-sticky-col ph-sticky-head">SKU</th>
                      <th className="ph-sticky-col-2 ph-sticky-head">Item ID</th>
                      <th className="ph-sticky-col-3 ph-sticky-head cell-number">Current MAP</th>
                      <th className="ph-sticky-head">Price changed</th>
                      {matrixDates.map((d) => <th key={d} className="cell-number ph-date-col ph-sticky-head">{d}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {matrixRows.map((row) => (
                      <tr key={row.sku}>
                        <td className="ph-sticky-col"><code>{row.sku}</code></td>
                        <td className="ph-sticky-col-2">{row.item_id || "—"}</td>
                        <td className="ph-sticky-col-3 cell-number">{money(row.current_map)}</td>
                        <td
                          className={row.price_changed ? "ph-price-changed" : "ph-price-unchanged"}
                          title={row.price_change_label && row.price_change_label !== row.price_changed_display
                            ? row.price_change_label
                            : undefined}
                        >
                          {row.price_changed_display || row.price_change_label || "—"}
                        </td>
                        {matrixDates.map((d) => (
                          <td key={`${row.sku}-${d}`} className="cell-number">
                            <MatrixPriceCell cell={row.prices?.[d]} />
                          </td>
                        ))}
                      </tr>
                    ))}
                    {!matrixRows.length ? (
                      <tr><td colSpan={4 + matrixDates.length} className="text-faint">No SKUs match this filter.</td></tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
              {matrixPages > 1 ? (
                <div className="ph-pagination">
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={matrixPage <= 0 || matrixLoading}
                    onClick={() => loadMatrix(matrixPage - 1)}
                  >
                    Previous
                  </button>
                  <span className="text-faint">
                    Page {matrixPage + 1} of {matrixPages}
                    {" · "}{matrixPreviewLimit} per page
                  </span>
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={matrixPage >= matrixPages - 1 || matrixLoading}
                    onClick={() => loadMatrix(matrixPage + 1)}
                  >
                    Next
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            !matrixLoading ? (
              <p className="text-faint ph-hint">Set a date range and click <strong>View Matrix</strong> to preview.</p>
            ) : null
          )}
        </div>
      ) : null}

      {activeTab === "detail" ? (
        <div className="ph-tab-panel">
          <div className="table-toolbar settings-filters ph-timeline-filters">
            <label className="settings-filter-label">
              SKU filter
              <input
                type="search"
                value={detailSkuFilter}
                onChange={(e) => setDetailSkuFilter(e.target.value)}
                placeholder="Optional SKU filter…"
              />
            </label>
            <label className="settings-filter-label">
              Source contains
              <input
                type="text"
                value={detailSourceFilter}
                onChange={(e) => setDetailSourceFilter(e.target.value)}
                placeholder="e.g. accountant_fvprice"
              />
            </label>
            <label className="settings-filter-label">
              Source type
              <select value={detailSourceType} onChange={(e) => setDetailSourceType(e.target.value)}>
                <option value="">All</option>
                <option value="accountant_fvprice">Accountant FV_PRICE</option>
                <option value="imported_rlp">Imported R_LP</option>
                <option value="zoho_live_sync">Zoho live sync</option>
                <option value="zoho_catalog_snapshot">Zoho catalog snapshot</option>
              </select>
            </label>
            <label className="settings-filter-label">
              From
              <input type="date" value={detailFrom} onChange={(e) => setDetailFrom(e.target.value)} />
            </label>
            <label className="settings-filter-label">
              To
              <input type="date" value={detailTo} onChange={(e) => setDetailTo(e.target.value)} />
            </label>
            <button type="button" className="btn btn-sm btn-primary" onClick={loadDetailList} disabled={detailListLoading}>
              Load detail
            </button>
          </div>

          <div className="ph-export-row">
            <span className="text-faint">Export detail (up to 5,000 rows):</span>
            <button type="button" className="btn btn-sm" onClick={() => exportDetail("csv")}>Detail CSV</button>
            <button type="button" className="btn btn-sm" onClick={() => exportDetail("xlsx")}>Detail Excel</button>
          </div>

          <ErrorBanner error={detailListError} onRetry={loadDetailList} />
          {detailListLoading ? <LoadingNotice>Loading price history detail…</LoadingNotice> : null}

          {detailList && !detailListLoading ? (
            <>
              <p className="text-faint ph-hint">
                Showing {filteredDetailRows.length} row(s)
                {filteredDetailRows.length !== detailList.count ? ` (filtered from ${detailList.count})` : ""}
                · {detailFrom} → {detailTo}
              </p>
              <div className="table-wrap">
                <table className="data-table settings-table">
                  <thead>
                    <tr>
                      <th>SKU</th>
                      <th>Item ID</th>
                      <th className="cell-number">MAP Price</th>
                      <th>Effective From</th>
                      <th>Effective To</th>
                      <th>Source</th>
                      <th>Type</th>
                      <th>Snapshot Month</th>
                      <th>Active Today</th>
                      <th>Captured At</th>
                      <th>Warning / Caution</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredDetailRows.map((row, i) => (
                      <tr key={`${row.sku}-${row.effective_from}-${i}`}>
                        <td><code>{row.sku}</code></td>
                        <td>{row.item_id || "—"}</td>
                        <td className="cell-number">{money(row.map_price)}</td>
                        <td>{row.effective_from}</td>
                        <td>{row.effective_to_display}</td>
                        <td className="cell-trunc" title={row.source}>{row.source}</td>
                        <td><SourceBadge kind={row.source_kind} /></td>
                        <td>{row.snapshot_month}</td>
                        <td>{row.is_active_for_today ? "Yes" : "—"}</td>
                        <td className="cell-trunc">{row.captured_at}</td>
                        <td className="cell-trunc">{row.warning_caution || row.source_caution || "—"}</td>
                      </tr>
                    ))}
                    {!filteredDetailRows.length ? (
                      <tr><td colSpan={11} className="text-faint">No rows match the current filters.</td></tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            !detailListLoading ? (
              <p className="text-faint ph-hint">Set filters and click <strong>Load detail</strong> to audit price_history rows.</p>
            ) : null
          )}
        </div>
      ) : null}
    </div>
  );
}
