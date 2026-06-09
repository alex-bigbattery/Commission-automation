import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch, readJson } from "../lib/api.js";
import { Banner, ErrorBanner, LoadingNotice, Pill } from "./ui.jsx";
import { IconAlert, IconInfo, IconSettings } from "./Icons.jsx";
import PriceHistoryLookup from "./PriceHistoryLookup.jsx";

const TABS = [
  { id: "rules", label: "Rules" },
  { id: "rates", label: "Rate Table" },
  { id: "price-history", label: "Price History" },
  { id: "roster", label: "Roster" },
  { id: "ticket", label: "Ticket Policy" },
];

function pct(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function pctDirect(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${Number(value).toFixed(digits)}%`;
}

function money(value) {
  return Number(value ?? 0).toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  });
}

function sourcePill(source) {
  if (source === "Config_Settings") return <Pill variant="info">Template</Pill>;
  if (source === "code_default") return <Pill>Code default</Pill>;
  return <Pill>{source || "—"}</Pill>;
}

function ReadOnlyBanner() {
  return (
    <Banner type="warning" icon={IconAlert}>
      <strong>Read-only.</strong> Editing is disabled in this version. Historical rules should be
      versioned by effective date before any save functionality is enabled.
    </Banner>
  );
}

function templateDisplayLabel(template) {
  if (!template) return "Not found";
  if (template.filename) return template.filename;
  if (!template.path) return "Not found";
  const normalized = String(template.path).replace(/\\/g, "/");
  const parts = normalized.split("/");
  return parts[parts.length - 1] || template.path;
}

function ConfigMeta({ template }) {
  if (!template) return null;
  return (
    <p className="text-faint settings-meta">
      Template: {template.exists ? templateDisplayLabel(template) : "Not found"}
      {template.modified_at ? ` · last modified ${new Date(template.modified_at).toLocaleString()}` : ""}
    </p>
  );
}

function thresholdDisplay(row) {
  const key = row.key || "";
  if (key.includes("epsilon") || key.startsWith("discount_")) return pct(row.value);
  if (key.includes("factor")) return Number(row.value).toFixed(2);
  if (key.includes("shipping") || key.includes("over_5000")) return money(row.value);
  return String(row.value);
}

function RulesTab({ data }) {
  const thresholds = data?.policy_thresholds || [];
  const bruce = data?.bruce_rates || {};

  return (
    <div className="settings-panel">
      <section className="card">
        <div className="card-header"><h3 className="card-title">Policy thresholds</h3></div>
        <div className="card-body table-wrap">
          <table className="data-table settings-table">
            <thead>
              <tr>
                <th>Setting</th>
                <th className="cell-number">Value</th>
                <th>Source</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {thresholds.map((row) => (
                <tr key={row.key}>
                  <td><code>{row.key}</code></td>
                  <td className="cell-number">{thresholdDisplay(row)}</td>
                  <td>{sourcePill(row.source)}</td>
                  <td className="cell-trunc">{row.note || (row.template_key ? `Config_Settings → ${row.template_key}` : "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <div className="card-header"><h3 className="card-title">Bruce commission rates</h3></div>
        <div className="card-body table-wrap">
          <table className="data-table settings-table">
            <thead>
              <tr>
                <th>Rate</th>
                <th className="cell-number">Value</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {["bruce_rep_rate", "bruce_company_rate"].map((key) => {
                const row = bruce[key];
                if (!row) return null;
                return (
                  <tr key={key}>
                    <td><code>{key}</code></td>
                    <td className="cell-number">{pct(row.value)}</td>
                    <td>{sourcePill(row.source)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {bruce.note ? <p className="text-faint settings-note">{bruce.note}</p> : null}
        </div>
      </section>
    </div>
  );
}

function RatesTab({ data }) {
  const rows = data?.rate_table || [];
  const note = rows[0]?.version_note;

  return (
    <section className="card">
      <div className="card-header"><h3 className="card-title">Commission rate table</h3></div>
      <div className="card-body">
        {note ? <p className="text-faint settings-note">{note}</p> : null}
        <div className="table-wrap">
          <table className="data-table settings-table">
            <thead>
              <tr>
                <th className="cell-number">Discount %</th>
                <th className="cell-number">Salaried %</th>
                <th className="cell-number">Non-salaried %</th>
                <th>Effective from</th>
                <th>Effective to</th>
                <th>Source / version</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={`tier-${i}`}>
                  <td className="cell-number">{pctDirect(row.discount_pct, 2)}</td>
                  <td className="cell-number">{pctDirect(row.salaried_commission_pct, 2)}</td>
                  <td className="cell-number">{pctDirect(row.non_salaried_commission_pct, 2)}</td>
                  <td>{row.effective_from || "—"}</td>
                  <td>{row.effective_to || "Open"}</td>
                  <td className="cell-trunc" title={row.source}>{row.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function RosterTab({ data }) {
  const rows = data?.rows || [];

  return (
    <section className="card">
      <div className="card-header"><h3 className="card-title">Roster / salesperson config</h3></div>
      <div className="card-body">
        <p className="text-faint settings-note">
          Source: <strong>{data?.config_source || "—"}</strong>
          {data?.sales_team_note ? ` · ${data.sales_team_note}` : ""}
        </p>
        {data?.b2c_coupon_reps?.length ? (
          <p className="text-faint settings-note">
            B2C coupon reps: {data.b2c_coupon_reps.join(", ")}
          </p>
        ) : null}
        <div className="table-wrap">
          <table className="data-table settings-table">
            <thead>
              <tr>
                <th>Salesperson</th>
                <th>Sheet key</th>
                <th>Status</th>
                <th>Pay type</th>
                <th>Company</th>
                <th>Executive</th>
                <th>Sales team</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.role}-${row.salesperson}`}>
                  <td>{row.salesperson}</td>
                  <td>{row.sheet_key || "—"}</td>
                  <td><Pill variant={row.status === "inactive" ? "warning" : row.status === "active" ? "success" : "info"}>{row.status}</Pill></td>
                  <td>{row.pay_type || "—"}</td>
                  <td>{row.company_account ? "Yes" : "—"}</td>
                  <td>{row.executive_account ? "Yes" : "—"}</td>
                  <td>{row.sales_team || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function TicketTab({ data }) {
  const rules = data?.rules || [];

  return (
    <section className="card">
      <div className="card-header"><h3 className="card-title">Ticket# policy</h3></div>
      <div className="card-body">
        <p className="text-faint settings-note">
          Field: <code>{data?.field || "CF.Ticket#"}</code> · Classifier: <code>{data?.classifier || "classify_ticket_number()"}</code>
        </p>
        <div className="table-wrap">
          <table className="data-table settings-table">
            <thead>
              <tr>
                <th>Rule</th>
                <th>Match</th>
                <th>Flags</th>
                <th>Auto-exclude</th>
                <th>Needs review</th>
                <th>Examples</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id}>
                  <td><strong>{rule.label}</strong></td>
                  <td>{rule.match}</td>
                  <td>{(rule.flags || []).join(", ") || "—"}</td>
                  <td>{rule.auto_exclude ? "Yes" : "No"}</td>
                  <td>{rule.force_pending ? "Yes" : "No"}</td>
                  <td>{(rule.examples || []).filter(Boolean).join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

export default function SettingsView() {
  const [tab, setTab] = useState("rules");
  const [commission, setCommission] = useState(null);
  const [roster, setRoster] = useState(null);
  const [loadingCommission, setLoadingCommission] = useState(true);
  const [loadingRoster, setLoadingRoster] = useState(true);
  const [error, setError] = useState(null);

  const loadCommission = useCallback(async () => {
    setLoadingCommission(true);
    setError(null);
    try {
      const res = await apiFetch("settings/commission");
      setCommission(await readJson(res));
    } catch (e) {
      setError(e);
    } finally {
      setLoadingCommission(false);
    }
  }, []);

  const loadRoster = useCallback(async () => {
    setLoadingRoster(true);
    try {
      const res = await apiFetch("settings/roster");
      setRoster(await readJson(res));
    } catch (e) {
      setError((prev) => prev || e);
    } finally {
      setLoadingRoster(false);
    }
  }, []);

  useEffect(() => {
    loadCommission();
    loadRoster();
  }, [loadCommission, loadRoster]);

  const loading = loadingCommission && !commission;

  const activeLabel = useMemo(() => TABS.find((t) => t.id === tab)?.label || "Settings", [tab]);

  return (
    <div className="page settings-page">
      <section className="card settings-hero">
        <div className="card-body">
          <h2 className="settings-hero-title"><IconSettings /> Commission Settings</h2>
          <p className="settings-hero-sub">
            Read-only view of commission rules, rate tables, MAP history, roster, and ticket policy.
            Values come from the master template, database, and code defaults — nothing on this page
            changes commission calculations.
          </p>
        </div>
      </section>

      <ReadOnlyBanner />
      <ErrorBanner error={error} onRetry={() => { loadCommission(); loadRoster(); }} />

      {loading ? <LoadingNotice>Loading configuration…</LoadingNotice> : null}

      {!loading && commission ? (
        <>
          <ConfigMeta template={commission.template} />

          <section className="card">
            <div className="card-header">
              <div className="tabs">
                {TABS.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className={`tab ${tab === t.id ? "active" : ""}`}
                    onClick={() => setTab(t.id)}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
              <span className="text-faint">{activeLabel}</span>
            </div>
            <div className="card-body settings-tab-body">
              {tab === "rules" && <RulesTab data={commission} />}
              {tab === "rates" && <RatesTab data={commission} />}
              {tab === "price-history" && <PriceHistoryLookup />}
              {tab === "roster" && (loadingRoster && !roster ? <LoadingNotice>Loading roster…</LoadingNotice> : <RosterTab data={roster} />)}
              {tab === "ticket" && <TicketTab data={commission.ticket_policy} />}
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
