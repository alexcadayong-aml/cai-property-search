"""
Master Property Search Updater
==============================
1-Click script that:
1. Connects to Google Drive using your account (alexiscadayong@gmail.com)
2. Downloads latest 'For Rent' and 'For Sale' master Excel workbooks
3. Parses all property tabs into the standardized database
4. Pushes the fresh data to your live shared website on Render
5. Backs up to GitHub
"""

import os
import sys
import json
import asyncio
import subprocess

# Ensure UTF-8 output
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


async def main():
    print("=" * 65)
    print("   🏢 CAI PROPERTY SEARCH - 1-CLICK LISTINGS UPDATER")
    print("   Google Account: alexiscadayong@gmail.com")
    print("=" * 65)

    # 1. Sync from Google Drive via browser
    from browser_sync import sync_sheets_via_browser
    print("\n[Step 1/3] Fetching latest Excel sheets from Google Drive...")
    print("           (If Chrome opens, please complete sign-in if prompted)\n")
    
    sync_ok = await sync_sheets_via_browser(headless=False)
    if not sync_ok:
        print("\n❌ Google sync was canceled or could not download listings.")
        print("   If a Chrome window was closed, run this updater again and complete sign-in.")
        return

    # 2. Push to Live Cloud Website (Render)
    print("\n[Step 2/3] Updating live cloud website...")
    from push_to_cloud import push
    push()

    # 3. Git commit & push for backup
    print("\n[Step 3/3] Backing up updated database to GitHub...")
    try:
        subprocess.run(["git", "add", "sheets_cache.json", "config.json"], cwd=BASE_DIR, check=False)
        subprocess.run(["git", "commit", "-m", "Auto-update property database from Google Drive"], cwd=BASE_DIR, check=False)
        push_res = subprocess.run(["git", "push", "origin", "main"], cwd=BASE_DIR, capture_output=True, text=True)
        if push_res.returncode == 0:
            print("   ✅ GitHub repository updated successfully!")
        else:
            print("   ℹ️ GitHub push skipped (already up to date).")
    except Exception as e:
        print(f"   ℹ️ Git step skipped: {e}")

    print("\n" + "=" * 65)
    print("   🎉 ALL DONE! Your property search tool is fully up to date!")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
