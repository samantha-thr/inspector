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
                first_64_hex TEXT NOT NULL DEFAULT '',
                first_256_sha256 TEXT NOT NULL DEFAULT '',
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
            "first_64_hex": "TEXT NOT NULL DEFAULT ''",
            "first_256_sha256": "TEXT NOT NULL DEFAULT ''",
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

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_sha256 ON models(sha256)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_md5 ON models(md5)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_crc32 ON models(crc32)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_size ON models(size)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_folder ON models(folder)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_filename ON models(filename)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_filename_type ON models(filename_type)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_som_version ON models(som_version)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_relative_path ON models(relative_path)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_first256 ON models(first_256_sha256)")
        self.db.commit()

    def get_existing_map(self) -> dict[str, sqlite3.Row]:
        rows = self.db.execute("SELECT path, size, mtime FROM models").fetchall()
        return {row["path"]: row for row in rows}

    def upsert_model(self, row: dict[str, Any]) -> None:
        self.db.execute("""
            INSERT OR REPLACE INTO models(
                path, root, relative_path, filename, folder, size, mtime,
                sha256, md5, crc32, filename_type, som_version, header,
                string_count, first_64_hex, first_256_sha256, last_scanned
            ) VALUES(
                :path, :root, :relative_path, :filename, :folder, :size, :mtime,
                :sha256, :md5, :crc32, :filename_type, :som_version, :header,
                :string_count, :first_64_hex, :first_256_sha256, :last_scanned
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
        self.set_setting("last_scan_root", root)
        self.db.commit()

    def set_setting(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, value))

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

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

    def duplicate_count_for_hash(self, sha256: str) -> int:
        return int(self.db.execute("""
            SELECT COUNT(*) FROM models WHERE sha256 = ? AND sha256 != ''
        """, (sha256,)).fetchone()[0])

    def models_by_hash(self, sha256: str):
        return self.db.execute("""
            SELECT filename, folder, relative_path, path, size, filename_type, som_version, sha256
            FROM models
            WHERE sha256 = ?
            ORDER BY folder, filename
        """, (sha256,)).fetchall()

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

    def folder_details(self, folder: str):
        return self.db.execute("""
            SELECT
                folder,
                COUNT(*) AS count,
                COALESCE(SUM(size), 0) AS total_size,
                COALESCE(AVG(size), 0) AS avg_size,
                COALESCE(MIN(size), 0) AS min_size,
                COALESCE(MAX(size), 0) AS max_size,
                SUM(CASE WHEN filename_type = 'Numeric Product ID' THEN 1 ELSE 0 END) AS numeric_count,
                SUM(CASE WHEN filename_type = 'Named Asset' THEN 1 ELSE 0 END) AS named_count,
                COUNT(DISTINCT sha256) AS unique_hashes
            FROM models
            WHERE folder = ?
            GROUP BY folder
        """, (folder,)).fetchone()

    def models_in_folder(self, folder: str, limit: int = 100):
        return self.db.execute("""
            SELECT filename, folder, relative_path, path, size, filename_type, som_version, sha256
            FROM models
            WHERE folder = ?
            ORDER BY filename
            LIMIT ?
        """, (folder, limit)).fetchall()

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

    def get_model_by_path(self, path: str):
        return self.db.execute("SELECT * FROM models WHERE path = ?", (path,)).fetchone()

    def get_model_by_relative_or_filename(self, query: str):
        return self.db.execute("""
            SELECT * FROM models
            WHERE relative_path = ?
               OR filename = ?
               OR path = ?
            ORDER BY relative_path
            LIMIT 1
        """, (query, query, query)).fetchone()

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

    def exact_duplicate_groups(self, limit: int = 100):
        return self.duplicate_hashes(limit)

    def same_size_groups(self, limit: int = 100):
        return self.db.execute("""
            SELECT size, COUNT(*) AS count
            FROM models
            GROUP BY size
            HAVING COUNT(*) > 1
            ORDER BY count DESC, size DESC
            LIMIT ?
        """, (limit,)).fetchall()

    def same_first256_groups(self, limit: int = 100):
        return self.db.execute("""
            SELECT first_256_sha256, COUNT(*) AS count
            FROM models
            WHERE first_256_sha256 != ''
            GROUP BY first_256_sha256
            HAVING COUNT(*) > 1
            ORDER BY count DESC
            LIMIT ?
        """, (limit,)).fetchall()

    def model_comparison_candidates(self, path: str, limit: int = 100):
        model = self.get_model_by_path(path)
        if not model:
            return []

        return self.db.execute("""
            SELECT *,
                CASE WHEN sha256 = ? AND sha256 != '' THEN 100 ELSE 0 END
              + CASE WHEN first_256_sha256 = ? AND first_256_sha256 != '' THEN 35 ELSE 0 END
              + CASE WHEN size = ? THEN 20 ELSE 0 END
              + CASE WHEN folder = ? THEN 10 ELSE 0 END
              + CASE WHEN filename_type = ? THEN 5 ELSE 0 END
              + CASE WHEN som_version = ? AND som_version != '' THEN 5 ELSE 0 END
                AS score
            FROM models
            WHERE path != ?
              AND (
                    sha256 = ?
                 OR first_256_sha256 = ?
                 OR size = ?
                 OR folder = ?
              )
            ORDER BY score DESC, size DESC, folder, filename
            LIMIT ?
        """, (
            model["sha256"],
            model["first_256_sha256"],
            model["size"],
            model["folder"],
            model["filename_type"],
            model["som_version"],
            model["path"],
            model["sha256"],
            model["first_256_sha256"],
            model["size"],
            model["folder"],
            limit,
        )).fetchall()

    def compare_two_models(self, path_a: str, path_b: str) -> dict[str, Any]:
        a = self.get_model_by_path(path_a)
        b = self.get_model_by_path(path_b)
        if not a or not b:
            return {"a": a, "b": b, "score": 0, "fields": []}

        checks = [
            ("Exact SHA256", a["sha256"], b["sha256"], 50),
            ("MD5", a["md5"], b["md5"], 15),
            ("CRC32", str(a["crc32"]), str(b["crc32"]), 10),
            ("File Size", str(a["size"]), str(b["size"]), 10),
            ("First 256 Hash", a["first_256_sha256"], b["first_256_sha256"], 10),
            ("First 64 Bytes", a["first_64_hex"], b["first_64_hex"], 5),
            ("Folder", a["folder"], b["folder"], 2),
            ("Filename Type", a["filename_type"], b["filename_type"], 2),
            ("SOM Version", a["som_version"], b["som_version"], 2),
        ]

        score = 0
        fields = []
        for label, va, vb, weight in checks:
            same = bool(va) and va == vb
            if same:
                score += weight
            fields.append({
                "label": label,
                "a": va,
                "b": vb,
                "same": same,
                "weight": weight,
            })

        score = min(score, 100)
        return {"a": a, "b": b, "score": score, "fields": fields}

    def folder_comparison(self, folder_a: str, folder_b: str) -> dict[str, Any]:
        a = self.folder_details(folder_a)
        b = self.folder_details(folder_b)
        overlap = self.db.execute("""
            SELECT COUNT(*) AS count
            FROM (
                SELECT sha256 FROM models WHERE folder = ? AND sha256 != ''
                INTERSECT
                SELECT sha256 FROM models WHERE folder = ? AND sha256 != ''
            )
        """, (folder_a, folder_b)).fetchone()["count"]

        return {"a": a, "b": b, "shared_hashes": overlap}

    def begin(self) -> None:
        self.db.execute("BEGIN")

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.close()
