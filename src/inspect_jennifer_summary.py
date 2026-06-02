from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_DIR = BASE_DIR / "data" / "input"

YEAR = 2026
MONTH = 3

b2b_file = INPUT_DIR / f"{YEAR}-{MONTH}_Commission B2B.xlsx"

print("Reading:", b2b_file)

xls = pd.ExcelFile(b2b_file)

print("\nSheets:")
for sheet in xls.sheet_names:
    print("-", sheet)

print("\nB2B Summary preview:")
summary_raw = pd.read_excel(b2b_file, sheet_name="B2B Summary", header=None)

pd.set_option("display.max_columns", 50)
pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 80)

print(summary_raw.head(80))
