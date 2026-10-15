"""Loads .env before anything imports cognee (cognee reads its settings at import time)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATIC = ROOT / "static"

load_dotenv(ROOT / ".env", override=True)

for var in ("SYSTEM_ROOT_DIRECTORY", "DATA_ROOT_DIRECTORY", "COGNEE_LOGS_DIR", "FASTEMBED_CACHE_PATH", "HF_HOME"):
    if os.getenv(var):
        Path(os.environ[var]).mkdir(parents=True, exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Free-tier quotas are per model, so the maker and the reviewer use different models
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MAKER_MODEL = os.getenv("GEMINI_MAKER_MODEL", "gemini-2.5-flash-lite")
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_FALLBACK_MODELS", "gemini-2.5-flash,gemini-2.5-flash-lite").split(",") if m.strip()]
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
