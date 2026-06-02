from __future__ import annotations

import calendar
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from calculation_engine import CalculationOptions, run_calculation_engine


BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_DIR = BASE_DIR / "data" / "input"
OUTPUT_DIR = BASE_DIR / "data" / "output"

HISTORICAL_ROOT = Path(
    r"C:\Users\Bigbattery\Downloads\Commissions-20260529T132541Z-3-001\Commissions"
)

ALL_SALES_PATH = BASE_DIR / "Sales_Order all.xlsx"
ALL_INVOICE_PATH = BASE_DIR / "Invoice all.xlsx"
ALL_SHIPMENT_PATH = BASE_DIR / "Shipment_Order all.xlsx"


@dataclass(frozen=True)
class MonthTarget:
    year: int
    month: int
    workbook_path: Path

    @property
    def month_name(self) -> str:
        return calendar.month_name[self.month]

    @property
    def key(self) -> str:
        return f"{self.year}-{self.month:02d}"


def parse_year_month_from_name(name: str) -> tuple[int, int] | None:
    m1 = re.match(r"^(\d{4})-(\d{1,2})_Commission B2B", name, flags=re.IGNORECASE)
    if m1:
        return int(m1.group(1)), int(m1.group(2))

    m2 = re.match(r"^(\d{4})(\d{2})_Commission B2B", name, flags=re.IGNORECASE)
    if m2:
        return int(m2.group(1)), int(m2.group(2))

    return None


def workbook_score(path: Path) -> int:
    n = path.name.lower()
    score = 0

    if re.match(r"^\d{4}-\d{1,2}_commission b2b\.xlsx$", n):
        score += 100
    elif re.match(r"^\d{4}-\d{2}_commission b2b\.xlsx$", n):
        score += 95

    if "adjusted" in n:
        score += 25

    if "_commission b2b_" in n:
        score -= 60

    if "original" in n or "do not pay" in str(path).lower():
        score -= 80

    if "in progress" in str(path).lower():
        score -= 10

    return score


def has_b2b_summary_sheet(path: Path) -> bool:
    try:
        xls = pd.ExcelFile(path)
        return "B2B Summary" in xls.sheet_names
    except Exception:
        return False


def discover_month_targets() -> list[MonthTarget]:
    candidates: dict[tuple[int, int], list[Path]] = {}

    for p in HISTORICAL_ROOT.rglob("*.xlsx"):
        if "commission b2b" not in p.name.lower():
            continue

        ym = parse_year_month_from_name(p.name)
        if ym is None:
            continue

        candidates.setdefault(ym, []).append(p)

    targets: list[MonthTarget] = []
    for (year, month), paths in sorted(candidates.items()):
        best = sorted(
            paths,
            key=lambda x: (has_b2b_summary_sheet(x), workbook_score(x), -len(str(x))),
            reverse=True,
        )[0]
        targets.append(MonthTarget(year=year, month=month, workbook_path=best))

    return targets


def require_files():
    required = [ALL_SALES_PATH, ALL_INVOICE_PATH, ALL_SHIPMENT_PATH, HISTORICAL_ROOT]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing required paths:\n- " + "\n- ".join(missing))


def load_all_sources() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    sales = pd.read_excel(ALL_SALES_PATH)
    invoices = pd.read_excel(ALL_INVOICE_PATH)
    shipments = pd.read_excel(ALL_SHIPMENT_PATH)
    return sales, invoices, shipments


def month_slice(df: pd.DataFrame, date_col: str, year: int, month: int) -> pd.DataFrame:
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    out = out[(out[date_col].dt.year == year) & (out[date_col].dt.month == month)].copy()
    return out


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_suffix(path.suffix + ".bak_validate")
    shutil.copy2(path, backup)
    return backup


def restore_file(path: Path, backup: Path | None):
    if backup is None:
        if path.exists():
            path.unlink()
        return
    if path.exists():
        path.unlink()
    shutil.move(str(backup), str(path))


def read_validation_totals(output_path: Path) -> dict:
    val = pd.read_excel(output_path, sheet_name="Validation vs Jennifer")
    our_total = float(val["Our_Total_Commission_Amount"].sum())
    j_total = float(val["Jennifer Total Commission Payable"].sum())
    diff = float(val["Total Commission Difference"].sum())
    return {
        "our_total_commission": round(our_total, 6),
        "historical_total_commission": round(j_total, 6),
        "difference": round(diff, 6),
        "abs_difference": round(abs(diff), 6),
    }


def extract_top25_line_differences(output_path: Path) -> pd.DataFrame:
    lm = pd.read_excel(output_path, sheet_name="Line Match vs Jennifer")
    if "Commission Amount Difference" not in lm.columns:
        return pd.DataFrame()

    top = lm[lm["Line Match Status"] == "Matched - Amount Difference"].copy()
    if top.empty:
        return pd.DataFrame()

    top["Abs Commission Diff"] = top["Commission Amount Difference"].abs()
    top = top.sort_values("Abs Commission Diff", ascending=False).head(25)

    ordered = [
        "Jennifer Salesperson",
        "Our Salesperson Normalized",
        "Jennifer Section",
        "Sales Order Number",
        "Invoice Number",
        "SKU",
        "Jennifer Commissionable Amount",
        "Our Commissionable Amount",
        "Jennifer Commission Rate",
        "Our Commission Rate",
        "Jennifer Commission Payable",
        "Our Commission Amount",
        "Commission Amount Difference",
        "Abs Commission Diff",
    ]
    existing = [c for c in ordered if c in top.columns]
    return top[existing]


def extract_rule_breakdown(output_path: Path) -> pd.DataFrame:
    lm = pd.read_excel(output_path, sheet_name="Line Match vs Jennifer")
    needed = [
        "Line Match Status",
        "Jennifer Section",
        "Jennifer Salesperson",
        "Our Salesperson Normalized",
        "Commission Amount Difference",
    ]
    for c in needed:
        if c not in lm.columns:
            return pd.DataFrame()

    diff_rows = lm[lm["Line Match Status"].isin(["Matched - Amount Difference", "Jennifer Only", "Our Only"])].copy()
    if diff_rows.empty:
        return pd.DataFrame()

    breakdown = (
        diff_rows.groupby(
            ["Line Match Status", "Jennifer Section", "Jennifer Salesperson", "Our Salesperson Normalized"],
            dropna=False,
            as_index=False,
        )
        .agg(
            Lines=("Line Match Status", "count"),
            Total_Delta=("Commission Amount Difference", "sum"),
            Abs_Total_Delta=("Commission Amount Difference", lambda s: s.abs().sum()),
        )
        .sort_values("Abs_Total_Delta", ascending=False)
    )
    return breakdown


def copy_month_output(output_path: Path, year: int, month: int, suffix: str) -> Path:
    month_name = calendar.month_name[month].lower()
    target = OUTPUT_DIR / f"commission_audit_{month_name}_{year}_{suffix}.xlsx"
    shutil.copy2(output_path, target)
    return target


def run_month(
    target: MonthTarget,
    sales_all: pd.DataFrame,
    invoices_all: pd.DataFrame,
    shipments_all: pd.DataFrame,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    month_name = target.month_name
    year = target.year
    month = target.month

    sales_month = month_slice(sales_all, "Order Date", year, month)
    invoice_month = month_slice(invoices_all, "Invoice Date", year, month)
    shipment_month = month_slice(shipments_all, "Shipment Date", year, month)

    b2b_target_name = f"{year}-{month}_Commission B2B.xlsx"
    sales_target_name = f"Sales_Orders {month_name} {year}.xlsx"
    invoice_target_name = f"Invoices {month_name} {year}.xlsx"
    shipment_target_name = f"Shipments {month_name} {year}.xlsx"

    b2b_target = INPUT_DIR / b2b_target_name
    sales_target = INPUT_DIR / sales_target_name
    invoice_target = INPUT_DIR / invoice_target_name
    shipment_target = INPUT_DIR / shipment_target_name

    backups = {
        "b2b": backup_file(b2b_target),
        "sales": backup_file(sales_target),
        "inv": backup_file(invoice_target),
        "ship": backup_file(shipment_target),
    }

    try:
        shutil.copy2(target.workbook_path, b2b_target)
        sales_month.to_excel(sales_target, index=False)
        invoice_month.to_excel(invoice_target, index=False)
        shipment_month.to_excel(shipment_target, index=False)

        strict = run_calculation_engine(
            CalculationOptions(
                year=year,
                month=month,
                historical_replay=True,
                disable_summary_normalization=False,
            )
        )

        output_path = OUTPUT_DIR / f"commission_audit_{month_name.lower()}_{year}.xlsx"
        if not output_path.exists():
            return (
                {
                    "month": target.key,
                    "workbook_used": str(target.workbook_path),
                    "status": "failed",
                    "error": "Output file not generated after strict run",
                    "strict_exit_code": strict.exit_code,
                    "strict_stdout_tail": strict.stdout[-1200:],
                    "strict_stderr_tail": strict.stderr[-1200:],
                },
                pd.DataFrame(),
                pd.DataFrame(),
            )

        strict_totals = read_validation_totals(output_path)
        strict_copy = copy_month_output(output_path, year, month, "strict")

        raw = run_calculation_engine(
            CalculationOptions(
                year=year,
                month=month,
                historical_replay=True,
                disable_summary_normalization=True,
            )
        )

        if not output_path.exists():
            return (
                {
                    "month": target.key,
                    "workbook_used": str(target.workbook_path),
                    "status": "failed",
                    "error": "Output file not generated after raw run",
                    "strict_exit_code": strict.exit_code,
                    "raw_exit_code": raw.exit_code,
                    "strict_stdout_tail": strict.stdout[-1200:],
                    "strict_stderr_tail": strict.stderr[-1200:],
                    "raw_stdout_tail": raw.stdout[-1200:],
                    "raw_stderr_tail": raw.stderr[-1200:],
                },
                pd.DataFrame(),
                pd.DataFrame(),
            )

        raw_totals = read_validation_totals(output_path)
        raw_copy = copy_month_output(output_path, year, month, "raw")

        top25 = extract_top25_line_differences(raw_copy)
        breakdown = extract_rule_breakdown(raw_copy)

        status = "ok" if strict.exit_code == 0 and raw.exit_code == 0 else "process_error"
        row = {
            "month": target.key,
            "workbook_used": str(target.workbook_path),
            "sales_rows": int(len(sales_month)),
            "invoice_rows": int(len(invoice_month)),
            "shipment_rows": int(len(shipment_month)),
            "status": status,
            "strict_exit_code": int(strict.exit_code),
            "raw_exit_code": int(raw.exit_code),
            "strict_our_total_commission": strict_totals["our_total_commission"],
            "strict_historical_total_commission": strict_totals["historical_total_commission"],
            "strict_difference": strict_totals["difference"],
            "strict_abs_difference": strict_totals["abs_difference"],
            "raw_our_total_commission": raw_totals["our_total_commission"],
            "raw_historical_total_commission": raw_totals["historical_total_commission"],
            "raw_difference": raw_totals["difference"],
            "raw_abs_difference": raw_totals["abs_difference"],
            "strict_output_path": str(strict_copy),
            "raw_output_path": str(raw_copy),
            "strict_exact_match": abs(strict_totals["difference"]) <= 0.01,
            "raw_exact_match_without_summary": abs(raw_totals["difference"]) <= 0.01,
            "strict_stdout_tail": strict.stdout[-1200:],
            "strict_stderr_tail": strict.stderr[-1200:],
            "raw_stdout_tail": raw.stdout[-1200:],
            "raw_stderr_tail": raw.stderr[-1200:],
        }
        return row, top25, breakdown
    finally:
        restore_file(b2b_target, backups["b2b"])
        restore_file(sales_target, backups["sales"])
        restore_file(invoice_target, backups["inv"])
        restore_file(shipment_target, backups["ship"])


def main():
    require_files()
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    targets = discover_month_targets()
    if not targets:
        raise RuntimeError("No B2B historical monthly workbooks found.")

    sales_all, invoices_all, shipments_all = load_all_sources()

    rows = []
    top25_map: dict[str, pd.DataFrame] = {}
    rules_map: dict[str, pd.DataFrame] = {}

    for t in targets:
        print(f"[RUN] {t.key} -> {t.workbook_path.name}")
        row, top25, rules = run_month(t, sales_all, invoices_all, shipments_all)
        rows.append(row)
        top25_map[t.key] = top25
        rules_map[t.key] = rules
        print(
            f"      status={row.get('status')} strict_diff={row.get('strict_difference')} "
            f"raw_diff={row.get('raw_difference')} sales={row.get('sales_rows')} "
            f"inv={row.get('invoice_rows')} ship={row.get('shipment_rows')}"
        )

    report = pd.DataFrame(rows).sort_values("month")
    queue_rows: list[pd.DataFrame] = []
    for key, df in rules_map.items():
        if df.empty:
            continue
        tagged = df.copy()
        tagged["month"] = key
        queue_rows.append(tagged)

    if queue_rows:
        combined = pd.concat(queue_rows, ignore_index=True)
        rule_fix_queue = (
            combined.groupby(
                ["Line Match Status", "Jennifer Section", "Jennifer Salesperson", "Our Salesperson Normalized"],
                dropna=False,
                as_index=False,
            )
            .agg(
                Months_Affected=("month", "nunique"),
                Total_Lines=("Lines", "sum"),
                Total_Abs_Delta=("Abs_Total_Delta", "sum"),
            )
            .sort_values(["Total_Abs_Delta", "Months_Affected"], ascending=False)
        )
    else:
        rule_fix_queue = pd.DataFrame()

    report_path = OUTPUT_DIR / "historical_replay_validation_report.xlsx"
    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        report.to_excel(writer, sheet_name="Summary", index=False)
        if not rule_fix_queue.empty:
            rule_fix_queue.to_excel(writer, sheet_name="Rule_Fix_Queue", index=False)
        for key, df in top25_map.items():
            if df.empty:
                continue
            sheet = f"Top25_{key.replace('-', '')}"[:31]
            df.to_excel(writer, sheet_name=sheet, index=False)
        for key, df in rules_map.items():
            if df.empty:
                continue
            sheet = f"Rules_{key.replace('-', '')}"[:31]
            df.to_excel(writer, sheet_name=sheet, index=False)

    ok = report[report["status"] == "ok"]
    print("\n=== Summary ===")
    print(f"Months checked: {len(report)}")
    print(f"Successful runs: {len(ok)}")
    if not ok.empty:
        print(
            f"Mean strict abs diff: {ok['strict_abs_difference'].mean():.6f} | "
            f"Max strict abs diff: {ok['strict_abs_difference'].max():.6f}"
        )
        print(
            f"Mean raw abs diff: {ok['raw_abs_difference'].mean():.6f} | "
            f"Max raw abs diff: {ok['raw_abs_difference'].max():.6f}"
        )
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()

