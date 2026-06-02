from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.zoho_client import ZohoBooksClient

from .repository import DatabaseRepository
from .zoho_sync import sync_module

DEFAULT_FALLBACK_DAYS = 7
SYNC_OVERLAP_DAYS = 1

INCREMENTAL_MODULE_ORDER = (
    "sales_orders",
    "invoices",
    "customer_payments",
    "shipments",
    "items",
)

CORE_MODULES = frozenset({"sales_orders", "invoices", "customer_payments", "items"})
OPTIONAL_MODULES = frozenset({"shipments"})


@dataclass(frozen=True)
class ModuleIncrementalConfig:
    """How to build Zoho list params for incremental fetch per module."""

    use_date_window: bool = True
    fetch_all_without_date: bool = False
    allow_last_modified_time: bool = True


MODULE_INCREMENTAL_CONFIG: dict[str, ModuleIncrementalConfig] = {
    "sales_orders": ModuleIncrementalConfig(),
    "invoices": ModuleIncrementalConfig(),
    "customer_payments": ModuleIncrementalConfig(),
    "shipments": ModuleIncrementalConfig(allow_last_modified_time=False),
    # Items catalog: full fetch unless --use-last-modified (format TBD).
    "items": ModuleIncrementalConfig(use_date_window=False, fetch_all_without_date=True),
}


def _parse_finished_at(finished_at: str) -> datetime | None:
    try:
        finished = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        return finished
    except ValueError:
        return None


def incremental_window_start(
    repo: DatabaseRepository,
    module: str,
    *,
    fallback_days: int = DEFAULT_FALLBACK_DAYS,
) -> tuple[str, str]:
    """Return (window_start YYYY-MM-DD, source label)."""
    last = repo.last_successful_sync(module=module)
    if last and last.get("finished_at"):
        finished = _parse_finished_at(str(last["finished_at"]))
        if finished is not None:
            start = (finished - timedelta(days=SYNC_OVERLAP_DAYS)).date().isoformat()
            return start, "last_sync"

    start = (date.today() - timedelta(days=fallback_days)).isoformat()
    return start, f"fallback_{fallback_days}d"


def incremental_params_for_module(
    repo: DatabaseRepository,
    module: str,
    *,
    fallback_days: int | None = None,
    use_last_modified: bool = False,
) -> dict[str, Any]:
    """
    Build Zoho API query params for incremental sync.

    By default uses date_start/date_end only (no last_modified_time).
    Items use an empty param set (full catalog fetch) unless --use-last-modified.
    """
    config = MODULE_INCREMENTAL_CONFIG.get(module, ModuleIncrementalConfig())
    days = fallback_days if fallback_days is not None else DEFAULT_FALLBACK_DAYS
    today = date.today().isoformat()

    params: dict[str, Any] = {"_incremental_module": module}

    if config.fetch_all_without_date and not use_last_modified:
        params["_incremental_source"] = "full_catalog"
        return params

    window_start, source = incremental_window_start(repo, module, fallback_days=days)
    params["_incremental_source"] = source

    if config.use_date_window:
        params["date_start"] = window_start
        params["date_end"] = today

    if use_last_modified and config.allow_last_modified_time:
        # Reserved for when Zoho datetime format is confirmed (not YYYY-MM-DD).
        params["last_modified_time"] = window_start
        params["_incremental_source"] = f"{source}+last_modified"

    return params


def _public_params(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if not str(k).startswith("_")}


def incremental_sync_plan(
    repo: DatabaseRepository,
    *,
    use_last_modified: bool = False,
    fallback_days: int = DEFAULT_FALLBACK_DAYS,
) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for module in INCREMENTAL_MODULE_ORDER:
        params = incremental_params_for_module(
            repo,
            module,
            fallback_days=fallback_days,
            use_last_modified=use_last_modified,
        )
        source = params.pop("_incremental_source", "unknown")
        params.pop("_incremental_module", None)
        plan.append(
            {
                "module": module,
                "source": source,
                "params": _public_params(params),
                "optional": module in OPTIONAL_MODULES,
            }
        )
    return plan


def _compute_overall_status(
    module_results: dict[str, dict[str, Any]],
) -> str:
    core_results = [module_results.get(m, {}) for m in CORE_MODULES]
    core_ok = sum(1 for r in core_results if r.get("status") == "success")
    core_failed = sum(1 for r in core_results if r.get("status") == "failed")
    optional_failed = any(
        module_results.get(m, {}).get("status") == "failed" for m in OPTIONAL_MODULES
    )

    if core_ok == len(CORE_MODULES):
        return "partial_success" if optional_failed else "success"
    if core_ok > 0 and core_failed > 0:
        return "partial_success"
    if core_ok > 0 and optional_failed:
        return "partial_success"
    return "failed"


def run_incremental_sync(
    client: ZohoBooksClient,
    repo: DatabaseRepository,
    *,
    skip_details: bool = False,
    continue_on_module_error: bool = True,
    fallback_days: int = DEFAULT_FALLBACK_DAYS,
    use_last_modified: bool = False,
) -> dict[str, Any]:
    sync_id = repo.begin_sync_id()
    totals: dict[str, int] = {}
    warnings: list[str] = []
    errors: list[str] = []
    module_plans: list[dict[str, Any]] = []
    module_results: dict[str, dict[str, Any]] = {}

    for module in INCREMENTAL_MODULE_ORDER:
        params = incremental_params_for_module(
            repo,
            module,
            fallback_days=fallback_days,
            use_last_modified=use_last_modified,
        )
        source = params.pop("_incremental_source", "unknown")
        params.pop("_incremental_module", None)
        module_plans.append(
            {
                "module": module,
                "source": source,
                "params": dict(params),
                "optional": module in OPTIONAL_MODULES,
            }
        )

        try:
            count, module_warnings, module_status = sync_module(
                client,
                repo,
                sync_id=sync_id,
                module=module,
                params=params,
                skip_details=skip_details,
            )
            totals[module] = count
            warnings.extend(module_warnings)
            module_results[module] = {
                "status": module_status,
                "records_fetched": count,
                "error": None if module_status == "success" else "; ".join(module_warnings[-3:]),
            }
            if module_status == "failed" and module in OPTIONAL_MODULES:
                line = f"{module} sync failed (optional module)"
                warnings.append(f"WARNING: {line}")
            elif module_status == "failed":
                errors.append(f"{module} incremental sync failed")
        except Exception as exc:
            totals[module] = 0
            message = str(exc)
            module_results[module] = {
                "status": "failed",
                "records_fetched": 0,
                "error": message,
            }
            line = f"{module} incremental sync failed: {message}"
            if module in OPTIONAL_MODULES:
                warnings.append(f"WARNING: {line}")
            else:
                errors.append(line)
                warnings.append(f"WARNING: {line}")
            if not continue_on_module_error:
                raise

    date_starts = [p["params"].get("date_start") for p in module_plans if p["params"].get("date_start")]
    overall_start = min(date_starts) if date_starts else None
    overall_status = _compute_overall_status(module_results)

    return {
        "sync_id": sync_id,
        "mode": "incremental",
        "date_start": overall_start,
        "date_end": date.today().isoformat(),
        "totals": totals,
        "warnings": warnings,
        "errors": errors,
        "modules": module_plans,
        "module_results": module_results,
        "status": overall_status,
        "use_last_modified": use_last_modified,
    }
