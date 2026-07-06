from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import REPORTS_PATH


KNOWLEDGE_DIR = Path("knowledge")
DEFAULT_KNOWLEDGE_FILE = KNOWLEDGE_DIR / "default_knowledge.json"


@dataclass
class FolderRule:
    folder: str
    label: str
    category: str
    confidence: int = 100
    notes: str = ""


class KnowledgeBase:
    """JSON-backed knowledge pack for There Inspector.

    2.7.0 starts this as read-mostly. Later releases can make it editable
    from the UI without changing analyzer code.
    """

    def __init__(self, path: Path = DEFAULT_KNOWLEDGE_FILE):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"folders": {}, "texture_roles": [], "assumptions": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"folders": {}, "texture_roles": [], "assumptions": []}

    def folder_info(self, folder: str) -> dict[str, Any]:
        return self.data.get("folders", {}).get(folder.lower(), {})

    def folder_label(self, folder: str) -> str:
        info = self.folder_info(folder)
        return info.get("label") or folder

    def folder_category(self, folder: str) -> str:
        info = self.folder_info(folder)
        return info.get("category") or "Unknown"

    def texture_role_for(self, folder: str, filename: str) -> dict[str, Any]:
        folder = folder.lower()
        filename = filename.lower()
        for rule in self.data.get("texture_roles", []):
            if rule.get("folder", "").lower() not in ("*", folder):
                continue
            suffixes = rule.get("suffixes", [])
            if any(filename.endswith(s.lower()) or filename.rsplit(".", 1)[0].endswith(s.lower()) for s in suffixes):
                return rule
        return {}

    def assumptions(self) -> list[dict[str, Any]]:
        return list(self.data.get("assumptions", []))

    def summary_rows(self) -> list[tuple[str, str, str, str]]:
        rows = []
        for folder, info in sorted(self.data.get("folders", {}).items()):
            rows.append((folder, info.get("label", ""), info.get("category", ""), info.get("notes", "")))
        return rows

    def sync_to_database(self, db) -> int:
        count = 0
        for folder, info in self.data.get("folders", {}).items():
            db.upsert_knowledge_rule(
                "folder",
                folder,
                info.get("label", folder),
                int(info.get("confidence", 100)),
                "knowledge_pack",
                info.get("notes", ""),
            )
            count += 1
        for rule in self.data.get("texture_roles", []):
            db.upsert_knowledge_rule(
                "texture_role",
                rule.get("folder", "*"),
                rule.get("role", "unknown"),
                int(rule.get("confidence", 80)),
                "knowledge_pack",
                rule.get("notes", ""),
            )
            count += 1
        return count
