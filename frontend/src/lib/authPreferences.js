const REMEMBER_KEY = "commission-auth-remember";
const EMAIL_KEY = "commission-auth-email";

/** Default true — users expect multi-day sessions when opting in at login. */
export function getRememberMe() {
  const stored = localStorage.getItem(REMEMBER_KEY);
  if (stored === null) return true;
  return stored === "true";
}

export function setRememberMe(value) {
  localStorage.setItem(REMEMBER_KEY, value ? "true" : "false");
  if (!value) {
    localStorage.removeItem(EMAIL_KEY);
  }
}

export function getRememberedEmail() {
  if (!getRememberMe()) return "";
  return localStorage.getItem(EMAIL_KEY) || "";
}

export function setRememberedEmail(email) {
  if (getRememberMe() && email) {
    localStorage.setItem(EMAIL_KEY, email.trim());
  } else {
    localStorage.removeItem(EMAIL_KEY);
  }
}
