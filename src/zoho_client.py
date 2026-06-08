from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = BASE_DIR / ".env"

logger = logging.getLogger(__name__)


@dataclass
class ZohoConfig:
    client_id: str
    client_secret: str
    refresh_token: str
    organization_id: str
    accounts_domain: str = "accounts.zoho.com"
    api_domain: str = "www.zohoapis.com"
    accounts_base_url: str = ""
    books_base_url: str = ""

    @property
    def token_url(self) -> str:
        if self.accounts_base_url:
            return f"{self.accounts_base_url.rstrip('/')}/oauth/v2/token"
        return f"https://{self.accounts_domain}/oauth/v2/token"

    @property
    def api_base_url(self) -> str:
        if self.books_base_url:
            return self.books_base_url.rstrip("/")
        return f"https://{self.api_domain}/books/v3"

    def safe_summary(self) -> dict[str, Any]:
        """Auth diagnostic with NO secret values — only presence booleans and
        the (non-secret) domains/URLs. Safe to log in production."""
        return {
            "refresh_token_present": bool(self.refresh_token),
            "client_id_present": bool(self.client_id),
            "client_secret_present": bool(self.client_secret),
            "organization_id_present": bool(self.organization_id),
            "accounts_domain": self.accounts_domain,
            "token_url": self.token_url,
            "api_base_url": self.api_base_url,
        }


class ZohoAuthError(RuntimeError):
    """Raised when OAuth credentials or token refresh fail."""


class ZohoApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _domain_from_url(url: str, default: str) -> str:
    if not url:
        return default
    parsed = urlparse(url)
    return parsed.netloc or default


def load_zoho_config(env_path: Path | None = None) -> ZohoConfig:
    load_dotenv(env_path or DEFAULT_ENV_PATH)

    required = {
        "ZOHO_CLIENT_ID": os.getenv("ZOHO_CLIENT_ID", "").strip(),
        "ZOHO_CLIENT_SECRET": os.getenv("ZOHO_CLIENT_SECRET", "").strip(),
        "ZOHO_REFRESH_TOKEN": os.getenv("ZOHO_REFRESH_TOKEN", "").strip(),
        "ZOHO_ORGANIZATION_ID": os.getenv("ZOHO_ORGANIZATION_ID", "").strip(),
    }

    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ZohoAuthError(
            "Missing required Zoho environment variables: "
            + ", ".join(missing)
            + ". Fill them in .env (see .env.example)."
        )

    accounts_base_url = os.getenv("ZOHO_ACCOUNTS_BASE_URL", "").strip()
    books_base_url = os.getenv("ZOHO_BOOKS_BASE_URL", "").strip()
    accounts_domain = os.getenv("ZOHO_ACCOUNTS_DOMAIN", "").strip() or _domain_from_url(
        accounts_base_url, "accounts.zoho.com"
    )
    api_domain = os.getenv("ZOHO_API_DOMAIN", "").strip() or _domain_from_url(
        books_base_url, "www.zohoapis.com"
    )

    return ZohoConfig(
        client_id=required["ZOHO_CLIENT_ID"],
        client_secret=required["ZOHO_CLIENT_SECRET"],
        refresh_token=required["ZOHO_REFRESH_TOKEN"],
        organization_id=required["ZOHO_ORGANIZATION_ID"],
        accounts_domain=accounts_domain,
        api_domain=api_domain,
        accounts_base_url=accounts_base_url,
        books_base_url=books_base_url,
    )


class ZohoBooksClient:
    """Server-side Zoho Books API client. Credentials come from environment only."""

    def __init__(self, config: ZohoConfig | None = None, *, timeout_seconds: int = 60):
        self.config = config or load_zoho_config()
        self.timeout_seconds = timeout_seconds
        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0
        # Startup/auth diagnostic — never prints secret values, only presence.
        logger.info("Zoho OAuth config (no secrets shown): %s", self.config.safe_summary())

    def refresh_access_token(self) -> str:
        response = requests.post(
            self.config.token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "refresh_token": self.config.refresh_token,
            },
            timeout=self.timeout_seconds,
        )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ZohoAuthError(
                "Zoho authentication failed: token endpoint returned a non-JSON response. "
                f"HTTP status {response.status_code}."
            ) from exc

        if response.status_code >= 400 or "access_token" not in payload:
            error_code = str(payload.get("error", "unknown_error"))
            message = payload.get("error_description") or payload.get("message") or response.text
            # Zoho returns "invalid_code" / "invalid_grant" when the REFRESH TOKEN
            # itself is bad — revoked, expired, for a different client, or a
            # temporary grant code was pasted in by mistake. The code/flow is fine;
            # the stored credential must be regenerated once.
            if error_code.lower() in ("invalid_code", "invalid_grant"):
                raise ZohoAuthError(
                    "The Zoho refresh token is invalid or revoked. Generate a new "
                    "refresh token once and update ZOHO_REFRESH_TOKEN in Render. "
                    "(A refresh token is long-lived — do NOT use a temporary grant "
                    f"code here.) Zoho error: {error_code}."
                )
            raise ZohoAuthError(
                "Zoho authentication failed while refreshing access token. "
                f"Error: {error_code}. Details: {message}"
            )

        expires_in = int(payload.get("expires_in", 3600))
        self._access_token = str(payload["access_token"])
        self._access_token_expires_at = time.time() + max(expires_in - 60, 30)
        return self._access_token

    def get_access_token(self) -> str:
        if self._access_token and time.time() < self._access_token_expires_at:
            return self._access_token
        return self.refresh_access_token()

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Zoho-oauthtoken {self.get_access_token()}",
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retry_on_unauthorized: bool = True,
    ) -> dict[str, Any]:
        url = f"{self.config.api_base_url}/{path.lstrip('/')}"
        query = {"organization_id": self.config.organization_id}
        if params:
            query.update(params)

        response = requests.request(
            method=method.upper(),
            url=url,
            headers=self._auth_headers(),
            params=query,
            json=json_body,
            timeout=self.timeout_seconds,
        )

        if response.status_code == 401 and retry_on_unauthorized:
            self.refresh_access_token()
            return self.request(
                method,
                path,
                params=params,
                json_body=json_body,
                retry_on_unauthorized=False,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ZohoApiError(
                f"Zoho API returned non-JSON response for {path} (HTTP {response.status_code}).",
                status_code=response.status_code,
            ) from exc

        code = payload.get("code")
        if response.status_code >= 400 or (code is not None and int(code) != 0):
            message = payload.get("message") or response.text
            raise ZohoApiError(
                f"Zoho API error for {path}: {message}",
                status_code=response.status_code,
                payload=payload,
            )

        return payload

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", path, params=params)

    def paginate(
        self,
        path: str,
        *,
        result_key: str,
        params: dict[str, Any] | None = None,
        per_page: int = 200,
    ) -> Iterator[dict[str, Any]]:
        page = 1
        base_params = dict(params or {})
        base_params["per_page"] = per_page

        while True:
            page_params = dict(base_params)
            page_params["page"] = page
            payload = self.get(path, params=page_params)
            rows = payload.get(result_key) or []
            if not isinstance(rows, list):
                raise ZohoApiError(
                    f"Unexpected Zoho pagination payload for {path}: missing list '{result_key}'.",
                    payload=payload,
                )

            for row in rows:
                yield row

            page_context = payload.get("page_context") or {}
            if not page_context.get("has_more_page"):
                break
            page += 1

    def list_all(self, path: str, result_key: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return list(self.paginate(path, result_key=result_key, params=params))

    def list_sales_orders(self, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self.list_all("salesorders", "salesorders", params=params)

    def list_invoices(self, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self.list_all("invoices", "invoices", params=params)

    def list_items(self, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self.list_all("items", "items", params=params)

    def list_customer_payments(self, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self.list_all("customerpayments", "customerpayments", params=params)

    def list_shipment_orders(self, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self.list_all("shipmentorders", "shipmentorders", params=params)

    def list_packages(self, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self.list_all("packages", "packages", params=params)

    def get_sales_order(self, salesorder_id: str) -> dict[str, Any]:
        payload = self.get(f"salesorders/{salesorder_id}")
        return payload.get("salesorder") or {}

    def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        payload = self.get(f"invoices/{invoice_id}")
        return payload.get("invoice") or {}

    def get_customer_payment(self, payment_id: str) -> dict[str, Any]:
        payload = self.get(f"customerpayments/{payment_id}")
        return (
            payload.get("payment")
            or payload.get("customerpayment")
            or {}
        )
