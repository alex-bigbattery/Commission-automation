from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv

from src.zoho_client import DEFAULT_ENV_PATH, ZohoAuthError, load_zoho_config

REQUIRED_VARS = (
    "ZOHO_CLIENT_ID",
    "ZOHO_CLIENT_SECRET",
    "ZOHO_REFRESH_TOKEN",
    "ZOHO_ORGANIZATION_ID",
)

OPTIONAL_VARS = (
    "ZOHO_ACCOUNTS_DOMAIN",
    "ZOHO_API_DOMAIN",
    "ZOHO_ACCOUNTS_BASE_URL",
    "ZOHO_BOOKS_BASE_URL",
)


def mask(value: str) -> str:
    value = value.strip()
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def main() -> None:
    env_path = DEFAULT_ENV_PATH
    print(f"Project root: {BASE_DIR}")
    print(f"Env file: {env_path}")
    print(f"Env file exists: {env_path.exists()}\n")

    load_dotenv(env_path)

    print("Required variables:")
    missing_required: list[str] = []
    for key in REQUIRED_VARS:
        raw = os.getenv(key, "")
        present = bool(str(raw).strip())
        status = "OK" if present else "MISSING"
        print(f"  {key}: {status} ({mask(raw) if present else 'not set'})")
        if not present:
            missing_required.append(key)

    print("\nOptional variables:")
    for key in OPTIONAL_VARS:
        raw = os.getenv(key, "")
        if str(raw).strip():
            print(f"  {key}: set ({mask(raw)})")
        else:
            print(f"  {key}: (not set, using default)")

    if missing_required:
        print("\nFAIL: Missing required Zoho environment variables.")
        print("Copy .env.example to .env and fill in Zoho Books OAuth credentials.")
        raise SystemExit(1)

    try:
        config = load_zoho_config(env_path)
    except ZohoAuthError as exc:
        print(f"\nFAIL: {exc}")
        raise SystemExit(1) from exc

    print("\nResolved configuration:")
    print(f"  Organization ID: {mask(config.organization_id)}")
    print(f"  Accounts domain: {config.accounts_domain}")
    print(f"  API domain: {config.api_domain}")
    print(f"  Token URL: {config.token_url}")
    print(f"  API base URL: {config.api_base_url}")
    print("\nOK: Zoho environment variables are present and parseable.")


if __name__ == "__main__":
    main()
