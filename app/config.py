"""Centralised configuration. All paths and keys flow through here."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WORKING_DIR = DATA_DIR / "working"
LOGS_DIR = BASE_DIR / "logs"

WORKING_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Source files are treated as read-only inputs. The assistant always operates
# on a working copy under data/working/ so a bad delete never destroys the
# originals.
SOURCE_FILES: dict[str, Path] = {
    "real_estate": DATA_DIR / "Real Estate Listings.xlsx",
    "marketing": DATA_DIR / "Marketing Campaigns.xlsx",
}

WORKING_FILES: dict[str, Path] = {
    "real_estate": WORKING_DIR / "Real Estate Listings.xlsx",
    "marketing": WORKING_DIR / "Marketing Campaigns.xlsx",
}

# Human-readable labels and routing hints that get injected into the prompt.
FILE_DESCRIPTIONS: dict[str, str] = {
    "real_estate": (
        "U.S. residential property listings. Use for any question about "
        "houses, condos, properties, listings, prices, bedrooms, cities, "
        "states, square footage, year built, sale status."
    ),
    "marketing": (
        "Marketing campaign performance data. Use for any question about "
        "campaigns, channels, budget, spend, impressions, clicks, conversions, "
        "revenue, ROI, CTR, conversion rate, dates a campaign ran."
    ),
}

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()

# Hard caps to defend against runaway LLM outputs.
MAX_PREVIEW_ROWS = 50
DEFAULT_PREVIEW_ROWS = 10
MAX_SAMPLE_VALUES_IN_PROMPT = 8
