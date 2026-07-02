from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from config import DATABASE_PATH


class Database:
    def __init__(self, db_path: Path = DATABASE_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self._create_schema()

    def _columns(self, table: str) -> set[str]:
        rows = self.db.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}

    def _add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        if column not in self._columns(table):
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _create_schema(self) -> None:
        # Create the table with the full v1.2.2 schema for new installs.
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS models(
                path TEXT PRIMARY KEY,
                filename TEXT NOT NULL DEFAULT '',
                folder TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                mtime REAL NOT NULL DEFAULT 0,
                sha256 TEXT NOT NULL DEFAULT '',
                md5 TEXT NOT NULL DEFAULT '',
                crc32 INTEGER NOT NULL DEFAULT 0,
                filename_type TEXT NOT NULL DEFAULT 'Unknown',
                last_scanned REAL NOT NULL DEFAULT 0
            )
        """)

        # Migrate older v1.0/v1.1 databases in-place.
        self._add_column_if_missing("models", "filename", "TEXT NOT NULL DEFAULT ''")
        self._add_column_if_missing("models", "folder", "TEXT NOT NULL DEFAULT ''")
        self._add_column_if_missing("models", "mtime", "REAL NOT NULL DEFAULT 0")
        self._add_column_if_missing("models", "filename_type", "TEXT NOT NULL DEFAULT 'Unknown'")
        self._add_column_if_missing("models", "last_scanned", "REAL NOT NULL DEFAULT 0")

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS scan_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started REAL NOT NULL,
                finished REAL NOT NULL,
                root TEXT NOT NULL,
                found INTEGER NOT NULL,
                scanned INTEGER NOT NULL,
                skipped INTEGER NOT NULL,
                elapsed REAL NOT NULL
            )
        """)

        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_sha256 ON models(sha256)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_folder ON models(folder)")
        self.db.commit()

    def get_existing_map(self) -> dict:
        rows = self.db.execute("SELECT path, size, mtime FROM models").fetchall()
        return {row["path"]: row for row in rows}

    def upsert_model(self, row: dict) -> None:
        self.db.execute("""
            INSERT OR REPLACE INTO models(
                path, filename, folder, size, mtime, sha256, md5, crc32,
                filename_type, last_scanned
            ) VALUES(
                :path, :filename, :folder, :size, :mtime, :sha256, :md5, :crc32,
                :filename_type, :last_scanned
            )
        """, row)

    def add_scan_history(self, root: str, started: float, found: int, scanned: int, skipped: int) -> None:
        finished = time.time()
        self.db.execute("""
            INSERT INTO scan_history(started, finished, root, found, scanned, skipped, elapsed)
            VALUES(?,?,?,?,?,?,?)
        """, (started, finished, root, found, scanned, skipped, finished - started))
        self.db.commit()

    def count_models(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM models").fetchone()[0])

    def duplicate_hash_count(self) -> int:
        return int(self.db.execute("""
            SELECT COUNT(*) FROM (
                SELECT sha256 FROM models
                WHERE sha256 != ''
                GROUP BY sha256
                HAVING COUNT(*) > 1
            )
        """).fetchone()[0])

    def filename_type_counts(self) -> dict:
        rows = self.db.execute("""
            SELECT filename_type, COUNT(*) AS count
            FROM models
            GROUP BY filename_type
            ORDER BY count DESC
        """).fetchall()
        return {row["filename_type"]: row["count"] for row in rows}

    def folder_counts(self, limit: int = 25):
        return self.db.execute("""
            SELECT folder, COUNT(*) AS count
            FROM models
            GROUP BY folder
            ORDER BY count DESC
            LIMIT ?
        """, (limit,)).fetchall()

    def search(self, term: str, limit: int = 50):
        like = f"%{term}%"
        return self.db.execute("""
            SELECT filename, folder, path, size, filename_type, sha256
            FROM models
            WHERE filename LIKE ? OR folder LIKE ? OR path LIKE ? OR sha256 LIKE ?
            ORDER BY folder, filename
            LIMIT ?
        """, (like, like, like, like, limit)).fetchall()

    def begin(self) -> None:
        self.db.execute("BEGIN")

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.close()
