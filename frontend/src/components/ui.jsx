import React, { createContext, useCallback, useContext, useState } from "react";
import { IconCheck, IconAlert, IconInfo, IconX } from "./Icons.jsx";
import { SUPPORT_CONTACT } from "../lib/api.js";

/* ---------------------------------------------------------------------------
   Toast system
   --------------------------------------------------------------------------- */

const ToastContext = createContext(() => {});

export function useToast() {
  return useContext(ToastContext);
}

const TOAST_ICONS = {
  success: IconCheck,
  warning: IconAlert,
  danger: IconAlert,
  info: IconInfo,
};

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const remove = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (toast) => {
      const id = Date.now() + Math.random();
      const entry = {
        id,
        type: toast.type || "info",
        title: toast.title || "",
        body: toast.body || "",
        duration: toast.duration ?? 5000,
      };
      setToasts((prev) => [...prev, entry]);
      if (entry.duration > 0) {
        setTimeout(() => remove(id), entry.duration);
      }
      return id;
    },
    [remove]
  );

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toast-stack">
        {toasts.map((t) => {
          const Icon = TOAST_ICONS[t.type] || IconInfo;
          return (
            <div key={t.id} className={`toast toast-${t.type}`}>
              <Icon />
              <div style={{ flex: 1 }}>
                {t.title && <div className="toast-title">{t.title}</div>}
                {t.body && <div className="toast-body">{t.body}</div>}
              </div>
              <button className="toast-close" onClick={() => remove(t.id)} aria-label="Close">
                <IconX style={{ width: 14, height: 14 }} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

/* ---------------------------------------------------------------------------
   KPI card
   --------------------------------------------------------------------------- */

export function KpiCard({ label, value, icon: Icon, variant = "default", trend }) {
  return (
    <div className={`kpi-card ${variant}`}>
      <div className="kpi-card-header">
        <span className="kpi-card-label">{label}</span>
        {Icon && (
          <span className="kpi-card-icon">
            <Icon />
          </span>
        )}
      </div>
      <div className="kpi-card-value">{value}</div>
      {trend && <div className="kpi-card-trend">{trend}</div>}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Banner
   --------------------------------------------------------------------------- */

export function Banner({ type = "info", icon: Icon, children }) {
  return (
    <div className={`banner banner-${type}`}>
      {Icon && <Icon />}
      <div>{children}</div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Error banner — friendly message + suggestion + contact developer
   --------------------------------------------------------------------------- */

export function ErrorBanner({ error, onRetry }) {
  if (!error) return null;
  const e = typeof error === "string" ? { message: error } : error || {};
  const contactHref = SUPPORT_CONTACT
    ? SUPPORT_CONTACT.includes("@")
      ? `mailto:${SUPPORT_CONTACT}`
      : SUPPORT_CONTACT
    : null;

  return (
    <div className="banner banner-danger error-banner" role="alert">
      <IconAlert />
      <div className="error-banner-body">
        {e.title && <div className="error-banner-title">{e.title}</div>}
        {e.message && <div className="error-banner-msg">{e.message}</div>}
        {e.suggestion && <div className="error-banner-suggestion">💡 {e.suggestion}</div>}
        <div className="error-banner-contact">
          Still need help?{" "}
          {contactHref ? (
            <a href={contactHref}>Contact the developer</a>
          ) : (
            <span>Contact the developer</span>
          )}
          {e.status ? <span className="error-banner-code"> · error {e.status}</span> : null}
          {e.detail ? (
            <details className="error-banner-details">
              <summary>Technical details</summary>
              <code>{e.detail}</code>
            </details>
          ) : null}
        </div>
      </div>
      {onRetry && (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Empty state
   --------------------------------------------------------------------------- */

export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <div className="empty-state">
      {Icon && (
        <div className="empty-state-icon">
          <Icon />
        </div>
      )}
      {title && <p className="empty-state-title">{title}</p>}
      {description && <p className="empty-state-desc">{description}</p>}
      {action}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Pill
   --------------------------------------------------------------------------- */

export function Pill({ variant = "default", dot = false, children }) {
  return (
    <span className={`pill ${variant !== "default" ? `pill-${variant}` : ""}`}>
      {dot && <span className="pill-dot" />}
      {children}
    </span>
  );
}

/* ---------------------------------------------------------------------------
   Loading notice (period / data fetch)
   --------------------------------------------------------------------------- */

export function LoadingNotice({ children, className = "" }) {
  return (
    <div className={`loading-notice ${className}`.trim()} role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Currency / number formatting helpers
   --------------------------------------------------------------------------- */

export function money(value) {
  return Number(value ?? 0).toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
  });
}

export function num(value) {
  return Number(value ?? 0).toLocaleString();
}
