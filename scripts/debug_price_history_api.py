"""Debug Price History API + DB (read-only)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.commission.settings_read import (  # noqa: E402
    get_price_history_for_sku,
    list_price_history_catalog,
    search_price_history,
)
from src.db.connection import DATABASE_URL, get_connection, using_postgres  # noqa: E402


def probe_http(base: str, path: str, token: str | None = None) -> dict:
    url = f"{base.rstrip('/')}{path}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"url": url, "status": resp.status, "body": json.loads(body) if body else {}}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"raw": body[:500]}
        return {"url": url, "status": e.code, "body": parsed}
    except Exception as e:
        return {"url": url, "status": "error", "body": {"error": str(e)}}


def main() -> None:
    print("=== 1. Database (backend .env DATABASE_URL) ===")
    print(f"using_postgres: {using_postgres()}")
    print(f"DATABASE_URL set: {bool(DATABASE_URL.strip())}")
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) AS c FROM price_history").fetchone()["c"]
    distinct = conn.execute("SELECT COUNT(DISTINCT sku) AS c FROM price_history").fetchone()["c"]
    print(f"price_history rows: {total}")
    print(f"distinct SKUs: {distinct}")

    pws = conn.execute(
        "SELECT sku, map_price, effective_from, effective_to, source, snapshot_month "
        "FROM price_history WHERE UPPER(sku) = 'PWS015' ORDER BY effective_from"
    ).fetchall()
    print(f"PWS015 rows: {len(pws)}")
    for r in pws:
        print(f"  {dict(r)}")

    print("\n=== 2. Python read layer (price_history table, not R_LP) ===")
    search = search_price_history("PWS015", limit=10)
    print(f"search_price_history('PWS015'): count={search['count']}")
    catalog = list_price_history_catalog(limit=5, offset=0)
    print(f"list_price_history_catalog(): total={catalog['total']} page={catalog['count']}")
    detail = get_price_history_for_sku("PWS015", template_path=ROOT / "data/templates/master_template_clean.xlsx")
    print(f"get_price_history_for_sku('PWS015'): row_count={detail['row_count']} current={detail['current_price']}")
    print(f"  sources: {[r['source'] for r in detail['rows']]}")

    print("\n=== 3. Search min-length behavior ===")
    for q in ("", "P", "PW", "PWS", "PWS015"):
        s = search_price_history(q, limit=5)
        print(f"  q={q!r:8} -> count={s['count']}")

    print("\n=== 4. HTTP endpoints ===")
    bases = [
        ("local", "http://127.0.0.1:8000"),
        ("render", "https://commission-backend-0d4h.onrender.com"),
    ]
    token = os.environ.get("DEBUG_SUPABASE_JWT", "").strip() or None
    if not token:
        print("(Set DEBUG_SUPABASE_JWT in .env to probe authenticated HTTP — skipping auth headers)")

    paths = [
        "/api/health",
        "/api/settings/price-history/search?q=PWS015",
        "/api/settings/price-history/catalog?limit=5",
        "/api/settings/price-history?sku=PWS015",
    ]
    for label, base in bases:
        print(f"\n--- {label} {base} ---")
        for path in paths:
            r = probe_http(base, path, token=token)
            status = r["status"]
            body = r["body"]
            summary = body
            if isinstance(body, dict):
                if "total" in body:
                    summary = {k: body[k] for k in ("total", "count", "row_count", "results") if k in body}
                elif "detail" in body:
                    summary = {"detail": body["detail"]}
            print(f"  {status} {path} -> {summary}")


if __name__ == "__main__":
    main()
