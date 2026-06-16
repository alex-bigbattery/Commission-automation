import { createClient } from "@supabase/supabase-js";
import { getRememberMe, setRememberMe } from "./authPreferences.js";

const url = import.meta.env.VITE_SUPABASE_URL || "";
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY || "";
const AUTH_STORAGE_KEY = "commission-automation-auth";

/** True when Supabase Auth is configured (production / gated local). */
export const authEnabled = Boolean(url && anonKey);

let _client = null;

function authStorage() {
  if (typeof window === "undefined") return undefined;
  return getRememberMe() ? window.localStorage : window.sessionStorage;
}

function buildClient() {
  return createClient(url, anonKey, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
      storage: authStorage(),
      storageKey: AUTH_STORAGE_KEY,
    },
  });
}

/** Shared Supabase client (recreated when remember-me preference changes). */
export function getSupabase() {
  if (!authEnabled) return null;
  if (!_client) {
    _client = buildClient();
  }
  return _client;
}

/** Apply remember-me choice before sign-in (localStorage = days-long session). */
export function reconfigureSupabase(rememberMe) {
  setRememberMe(rememberMe);
  _client = buildClient();
  return _client;
}

/** @deprecated Prefer getSupabase() — kept for gradual migration. */
export const supabase = authEnabled
  ? new Proxy(
      {},
      {
        get(_target, prop) {
          const client = getSupabase();
          if (!client) return undefined;
          const value = client[prop];
          return typeof value === "function" ? value.bind(client) : value;
        },
      }
    )
  : null;
