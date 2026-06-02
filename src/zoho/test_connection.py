from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.zoho_client import ZohoApiError, ZohoAuthError, ZohoBooksClient, load_zoho_config


def main() -> None:
    print("Zoho Books connection test")
    print(f"Project root: {BASE_DIR}\n")

    try:
        config = load_zoho_config()
    except ZohoAuthError as exc:
        print(f"FAIL: {exc}")
        print("Run: python src/zoho/check_env.py")
        raise SystemExit(1) from exc

    client = ZohoBooksClient(config, timeout_seconds=30)

    try:
        print("Refreshing OAuth access token...")
        token = client.refresh_access_token()
        print(f"OK: Access token received ({len(token)} chars)\n")

        print("Calling Zoho Books API (items, page 1)...")
        payload = client.get("items", params={"page": 1, "per_page": 1})
        items = payload.get("items") or []
        page_context = payload.get("page_context") or {}
        print(f"OK: API response code={payload.get('code')}")
        print(f"  Items returned on page: {len(items)}")
        print(f"  Has more pages: {page_context.get('has_more_page', 'unknown')}")

        if items:
            first = items[0]
            print(f"  Sample item: {first.get('sku', '?')} — {first.get('name', '(no name)')}")

        print("\nSUCCESS: Zoho Books connection is working.")
    except ZohoAuthError as exc:
        print(f"\nFAIL (auth): {exc}")
        raise SystemExit(1) from exc
    except ZohoApiError as exc:
        print(f"\nFAIL (API): {exc}")
        if exc.payload:
            print(f"  Payload: {exc.payload}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
