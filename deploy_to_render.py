#!/usr/bin/env python3
"""
🚀 4Ever Bot — Render Auto-Deploy Script

Usage:
    export RENDER_API_KEY=rnd_...
    export TELEGRAM_BOT_TOKEN=...
    export GEMINI_API_KEY=AIza...
    python deploy_to_render.py https://github.com/USERNAME/4ever-bot

What it does:
    1. Creates a Background Worker on Render linked to your GitHub repo
    2. Sets up env vars (tokens stored encrypted on Render)
    3. Triggers first deploy
    4. Polls deploy status until live
    5. Prints bot URL when ready
"""

import os
import sys
import time
import json
import requests

# ─── Config ─────────────────────────────────────────────────
RENDER_API_KEY = os.getenv("RENDER_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([RENDER_API_KEY, TELEGRAM_BOT_TOKEN, GEMINI_API_KEY]):
    print("❌ Missing environment variables. Set:")
    print("   RENDER_API_KEY, TELEGRAM_BOT_TOKEN, GEMINI_API_KEY")
    sys.exit(1)

if len(sys.argv) < 2:
    print("❌ Usage: python deploy_to_render.py <github_repo_url>")
    print("   Example: python deploy_to_render.py https://github.com/me/4ever-bot")
    sys.exit(1)

REPO_URL = sys.argv[1].rstrip("/")
if not REPO_URL.startswith("https://github.com/"):
    print(f"❌ Invalid GitHub URL: {REPO_URL}")
    sys.exit(1)

API = "https://api.render.com/v1"
HDR = {
    "Authorization": f"Bearer {RENDER_API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}


# ─── Helpers ────────────────────────────────────────────────
def api(method, path, **kwargs):
    url = f"{API}{path}"
    r = requests.request(method, url, headers=HDR, timeout=30, **kwargs)
    if r.status_code >= 400:
        print(f"❌ {method} {path} → {r.status_code}")
        print(f"   {r.text}")
        sys.exit(1)
    return r.json() if r.text else {}


# ─── Main ───────────────────────────────────────────────────
print("🚀 4Ever Bot — Render Auto-Deploy")
print("=" * 50)

# 1. Get owner
print("\n[1/5] Finding Render account...")
owners = api("GET", "/owners")
owner_id = owners[0]["owner"]["id"]
owner_name = owners[0]["owner"]["name"]
print(f"   ✓ {owner_name} ({owner_id})")

# 2. Check if service already exists
print(f"\n[2/5] Checking for existing '4ever-bot' service...")
services = api("GET", "/services?ownerId=" + owner_id)
existing = None
for s in services:
    if s.get("service", {}).get("name") == "4ever-bot":
        existing = s["service"]
        break

if existing:
    print(f"   ⚠️  Service exists: {existing['id']}")
    print("   Skipping create, just updating env vars + redeploy...")
    service_id = existing["id"]
else:
    # 3. Create new service
    print(f"\n[3/5] Creating Background Worker linked to {REPO_URL}...")
    service_body = {
        "type": "background_worker",
        "name": "4ever-bot",
        "ownerId": owner_id,
        "repo": REPO_URL,
        "autoDeploy": "yes",
        "branch": "main",
        "rootDir": "",
        "serviceDetails": {
            "env": "python",
            "plan": "free",
            "region": "frankfurt",
            "buildCommand": "bash build.sh",
            "startCommand": "python bot.py",
            "envSpecificDetails": {
                "pythonVersion": "3.11"
            }
        },
        "envVars": [
            {"key": "TELEGRAM_BOT_TOKEN", "value": TELEGRAM_BOT_TOKEN},
            {"key": "GEMINI_API_KEY", "value": GEMINI_API_KEY},
            {"key": "PYTHON_VERSION", "value": "3.11"},
        ]
    }
    result = api("POST", "/services", json=service_body)
    service_id = result["service"]["id"]
    print(f"   ✓ Created service: {service_id}")

# 4. Trigger deploy
print(f"\n[4/5] Triggering deploy...")
deploy = api("POST", f"/services/{service_id}/deploys",
             json={"clearCache": "clear"})
deploy_id = deploy["id"]
print(f"   ✓ Deploy started: {deploy_id}")
print(f"   📊 Live logs: https://dashboard.render.com/worker/{service_id}/logs")

# 5. Poll status
print(f"\n[5/5] Waiting for deploy to complete (~3-5 min)...")
start = time.time()
last_status = ""
while True:
    elapsed = int(time.time() - start)
    if elapsed > 600:
        print(f"\n⏱  Timeout after 10 min. Check logs:")
        print(f"   https://dashboard.render.com/worker/{service_id}")
        sys.exit(1)

    d = api("GET", f"/services/{service_id}/deploys/{deploy_id}")
    status = d.get("status", "unknown")

    if status != last_status:
        print(f"   [{elapsed:3d}s] Status: {status}")
        last_status = status

    if status == "live":
        print(f"\n🎉 DEPLOYED SUCCESSFULLY!")
        print(f"   Bot is now running on Render.")
        print(f"   Dashboard: https://dashboard.render.com/worker/{service_id}")
        print(f"   Logs:      https://dashboard.render.com/worker/{service_id}/logs")
        print(f"\n   👉 Open Telegram and message: @rasof_bot")
        print(f"   👉 Send: /start  then  منشور")
        break
    elif status in ("build_failed", "update_failed", "canceled", "deactivated"):
        print(f"\n❌ Deploy {status}. Check logs:")
        print(f"   https://dashboard.render.com/worker/{service_id}/logs")
        sys.exit(1)

    time.sleep(15)
