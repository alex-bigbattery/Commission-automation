import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL || "";
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY || "";

/** True when Supabase Auth is configured (production / gated local). */
export const authEnabled = Boolean(url && anonKey);

export const supabase = authEnabled ? createClient(url, anonKey) : null;
