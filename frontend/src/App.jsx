import React, { useEffect, useMemo, useState } from "react";
import { apiFetch, readJson } from "./lib/api.js";
import GenerateView from "./components/GenerateView.jsx";
import AdjustmentsView from "./components/AdjustmentsView.jsx";
import AuditReviewView from "./components/AuditReviewView.jsx";
import CommissionsView from "./components/CommissionsView.jsx";
import ZohoView from "./components/ZohoView.jsx";
import ReportsView from "./components/ReportsView.jsx";
import HelpView from "./components/HelpView.jsx";
import SettingsView from "./components/SettingsView.jsx";
import { IconAudit, IconCloud, IconHistory, IconReports, IconSparkle, IconList, IconInfo, IconSettings } from "./components/Icons.jsx";
import { useAuth } from "./context/AuthContext.jsx";
import { authEnabled } from "./lib/supabase.js";

const TABS = [
  { id: "generate", label: "Generate Commissions", subtitle: "Generate the month's workbook in one click", icon: IconSparkle },
  { id: "adjustments", label: "Adjustments", subtitle: "Accounting review and adjustments", icon: IconList },
  { id: "audit", label: "Audit", subtitle: "Detailed analysis and validation", icon: IconAudit },
  { id: "commissions", label: "History", subtitle: "Previous commission workbooks", icon: IconHistory },
  { id: "zoho", label: "Zoho Books", subtitle: "Exported operational data", icon: IconCloud },
  { id: "reports", label: "Reports", subtitle: "Generated audits", icon: IconReports },
  { id: "settings", label: "Commission Settings", subtitle: "Rules, rates, MAP history, and roster (read-only)", icon: IconSettings },
  { id: "help", label: "Help", subtitle: "User guide & how the flow works", icon: IconInfo },
];

function formatDatabaseBackend(backend) {
  if (backend === "postgres") return "Postgres";
  if (backend === "sqlite") return "SQLite";
  return backend || "—";
}

export default function App() {
  const [activeTab, setActiveTab] = useState("generate");
  const [databaseBackend, setDatabaseBackend] = useState(null);
  const active = useMemo(() => TABS.find((tab) => tab.id === activeTab) || TABS[0], [activeTab]);
  const { user, signOut } = useAuth();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch("health");
        const payload = await readJson(res);
        if (!cancelled) setDatabaseBackend(payload?.database_backend || null);
      } catch {
        if (!cancelled) setDatabaseBackend(null);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="sidebar-brand">
          <div className="sidebar-brand-logo">BB</div>
          <div className="sidebar-brand-text">
            <div className="sidebar-brand-title">Commission Automation</div>
            <div className="sidebar-brand-subtitle">Big Battery</div>
          </div>
        </div>

        <div className="sidebar-section-label">Navigation</div>
        <nav className="sidebar-nav">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`sidebar-link ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <tab.icon />
              {tab.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-status-block">
            <div className="sidebar-status-row">
              <span className="sidebar-status-label">Database</span>
              <span className="sidebar-status-value">{formatDatabaseBackend(databaseBackend)}</span>
            </div>
            <div className="sidebar-status-row">
              <span className="sidebar-status-label">Source</span>
              <span className="sidebar-status-value">Zoho Books</span>
            </div>
            {authEnabled && user ? (
              <div className="sidebar-status-row">
                <span className="sidebar-status-label">User</span>
                <span className="sidebar-status-value sidebar-user-email">{user.email}</span>
              </div>
            ) : null}
          </div>
          {authEnabled ? (
            <button type="button" className="btn btn-sm btn-block sidebar-signout" onClick={() => signOut()}>
              Sign out
            </button>
          ) : null}
          Recommended flow: Sync -&gt; Audit -&gt; Review -&gt; Export.
        </div>
      </aside>

      <main className="app-main">
        <header className="app-header">
          <div className="app-header-title">
            <h1>{active.label}</h1>
            <span className="app-header-subtitle">{active.subtitle}</span>
          </div>
          <div className="app-header-meta">
            <span className="pill pill-info">
              <span className="pill-dot" />
              Internal production
            </span>
          </div>
        </header>

        <nav className="mobile-nav">
          {TABS.map((tab) => (
            <button
              key={`mobile-${tab.id}`}
              type="button"
              className={`mobile-nav-btn ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <tab.icon />
              <span>{tab.label}</span>
            </button>
          ))}
        </nav>

        <div className="app-content">
          {activeTab === "generate" && <GenerateView />}
          {activeTab === "adjustments" && <AdjustmentsView />}
          {activeTab === "audit" && <AuditReviewView />}
          {activeTab === "commissions" && <CommissionsView />}
          {activeTab === "zoho" && <ZohoView />}
          {activeTab === "reports" && <ReportsView />}
          {activeTab === "settings" && <SettingsView />}
          {activeTab === "help" && <HelpView />}
        </div>
      </main>
    </div>
  );
}
