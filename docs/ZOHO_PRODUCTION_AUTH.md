# Zoho OAuth — Production Setup (no monthly token changes)

The app authenticates to Zoho Books with a **long-lived refresh token** and mints
**short-lived access tokens** automatically. Once `ZOHO_REFRESH_TOKEN` is set in
Render, **you should not have to touch Zoho tokens again** unless the refresh
token is revoked.

## The two kinds of token (don't confuse them)

| | Grant code | Access token | **Refresh token** |
|---|---|---|---|
| Lifetime | ~1–10 minutes | ~1 hour | **Until revoked (long-term)** |
| Purpose | One-time, exchanged for tokens | Sent on each API call | **Stored credential; mints access tokens** |
| Where it lives | Used once, then discarded | In memory only (never stored) | **`ZOHO_REFRESH_TOKEN` env var (Render)** |

- The **grant code** is temporary. It is only used **once** to obtain a refresh
  token. **Never** put a grant code in `ZOHO_REFRESH_TOKEN`.
- The **access token** is generated on demand by `refresh_access_token()` and kept
  only in memory — it is never the stored credential.
- The **refresh token** is the durable credential. Store it in **Render** and leave
  it. The app uses it (with the client id/secret) to get fresh access tokens.

## How the code uses it

`src/zoho_client.py`:
- `refresh_access_token()` POSTs to the accounts token URL with
  `grant_type=refresh_token`, `client_id`, `client_secret`, `refresh_token`.
- `get_access_token()` caches the access token in memory and refreshes it ~60s
  before expiry. The refresh token is read from the environment every run.
- On startup, the client logs a **safe diagnostic** (no secret values):
  `refresh_token_present`, `client_id_present`, `client_secret_present`,
  `organization_id_present`, `accounts_domain`, `token_url`, `api_base_url`.

## Required Render environment variables

```
ZOHO_CLIENT_ID
ZOHO_CLIENT_SECRET
ZOHO_REFRESH_TOKEN          <- the long-lived credential
ZOHO_ORGANIZATION_ID
ZOHO_ACCOUNTS_DOMAIN        (e.g. accounts.zoho.com  — or .eu/.in/.com.au for your DC)
ZOHO_API_DOMAIN            (e.g. www.zohoapis.com)
# optional explicit overrides:
ZOHO_ACCOUNTS_BASE_URL     (e.g. https://accounts.zoho.com)
ZOHO_BOOKS_BASE_URL        (e.g. https://www.zohoapis.com/books/v3)
```

> The accounts domain **must match the data center** your Zoho org lives in
> (`.com`, `.eu`, `.in`, `.com.au`). A mismatch causes auth failures.

## Required scopes

The app only **reads** Books data (sales orders, invoices, items, customer
payments, shipments/packages). Generate the refresh token with at least:

```
ZohoBooks.salesorders.READ
ZohoBooks.invoices.READ
ZohoBooks.items.READ
ZohoBooks.customerpayments.READ
ZohoBooks.settings.READ
```

Simplest equivalent (broad): `ZohoBooks.fullaccess.all`.

## Generating a new refresh token (ONE TIME — only if revoked)

Do this **only** if you see the error below (invalid/revoked token). Otherwise leave
`ZOHO_REFRESH_TOKEN` as-is.

1. In the [Zoho API Console](https://api-console.zoho.com/) open your **Self Client**
   (or create a Self Client for server-to-server).
2. Generate a **grant code** with the scopes above (and your org's data center).
   The grant code is valid only a few minutes.
3. Exchange the grant code for tokens (once), e.g.:
   ```bash
   curl -s -X POST "https://accounts.zoho.com/oauth/v2/token" \
     -d "grant_type=authorization_code" \
     -d "client_id=$ZOHO_CLIENT_ID" \
     -d "client_secret=$ZOHO_CLIENT_SECRET" \
     -d "code=THE_GRANT_CODE"
   ```
   (Use the accounts domain for your data center.)
4. From the JSON response, copy the **`refresh_token`** value (NOT `access_token`,
   NOT the grant code).
5. In **Render → commission-backend → Environment**, set `ZOHO_REFRESH_TOKEN` to
   that refresh token. Save → Render redeploys. Done — no monthly changes needed.

## The error you may see

```
The Zoho refresh token is invalid or revoked. Generate a new refresh token once
and update ZOHO_REFRESH_TOKEN in Render. Zoho error: invalid_code.
```

This means the stored refresh token is bad — **revoked, expired, generated for a
different client, or a temporary grant code was pasted in by mistake.** The code is
fine; follow "Generating a new refresh token" above, once.

### Why a refresh token goes invalid
- Someone re-generated tokens in the Zoho console (Zoho caps refresh tokens per
  client; old ones get revoked).
- The token was created for a different client id/secret or data center.
- A **grant code** (temporary) was stored instead of the refresh token.
- The token was explicitly revoked.

## Do NOT
- Do not store the access token as the main credential (it expires hourly).
- Do not store a grant code in `ZOHO_REFRESH_TOKEN`.
- Do not regenerate the refresh token on a schedule — only when revoked/invalid.
- Do not print secret values in logs (the diagnostic only prints presence booleans).
