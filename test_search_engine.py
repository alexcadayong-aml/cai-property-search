import sys
sys.stdout.reconfigure(encoding='utf-8')
from sheets_client import SheetsClient
from search_engine import PropertySearchEngine

client = SheetsClient()
data = client.sync_data()
engine = PropertySearchEngine(data["records"])

test_queries = [
    "Two Roxas 34A",
    "Park Central 46D",
    "₱450,000",
    "Ayala Triangle",
    "COMMERCIAL Taguig",
    "130,000,000"
]

print(f"Total Database Records: {len(data['records'])}\n")

for q in test_queries:
    results = engine.search(q, limit=3)
    print(f"=== SEARCH QUERY: '{q}' (Matches found: {len(results)}) ===")
    for idx, r in enumerate(results, 1):
        print(f"[{idx}] Source: {r['sheet_source']} | Score: {r['relevance_score']}")
        print(f"    Unit/Address: {r['unit_no_address']}")
        print(f"    Price: {r['price']}")
        print(f"    Availability: {r['availability']}")
        print(f"    Source/Listing: {r['source_or_listing']}")
        print(f"    Matched Columns: {', '.join(r['matched_columns'][:5])}")
        desc_line = r['property_description'].replace('\n', ' ')[:90]
        print(f"    Description: {desc_line}...")
        print()
