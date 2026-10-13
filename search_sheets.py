"""
Universal Property Search Tool — Google Sheets & Local Cache
============================================================
Command-line search tool, interactive search shell, and web launcher.
Searches any keyword across ANY column in both Google Sheets (Rent & Sale).

Usage Examples:
  py search_sheets.py "Park Central"            # Search keyword
  py search_sheets.py "Two Roxas 34A"           # Multi-column search
  py search_sheets.py "₱450,000"                # Search by price
  py search_sheets.py "Makati" --sheet rent     # Filter by rent
  py search_sheets.py --interactive             # Interactive live search shell
  py search_sheets.py --sync                    # Re-sync from Google Sheets
  py search_sheets.py --status                  # Check sheet & permission status
  py search_sheets.py --server                  # Launch Web UI on http://localhost:5050
  py search_sheets.py "Taguig" --json           # Export results as JSON
"""

import sys
import os
import json
import argparse
import webbrowser

# Ensure UTF-8 output on Windows consoles
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from sheets_client import SheetsClient
from search_engine import PropertySearchEngine


def format_card(record: dict, idx: int) -> str:
    """Format a property record for clean terminal display."""
    lines = []
    sheet_tag = f"[{record.get('sheet_source', 'Sheet').upper()}]"
    avail = record.get("availability", "UNKNOWN").upper()
    avail_tag = f"({avail})"
    
    unit = record.get("unit_no_address") or record.get("location") or "Unspecified Unit"
    price = record.get("price") or "Price on Request"
    source = record.get("source_or_listing") or "DIRECT"
    directness = record.get("directness", "Direct")
    matched = ", ".join(record.get("matched_columns", [])[:4])

    lines.append(f"┌── #{idx} {sheet_tag} {unit} ── {avail_tag}")
    lines.append(f"│ 💰 Price:             {price}")
    lines.append(f"│ 🏢 Unit No. / Addr:   {unit}")
    lines.append(f"│ 🟢 Availability:      {avail}")
    lines.append(f"│ 👤 Source / Listing:  {source} [{directness.upper()}]")
    if matched:
        lines.append(f"│ 🔍 Matched in:        {matched}")
    
    desc = record.get("property_description", "").strip()
    if desc:
        desc_preview = desc.replace("\r", "").replace("\n", "\n│    ")
        if len(desc_preview) > 400:
            desc_preview = desc_preview[:400] + "..."
        lines.append(f"│ 📝 Description:")
        lines.append(f"│    {desc_preview}")
    
    photos = record.get("photos", "")
    if photos and photos.startswith("http"):
        lines.append(f"│ 📸 Photos:            {photos}")

    lines.append("└───────────────────────────────────────────────────────────")
    return "\n".join(lines)


def display_results(results: list, query: str):
    """Print results cleanly."""
    if not results:
        print(f"\n❌ No matching properties found for query: '{query}'")
        print("💡 Tip: Try fewer keywords, or run with --fuzzy for typo-tolerant matching.\n")
        return

    print(f"\n✅ Found {len(results)} matching properties for '{query}':\n")
    for idx, r in enumerate(results, 1):
        print(format_card(r, idx))
        print()


def run_interactive_mode(engine: PropertySearchEngine):
    """Run an interactive search REPL in the terminal."""
    print("\n=======================================================")
    print("🔍 Interactive Property Search Shell")
    print(f"   Database: {len(engine.records):,} total listings loaded")
    print("   Type any keyword to search across ALL columns.")
    print("   Commands: ':rent <q>', ':sale <q>', ':sync', ':quit'")
    print("=======================================================\n")

    while True:
        try:
            q = input("search> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if not q:
            continue
        if q.lower() in [":quit", ":q", "exit", "quit"]:
            print("Goodbye!")
            break
        elif q.lower() == ":sync":
            client = SheetsClient()
            data = client.sync_data(force_refresh=True)
            engine.set_records(data.get("records", []))
            print(f"Synced! {len(engine.records)} properties ready.\n")
            continue

        sheet_filter = "all"
        if q.startswith(":rent "):
            sheet_filter = "For Rent"
            q = q[6:].strip()
        elif q.startswith(":sale "):
            sheet_filter = "For Sale"
            q = q[6:].strip()

        results = engine.search(query=q, sheet_filter=sheet_filter, limit=15)
        display_results(results, q)


def main():
    parser = argparse.ArgumentParser(
        description="Search tool across 2 Google Sheets (Rent & Sale) with universal column matching."
    )
    parser.add_argument("query", nargs="?", default="", help="Search keyword(s) across any columns")
    parser.add_argument("--sheet", choices=["all", "rent", "sale"], default="all", help="Filter by sheet type")
    parser.add_argument("--avail", choices=["all", "available", "leased", "sold"], default="all", help="Filter by availability")
    parser.add_argument("--limit", type=int, default=25, help="Maximum number of results to display (default 25)")
    parser.add_argument("--direct", action="store_true", help="Filter out One Away listings (Direct listings only)")
    parser.add_argument("--one-away", action="store_true", help="Show One Away listings only")
    parser.add_argument("--fuzzy", action="store_true", help="Enable fuzzy / typo-tolerant matching")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of text cards")
    parser.add_argument("--sync", action="store_true", help="Force sync with Google Sheets / CSV fallbacks")
    parser.add_argument("--status", action="store_true", help="Display Google connection & permission status")
    parser.add_argument("--server", action="store_true", help="Launch the Web Dashboard server")
    parser.add_argument("--login-sync", action="store_true", help="Authenticate with your Google Account (alexiscadayong@gmail.com) in Chrome and sync live private sheets")
    parser.add_argument("-i", "--interactive", action="store_true", help="Launch interactive search mode")
    parser.add_argument("--port", type=int, default=5055, help="Web server port (default 5055)")

    args = parser.parse_args()

    client = SheetsClient()

    # 1. Action: Login Sync via Google Chrome
    if args.login_sync:
        import asyncio
        from browser_sync import sync_sheets_via_browser
        success = asyncio.run(sync_sheets_via_browser(headless=False))
        if success:
            print("\n🚀 Live Google Sheets are now synced! You can run 'py search_sheets.py' or check http://localhost:5055.")
        return

    # 2. Action: Server
    if args.server:
        from server import run_server
        webbrowser.open(f"http://127.0.0.1:{args.port}")
        run_server(port=args.port)
        return

    # 2. Action: Force Sync
    if args.sync:
        print("🔄 Syncing data from Google Sheets / Local Fallbacks...")
        report = client.sync_data(force_refresh=True)
        print(f"✅ Sync complete! Total records loaded: {report.get('total_records', 0):,}")
        for sheet_name, info in report.get("sheets", {}).items():
            print(f"   • {sheet_name}: {info.get('count', 0)} rows (Source: {info.get('source')})")
        if report.get("errors"):
            print("\n⚠️ Warnings/Errors encountered during sync:")
            for err in report["errors"]:
                print(f"   • [{err.get('sheet')}] {err.get('error')}")
        return

    # 3. Action: Status
    if args.status:
        data = client.sync_data(force_refresh=False)
        print("\n================ Google Sheets Status ================")
        print(f"Total Property Records: {data.get('total_records', 0):,}")
        print(f"Last Cache Timestamp:   {data.get('timestamp', 'N/A')}")
        print(f"Service Account Email:  {client.service_account_email or 'Not configured (using CSV)'}")
        for s_name, s_info in data.get("sheets", {}).items():
            print(f"   • {s_name}: {s_info.get('count', 0)} rows | Source: {s_info.get('source')}")
        print("=====================================================\n")
        return

    # Load data
    data = client.sync_data(force_refresh=False)
    engine = PropertySearchEngine(data.get("records", []))

    # 4. Action: Interactive Shell
    if args.interactive:
        run_interactive_mode(engine)
        return

    # 5. Action: Command Line Query
    if not args.query:
        # If no arguments provided, show help and enter interactive mode
        print("No search query provided. Entering interactive search mode...")
        print("(Run 'py search_sheets.py --help' to see all options)\n")
        run_interactive_mode(engine)
        return

    sheet_map = {"all": "all", "rent": "For Rent", "sale": "For Sale"}
    directness_filter = "all"
    if args.direct:
        directness_filter = "direct"
    elif args.one_away:
        directness_filter = "one_away"

    results = engine.search(
        query=args.query,
        sheet_filter=sheet_map.get(args.sheet, "all"),
        availability_filter=args.avail,
        directness_filter=directness_filter,
        limit=args.limit,
        enable_fuzzy=args.fuzzy
    )

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        display_results(results, args.query)


if __name__ == "__main__":
    main()
