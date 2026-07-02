from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

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
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS models(
                path TEXT PRIMARY KEY,
                root TEXT NOT NULL DEFAULT '',
                relative_path TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL DEFAULT '',
                folder TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                mtime REAL NOT NULL DEFAULT 0,
                sha256 TEXT NOT NULL DEFAULT '',
                md5 TEXT NOT NULL DEFAULT '',
                crc32 INTEGER NOT NULL DEFAULT 0,
                filename_type TEXT NOT NULL DEFAULT 'Unknown',
                som_version TEXT NOT NULL DEFAULT '',
                header TEXT NOT NULL DEFAULT '',
                string_count INTEGER NOT NULL DEFAULT 0,
                last_scanned REAL NOT NULL DEFAULT 0
            )
        """)

        migrations = {
            "root": "TEXT NOT NULL DEFAULT ''",
            "relative_path": "TEXT NOT NULL DEFAULT ''",
            "filename": "TEXT NOT NULL DEFAULT ''",
            "folder": "TEXT NOT NULL DEFAULT ''",
            "mtime": "REAL NOT NULL DEFAULT 0",
            "filename_type": "TEXT NOT NULL DEFAULT 'Unknown'",
            "som_version": "TEXT NOT NULL DEFAULT ''",
            "header": "TEXT NOT NULL DEFAULT ''",
            "string_count": "INTEGER NOT NULL DEFAULT 0",
            "last_scanned": "REAL NOT NULL DEFAULT 0",
        }

        for column, definition in migrations.items():
            self._add_column_if_missing("models", column, definition)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS scan_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started REAL NOT NULL,
                finished REAL NOT NULL,
                root TEXT NOT NULL,
                found INTEGER NOT NULL,
                scanned INTEGER NOT NULL,
                skipped INTEGER NOT NULL,
                errors INTEGER NOT NULL DEFAULT 0,
                elapsed REAL NOT NULL
            )
        """)

        if "errors" not in self._columns("scan_history"):
            self.db.execute("ALTER TABLE scan_history ADD COLUMN errors INTEGER NOT NULL DEFAULT 0")

        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_sha256 ON models(sha256)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_folder ON models(folder)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_filename ON models(filename)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_filename_type ON models(filename_type)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_som_version ON models(som_version)")
        self.db.commit()

    def get_existing_map(self) -> dict[str, sqlite3.Row]:
        rows = self.db.execute("SELECT path, size, mtime FROM models").fetchall()
        return {row["path"]: row for row in rows}

    def upsert_model(self, row: dict[str, Any]) -> None:
        self.db.execute("""
            INSERT OR REPLACE INTO models(
                path, root, relative_path, filename, folder, size, mtime,
                sha256, md5, crc32, filename_type, som_version, header,
                string_count, last_scanned
            ) VALUES(
                :path, :root, :relative_path, :filename, :folder, :size, :mtime,
                :sha256, :md5, :crc32, :filename_type, :som_version, :header,
                :string_count, :last_scanned
            )
        """, row)

    def add_scan_history(
        self,
        root: str,
        started: float,
        found: int,
        scanned: int,
        skipped: int,
        errors: int,
    ) -> None:
        finished = time.time()
        self.db.execute("""
            INSERT INTO scan_history(started, finished, root, found, scanned, skipped, errors, elapsed)
            VALUES(?,?,?,?,?,?,?,?)
        """, (started, finished, root, found, scanned, skipped, errors, finished - started))
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

    def filename_type_counts(self) -> dict[str, int]:
        rows = self.db.execute("""
            SELECT filename_type, COUNT(*) AS count
            FROM models
            GROUP BY filename_type
            ORDER BY count DESC
        """).fetchall()
        return {row["filename_type"]: row["count"] for row in rows}

    def som_version_counts(self) -> dict[str, int]:
        rows = self.db.execute("""
            SELECT CASE WHEN som_version = '' THEN 'Unknown' ELSE som_version END AS version,
                   COUNT(*) AS count
            FROM models
            GROUP BY version
            ORDER BY count DESC
        """).fetchall()
        return {row["version"]: row["count"] for row in rows}

    def folder_counts(self, limit: int = 25):
        return self.db.execute("""
            SELECT folder, COUNT(*) AS count
            FROM models
            GROUP BY folder
            ORDER BY count DESC
            LIMIT ?
        """, (limit,)).fetchall()

    def size_stats(self) -> dict[str, Any]:
        row = self.db.execute("""
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(size), 0) AS total_size,
                COALESCE(AVG(size), 0) AS avg_size,
                COALESCE(MIN(size), 0) AS min_size,
                COALESCE(MAX(size), 0) AS max_size
            FROM models
        """).fetchone()

        largest = self.db.execute("""
            SELECT filename, folder, size, path
            FROM models
            ORDER BY size DESC
            LIMIT 1
        """).fetchone()

        smallest = self.db.execute("""
            SELECT filename, folder, size, path
            FROM models
            ORDER BY size ASC
            LIMIT 1
        """).fetchone()

        return {
            "total": row["total"],
            "total_size": row["total_size"],
            "avg_size": row["avg_size"],
            "min_size": row["min_size"],
            "max_size": row["max_size"],
            "largest": largest,
            "smallest": smallest,
        }

    def latest_scan(self):
        return self.db.execute("""
            SELECT *
            FROM scan_history
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

    def search(self, term: str, limit: int = 100):
        like = f"%{term}%"
        return self.db.execute("""
            SELECT filename, folder, relative_path, path, size, filename_type, som_version, sha256
            FROM models
            WHERE filename LIKE ?
               OR folder LIKE ?
               OR relative_path LIKE ?
               OR path LIKE ?
               OR sha256 LIKE ?
            ORDER BY folder, filename
            LIMIT ?
        """, (like, like, like, like, like, limit)).fetchall()

    def duplicate_hashes(self, limit: int = 100):
        return self.db.execute("""
            SELECT sha256, COUNT(*) AS count, SUM(size) AS total_size
            FROM models
            WHERE sha256 != ''
            GROUP BY sha256
            HAVING COUNT(*) > 1
            ORDER BY count DESC, total_size DESC
            LIMIT ?
        """, (limit,)).fetchall()

    def begin(self) -> None:
        self.db.execute("BEGIN")

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.close()
