from __future__ import annotations

import base64
import calendar
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SupabaseAuthMiddleware)


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


class SyncFullRequest(BaseModel):
    date_start: str = "2021-01-01"
    date_end: str = "today"
    skip_details: bool = False


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
    frame = pd.read_excel(path, sheet_name=0)
    return len(frame.index)


def _sheet_metrics(path: Path, sheet_name: str) -> tuple[int, list[dict]]:
    if not path.exists():
        return 0, []
    workbook = pd.ExcelFile(path)
    if sheet_name not in workbook.sheet_names:
        return 0, []
    frame = pd.read_excel(path, sheet_name=sheet_name).fillna("")
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
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (datetime,)):
        return value.isoformat(sep=" ")
    return value


def _frame_to_grid(frame: pd.DataFrame, limit: int = 500) -> dict:
    if frame is None or frame.empty:
        return {"columns": [], "rows": [], "total_rows": 0}
    sliced = frame.head(limit).copy()
    columns = [str(c) for c in sliced.columns.tolist()]
    rows = [{col: _json_ready(row[col]) for col in columns} for _, row in sliced.iterrows()]
    return {"columns": columns, "rows": rows, "total_rows": int(len(frame))}


@app.get("/api/commissions/sqlite/summary")
def commissions_sqlite_summary(year: int, month: int) -> dict:
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be 1-12.")
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
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be 1-12.")
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
    return OUTPUT_DIR / f"commission_b2b_{calendar.month_name[month].lower()}_{year}.xlsx"


def _commission_meta_path(year: int, month: int) -> Path:
    return OUTPUT_DIR / f"commission_b2b_{calendar.month_name[month].lower()}_{year}.meta.json"


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
    if body.month < 1 or body.month > 12:
        raise HTTPException(status_code=400, detail="Month must be 1-12.")
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
    return {"salespeople": [s for s, _ in ALL_SHEETS_ORDERED]}


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
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be 1-12.")
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

    rows = []
    for row in result.audit_rows:
        if not matches(row):
            continue
        adj = adj_map.get(row["line_uid"])
        merged = dict(row)
        merged["adjustment_record"] = dict(adj) if adj else None
        rows.append(merged)

    return {
        "year": year,
        "month": month,
        "row_count": len(rows),
        "rows": rows,
        "kpis": result.kpis,
        "totals_by_sheet": result.totals_by_sheet,
        "roster": [s for s, _ in ALL_SHEETS_ORDERED],
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
    if body.period_month < 1 or body.period_month > 12:
        raise HTTPException(status_code=400, detail="Month must be 1-12.")
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
    if body.month < 1 or body.month > 12:
        raise HTTPException(status_code=400, detail="Month must be 1-12.")

    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    expected_paths = _period_input_paths(body.year, body.month)
    saved: list[dict] = []

    for payload in body.files:
        if payload.kind not in expected_paths:
            raise HTTPException(status_code=400, detail=f"Unsupported file kind '{payload.kind}'.")
        target = expected_paths[payload.kind]
        try:
            data = base64.b64decode(payload.content_base64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base64 for '{payload.kind}'.") from exc
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
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be 1-12.")
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
        sqlite_period = {
            "ready": has_period_data(year, month),
            "counts": period_counts(year, month),
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
        report_path = OUTPUT_DIR / report_id
    else:
        generated = _generated_reports()
        if not generated:
            raise HTTPException(status_code=404, detail="No generated report found.")
        report_path = generated[0]

    exceptions_count, _ = _sheet_metrics(report_path, "Exceptions")
    _, line_rows = _sheet_metrics(report_path, "Line Match vs Jennifer")
    _, validation_rows = _sheet_metrics(report_path, "Validation vs Jennifer")
    _, commission_rows = _sheet_metrics(report_path, "Commission Detail")

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
    }


@app.get("/api/downloads/reports/{report_id}")
def download_report(report_id: str):
    path = OUTPUT_DIR / report_id
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
    frame = pd.read_excel(path, sheet_name=sheet_name).fillna("")
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


@app.post("/api/sync/incremental")
def sync_incremental(skip_details: bool = False) -> dict:
    init_database()
    try:
        config = load_zoho_config()
        client = ZohoBooksClient(config)
        client.refresh_access_token()
    except ZohoAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    with DatabaseRepository() as repo:
        result = run_incremental_sync(
            client,
            repo,
            skip_details=skip_details,
            continue_on_module_error=True,
        )

    db_info = database_status()
    if result["status"] == "failed" and result.get("errors"):
        raise HTTPException(status_code=502, detail="; ".join(result["errors"]))

    return {
        "status": result["status"],
        "mode": "incremental",
        "sync_id": result["sync_id"],
        "totals": result["totals"],
        "modules": result.get("modules"),
        "warnings": result.get("warnings") or [],
        "errors": result.get("errors") or [],
        "db": db_info,
    }


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
    if source == "report":
        path = OUTPUT_DIR / workbook_id
    else:
        path = EXPORT_DIR / workbook_id

    if not path.exists():
        raise HTTPException(status_code=404, detail="Workbook not found.")
    return path


@app.get("/api/config")
def config() -> dict:
    return {
        "commissions_dir": str(COMMISSIONS_DIR),
        "commissions_dir_exists": COMMISSIONS_DIR.exists(),
    }
