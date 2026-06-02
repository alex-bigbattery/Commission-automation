from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class CalculationOptions:
    year: int
    month: int
    historical_replay: bool = False
    disable_summary_normalization: bool = False


@dataclass(frozen=True)
class CalculationResult:
    exit_code: int
    stdout: str
    stderr: str


def run_calculation_engine(options: CalculationOptions) -> CalculationResult:
    """
    Calculation engine wrapper.
    Runs src/main.py as the isolated calculation process for a month.
    """
    cmd = [
        sys.executable,
        "-m",
        "src.main",
        "--year",
        str(options.year),
        "--month",
        str(options.month),
        "--data-source",
        "sqlite",
    ]

    if options.historical_replay:
        cmd.append("--historical-replay")

    if options.disable_summary_normalization:
        cmd.append("--disable-summary-normalization")

    proc = subprocess.run(
        cmd,
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
    )

    return CalculationResult(
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )

