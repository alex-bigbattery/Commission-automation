import { supabase } from "./supabase.js";

const API = "/api";

export { API };

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

  return fetch(url, { ...options, headers });
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
    const msg = data.detail || data.message || res.statusText || `HTTP ${res.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export async function downloadApi(path, filename = "download") {
  const res = await apiFetch(path);
  if (!res.ok) {
    await readJson(res);
  }
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}
