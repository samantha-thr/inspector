from __future__ import annotations

from pathlib import Path

APP_NAME = "There Inspector"
VERSION = "1.5.0"

DEFAULT_SCAN_PATH = r"C:\Makena\There\ThereClient\Resources"
DATABASE_PATH = Path("database") / "inspector.db"
REPORTS_PATH = Path("reports")
LOGS_PATH = Path("logs")

MODEL_EXTENSION = ".model"
HASH_CHUNK_SIZE = 1024 * 1024
BATCH_SIZE = 1000
SEARCH_LIMIT = 100
DUPLICATE_LIMIT = 100
FOLDER_LIMIT = 100
SIMILARITY_LIMIT = 100

PREFIX_HASH_SIZE = 256
PREFIX_LARGE_HASH_SIZE = 4096
SUFFIX_HASH_SIZE = 4096
SAMPLE_STRING_LIMIT = 25
