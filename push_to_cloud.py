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

CACHE_FILE = os.path.join(os.path.dirname(__file__), "sheets_cache.json")
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")


def get_cloud_url():
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    cloud_url = cfg.get("cloud_deploy", {}).get("url", "").rstrip("/")
    if cloud_url:
        return cloud_url

    # Check environment variable
    cloud_url = os.environ.get("CLOUD_URL", "").rstrip("/")
    if cloud_url:
        return cloud_url

    # Ask user interactively if running in terminal
    try:
        print("\n" + "=" * 60)
        print("🌐 RENDER CLOUD SETUP")
        print("Please enter your live website URL from Render")
        print("Example: https://cai-property-search.onrender.com")
        print("=" * 60)
        user_url = input("Render URL: ").strip().rstrip("/")
        if user_url:
            if "cloud_deploy" not in cfg:
                cfg["cloud_deploy"] = {}
            cfg["cloud_deploy"]["url"] = user_url
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            print(f"✅ Saved website URL to config.json!")
            return user_url
    except Exception:
        pass

    return ""


def push(cloud_url=None):
    if not os.path.exists(CACHE_FILE):
        print(f"❌ Error: {CACHE_FILE} not found. Run sync first.")
        return False

    if not cloud_url:
        cloud_url = get_cloud_url()

    if not cloud_url:
        print("⚠️ No cloud URL configured. Skipping cloud push.")
        print("You can add it anytime to config.json under 'cloud_deploy': { 'url': '...' }")
        return False

    print(f"\n🚀 Pushing property database to {cloud_url}...")
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
