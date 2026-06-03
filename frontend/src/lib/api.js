import { supabase } from "./supabase.js";

/** Local dev: `/api` (Vite proxy). Production: set VITE_API_BASE_URL to Render (bypasses Netlify 26s proxy limit). */
const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").trim().replace(/\/$/, "");
const API = API_BASE ? `${API_BASE}/api` : "/api";

// Optional developer/support contact (email or URL). Set VITE_SUPPORT_CONTACT
// in the frontend env to turn the "Contact the developer" text into a link.
export const SUPPORT_CONTACT = (import.meta.env?.VITE_SUPPORT_CONTACT || "").trim();

export { API };

/**
 * Friendly, user-facing error carrying a title, a suggested fix, and the raw
 * technical detail (for the developer).
 */
export class ApiError extends Error {
  constructor({ title, message, suggestion, status, detail }) {
    super(message || title || "Something went wrong");
    this.name = "ApiError";
    this.title = title || "Something went wrong";
    this.suggestion = suggestion || "";
    this.status = status ?? null;
    this.detail = detail || "";
  }
}

function friendlyError(status, rawDetail) {
  const d = (typeof rawDetail === "string" ? rawDetail : "").trim();
  const lower = d.toLowerCase();

  if (status === 0) {
    return {
      title: "Can't reach the server",
      message: "The app couldn't connect to the server.",
      suggestion:
        "Check your internet connection. If the app was just opened, the server may still be starting — wait about 30 seconds and try again.",
    };
  }
  if (status === 504 || status === 408 || lower.includes("timeout") || lower.includes("timed out")) {
    return {
      title: "The server took too long to respond",
      message: "The request timed out before it finished.",
      suggestion:
        "Generating commissions can take 1–2 minutes on the first try (server wake-up). Wait a moment and try again. If it keeps failing, refresh the page and retry.",
    };
  }
  if (status === 502 || status === 503) {
    return {
      title: "Service temporarily unavailable",
      message: d || "The server or an upstream service (like Zoho) is unavailable right now.",
      suggestion: "Please wait a moment and try again. If it keeps failing, contact the developer.",
    };
  }
  if (status === 401 || status === 403) {
    return {
      title: "Session expired or access denied",
      message: d || "Your session expired or you don't have access.",
      suggestion: "Please sign in again. If the problem continues, contact the developer.",
    };
  }
  if (status === 404) {
    return {
      title: "Not found",
      message: d || "The requested item could not be found.",
      suggestion: "Refresh the page and try again. If it persists, contact the developer.",
    };
  }
  if (status === 400 || status === 422) {
    // Usually a user-actionable validation message — show it as-is.
    return {
      title: "Please check the request",
      message: d || "The request was invalid.",
      suggestion: "",
    };
  }
  if (status >= 500 || lower.includes("internal server error")) {
    return {
      title: "Something went wrong on the server",
      message: "An unexpected error happened while processing your request.",
      suggestion:
        "Try again in a moment. If it keeps happening, contact the developer and share the error code below.",
    };
  }
  return {
    title: "Something went wrong",
    message: d || (status ? `The request failed (error ${status}).` : "The request failed."),
    suggestion: "Try again. If it persists, contact the developer.",
  };
}

export async function apiFetch(path, options = {}) {
  const url = path.startsWith("/") ? path : `${API}/${path}`;
  const headers = new Headers(options.headers || {});

  if (supabase) {
    const { data } = await supabase.auth.getSession();
    const token = data.session?.access_token;
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  try {
    return await fetch(url, { ...options, headers });
  } catch (err) {
    // Network failure / server unreachable.
    const f = friendlyError(0, String(err?.message || err));
    throw new ApiError({ ...f, status: 0, detail: String(err?.message || err) });
  }
}

export async function readJson(res) {
  const text = await res.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!res.ok) {
    const rawDetail = data.detail || data.message || res.statusText || "";
    const f = friendlyError(res.status, rawDetail);
    throw new ApiError({
      ...f,
      status: res.status,
      detail: typeof rawDetail === "string" ? rawDetail : JSON.stringify(rawDetail),
    });
  }
  return data;
}

export async function downloadApi(path, filename = "download") {
  const res = await apiFetch(path);
  if (!res.ok) {
    await readJson(res); // throws a friendly ApiError
  }
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}
