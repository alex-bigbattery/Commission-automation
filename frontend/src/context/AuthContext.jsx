import React, { createContext, useContext, useEffect, useMemo, useState } from "react";
import LoginView from "../components/LoginView.jsx";
import { authEnabled, supabase } from "../lib/supabase.js";

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

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
