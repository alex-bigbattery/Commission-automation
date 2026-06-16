import React, { createContext, useContext, useEffect, useMemo, useState } from "react";
import LoginView from "../components/LoginView.jsx";
import { authEnabled, getSupabase } from "../lib/supabase.js";
import { isAllowedEmail } from "../lib/authConfig.js";

const AuthContext = createContext({
  session: null,
  user: null,
  signOut: async () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(authEnabled);

  useEffect(() => {
    const supabase = getSupabase();
    if (!supabase) {
      setLoading(false);
      return undefined;
    }

    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
    });

    return () => listener.subscription.unsubscribe();
  }, []);

  const value = useMemo(
    () => ({
      session,
      user: session?.user ?? null,
      signOut: async () => {
        const supabase = getSupabase();
        if (supabase) {
          await supabase.auth.signOut();
        }
      },
    }),
    [session]
  );

  if (loading) {
    return (
      <div className="login-page">
        <div className="login-card">
          <p className="login-subtitle">Loading session…</p>
        </div>
      </div>
    );
  }

  if (authEnabled && !session) {
    return <LoginView />;
  }

  // Defense in depth: a session can exist for any Supabase project user, but
  // only allowlisted emails may use the commission system. The backend returns
  // 403 for these too — here we block the UI and offer a way to switch accounts.
  if (authEnabled && session && !isAllowedEmail(session.user?.email)) {
    return (
      <div className="login-page">
        <div className="login-card">
          <div className="login-brand">
            <div className="sidebar-brand-logo">BB</div>
            <div>
              <h1 className="login-title">Sin acceso</h1>
              <p className="login-subtitle">
                La cuenta <strong>{session.user?.email}</strong> no tiene acceso al
                sistema de comisiones.
              </p>
            </div>
          </div>
          <button
            type="button"
            className="btn btn-primary btn-block"
            onClick={value.signOut}
          >
            Usar otra cuenta
          </button>
        </div>
      </div>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
