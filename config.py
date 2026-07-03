from __future__ import annotations
from pathlib import Path

APP_NAME = "There Inspector"
VERSION = "2.1.0"

DEFAULT_SCAN_PATH = r"C:\Makena\There\ThereClient\Resources"
DATABASE_PATH = Path("database") / "inspector_v2.db"
REPORTS_PATH = Path("reports")
LOGS_PATH = Path("logs")

MODEL_EXTENSION = ".model"
TEXTURE_EXTENSIONS = (".dds", ".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp")

HASH_CHUNK_SIZE = 1024 * 1024
BATCH_SIZE = 1000

SEARCH_LIMIT = 100
TABLE_LIMIT = 100
FAMILY_LIMIT = 100
LINK_LIMIT = 100
EVIDENCE_LIMIT = 100

SCHEMA_VERSION = 3
