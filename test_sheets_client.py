import sys
sys.stdout.reconfigure(encoding='utf-8')
from sheets_client import SheetsClient

client = SheetsClient()
report = client.sync_data(force_refresh=True)

print("=== SYNC REPORT ===")
print("Total Records:", report["total_records"])
for sheet_name, info in report["sheets"].items():
    print(f" - {sheet_name}: {info['count']} rows (Source: {info['source']})")

if report["records"]:
    sample = report["records"][0]
    print("\n=== SAMPLE RECORD ===")
    print("Sheet:", sample["sheet_source"])
    print("Description:", repr(sample["property_description"][:80]))
    print("Price:", sample["price"])
    print("Unit / Address:", sample["unit_no_address"])
    print("Availability:", sample["availability"])
    print("Source / Listing:", sample["source_or_listing"])
    print("All Columns count:", len(sample["all_columns"]))
