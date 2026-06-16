// Email allowlist for the commission app — mirrors the affiliate dashboard's
// authConfig.js so the same Big Battery people sign in with the same Supabase
// credentials. Both apps share one Supabase project; this list gates access.
//
// Override with VITE_COMMISSION_ALLOWED_EMAILS (comma-separated) if needed.
// The backend enforces the same gate (backend/auth_allowlist.py) — this copy is
// for UX (instant feedback on the login screen).

const DEFAULT_ALLOWED_EMAILS = [
  "alex.g@bigbattery.com",
  "honey.g@bigbattery.com",
  "receivables@bigbattery.com",
  "jennifer.z@bigbattery.com",
  "santiago.o@bigbattery.com",
  "marshall@bigbattery.com",
  "kunal.d@bigbattery.com",
];

export const DASHBOARD_EMAIL_DOMAIN = "@bigbattery.com";

function loadAllowed() {
  const raw = (import.meta.env?.VITE_COMMISSION_ALLOWED_EMAILS || "").trim();
  const list = raw
    ? raw.split(",").map((e) => e.trim().toLowerCase()).filter(Boolean)
    : DEFAULT_ALLOWED_EMAILS.map((e) => e.toLowerCase());
  return new Set(list);
}

const ALLOWED_EMAIL_SET = loadAllowed();

/** "alex.g" or "alex.g@bigbattery.com" -> alex.g@bigbattery.com */
export function toDashboardEmail(input) {
  const raw = String(input || "").trim().toLowerCase();
  if (!raw) return "";
  if (raw.includes("@")) return raw;
  return `${raw}${DASHBOARD_EMAIL_DOMAIN}`;
}

/** Empty allowlist (env-cleared) means no gating. */
export function isAllowedEmail(email) {
  if (ALLOWED_EMAIL_SET.size === 0) return true;
  return ALLOWED_EMAIL_SET.has(String(email || "").trim().toLowerCase());
}
