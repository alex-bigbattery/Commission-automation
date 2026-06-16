import React, { useState } from "react";
import {
  getRememberMe,
  getRememberedEmail,
  setRememberedEmail,
  setRememberMe,
} from "../lib/authPreferences.js";
import { reconfigureSupabase } from "../lib/supabase.js";
import { isAllowedEmail, toDashboardEmail } from "../lib/authConfig.js";

/** Map a Supabase / access error into a clear Spanish message. */
function friendlySignInError(signInError) {
  const msg = String(signInError?.message || "").toLowerCase();
  if (msg.includes("invalid login") || msg.includes("invalid credentials")) {
    return "Usuario o contraseña incorrectos.";
  }
  if (msg.includes("email not confirmed")) {
    return "Tu cuenta aún no está confirmada. Revisa tu correo o contacta al administrador.";
  }
  if (msg.includes("too many requests") || msg.includes("rate limit")) {
    return "Demasiados intentos. Espera un momento y vuelve a intentar.";
  }
  if (msg.includes("network") || msg.includes("fetch")) {
    return "No se pudo conectar con el servidor. Revisa tu conexión e inténtalo de nuevo.";
  }
  return signInError?.message || "No se pudo iniciar sesión.";
}

export default function LoginView() {
  // The remembered value may be a full email or a bare username.
  const [identifier, setIdentifier] = useState(() => getRememberedEmail());
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMeChecked] = useState(() => getRememberMe());
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");

    const email = toDashboardEmail(identifier);
    if (!email) {
      setError("Escribe tu usuario o correo.");
      return;
    }
    // Instant feedback before hitting the network (backend enforces this too).
    if (!isAllowedEmail(email)) {
      setError("Tu cuenta no tiene acceso al sistema de comisiones.");
      return;
    }

    setLoading(true);
    try {
      setRememberMe(rememberMe);
      if (rememberMe) {
        setRememberedEmail(identifier.trim());
      }
      const supabase = reconfigureSupabase(rememberMe);
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email,
        password,
      });
      if (signInError) {
        setError(friendlySignInError(signInError));
        return;
      }
      // Authenticated — but Supabase will sign in ANY project user. Enforce the
      // allowlist client-side too: drop the session if the email isn't allowed.
      if (!isAllowedEmail(email)) {
        await supabase.auth.signOut();
        setError("Tu cuenta no tiene acceso al sistema de comisiones.");
      }
    } catch (err) {
      setError(friendlySignInError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <div className="sidebar-brand-logo">BB</div>
          <div>
            <h1 className="login-title">Commission Automation</h1>
            <p className="login-subtitle">Inicia sesión con tu cuenta de Big Battery</p>
          </div>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <label className="field">
            <span className="field-label">Usuario o correo</span>
            <input
              type="text"
              className="input"
              autoComplete="username"
              placeholder="alex.g"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              required
            />
            <span className="field-hint">
              Puedes escribir solo tu usuario — agregamos @bigbattery.com automáticamente.
            </span>
          </label>
          <label className="field">
            <span className="field-label">Contraseña</span>
            <div className="input-with-action">
              <input
                type={showPassword ? "text" : "password"}
                className="input"
                autoComplete={rememberMe ? "current-password" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <button
                type="button"
                className="input-action"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Ocultar contraseña" : "Mostrar contraseña"}
                title={showPassword ? "Ocultar contraseña" : "Mostrar contraseña"}
              >
                {showPassword ? "Ocultar" : "Mostrar"}
              </button>
            </div>
          </label>
          <label className="login-remember">
            <input
              type="checkbox"
              checked={rememberMe}
              onChange={(e) => setRememberMeChecked(e.target.checked)}
            />
            <span>
              Recordarme
              <span className="login-remember-hint"> — mantener la sesión en este dispositivo</span>
            </span>
          </label>
          {error ? <div className="banner banner-error">{error}</div> : null}
          <button type="submit" className="btn btn-primary btn-block" disabled={loading}>
            {loading ? "Iniciando sesión…" : "Iniciar sesión"}
          </button>
        </form>
      </div>
    </div>
  );
}
