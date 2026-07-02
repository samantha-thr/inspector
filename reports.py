from __future__ import annotations

import csv
import json
from pathlib import Path

from config import REPORTS_PATH
from database import Database


def export_search_results(rows, filename: str = "search_results.csv") -> Path:
    REPORTS_PATH.mkdir(parents=True, exist_ok=True)
    out = REPORTS_PATH / filename
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["folder", "filename", "type", "size", "som_version", "sha256", "path"])
        for row in rows:
            writer.writerow([row["folder"], row["filename"], row["filename_type"], row["size"], row["som_version"], row["sha256"], row["path"]])
    return out


def export_database_summary(filename: str = "database_summary.json") -> Path:
    REPORTS_PATH.mkdir(parents=True, exist_ok=True)
    db = Database()
    data = {
        "models": db.count_models(),
        "duplicate_hash_groups": db.duplicate_hash_count(),
        "filename_types": db.filename_type_counts(),
        "som_versions": db.som_version_counts(),
        "size_stats": {k: v for k, v in db.size_stats().items() if k not in ("largest", "smallest")},
    }
    db.close()
    out = REPORTS_PATH / filename
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return out
