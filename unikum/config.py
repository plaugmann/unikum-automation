"""Indstillinger, laest fra .env med fornuftige defaults."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
ATTACHMENT_DIR = DATA_DIR / "attachments"
PROFILE_DIR = ROOT / "browser_profile"
TOKEN_CACHE = DATA_DIR / "tokens.json"
COOKIE_CACHE = DATA_DIR / "cookies.json"
DB_PATH = DATA_DIR / "unikum.db"

for _d in (DATA_DIR, ATTACHMENT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Unikum ---------------------------------------------------------------
GUARDIAN_PID = os.getenv("UNIKUM_PID", "11373201365")
CHILD_PID = os.getenv("UNIKUM_CHILD_PID", "10773547324")
SINCE = os.getenv("UNIKUM_SINCE", "2026-08-01")

BASE = "https://start.unikum.net"
START_URL = f"{BASE}/unikum/start.html?__pid={GUARDIAN_PID}"
LOGIN_URL = f"{BASE}/unikum/appLogin.ahtml"
GRAPHQL_URL = "https://clients.unikum.net/fam/v1/"
TOKEN_KEY = "@unikum/oauth-tokens/unikum-client"

# --- OpenAI ---------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")

# --- Webserver ------------------------------------------------------------
FEED_TOKEN = os.getenv("FEED_TOKEN", "")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://localhost:{PORT}")

# --- Skyen ----------------------------------------------------------------
# Adressen paa Cloudflare-workeren. Er den tom, publiceres der ikke, og alt
# koerer rent lokalt.
CLOUD_URL = os.getenv("CLOUD_URL", "").strip()
CLOUD_PUSH_TOKEN = os.getenv("CLOUD_PUSH_TOKEN", "").strip()

# Bruges kun til at udrulle workeren, ikke i daglig drift.
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "").strip()
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "").strip()
