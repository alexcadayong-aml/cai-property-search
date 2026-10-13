import sys
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
excel_path = r'C:\Users\mcjld\Downloads\CAI_AGENTS LEASE (2).xlsx'
xl = pd.ExcelFile(excel_path)
print("Excel tabs:", xl.sheet_names)

for s in xl.sheet_names:
    df = xl.parse(s)
    print(f"\nTab: {s} (Shape: {df.shape})")
    for col in df.columns:
        m = df[col].astype(str).str.contains('512|arbor lanes', case=False, na=False)
        if m.sum() > 0:
            print(f"  -> Found in col '{col}': {m.sum()} rows")
            for val in df.loc[m, col].dropna().head(2):
                print(f"     Sample value: {repr(str(val)[:80])}")
