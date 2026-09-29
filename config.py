from __future__ import annotations
import os
from pathlib import Path

APP_NAME = "There Inspector"
VERSION = "3.0.0-dev11"

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SCAN_PATH = r"C:\Makena\There\ThereClient\Resources"
DATABASE_PATH = Path(os.environ.get("THERE_INSPECTOR_DB", str(PROJECT_DIR / "database" / "inspector_v2.db")))
REPORTS_PATH = PROJECT_DIR / "reports"
LOGS_PATH = PROJECT_DIR / "logs"

MODEL_EXTENSION = ".model"
TEXTURE_EXTENSIONS = (".dds", ".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp")

HASH_CHUNK_SIZE = 1024 * 1024
BATCH_SIZE = 1000

SEARCH_LIMIT = 100
TABLE_LIMIT = 100
FAMILY_LIMIT = 100
LINK_LIMIT = 100
EVIDENCE_LIMIT = 100

SCHEMA_VERSION = 11
