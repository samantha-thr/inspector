from __future__ import annotations
import os
from pathlib import Path

APP_NAME = "There Inspector"
VERSION = "3.0.0-dev3"

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SCAN_PATH = r"C:\Makena\There\ThereClient\Resources"
def _discover_database() -> Path:
    override = os.environ.get("THERE_INSPECTOR_DB")
    if override:
        return Path(override)

    local = PROJECT_DIR / "database" / "inspector_v2.db"
    candidates = [local]

    # Development commonly uses parallel checkouts such as ThereInspector,
    # ThereInspector_v1, etc. Prefer the largest existing Inspector DB so a
    # fresh GUI checkout does not silently open a new empty database.
    try:
        for sibling in PROJECT_DIR.parent.glob("ThereInspector*"):
            candidate = sibling / "database" / "inspector_v2.db"
            if candidate not in candidates:
                candidates.append(candidate)
    except OSError:
        pass

    existing = [p for p in candidates if p.is_file()]
    if existing:
        return max(existing, key=lambda p: p.stat().st_size)
    return local

DATABASE_PATH = _discover_database()
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
