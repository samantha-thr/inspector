from __future__ import annotations

from pathlib import Path

APP_NAME = "There Inspector"
VERSION = "1.2.2"

DEFAULT_SCAN_PATH = r"C:\Makena\There\ThereClient\Resources"
DATABASE_PATH = Path("database") / "inspector.db"
REPORTS_PATH = Path("reports")
LOGS_PATH = Path("logs")

MODEL_EXTENSION = ".model"
BATCH_SIZE = 1000
