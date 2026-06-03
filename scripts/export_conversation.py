"""
Export the Claude Code conversation transcript(s) for this project into a single
readable .txt — so a new account/session can be given the full context.

Reads the local session JSONL (never leaves your machine) and writes
CONVERSATION_CONTEXT.txt at the repo root.

Run:  python scripts/export_conversation.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRANSCRIPT_DIR = Path.home() / ".claude" / "projects" / "E--commission-automation"
OUT = REPO / "CONVERSATION_CONTEXT.txt"
MAX_BLOCK = 8000  # truncate any single huge pasted block

BRIEF = """\
================================================================================
 BIG BATTERY — COMMISSION AUTOMATION  ·  PROJECT HANDOFF / CONTEXT
================================================================================
Purpose: replace the ~2h manual monthly B2B commission process. Reads Zoho data
(already synced to a database), applies Big Battery's commission rules, lets
Accounting review/adjust edge cases, and exports the B2B workbook used for pay.

STACK
  • Backend: FastAPI (backend/app.py), uvicorn. DB layer in src/db/.
  • DB: SQLite locally OR Supabase Postgres in the cloud (DATABASE_URL env).
        Connection auto-adapts placeholders (src/db/connection.py, db_utils.py).
  • Commission engine (single source of truth): src/commission/sqlite_to_workbook.py
        -> build_salespeople_from_sqlite() + workbook_builder_v2.py (Excel output).
  • Returned-qty rule (shared helper): src/commission/returns.py
  • Audit/reconciliation: src/main.py + src/commission/b2b_reconciliation.py
  • Frontend: React + Vite (frontend/src). Tabs: Generate, Adjustments, Audit,
        History, Zoho Books, Reports, Help. API helper: frontend/src/lib/api.js.

KEY RULES IMPLEMENTED
  • Route by CF.Sales Team (B2B vs B2C/Exec) from invoice raw_json.
  • MAP from curated R_LP table (not items.rate) -> discount -> commission tier.
    Non-salaried reps (Brett, Leslie, Carmen, Garrett) earn the higher tier.
  • Current vs prior period by Sales Order order_date.
  • Returned quantity nets out commission (fully returned -> $0; partial prorated).
    Source: Sales Order line quantity_returned in raw_json. Verified: SO-03660/Brett.
  • Manual Adjustments layer (src/db/adjustments.py, table manual_adjustments):
    applied AFTER calc, BEFORE export. Raw Zoho never modified.
  • Workbook: per-salesperson sheets + B2B Summary (Draft/Final banner) +
    Adjustments Audit + Reconciliation (Check A/B = 0) + B2B Payable vs Jennifer
    + Excluded from Payable + Legacy "diagnostic only" sheets.

DEPLOY
  • Frontend -> Netlify (netlify.toml proxies /api to Render; long ops call Render
    directly via VITE_API_BASE_URL because Netlify proxy times out at ~26s).
  • Backend -> Render (render.yaml). DB = Supabase Postgres (DATABASE_URL).
  • Sync runs in the BACKGROUND (POST /api/sync/incremental returns immediately;
    poll GET /api/sync/incremental/status) to avoid 504 gateway timeouts.
  • Auth: Supabase JWT (backend/auth_middleware.py).
  • GitHub: alex-bigbattery/Commission-automation (DB & customer data gitignored).

HOW TO RUN LOCALLY
  • Iniciar Comisiones.bat  (starts backend :8000 + frontend :5173, opens browser)
  • Generate a workbook (CLI): python -m src.commission.build_workbook --year Y --month M
  • Regenerate audit (CLI):    python -m src.main --year Y --month M --data-source sqlite

IMPORTANT OPERATIONAL NOTE
  • Do NOT run Python/DB/Zoho scripts while a Zoho import is running in another
    terminal — it can break the import.

The full chronological conversation follows below.
"""


def block_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    parts: list[str] = []
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                parts.append((b.get("text") or "").strip())
            elif t == "tool_use":
                parts.append(f"[action: {b.get('name', 'tool')}]")
            # tool_result / thinking / images are skipped for readability
    return "\n".join(p for p in parts if p)


def main() -> None:
    files = sorted(TRANSCRIPT_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit(f"No transcript .jsonl found in {TRANSCRIPT_DIR}")

    chunks: list[str] = [BRIEF]
    msg_count = 0
    for jf in files:
        with open(jf, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") not in ("user", "assistant"):
                    continue
                msg = o.get("message") or {}
                role = msg.get("role") or o.get("type")
                txt = block_text(msg.get("content"))
                if not txt:
                    continue  # skips tool-result-only user turns
                if len(txt) > MAX_BLOCK:
                    txt = txt[:MAX_BLOCK] + f"\n…[truncated {len(txt) - MAX_BLOCK} chars]"
                ts = o.get("timestamp", "")
                label = "USER" if role == "user" else "ASSISTANT"
                chunks.append(f"\n{'=' * 70}\n{label}   {ts}\n{'=' * 70}\n{txt}\n")
                msg_count += 1

    OUT.write_text("".join(chunks), encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT}")
    print(f"  Messages: {msg_count}  |  Size: {kb:,.0f} KB")
    print(f"  Source files: {[f.name for f in files]}")


if __name__ == "__main__":
    main()
