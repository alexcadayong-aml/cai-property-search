"""
Google Account Browser Synchronizer
===================================
Uses a persistent Google Chrome session to authenticate as alexiscadayong@gmail.com
and pull live private Google Sheets directly into the search engine database.
Zero Google Cloud setup or API keys required!
"""

import os
import sys
import json
import asyncio
import io
import csv
from datetime import datetime
from playwright.async_api import async_playwright
import pandas as pd
from sheets_client import SheetsClient, find_header_row_index, normalize_record

# Ensure UTF-8 output
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def parse_xlsx_bytes_into_records(xlsx_bytes: bytes, sheet_name: str) -> list:
    """Parses all property tabs from an Excel workbook (.xlsx) into standardized records."""
    xl = pd.ExcelFile(io.BytesIO(xlsx_bytes))
    all_records = []
    print(f"   📑 Excel tabs detected in '{sheet_name}': {xl.sheet_names}")

    for tab in xl.sheet_names:
        # Skip search helpers or empty tabs
        if "SEARCH" in tab.upper() or "INDEX" in tab.upper():
            continue

        try:
            df = xl.parse(tab, header=None)
            if df.empty or df.shape[0] < 2:
                continue

            raw_rows = df.fillna("").values.tolist()
            header_idx = find_header_row_index(raw_rows)
            headers = [str(c).strip() for c in raw_rows[header_idx]]

            # Clean / deduplicate headers
            clean_headers = []
            counts = {}
            for h in headers:
                if not h:
                    h = f"COL_{len(clean_headers)+1}"
                counts[h] = counts.get(h, 0) + 1
                clean_headers.append(f"{h}_{counts[h]}" if counts[h] > 1 else h)

            tab_count = 0
            for row_idx, row in enumerate(raw_rows[header_idx + 1:], start=header_idx + 2):
                if not any(str(c).strip() for c in row):
                    continue
                row_dict = {}
                for col_idx, col_name in enumerate(clean_headers):
                    val = row[col_idx] if col_idx < len(row) else ""
                    row_dict[col_name] = str(val).strip()

                rec = normalize_record(row_dict, sheet_name, f"{tab}_{row_idx}")
                # Ensure category is populated
                if not rec.get("category"):
                    rec["category"] = tab
                if rec["property_description"] or rec["unit_no_address"] or rec["price"]:
                    all_records.append(rec)
                    tab_count += 1

            print(f"      • Tab '{tab}': {tab_count} properties extracted")
        except Exception as e:
            print(f"      ⚠️ Error parsing tab '{tab}': {e}")

    return all_records


async def download_sheet_data(page, context, sheet_url: str, name: str) -> tuple:
    """
    Downloads sheet data using the authenticated browser session.
    Returns (records_list, backup_bytes, format_type).
    """
    print(f"\n📂 Opening '{name}' sheet...")
    await page.goto(sheet_url, wait_until="domcontentloaded")
    await asyncio.sleep(2)

    # Check if user needs to log in
    if "accounts.google.com" in page.url or "ServiceLogin" in page.url:
        print(f"\n" + "="*60)
        print("👉 ACTION REQUIRED: Google Sign-In Window")
        print("   Please log in with alexiscadayong@gmail.com in the Chrome window.")
        print("   Waiting for you to complete sign-in...")
        print("="*60)
        login_waited = 0
        while "accounts.google.com" in page.url:
            if page.is_closed():
                print("❌ Browser window was closed.")
                return [], None, None
            await asyncio.sleep(2)
            login_waited += 2
            if login_waited > 300:
                print("⚠️ Login wait timed out after 5 minutes.")
                return [], None, None
        print("✅ Sign-in detected! Resuming download...")
        await page.goto(sheet_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

    # Extract Sheet ID and gid
    sheet_id = ""
    gid = "0"
    if "/spreadsheets/d/" in sheet_url:
        sheet_id = sheet_url.split("/spreadsheets/d/")[1].split("/")[0].split("?")[0]
    if "gid=" in sheet_url:
        gid = sheet_url.split("gid=")[1].split("&")[0].split("#")[0]

    print(f"📥 Connecting to '{name}' (Sheet ID: {sheet_id})...")

    # Strategy 1: Fetch complete .xlsx workbook via page.request
    xlsx_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    try:
        res = await page.request.get(xlsx_url)
        if res.status == 200:
            body = await res.body()
            if body.startswith(b"PK\x03\x04"):  # Valid zip/xlsx file magic bytes
                print(f"✅ Successfully downloaded Excel workbook ({len(body):,} bytes)!")
                records = parse_xlsx_bytes_into_records(body, name)
                return records, body, "xlsx"
    except Exception as e:
        print(f"   ℹ️ Strategy 1 (.xlsx export request) skipped: {e}")

    # Strategy 2: Google Drive direct download URL
    drive_url = f"https://drive.google.com/uc?id={sheet_id}&export=download"
    try:
        res = await page.request.get(drive_url)
        if res.status == 200:
            body = await res.body()
            if body.startswith(b"PK\x03\x04"):
                print(f"✅ Downloaded Excel workbook via Google Drive ({len(body):,} bytes)!")
                records = parse_xlsx_bytes_into_records(body, name)
                return records, body, "xlsx"
    except Exception as e:
        print(f"   ℹ️ Strategy 2 (Drive download) skipped: {e}")

    # Strategy 3: Browser-triggered download
    try:
        print("   🔄 Triggering browser native download...")
        async with page.expect_download(timeout=15000) as download_info:
            await page.evaluate(f"window.location.href = '{xlsx_url}'")
        download = await download_info.value
        temp_path = os.path.abspath(f"temp_sync_{name.replace(' ', '_')}.xlsx")
        await download.save_as(temp_path)
        with open(temp_path, "rb") as f:
            body = f.read()
        try:
            os.remove(temp_path)
        except Exception:
            pass
        if body.startswith(b"PK\x03\x04"):
            print(f"✅ Browser downloaded {len(body):,} bytes Excel file!")
            records = parse_xlsx_bytes_into_records(body, name)
            return records, body, "xlsx"
    except Exception as e:
        print(f"   ℹ️ Strategy 3 (native browser download) skipped: {e}")

    # Strategy 4: Fallback to CSV export
    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    try:
        res = await page.request.get(csv_url)
        if res.status == 200:
            text = await res.text()
            if "html" not in text[:200].lower():
                reader = csv.reader(io.StringIO(text))
                rows = list(reader)
                client = SheetsClient()
                records = client.parse_rows_into_records(rows, name)
                print(f"✅ Fallback CSV export succeeded ({len(records)} records)!")
                return records, text.encode("utf-8"), "csv"
    except Exception as e:
        print(f"   ❌ Strategy 4 (CSV) failed: {e}")

    return [], None, None


async def sync_sheets_via_browser(headless=False):
    client = SheetsClient()
    cfg = client.config

    rent_url = cfg.get("sheet_rent", {}).get("spreadsheet_id_or_url", "").strip()
    sale_url = cfg.get("sheet_sale", {}).get("spreadsheet_id_or_url", "").strip()

    if not rent_url and not sale_url:
        print("❌ No Google Sheet URLs configured. Please configure them in config.json first.")
        return False

    profile_dir = os.path.abspath("Google_Account_Profile")
    os.makedirs(profile_dir, exist_ok=True)

    print("\n" + "="*60)
    print("🌐 Launching Google Chrome to sync private Google Sheets...")
    print(f"   Account: alexiscadayong@gmail.com")
    print(f"   Profile storage: {profile_dir}")
    print("="*60)

    async with async_playwright() as p:
        context = None
        for ch in ["chrome", None]:
            try:
                launch_kwargs = {
                    "user_data_dir": profile_dir,
                    "headless": headless,
                    "accept_downloads": True,
                    "args": [
                        "--start-maximized",
                        "--no-sandbox",
                        "--disable-blink-features=AutomationControlled"
                    ]
                }
                if ch:
                    launch_kwargs["channel"] = ch
                context = await p.chromium.launch_persistent_context(**launch_kwargs)
                break
            except Exception as err:
                print(f"   ℹ️ Launch attempt (channel={ch}) error: {err}")

        if not context:
            print("❌ Could not launch Chrome or Chromium browser.")
            return False

        page = context.pages[0] if context.pages else await context.new_page()

        all_records = []
        sync_report = {
            "timestamp": datetime.now().isoformat(),
            "sheets": {},
            "errors": [],
            "service_account_email": "alexiscadayong@gmail.com (Authenticated Google Session)",
            "total_records": 0
        }

        # 1. Fetch Rent Sheet
        if rent_url:
            rent_name = cfg.get("sheet_rent", {}).get("name", "For Rent")
            try:
                records, raw_data, fmt = await download_sheet_data(page, context, rent_url, rent_name)
                if records:
                    all_records.extend(records)
                    sync_report["sheets"][rent_name] = {
                        "source": "google_account_live",
                        "count": len(records),
                        "status": "ok",
                        "format": fmt
                    }
                    if fmt == "xlsx":
                        with open("Master_For_Rent.xlsx", "wb") as f:
                            f.write(raw_data)
                        print(f"💾 Saved updated master workbook to Master_For_Rent.xlsx")
                    elif fmt == "csv":
                        with open("Master_For_Rent.csv", "wb") as f:
                            f.write(raw_data)
                        print(f"💾 Saved updated backup to Master_For_Rent.csv")
            except Exception as e:
                print(f"❌ Error syncing {rent_name}: {e}")
                sync_report["errors"].append({"sheet": rent_name, "error": str(e)})

        # 2. Fetch Sale Sheet
        if sale_url:
            sale_name = cfg.get("sheet_sale", {}).get("name", "For Sale")
            try:
                records, raw_data, fmt = await download_sheet_data(page, context, sale_url, sale_name)
                if records:
                    all_records.extend(records)
                    sync_report["sheets"][sale_name] = {
                        "source": "google_account_live",
                        "count": len(records),
                        "status": "ok",
                        "format": fmt
                    }
                    if fmt == "xlsx":
                        with open("Master_For_Sale.xlsx", "wb") as f:
                            f.write(raw_data)
                        print(f"💾 Saved updated master workbook to Master_For_Sale.xlsx")
                    elif fmt == "csv":
                        with open("Master_For_Sale.csv", "wb") as f:
                            f.write(raw_data)
                        print(f"💾 Saved updated backup to Master_For_Sale.csv")
            except Exception as e:
                print(f"❌ Error syncing {sale_name}: {e}")
                sync_report["errors"].append({"sheet": sale_name, "error": str(e)})

        await context.close()

        if all_records:
            sync_report["total_records"] = len(all_records)
            sync_report["records"] = all_records
            cache_file = os.path.abspath(cfg.get("cache", {}).get("cache_file", "sheets_cache.json"))
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(sync_report, f, ensure_ascii=False, indent=2)

            print("\n" + "="*60)
            print(f"🎉 SYNC COMPLETE! Total {len(all_records):,} properties loaded!")
            print(f"   Saved to cache: {cache_file}")
            print("="*60 + "\n")
            return True
        else:
            print("\n⚠️ Could not extract records. Please ensure your Google account has viewer access.")
            return False


if __name__ == "__main__":
    asyncio.run(sync_sheets_via_browser(headless=False))
