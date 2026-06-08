from __future__ import annotations

import base64
import calendar
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from .auth_middleware import SupabaseAuthMiddleware

from .commission_reader import (
    COMMISSIONS_DIR,
    build_commissions_tree,
    list_workbook_sheets,
    read_sheet_grid,
    resolve_workbook,
)
from src.calculation_engine import CalculationOptions, run_calculation_engine
from src.commission.sqlite_data_source import (
    database_status,
    has_period_data,
    line_type_counts,
    load_commission_input,
    period_counts,
)
from src.commission.roster import roster_rep_sheet_keys
from src.commission.price_history_matrix import (
    export_price_history_file,
    get_price_history_detail_list,
    get_price_history_matrix,
)
from src.commission.settings_read import (
    get_commission_settings,
    get_price_history_for_sku,
    get_roster_settings,
    list_price_history_catalog,
    query_price_history,
    search_price_history,
)
from src.commission.sqlite_to_workbook import (
    ALL_SHEETS_ORDERED,
    build_salespeople_from_sqlite,
    generate_commission_workbook,
    load_map_from_template,
    load_tiers_from_template,
)
from src.db.adjustments import (
    delete_adjustment,
    get_adjustment_map,
    list_adjustments,
    upsert_adjustment,
)
from src.db.connection import DB_PATH, init_database, using_postgres
from src.db.incremental_sync import incremental_sync_plan, run_incremental_sync
from src.db.repository import DatabaseRepository
from src.zoho_client import ZohoApiError, ZohoAuthError, ZohoBooksClient, load_zoho_config

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
DB_DIR = BASE_DIR / "data" / "db"
EXPORT_DIR = BASE_DIR / "data" / "zoho"
OUTPUT_DIR = BASE_DIR / "data" / "output"
INPUT_DIR = BASE_DIR / "data" / "input"
RAW_DIR = BASE_DIR / "data" / "zoho_raw"
TEMPLATES_DIR = BASE_DIR / "data" / "templates"
MASTER_TEMPLATE = TEMPLATES_DIR / "master_template_clean.xlsx"
SYNC_SCRIPT = SRC_DIR / "db" / "sync_zoho_to_sqlite.py"

app = FastAPI(title="Commission Automation API", version="1.1.0")

_default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
_extra = os.environ.get("ALLOWED_ORIGINS", "")
if _extra.strip():
    _default_origins.extend(o.strip() for o in _extra.split(",") if o.strip())

# IMPORTANT — middleware order: the LAST add_middleware is the OUTERMOST.
# Auth is added first (inner) and CORS last (outer) so that EVERY response —
# including 401s/errors raised by the auth middleware — passes back through
# CORSMiddleware and carries the Access-Control-Allow-Origin header. Otherwise
# the browser reports an auth 401 as an opaque "blocked by CORS policy" error.
app.add_middleware(SupabaseAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class FetchRequest(BaseModel):
    date: str | None = Field(default=None, description="YYYY-MM-DD")
    date_start: str | None = None
    date_end: str | None = None
    year: int | None = None
    month: int | None = None


class UploadedFilePayload(BaseModel):
    kind: str
    filename: str
    content_base64: str


class UploadBatchRequest(BaseModel):
    year: int
    month: int
    files: list[UploadedFilePayload]


class RunAuditRequest(BaseModel):
    year: int
    month: int
    historical_replay: bool = False
    disable_summary_normalization: bool = False


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SyncFullRequest(BaseModel):
    date_start: str = "2021-01-01"
    date_end: str = "today"
    skip_details: bool = False

    @field_validator("date_start", "date_end")
    @classmethod
    def _validate_date(cls, v: str) -> str:
        if v != "today" and not _DATE_RE.match(v):
            raise ValueError("Date must be YYYY-MM-DD or 'today'.")
        return v


class GenerateCommissionRequest(BaseModel):
    year: int
    month: int


class AdjustmentPayload(BaseModel):
    period_year: int
    period_month: int
    line_uid: str | None = None
    sales_order_number: str | None = None
    invoice_number: str | None = None
    sku: str | None = None
    original_salesperson: str | None = None
    adjusted_salesperson: str | None = None
    original_commissionable: float | None = None
    adjusted_commissionable: float | None = None
    original_map: float | None = None
    adjusted_map: float | None = None
    original_discount: float | None = None
    adjusted_discount: float | None = None
    exclude_flag: bool = False
    classification: str | None = None
    reason: str | None = None
    reviewer: str | None = None
    approval_status: str | None = "pending"


FILE_KIND_TO_TEMPLATE = {
    "b2b": "{year}-{month}_Commission B2B.xlsx",
    "sales_orders": "Sales_Orders {month_name} {year}.xlsx",
    "invoices": "Invoices {month_name} {year}.xlsx",
    "shipments": "Shipments {month_name} {year}.xlsx",
    "items": "Items {month_name} {year}.xlsx",
}


def _zoho_workbooks() -> list[Path]:
    return sorted(EXPORT_DIR.glob("zoho_export_*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)


def _generated_reports() -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    return sorted(OUTPUT_DIR.glob("commission_audit_*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)


def _period_file_name(kind: str, year: int, month: int) -> str:
    month_name = calendar.month_name[month]
    template = FILE_KIND_TO_TEMPLATE.get(kind)
    if not template:
        raise HTTPException(status_code=400, detail=f"Unsupported file kind '{kind}'.")
    return template.format(year=year, month=month, month_name=month_name)


def _period_input_paths(year: int, month: int) -> dict[str, Path]:
    return {
        kind: INPUT_DIR / _period_file_name(kind, year, month)
        for kind in FILE_KIND_TO_TEMPLATE
    }


def _period_source_file(year: int, month: int) -> Path:
    return INPUT_DIR / f"_source_{year}_{month:02d}.json"


def _write_period_source(year: int, month: int, source: str, extra: dict | None = None) -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "year": year,
        "month": month,
        "source": source,
    }
    if extra:
        payload.update(extra)
    _period_source_file(year, month).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_period_source(year: int, month: int) -> dict:
    path = _period_source_file(year, month)
    if not path.exists():
        return {"source": "Unknown"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"source": "Unknown"}


def _count_excel_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        frame = pd.read_excel(path, sheet_name=0)
        return len(frame.index)
    except Exception:
        return 0  # never let a corrupt/locked file 500 the status endpoint


_NOTE_PREFIXES = ("LEGACY DIAGNOSTIC", "This sheet is sourced", "Our calculated commission")


def _read_report_df(path: Path, sheet_name: str) -> pd.DataFrame:
    """Read a report sheet, skipping a leading note/banner row (legacy or source-of-truth) if present."""
    try:
        probe = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=1)
    except Exception:
        return pd.DataFrame()
    skip = False
    if not probe.empty and probe.shape[1]:
        first = str(probe.iloc[0, 0] or "")
        skip = first.startswith(_NOTE_PREFIXES)
    return pd.read_excel(path, sheet_name=sheet_name, header=1 if skip else 0).fillna("")


def _sheet_metrics(path: Path, sheet_name: str) -> tuple[int, list[dict]]:
    if not path.exists():
        return 0, []
    workbook = pd.ExcelFile(path)
    if sheet_name not in workbook.sheet_names:
        return 0, []
    frame = _read_report_df(path, sheet_name)
    rows = frame.to_dict(orient="records")
    return len(rows), rows


def _extract_zoho_to_input(workbook_path: Path, year: int, month: int) -> dict:
    paths = _period_input_paths(year, month)
    workbook = pd.ExcelFile(workbook_path)
    sheet_map = {
        "sales_orders": "Sales Orders",
        "invoices": "Invoices",
        "shipments": "Shipments",
        "items": "Items",
    }
    counts: dict[str, int] = {}
    for kind, sheet_name in sheet_map.items():
        target = paths[kind]
        if sheet_name in workbook.sheet_names:
            frame = pd.read_excel(workbook_path, sheet_name=sheet_name)
            frame.to_excel(target, index=False)
            counts[kind] = len(frame.index)
        else:
            counts[kind] = 0
    return counts


def _resolve_historical_b2b(year: int, month: int) -> Path | None:
    tree = build_commissions_tree()
    year_entry = next((y for y in tree.get("years", []) if str(y.get("year")) == str(year)), None)
    if not year_entry:
        return None
    month_prefix = f"{year}-{month}"
    exact = next((m for m in year_entry.get("months", []) if str(m.get("label", "")).startswith(month_prefix)), None)
    if exact:
        return Path(exact["workbook_path"])
    fallback = year_entry.get("months", [])[-1] if year_entry.get("months") else None
    if fallback:
        return Path(fallback["workbook_path"])
    return None


def _ensure_b2b_input(year: int, month: int) -> Path:
    paths = _period_input_paths(year, month)
    if paths["b2b"].exists():
        return paths["b2b"]

    historical = _resolve_historical_b2b(year, month)
    if historical and historical.exists():
        paths["b2b"].write_bytes(historical.read_bytes())
        return paths["b2b"]

    raise HTTPException(
        status_code=400,
        detail="No B2B workbook found for selected period. Upload Historical Commission Workbook in Optional Historical Validation.",
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/commissions/tree")
def commissions_tree() -> dict:
    return build_commissions_tree()


@app.get("/api/commissions/workbooks/{workbook_id}/meta")
def commission_workbook_meta(workbook_id: str) -> dict:
    try:
        path = resolve_workbook(workbook_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    meta = list_workbook_sheets(path)
    return {
        "workbook_id": workbook_id,
        "path": str(path),
        **meta,
    }


@app.get("/api/commissions/workbooks/{workbook_id}/sheets/{sheet_name}")
def commission_sheet_grid(workbook_id: str, sheet_name: str) -> dict:
    try:
        path = resolve_workbook(workbook_id)
        grid = read_sheet_grid(path, sheet_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"workbook_id": workbook_id, **grid}


@app.get("/api/commissions/workbooks/{workbook_id}/download")
def commission_workbook_download(workbook_id: str):
    try:
        path = resolve_workbook(workbook_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path=path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _json_ready(value):
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


def _frame_to_grid(frame: pd.DataFrame, limit: int = 500) -> dict:
    if frame is None or frame.empty:
        return {"columns": [], "rows": [], "total_rows": 0}
    sliced = frame.head(limit).copy()
    columns = [str(c) for c in sliced.columns.tolist()]
    rows = [{col: _json_ready(row[col]) for col in columns} for _, row in sliced.iterrows()]
    return {"columns": columns, "rows": rows, "total_rows": int(len(frame))}


def _validate_period(year: int, month: int) -> None:
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be 1-12.")
    if year < 2015 or year > 2100:
        raise HTTPException(status_code=400, detail="Year must be between 2015 and 2100.")


@app.get("/api/commissions/sqlite/summary")
def commissions_sqlite_summary(year: int, month: int) -> dict:
    _validate_period(year, month)
    data = load_commission_input(year, month)
    line_counts = line_type_counts(data)
    table_counts = {
        "sales_orders": len(data.sales_orders),
        "sales_order_lines": len(data.sales_order_lines),
        "invoices": len(data.invoices),
        "invoice_lines": len(data.invoice_lines),
        "items": len(data.items),
        "customer_payments": len(data.customer_payments),
        "customer_payment_invoices": len(data.customer_payment_invoices),
    }
    return {
        "year": year,
        "month": month,
        "table_counts": table_counts,
        "line_type_counts": line_counts,
    }


@app.get("/api/commissions/sqlite/table")
def commissions_sqlite_table(year: int, month: int, table: str, limit: int = Query(default=500, ge=1, le=5000)) -> dict:
    _validate_period(year, month)
    data = load_commission_input(year, month)
    table_map = {
        "sales_orders": data.sales_orders,
        "sales_order_lines": data.sales_order_lines,
        "invoices": data.invoices,
        "invoice_lines": data.invoice_lines,
        "items": data.items,
        "customer_payments": data.customer_payments,
        "customer_payment_invoices": data.customer_payment_invoices,
    }
    if table not in table_map:
        raise HTTPException(status_code=400, detail=f"Unsupported table '{table}'.")
    grid = _frame_to_grid(table_map[table], limit=limit)
    return {
        "year": year,
        "month": month,
        "table": table,
        **grid,
    }


# --- Jennifer-style commission workbook generation ---------------------------


def _commission_output_path(year: int, month: int) -> Path:
    return OUTPUT_DIR / f"{year}-{month}_Commission B2B.xlsx"


def _commission_meta_path(year: int, month: int) -> Path:
    return OUTPUT_DIR / f"{year}-{month}_Commission B2B.meta.json"


def _read_commission_meta(year: int, month: int) -> dict | None:
    path = _commission_meta_path(year, month)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@app.post("/api/commission/generate")
def commission_generate(body: GenerateCommissionRequest) -> dict:
    _validate_period(body.year, body.month)
    if not MASTER_TEMPLATE.exists():
        raise HTTPException(status_code=500, detail=f"Master template not found at {MASTER_TEMPLATE}.")
    if not has_period_data(body.year, body.month):
        raise HTTPException(
            status_code=400,
            detail="No hay datos en SQLite para este mes. Ejecuta 'Sincronizar Zoho' primero.",
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = _commission_output_path(body.year, body.month)
    try:
        result = generate_commission_workbook(
            body.year,
            body.month,
            template_path=MASTER_TEMPLATE,
            output_path=output,
        )
    except Exception as exc:  # noqa: BLE001 - surface any generation error to the UI
        raise HTTPException(status_code=500, detail=f"Error generando el libro: {exc}") from exc

    exceptions = [e.as_dict() for e in result.exceptions]
    meta = {
        "report_id": output.name,
        "year": body.year,
        "month": body.month,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kpis": result.kpis,
        "totals_by_sheet": result.totals_by_sheet,
        "exceptions": exceptions,
    }
    _commission_meta_path(body.year, body.month).write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8"
    )
    _write_period_source(body.year, body.month, "Zoho SQLite", {"commission_workbook": output.name})

    return {
        "status": "ok",
        "report_id": output.name,
        "kpis": result.kpis,
        "totals_by_sheet": result.totals_by_sheet,
        "exception_count": len(exceptions),
        "exceptions_preview": exceptions[:50],
    }


@app.get("/api/commission/summary")
def commission_summary(year: int = Query(...), month: int = Query(...)) -> dict:
    meta = _read_commission_meta(year, month)
    output = _commission_output_path(year, month)
    if not meta or not output.exists():
        return {"generated": False, "year": year, "month": month}
    return {
        "generated": True,
        "report_id": meta.get("report_id", output.name),
        "year": year,
        "month": month,
        "generated_at": meta.get("generated_at"),
        "kpis": meta.get("kpis", {}),
        "totals_by_sheet": meta.get("totals_by_sheet", {}),
        "exception_count": len(meta.get("exceptions", [])),
    }


@app.get("/api/commission/exceptions")
def commission_exceptions(year: int = Query(...), month: int = Query(...)) -> dict:
    meta = _read_commission_meta(year, month)
    rows = meta.get("exceptions", []) if meta else []
    columns = ["Salesperson", "Invoice", "Sales Order", "SKU", "Amount", "Reason"]
    return {"year": year, "month": month, "columns": columns, "rows": rows, "row_count": len(rows)}


# --- Accounting Adjustments layer -------------------------------------------


@app.get("/api/adjustments/roster")
def adjustments_roster() -> dict:
    """Valid salesperson sheet keys for the reassignment dropdown."""
    return {"salespeople": roster_rep_sheet_keys()}


@app.get("/api/adjustments/lines")
def adjustments_lines(
    year: int = Query(...),
    month: int = Query(...),
    salesperson: str | None = None,
    flag: str | None = None,
    sales_order: str | None = None,
    invoice: str | None = None,
    sku: str | None = None,
    sales_team: str | None = None,
) -> dict:
    """Per-line review grid: system / adjustment / final, with any stored adjustment merged in."""
    _validate_period(year, month)
    if not has_period_data(year, month):
        raise HTTPException(
            status_code=400,
            detail="No hay datos en SQLite para este mes. Sincroniza Zoho primero.",
        )
    tiers = load_tiers_from_template(MASTER_TEMPLATE)
    rlp = load_map_from_template(MASTER_TEMPLATE)
    result = build_salespeople_from_sqlite(year, month, tiers=tiers, rlp_map=rlp, apply_adjustments=True)
    adj_map = get_adjustment_map(year, month)

    def matches(row: dict) -> bool:
        checks = [
            (salesperson, row.get("salesperson")),
            (flag, row.get("flags")),
            (sales_order, row.get("sales_order")),
            (invoice, row.get("invoice")),
            (sku, row.get("sku")),
            (sales_team, row.get("sales_team")),
        ]
        for needle, hay in checks:
            if needle and needle.strip().lower() not in str(hay or "").lower():
                return False
        return True

    # Virtual annotation: tag any line whose sales_order's revenue total exceeds
    # the $5,000 threshold (the same SO-grouping the engine already uses for
    # FREE_SHIPPING_THRESHOLD). Read-only / API-time only; NEVER written to DB,
    # NEVER affects commission totals, NEVER mutates the row's `flags` field.
    # The engine does not read this attribute.
    OVER_5000_THRESHOLD = 5000.0
    so_revenue_total: dict[str, float] = {}
    for r in result.audit_rows:
        so_key = r.get("sales_order")
        if so_key:
            try:
                so_revenue_total[so_key] = so_revenue_total.get(so_key, 0.0) + float(r.get("revenue") or 0.0)
            except (TypeError, ValueError):
                pass

    rows = []
    for row in result.audit_rows:
        if not matches(row):
            continue
        adj = adj_map.get(row["line_uid"])
        merged = dict(row)
        merged["adjustment_record"] = dict(adj) if adj else None
        so_key = row.get("sales_order")
        merged["over_5000_review"] = bool(
            so_key and so_revenue_total.get(so_key, 0.0) > OVER_5000_THRESHOLD
        )
        rows.append(merged)

    return {
        "year": year,
        "month": month,
        "row_count": len(rows),
        "rows": rows,
        "kpis": result.kpis,
        "totals_by_sheet": result.totals_by_sheet,
        "roster": roster_rep_sheet_keys(),
    }


@app.get("/api/adjustments")
def adjustments_list(
    year: int = Query(...),
    month: int = Query(...),
    sales_order: str | None = None,
    invoice: str | None = None,
    sku: str | None = None,
    approval_status: str | None = None,
) -> dict:
    items = list_adjustments(
        year,
        month,
        sales_order_number=sales_order,
        invoice_number=invoice,
        sku=sku,
        approval_status=approval_status,
    )
    return {"year": year, "month": month, "adjustments": items, "count": len(items)}


@app.post("/api/adjustments")
def adjustments_upsert(body: AdjustmentPayload) -> dict:
    _validate_period(body.period_year, body.period_month)
    if not (body.line_uid or body.invoice_number or body.sales_order_number):
        raise HTTPException(status_code=400, detail="Provide line_uid or invoice/sales order to identify the line.")
    record = upsert_adjustment(body.model_dump())
    return {"status": "ok", "adjustment": record}


@app.delete("/api/adjustments/{adjustment_id}")
def adjustments_delete(adjustment_id: int) -> dict:
    if not delete_adjustment(adjustment_id):
        raise HTTPException(status_code=404, detail="Adjustment not found.")
    return {"status": "ok", "deleted": adjustment_id}


@app.post("/api/uploads")
def upload_period_files(body: UploadBatchRequest) -> dict:
    _validate_period(body.year, body.month)

    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    expected_paths = _period_input_paths(body.year, body.month)
    saved: list[dict] = []

    MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB per file
    for payload in body.files:
        if payload.kind not in expected_paths:
            raise HTTPException(status_code=400, detail=f"Unsupported file kind '{payload.kind}'.")
        target = expected_paths[payload.kind]
        try:
            data = base64.b64decode(payload.content_base64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base64 for '{payload.kind}'.") from exc
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"File '{payload.kind}' exceeds 50 MB limit.")
        target.write_bytes(data)
        saved.append(
            {
                "kind": payload.kind,
                "saved_as": target.name,
                "size_bytes": target.stat().st_size,
            }
        )

    _write_period_source(body.year, body.month, "Uploaded Historical Excel")
    return {"status": "ok", "saved": saved}


@app.get("/api/input/status")
def input_status(year: int = Query(...), month: int = Query(...)) -> dict:
    _validate_period(year, month)
    expected_paths = _period_input_paths(year, month)
    files = {
        kind: {
            "path": str(path),
            "exists": path.exists(),
            "rows": _count_excel_rows(path) if path.exists() else 0,
        }
        for kind, path in expected_paths.items()
    }
    source = _read_period_source(year, month)
    sqlite_period = {"ready": False, "counts": {}}
    try:
        counts = period_counts(year, month)  # single set of cheap COUNT(*) queries
        sqlite_period = {
            "ready": counts.get("sales_orders", 0) > 0 and counts.get("invoices", 0) > 0,
            "counts": counts,
        }
    except Exception:
        sqlite_period = {"ready": False, "counts": {}}
    if sqlite_period.get("ready"):
        source = {"source": "Zoho SQLite"}
    elif source.get("source") == "Unknown":
        has_core = (
            files["sales_orders"]["exists"]
            and files["invoices"]["exists"]
            and files["shipments"]["exists"]
        )
        if has_core:
            source = {"source": "Uploaded Historical Excel"}
    return {
        "year": year,
        "month": month,
        "files": files,
        "source": source,
        "sqlite_period": sqlite_period,
    }


@app.post("/api/audit/run")
def run_audit(body: RunAuditRequest) -> dict:
    if not has_period_data(body.year, body.month):
        raise HTTPException(
            status_code=400,
            detail="No commission period data in SQLite for this month. Run Sync Latest Zoho Data or Initial Historical Sync first.",
        )
    _write_period_source(
        body.year,
        body.month,
        "Zoho SQLite",
        {"data_source": "sqlite", "counts": period_counts(body.year, body.month)},
    )
    _ensure_b2b_input(body.year, body.month)

    options = CalculationOptions(
        year=body.year,
        month=body.month,
        historical_replay=body.historical_replay,
        disable_summary_normalization=body.disable_summary_normalization,
    )
    result = run_calculation_engine(options)
    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "Commission audit failed."
        raise HTTPException(status_code=502, detail=detail)

    latest = _generated_reports()[0] if _generated_reports() else None
    if latest is None:
        raise HTTPException(status_code=500, detail="Audit finished but no output report was created.")

    if body.historical_replay:
        _write_period_source(body.year, body.month, "Replay Test")

    return {
        "status": "ok",
        "report_id": latest.name,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-2000:],
    }


@app.get("/api/audit/summary")
def audit_summary(report_id: str | None = None, year: int | None = None, month: int | None = None) -> dict:
    if report_id:
        report_path = _safe_output_path(report_id)
    else:
        generated = _generated_reports()
        if not generated:
            raise HTTPException(status_code=404, detail="No generated report found.")
        report_path = generated[0]

    exceptions_count, _ = _sheet_metrics(report_path, "Legacy Exceptions")
    _, line_rows = _sheet_metrics(report_path, "Legacy Line Match vs Jennifer")
    _, validation_rows = _sheet_metrics(report_path, "Legacy Validation vs Jennifer")
    _, commission_rows = _sheet_metrics(report_path, "Legacy Commission Detail")

    matched = sum(1 for row in line_rows if str(row.get("Line Match Status", "")) == "Matched")
    matched_amount_diff = sum(
        1 for row in line_rows if str(row.get("Line Match Status", "")) == "Matched - Amount Difference"
    )
    jennifer_only = sum(1 for row in line_rows if str(row.get("Line Match Status", "")) == "Jennifer Only")
    our_only = sum(1 for row in line_rows if str(row.get("Line Match Status", "")) == "Our Only")
    amount_diff_total = sum(float(row.get("Commission Amount Difference", 0) or 0) for row in line_rows)
    commissionable_lines = sum(1 for row in commission_rows if str(row.get("Commissionable Flag", "")) == "Yes")
    estimated_commission = sum(float(row.get("Commission Amount", 0) or 0) for row in commission_rows)
    amount_under_review = sum(
        float(row.get("Commission Amount", 0) or 0)
        for row in commission_rows
        if str(row.get("Commissionable Flag", "")) != "Yes"
    )

    input_counts = {"sales_orders": 0, "invoices": 0, "shipments": 0, "items": 0, "payments": 0}
    source = {"source": "Unknown"}
    has_historical_workbook = False
    # Phase A: per-management-category aggregation. Computed from live audit_rows
    # (NOT the legacy Excel sheets above). Read-only -- never writes anywhere and
    # cannot move money. Empty dict if year+month not provided.
    category_breakdown: dict[str, dict[str, float]] = {}
    if year and month:
        try:
            period_sqlite = period_counts(year, month)
            input_counts["sales_orders"] = period_sqlite.get("sales_orders", 0)
            input_counts["invoices"] = period_sqlite.get("invoices", 0)
            input_counts["shipments"] = period_sqlite.get("shipments", 0)
            input_counts["items"] = period_sqlite.get("items", 0)
            input_counts["payments"] = period_sqlite.get("payments", 0)
        except Exception:
            period_paths = _period_input_paths(year, month)
            input_counts["sales_orders"] = _count_excel_rows(period_paths["sales_orders"])
            input_counts["invoices"] = _count_excel_rows(period_paths["invoices"])
            input_counts["shipments"] = _count_excel_rows(period_paths["shipments"])
            input_counts["items"] = _count_excel_rows(period_paths["items"])
        period_paths = _period_input_paths(year, month)
        has_historical_workbook = period_paths["b2b"].exists()
        source = _read_period_source(year, month)
        # Build the live category breakdown. Guarded so a sqlite-data-missing
        # error never breaks the summary response.
        try:
            if has_period_data(year, month):
                tiers_cb = load_tiers_from_template(MASTER_TEMPLATE)
                rlp_cb = load_map_from_template(MASTER_TEMPLATE)
                result_cb = build_salespeople_from_sqlite(
                    year, month, tiers=tiers_cb, rlp_map=rlp_cb, apply_adjustments=True,
                )
                # Pre-compute SO totals once to surface over_5000_review here too
                so_rev_cb: dict[str, float] = {}
                for r in result_cb.audit_rows:
                    so_key = r.get("sales_order")
                    if so_key:
                        try:
                            so_rev_cb[so_key] = so_rev_cb.get(so_key, 0.0) + float(r.get("revenue") or 0.0)
                        except (TypeError, ValueError):
                            pass

                def _bump(bucket: str, row: dict) -> None:
                    cb = category_breakdown.setdefault(
                        bucket, {"count": 0, "amount_held": 0.0, "amount_paid": 0.0, "revenue": 0.0},
                    )
                    cb["count"] += 1
                    sys_c = float(row.get("system_commission") or 0.0)
                    fin_c = float(row.get("final_commission") or 0.0)
                    cb["amount_held"] += round(sys_c - fin_c, 2)
                    cb["amount_paid"] += fin_c
                    cb["revenue"] += float(row.get("revenue") or 0.0)

                for row in result_cb.audit_rows:
                    for tag in (row.get("category_tags") or []):
                        _bump(tag, row)
                    so_key = row.get("sales_order")
                    if so_key and so_rev_cb.get(so_key, 0.0) > 5000.0:
                        _bump("over_5000_review", row)
                # Round + finalize
                for cb in category_breakdown.values():
                    cb["amount_held"] = round(cb["amount_held"], 2)
                    cb["amount_paid"] = round(cb["amount_paid"], 2)
                    cb["revenue"] = round(cb["revenue"], 2)
        except Exception as exc:
            category_breakdown = {"__error__": {"count": 0, "amount_held": 0.0, "amount_paid": 0.0, "revenue": 0.0, "error": str(exc)}}

    return {
        "report_id": report_path.name,
        "source": source,
        "has_historical_workbook": has_historical_workbook,
        "cards": {
            "sales_orders_count": input_counts["sales_orders"],
            "invoice_count": input_counts["invoices"],
            "shipment_count": input_counts["shipments"],
            "items_count": input_counts["items"],
            "commissionable_lines": commissionable_lines,
            "exceptions_count": exceptions_count,
            "estimated_commission": round(estimated_commission, 2),
            "amount_under_review": round(amount_under_review, 2),
            "matched_lines": matched + matched_amount_diff,
            "historical_workbook_only_lines": jennifer_only,
            "system_only_lines": our_only,
            "amount_difference_total": round(amount_diff_total, 2),
            "validation_rows": len(validation_rows),
        },
        "category_breakdown": category_breakdown,
    }


def _safe_output_path(report_id: str) -> Path:
    """Resolve report_id inside OUTPUT_DIR, rejecting path traversal attempts."""
    path = (OUTPUT_DIR / report_id).resolve()
    if not str(path).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid report id.")
    return path


@app.get("/api/downloads/reports/{report_id}")
def download_report(report_id: str):
    path = _safe_output_path(report_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found.")
    return FileResponse(path=path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/zoho/workbooks")
def zoho_workbooks() -> dict:
    items = [
        {"id": p.name, "label": p.stem, "source": "zoho", "path": str(p)}
        for p in _zoho_workbooks()
    ]
    return {"workbooks": items}


@app.get("/api/zoho/status")
def zoho_status() -> dict:
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        config = load_zoho_config()
        client = ZohoBooksClient(config, timeout_seconds=12)
        client.refresh_access_token()
        client.get("items", params={"page": 1, "per_page": 1})
        return {
            "connected": True,
            "label": "Connected to Zoho",
            "source": "Zoho Live",
            "checked_at": checked_at,
        }
    except (ZohoAuthError, ZohoApiError) as exc:
        return {
            "connected": False,
            "label": "Zoho connection error",
            "source": "Zoho Live",
            "checked_at": checked_at,
            "detail": str(exc),
        }


@app.get("/api/reports")
def generated_reports() -> dict:
    items = [
        {"id": p.name, "label": p.stem, "source": "report", "path": str(p)}
        for p in _generated_reports()
    ]
    return {"workbooks": items}


@app.get("/api/workbooks/{workbook_id}/sheets")
def list_sheets(workbook_id: str, source: str = Query("zoho")) -> dict:
    path = _resolve_data_workbook(workbook_id, source)
    workbook = pd.ExcelFile(path)
    return {"workbook": workbook_id, "sheets": workbook.sheet_names}


@app.get("/api/workbooks/{workbook_id}/sheets/{sheet_name}")
def get_sheet(workbook_id: str, sheet_name: str, source: str = Query("zoho")) -> dict:
    path = _resolve_data_workbook(workbook_id, source)
    workbook = pd.ExcelFile(path)
    if sheet_name not in workbook.sheet_names:
        raise HTTPException(status_code=404, detail=f"Sheet '{sheet_name}' not found.")
    frame = _read_report_df(path, sheet_name)
    columns = [str(c) for c in frame.columns.tolist()]
    rows = frame.to_dict(orient="records")
    return {
        "workbook": workbook_id,
        "sheet": sheet_name,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
    }


def _run_sync_subprocess(args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SYNC_SCRIPT), *args]
    return subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True)


@app.get("/api/db/status")
def db_status() -> dict:
    try:
        status = database_status()
        return {"status": "ok", **status}
    except Exception as exc:
        return {
            "status": "error",
            "detail": str(exc),
            "database_backend": "postgres" if using_postgres() else "sqlite",
            "database_path": "DATABASE_URL" if using_postgres() else str(DB_PATH),
            "exists": True if using_postgres() else DB_PATH.exists(),
            "last_sync_time": None,
            "counts": {},
            "latest_runs": [],
        }


@app.post("/api/sync/full")
def sync_full(body: SyncFullRequest) -> dict:
    args = [
        "--mode",
        "full",
        "--date-start",
        body.date_start,
        "--date-end",
        body.date_end,
    ]
    if body.skip_details:
        args.append("--skip-details")
    proc = _run_sync_subprocess(args)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "Full sync failed."
        raise HTTPException(status_code=502, detail=detail)
    db_info = database_status()
    return {
        "status": "ok",
        "mode": "full",
        "stdout": proc.stdout[-8000:],
        "warnings": [line for line in proc.stderr.splitlines() if line.startswith("WARNING:")],
        "db": db_info,
    }


@app.get("/api/sync/incremental/plan")
def sync_incremental_plan() -> dict:
    """Preview per-module fetch windows without calling Zoho."""
    init_database()
    with DatabaseRepository() as repo:
        return {"modules": incremental_sync_plan(repo)}


# --- Background incremental sync (avoids gateway 504 on long syncs) ----------
_sync_lock = threading.Lock()
_sync_state: dict = {
    "running": False,
    "status": "idle",       # idle | running | completed | failed
    "sync_id": None,
    "started_at": None,
    "finished_at": None,
    "totals": None,
    "modules": None,
    "warnings": [],
    "errors": [],
    "db": None,
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run_sync_job(skip_details: bool) -> None:
    try:
        config = load_zoho_config()
        client = ZohoBooksClient(config)
        client.refresh_access_token()
        with DatabaseRepository() as repo:
            result = run_incremental_sync(
                client, repo, skip_details=skip_details, continue_on_module_error=True
            )
        with _sync_lock:
            _sync_state.update(
                status=result.get("status", "completed") if result.get("status") != "failed" else "failed",
                sync_id=result.get("sync_id"),
                totals=result.get("totals"),
                modules=result.get("modules"),
                warnings=result.get("warnings") or [],
                errors=result.get("errors") or [],
                db=database_status(),
            )
    except Exception as exc:  # ZohoAuthError, network, etc.
        with _sync_lock:
            _sync_state.update(status="failed", errors=[str(exc)])
    finally:
        with _sync_lock:
            _sync_state["running"] = False
            _sync_state["finished_at"] = _utcnow()


@app.post("/api/sync/incremental")
def sync_incremental(skip_details: bool = False) -> dict:
    """Start the incremental Zoho sync in the background and return immediately.

    Long syncs used to run inside the request and hit the gateway's 504 timeout
    (and blocked other endpoints). Poll GET /api/sync/incremental/status instead.
    """
    init_database()
    with _sync_lock:
        if _sync_state["running"]:
            return {"status": "running", "message": "A sync is already in progress.", **_sync_state}
        _sync_state.update(
            running=True, status="running", started_at=_utcnow(),
            finished_at=None, totals=None, modules=None, warnings=[], errors=[], db=None,
        )
    try:
        threading.Thread(target=_run_sync_job, args=(skip_details,), daemon=True).start()
    except Exception as exc:
        with _sync_lock:
            _sync_state.update(running=False, status="failed", errors=[str(exc)])
        raise HTTPException(status_code=500, detail="Failed to start sync thread.") from exc
    return {"status": "started", "mode": "incremental", "message": "Sync started in background."}


@app.get("/api/sync/incremental/status")
def sync_incremental_status() -> dict:
    return {"mode": "incremental", **_sync_state}


@app.post("/api/fetch")
def fetch_zoho_data(body: FetchRequest) -> dict:
    cmd = [sys.executable, str(SRC_DIR / "fetch_zoho_data.py")]
    if body.date:
        cmd.extend(["--date", body.date])
    elif body.date_start and body.date_end:
        cmd.extend(["--date-start", body.date_start, "--date-end", body.date_end])
    elif body.year and body.month:
        cmd.extend(["--year", str(body.year), "--month", str(body.month)])
    else:
        raise HTTPException(status_code=400, detail="Provide date, date range, or year/month.")

    proc = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "Zoho fetch failed."
        raise HTTPException(status_code=502, detail=detail)

    latest = _zoho_workbooks()[0] if _zoho_workbooks() else None
    if latest is None:
        raise HTTPException(status_code=500, detail="Fetch completed but no workbook was created.")

    extraction_counts: dict[str, int] = {}
    mirrored_path: Path | None = None
    if body.year and body.month:
        extraction_counts = _extract_zoho_to_input(latest, body.year, body.month)
        b2b_status = "missing"
        try:
            _ensure_b2b_input(body.year, body.month)
            b2b_status = "ready"
        except HTTPException:
            b2b_status = "missing"
        mirrored_path = RAW_DIR / f"{body.year}_{body.month:02d}_zoho_export.xlsx"
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        mirrored_path.write_bytes(latest.read_bytes())
        _write_period_source(
            body.year,
            body.month,
            "Zoho Live",
            {
                "zoho_workbook": str(latest),
                "zoho_mirror_file": str(mirrored_path),
                "counts": extraction_counts,
                "b2b_status": b2b_status,
            },
        )

    return {
        "status": "ok",
        "workbook": latest.name,
        "zoho_mirror_file": str(mirrored_path) if mirrored_path else None,
        "counts": extraction_counts,
        "stdout": proc.stdout[-4000:],
        "warnings": [line for line in proc.stderr.splitlines() if line.startswith("WARNING:")],
    }


def _resolve_data_workbook(workbook_id: str, source: str) -> Path:
    if source not in ("report", "zoho"):
        raise HTTPException(status_code=400, detail="Invalid source.")
    base = OUTPUT_DIR if source == "report" else EXPORT_DIR
    path = (base / workbook_id).resolve()
    if not str(path).startswith(str(base.resolve())):
        raise HTTPException(status_code=400, detail="Invalid workbook id.")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Workbook not found.")
    return path


@app.get("/api/config")
def config() -> dict:
    return {
        "commissions_dir": str(COMMISSIONS_DIR),
        "commissions_dir_exists": COMMISSIONS_DIR.exists(),
    }


@app.get("/api/settings/commission")
def settings_commission() -> dict:
    """Read-only commission rules, rate table, thresholds, Bruce rates, ticket policy."""
    return get_commission_settings(MASTER_TEMPLATE)


@app.get("/api/settings/roster")
def settings_roster() -> dict:
    """Read-only roster / people configuration."""
    return get_roster_settings()


@app.get("/api/settings/price-history/search")
def settings_price_history_search(
    q: str = Query(""),
    limit: int = Query(25, ge=1, le=100),
) -> dict:
    """Autocomplete search for SKUs / item_ids in price_history."""
    return search_price_history(q, limit=limit)


@app.get("/api/settings/price-history/catalog")
def settings_price_history_catalog(
    q: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Paginated catalog of all SKUs in price_history (dropdown + browse table)."""
    return list_price_history_catalog(q=q, limit=limit, offset=offset)


@app.get("/api/settings/price-history")
def settings_price_history(
    sku: str | None = None,
    snapshot_month: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict:
    """Read-only price_history: full SKU trajectory or browse mode."""
    if sku and sku.strip():
        return get_price_history_for_sku(
            sku.strip(),
            template_path=MASTER_TEMPLATE,
            source=source,
            snapshot_month=snapshot_month,
            date_from=date_from,
            date_to=date_to,
        )
    return query_price_history(
        sku=sku,
        snapshot_month=snapshot_month,
        limit=limit,
        offset=offset,
    )


@app.get("/api/settings/price-history/matrix")
def settings_price_history_matrix(
    q: str = Query(""),
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    granularity: str | None = Query(None),
    include_fallback: bool = Query(False),
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict:
    """Read-only SKU × date MAP matrix from price_history."""
    try:
        return get_price_history_matrix(
            q=q,
            from_date=from_date,
            to_date=to_date,
            granularity=granularity,
            include_fallback=include_fallback,
            limit=limit,
            offset=offset,
            template_path=MASTER_TEMPLATE,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/settings/price-history/detail-list")
def settings_price_history_detail_list(
    q: str = Query(""),
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict:
    """Read-only flat list of price_history rows for matrix browse / export preview."""
    return get_price_history_detail_list(
        q=q,
        from_date=from_date,
        to_date=to_date,
        sku_limit=limit,
        sku_offset=offset,
    )


@app.get("/api/settings/price-history/export")
def settings_price_history_export(
    mode: str = Query("detail", pattern="^(detail|matrix)$"),
    format: str = Query("csv", alias="format", pattern="^(csv|xlsx)$"),
    q: str = Query(""),
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    granularity: str | None = Query(None),
    include_fallback: bool = Query(False),
):
    """Download price_history matrix or detail as CSV / XLSX (read-only generation)."""
    try:
        content, filename, media_type = export_price_history_file(
            mode=mode,  # type: ignore[arg-type]
            fmt=format,  # type: ignore[arg-type]
            q=q,
            from_date=from_date,
            to_date=to_date,
            granularity=granularity,
            include_fallback=include_fallback,
            template_path=MASTER_TEMPLATE,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
