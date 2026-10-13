"""
Cloud Database Synchronizer
===========================
Uploads your latest local property cache (synced with your Google account)
directly to your live 24/7 cloud website (Render, Railway, etc.)
so all teammates get updated listings in real time without redeploying.
"""

import os
import sys
import json
import requests

CACHE_FILE = "sheets_cache.json"
CONFIG_FILE = "config.json"


def push():
    if not os.path.exists(CACHE_FILE):
        print("❌ Error: sheets_cache.json not found. Run sync first.")
        return False

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    cloud_url = cfg.get("cloud_deploy", {}).get("url", "").rstrip("/")
    if not cloud_url:
        print("⚠️ No cloud URL configured yet.")
        print("Once your website is deployed (e.g. on Render), add its URL to config.json:")
        print('  "cloud_deploy": { "url": "https://cai-property-search.onrender.com" }')
        return False

    print(f"🚀 Pushing local property database to {cloud_url}...")
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        cache_data = json.load(f)

    total = len(cache_data.get("records", []))
    print(f"   Payload: {total:,} properties")

    target_endpoint = f"{cloud_url}/api/admin/push-data"
    try:
        res = requests.post(
            target_endpoint,
            json=cache_data,
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        if res.status_code == 200:
            print(f"🎉 SUCCESS! Live cloud website updated with {total:,} properties!")
            return True
        else:
            print(f"❌ Failed to push data (HTTP {res.status_code}): {res.text}")
            return False
    except Exception as e:
        print(f"❌ Network error pushing to cloud: {e}")
        return False


if __name__ == "__main__":
    push()
