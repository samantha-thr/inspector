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
                prefix_4k_sha256 TEXT NOT NULL DEFAULT '',
                suffix_4k_sha256 TEXT NOT NULL DEFAULT '',
                middle_4k_sha256 TEXT NOT NULL DEFAULT '',
                entropy REAL NOT NULL DEFAULT 0,
                printable_ratio REAL NOT NULL DEFAULT 0,
                zero_ratio REAL NOT NULL DEFAULT 0,
                sample_strings TEXT NOT NULL DEFAULT '',
                last_scanned REAL NOT NULL DEFAULT 0
            )
        """)

        model_migrations = {
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
            "prefix_4k_sha256": "TEXT NOT NULL DEFAULT ''",
            "suffix_4k_sha256": "TEXT NOT NULL DEFAULT ''",
            "middle_4k_sha256": "TEXT NOT NULL DEFAULT ''",
            "entropy": "REAL NOT NULL DEFAULT 0",
            "printable_ratio": "REAL NOT NULL DEFAULT 0",
            "zero_ratio": "REAL NOT NULL DEFAULT 0",
            "sample_strings": "TEXT NOT NULL DEFAULT ''",
            "last_scanned": "REAL NOT NULL DEFAULT 0",
        }
        for column, definition in model_migrations.items():
            self._add_column_if_missing("models", column, definition)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS textures(
                path TEXT PRIMARY KEY,
                root TEXT NOT NULL DEFAULT '',
                relative_path TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL DEFAULT '',
                folder TEXT NOT NULL DEFAULT '',
                extension TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                mtime REAL NOT NULL DEFAULT 0,
                sha256 TEXT NOT NULL DEFAULT '',
                md5 TEXT NOT NULL DEFAULT '',
                crc32 INTEGER NOT NULL DEFAULT 0,
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                mode TEXT NOT NULL DEFAULT '',
                has_alpha INTEGER NOT NULL DEFAULT 0,
                avg_r REAL NOT NULL DEFAULT 0,
                avg_g REAL NOT NULL DEFAULT 0,
                avg_b REAL NOT NULL DEFAULT 0,
                avg_a REAL NOT NULL DEFAULT 255,
                ahash TEXT NOT NULL DEFAULT '',
                analysis_status TEXT NOT NULL DEFAULT '',
                last_scanned REAL NOT NULL DEFAULT 0
            )
        """)

        texture_migrations = {
            "root": "TEXT NOT NULL DEFAULT ''",
            "relative_path": "TEXT NOT NULL DEFAULT ''",
            "filename": "TEXT NOT NULL DEFAULT ''",
            "folder": "TEXT NOT NULL DEFAULT ''",
            "extension": "TEXT NOT NULL DEFAULT ''",
            "size": "INTEGER NOT NULL DEFAULT 0",
            "mtime": "REAL NOT NULL DEFAULT 0",
            "sha256": "TEXT NOT NULL DEFAULT ''",
            "md5": "TEXT NOT NULL DEFAULT ''",
            "crc32": "INTEGER NOT NULL DEFAULT 0",
            "width": "INTEGER NOT NULL DEFAULT 0",
            "height": "INTEGER NOT NULL DEFAULT 0",
            "mode": "TEXT NOT NULL DEFAULT ''",
            "has_alpha": "INTEGER NOT NULL DEFAULT 0",
            "avg_r": "REAL NOT NULL DEFAULT 0",
            "avg_g": "REAL NOT NULL DEFAULT 0",
            "avg_b": "REAL NOT NULL DEFAULT 0",
            "avg_a": "REAL NOT NULL DEFAULT 255",
            "ahash": "TEXT NOT NULL DEFAULT ''",
            "analysis_status": "TEXT NOT NULL DEFAULT ''",
            "last_scanned": "REAL NOT NULL DEFAULT 0",
        }
        for column, definition in texture_migrations.items():
            self._add_column_if_missing("textures", column, definition)

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
                elapsed REAL NOT NULL,
                scan_type TEXT NOT NULL DEFAULT 'model'
            )
        """)
        if "errors" not in self._columns("scan_history"):
            self.db.execute("ALTER TABLE scan_history ADD COLUMN errors INTEGER NOT NULL DEFAULT 0")
        if "scan_type" not in self._columns("scan_history"):
            self.db.execute("ALTER TABLE scan_history ADD COLUMN scan_type TEXT NOT NULL DEFAULT 'model'")

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Model indexes
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
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_prefix4k ON models(prefix_4k_sha256)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_suffix4k ON models(suffix_4k_sha256)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_models_middle4k ON models(middle_4k_sha256)")

        # Texture indexes
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_textures_sha256 ON textures(sha256)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_textures_size ON textures(size)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_textures_folder ON textures(folder)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_textures_filename ON textures(filename)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_textures_dimensions ON textures(width, height)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_textures_ahash ON textures(ahash)")

        self.db.commit()

    # -------------------------
    # Model methods
    # -------------------------
    def get_existing_map(self) -> dict[str, sqlite3.Row]:
        rows = self.db.execute("SELECT path, size, mtime FROM models").fetchall()
        return {row["path"]: row for row in rows}

    def upsert_model(self, row: dict[str, Any]) -> None:
        self.db.execute("""
            INSERT OR REPLACE INTO models(
                path, root, relative_path, filename, folder, size, mtime,
                sha256, md5, crc32, filename_type, som_version, header,
                string_count, first_64_hex, first_256_sha256, prefix_4k_sha256,
                suffix_4k_sha256, middle_4k_sha256, entropy, printable_ratio,
                zero_ratio, sample_strings, last_scanned
            ) VALUES(
                :path, :root, :relative_path, :filename, :folder, :size, :mtime,
                :sha256, :md5, :crc32, :filename_type, :som_version, :header,
                :string_count, :first_64_hex, :first_256_sha256, :prefix_4k_sha256,
                :suffix_4k_sha256, :middle_4k_sha256, :entropy, :printable_ratio,
                :zero_ratio, :sample_strings, :last_scanned
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
        scan_type: str = "model",
    ) -> None:
        finished = time.time()
        self.db.execute("""
            INSERT INTO scan_history(started, finished, root, found, scanned, skipped, errors, elapsed, scan_type)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (started, finished, root, found, scanned, skipped, errors, finished - started, scan_type))
        self.set_setting(f"last_{scan_type}_scan_root", root)
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
                SELECT sha256 FROM models WHERE sha256 != ''
                GROUP BY sha256 HAVING COUNT(*) > 1
            )
        """).fetchone()[0])

    def duplicate_count_for_hash(self, sha256: str) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM models WHERE sha256 = ? AND sha256 != ''", (sha256,)).fetchone()[0])

    def models_by_hash(self, sha256: str):
        return self.db.execute("""
            SELECT filename, folder, relative_path, path, size, filename_type, som_version, sha256
            FROM models WHERE sha256 = ? ORDER BY folder, filename
        """, (sha256,)).fetchall()

    def filename_type_counts(self) -> dict[str, int]:
        rows = self.db.execute("""
            SELECT filename_type, COUNT(*) AS count FROM models
            GROUP BY filename_type ORDER BY count DESC
        """).fetchall()
        return {row["filename_type"]: row["count"] for row in rows}

    def som_version_counts(self) -> dict[str, int]:
        rows = self.db.execute("""
            SELECT CASE WHEN som_version = '' THEN 'Unknown' ELSE som_version END AS version,
                   COUNT(*) AS count
            FROM models GROUP BY version ORDER BY count DESC
        """).fetchall()
        return {row["version"]: row["count"] for row in rows}

    def folder_counts(self, limit: int = 25):
        return self.db.execute("""
            SELECT folder, COUNT(*) AS count FROM models
            GROUP BY folder ORDER BY count DESC LIMIT ?
        """, (limit,)).fetchall()

    def folder_details(self, folder: str):
        return self.db.execute("""
            SELECT folder, COUNT(*) AS count,
                COALESCE(SUM(size), 0) AS total_size,
                COALESCE(AVG(size), 0) AS avg_size,
                COALESCE(MIN(size), 0) AS min_size,
                COALESCE(MAX(size), 0) AS max_size,
                SUM(CASE WHEN filename_type = 'Numeric Product ID' THEN 1 ELSE 0 END) AS numeric_count,
                SUM(CASE WHEN filename_type = 'Named Asset' THEN 1 ELSE 0 END) AS named_count,
                COUNT(DISTINCT sha256) AS unique_hashes,
                COALESCE(AVG(entropy), 0) AS avg_entropy,
                COALESCE(AVG(printable_ratio), 0) AS avg_printable_ratio,
                COALESCE(AVG(zero_ratio), 0) AS avg_zero_ratio
            FROM models WHERE folder = ? GROUP BY folder
        """, (folder,)).fetchone()

    def models_in_folder(self, folder: str, limit: int = 100):
        return self.db.execute("""
            SELECT filename, folder, relative_path, path, size, filename_type, som_version, sha256
            FROM models WHERE folder = ? ORDER BY filename LIMIT ?
        """, (folder, limit)).fetchall()

    def size_stats(self) -> dict[str, Any]:
        row = self.db.execute("""
            SELECT COUNT(*) AS total, COALESCE(SUM(size), 0) AS total_size,
                COALESCE(AVG(size), 0) AS avg_size,
                COALESCE(MIN(size), 0) AS min_size,
                COALESCE(MAX(size), 0) AS max_size
            FROM models
        """).fetchone()
        largest = self.db.execute("SELECT filename, folder, size, path FROM models ORDER BY size DESC LIMIT 1").fetchone()
        smallest = self.db.execute("SELECT filename, folder, size, path FROM models ORDER BY size ASC LIMIT 1").fetchone()
        return dict(row) | {"largest": largest, "smallest": smallest}

    def latest_scan(self, scan_type: str | None = None):
        if scan_type:
            return self.db.execute(
                "SELECT * FROM scan_history WHERE scan_type = ? ORDER BY id DESC LIMIT 1",
                (scan_type,),
            ).fetchone()
        return self.db.execute("SELECT * FROM scan_history ORDER BY id DESC LIMIT 1").fetchone()

    def search(self, term: str, limit: int = 100):
        like = f"%{term}%"
        return self.db.execute("""
            SELECT filename, folder, relative_path, path, size, filename_type, som_version, sha256
            FROM models
            WHERE filename LIKE ? OR folder LIKE ? OR relative_path LIKE ? OR path LIKE ? OR sha256 LIKE ?
            ORDER BY folder, filename LIMIT ?
        """, (like, like, like, like, like, limit)).fetchall()

    def get_model_by_path(self, path: str):
        return self.db.execute("SELECT * FROM models WHERE path = ?", (path,)).fetchone()

    def get_model_by_relative_or_filename(self, query: str):
        return self.db.execute("""
            SELECT * FROM models
            WHERE relative_path = ? OR filename = ? OR path = ?
            ORDER BY relative_path LIMIT 1
        """, (query, query, query)).fetchone()

    def duplicate_hashes(self, limit: int = 100):
        return self.db.execute("""
            SELECT sha256, COUNT(*) AS count, SUM(size) AS total_size
            FROM models WHERE sha256 != ''
            GROUP BY sha256 HAVING COUNT(*) > 1
            ORDER BY count DESC, total_size DESC LIMIT ?
        """, (limit,)).fetchall()

    def model_comparison_candidates(self, path: str, limit: int = 100):
        model = self.get_model_by_path(path)
        if not model:
            return []
        return self.db.execute("""
            SELECT *,
                CASE WHEN sha256 = ? AND sha256 != '' THEN 100 ELSE 0 END
              + CASE WHEN prefix_4k_sha256 = ? AND prefix_4k_sha256 != '' THEN 45 ELSE 0 END
              + CASE WHEN suffix_4k_sha256 = ? AND suffix_4k_sha256 != '' THEN 35 ELSE 0 END
              + CASE WHEN middle_4k_sha256 = ? AND middle_4k_sha256 != '' THEN 25 ELSE 0 END
              + CASE WHEN first_256_sha256 = ? AND first_256_sha256 != '' THEN 20 ELSE 0 END
              + CASE WHEN size = ? THEN 15 ELSE 0 END
              + CASE WHEN folder = ? THEN 8 ELSE 0 END
              + CASE WHEN filename_type = ? THEN 4 ELSE 0 END
              + CASE WHEN ABS(entropy - ?) < 0.05 THEN 4 ELSE 0 END
              + CASE WHEN ABS(printable_ratio - ?) < 0.01 THEN 3 ELSE 0 END
              + CASE WHEN ABS(zero_ratio - ?) < 0.01 THEN 3 ELSE 0 END
              + CASE WHEN som_version = ? AND som_version != '' THEN 3 ELSE 0 END
                AS score
            FROM models
            WHERE path != ?
              AND (
                    sha256 = ?
                 OR prefix_4k_sha256 = ?
                 OR suffix_4k_sha256 = ?
                 OR middle_4k_sha256 = ?
                 OR first_256_sha256 = ?
                 OR size = ?
                 OR folder = ?
              )
            ORDER BY score DESC, size DESC, folder, filename
            LIMIT ?
        """, (
            model["sha256"], model["prefix_4k_sha256"], model["suffix_4k_sha256"],
            model["middle_4k_sha256"], model["first_256_sha256"], model["size"],
            model["folder"], model["filename_type"], model["entropy"],
            model["printable_ratio"], model["zero_ratio"], model["som_version"],
            model["path"], model["sha256"], model["prefix_4k_sha256"],
            model["suffix_4k_sha256"], model["middle_4k_sha256"],
            model["first_256_sha256"], model["size"], model["folder"], limit
        )).fetchall()

    def compare_two_models(self, path_a: str, path_b: str) -> dict[str, Any]:
        a = self.get_model_by_path(path_a)
        b = self.get_model_by_path(path_b)
        if not a or not b:
            return {"a": a, "b": b, "score": 0, "fields": []}

        checks = [
            ("Exact SHA256", a["sha256"], b["sha256"], 100),
            ("MD5", a["md5"], b["md5"], 25),
            ("CRC32", str(a["crc32"]), str(b["crc32"]), 15),
            ("File Size", str(a["size"]), str(b["size"]), 15),
            ("Prefix 4K Hash", a["prefix_4k_sha256"], b["prefix_4k_sha256"], 35),
            ("Suffix 4K Hash", a["suffix_4k_sha256"], b["suffix_4k_sha256"], 30),
            ("Middle 4K Hash", a["middle_4k_sha256"], b["middle_4k_sha256"], 20),
            ("First 256 Hash", a["first_256_sha256"], b["first_256_sha256"], 15),
            ("First 64 Bytes", a["first_64_hex"], b["first_64_hex"], 5),
            ("Entropy", f"{a['entropy']:.4f}", f"{b['entropy']:.4f}", 5 if abs(a["entropy"] - b["entropy"]) < 0.05 else 0),
            ("Printable Ratio", f"{a['printable_ratio']:.4f}", f"{b['printable_ratio']:.4f}", 4 if abs(a["printable_ratio"] - b["printable_ratio"]) < 0.01 else 0),
            ("Zero Ratio", f"{a['zero_ratio']:.4f}", f"{b['zero_ratio']:.4f}", 4 if abs(a["zero_ratio"] - b["zero_ratio"]) < 0.01 else 0),
            ("Folder", a["folder"], b["folder"], 2),
            ("Filename Type", a["filename_type"], b["filename_type"], 2),
            ("SOM Version", a["som_version"], b["som_version"], 2),
        ]

        score = 0
        fields = []
        for label, va, vb, weight in checks:
            same = bool(va) and va == vb
            if label in ("Entropy", "Printable Ratio", "Zero Ratio"):
                same = weight > 0
            if same:
                score += weight
            fields.append({"label": label, "a": va, "b": vb, "same": same, "weight": weight})
        return {"a": a, "b": b, "score": min(score, 100), "fields": fields}

    def folder_comparison(self, folder_a: str, folder_b: str) -> dict[str, Any]:
        a = self.folder_details(folder_a)
        b = self.folder_details(folder_b)
        overlap = self.db.execute("""
            SELECT COUNT(*) AS count FROM (
                SELECT sha256 FROM models WHERE folder = ? AND sha256 != ''
                INTERSECT
                SELECT sha256 FROM models WHERE folder = ? AND sha256 != ''
            )
        """, (folder_a, folder_b)).fetchone()["count"]
        return {"a": a, "b": b, "shared_hashes": overlap}

    # -------------------------
    # Texture methods
    # -------------------------
    def get_existing_texture_map(self) -> dict[str, sqlite3.Row]:
        rows = self.db.execute("SELECT path, size, mtime FROM textures").fetchall()
        return {row["path"]: row for row in rows}

    def upsert_texture(self, row: dict[str, Any]) -> None:
        self.db.execute("""
            INSERT OR REPLACE INTO textures(
                path, root, relative_path, filename, folder, extension, size, mtime,
                sha256, md5, crc32, width, height, mode, has_alpha,
                avg_r, avg_g, avg_b, avg_a, ahash, analysis_status, last_scanned
            ) VALUES(
                :path, :root, :relative_path, :filename, :folder, :extension, :size, :mtime,
                :sha256, :md5, :crc32, :width, :height, :mode, :has_alpha,
                :avg_r, :avg_g, :avg_b, :avg_a, :ahash, :analysis_status, :last_scanned
            )
        """, row)

    def count_textures(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM textures").fetchone()[0])

    def texture_stats(self) -> dict[str, Any]:
        row = self.db.execute("""
            SELECT COUNT(*) AS total,
                COALESCE(SUM(size), 0) AS total_size,
                COALESCE(AVG(size), 0) AS avg_size,
                COUNT(DISTINCT sha256) AS unique_hashes
            FROM textures
        """).fetchone()
        return dict(row)

    def search_textures(self, term: str, limit: int = 100):
        like = f"%{term}%"
        return self.db.execute("""
            SELECT * FROM textures
            WHERE filename LIKE ? OR folder LIKE ? OR relative_path LIKE ? OR path LIKE ? OR sha256 LIKE ?
            ORDER BY folder, filename LIMIT ?
        """, (like, like, like, like, like, limit)).fetchall()

    def get_texture_by_path(self, path: str):
        return self.db.execute("SELECT * FROM textures WHERE path = ?", (path,)).fetchone()

    def get_texture_by_relative_or_filename(self, query: str):
        return self.db.execute("""
            SELECT * FROM textures
            WHERE relative_path = ? OR filename = ? OR path = ?
            ORDER BY relative_path LIMIT 1
        """, (query, query, query)).fetchone()

    def duplicate_texture_hashes(self, limit: int = 100):
        return self.db.execute("""
            SELECT sha256, COUNT(*) AS count, SUM(size) AS total_size
            FROM textures WHERE sha256 != ''
            GROUP BY sha256 HAVING COUNT(*) > 1
            ORDER BY count DESC, total_size DESC LIMIT ?
        """, (limit,)).fetchall()

    def textures_by_hash(self, sha256: str):
        return self.db.execute("""
            SELECT * FROM textures WHERE sha256 = ? ORDER BY folder, filename
        """, (sha256,)).fetchall()

    def similar_textures(self, path: str, limit: int = 100):
        tex = self.get_texture_by_path(path)
        if not tex:
            return []
        return self.db.execute("""
            SELECT *,
                CASE WHEN sha256 = ? AND sha256 != '' THEN 100 ELSE 0 END
              + CASE WHEN ahash = ? AND ahash != '' THEN 45 ELSE 0 END
              + CASE WHEN width = ? AND width != 0 THEN 10 ELSE 0 END
              + CASE WHEN height = ? AND height != 0 THEN 10 ELSE 0 END
              + CASE WHEN has_alpha = ? THEN 5 ELSE 0 END
              + CASE WHEN ABS(avg_r - ?) < 8 AND ABS(avg_g - ?) < 8 AND ABS(avg_b - ?) < 8 THEN 20 ELSE 0 END
              + CASE WHEN ABS(size - ?) < 128 THEN 5 ELSE 0 END
                AS score
            FROM textures
            WHERE path != ?
              AND (
                    sha256 = ?
                 OR ahash = ?
                 OR (width = ? AND height = ?)
                 OR ABS(avg_r - ?) < 20
                 OR ABS(avg_g - ?) < 20
                 OR ABS(avg_b - ?) < 20
              )
            ORDER BY score DESC, folder, filename
            LIMIT ?
        """, (
            tex["sha256"], tex["ahash"], tex["width"], tex["height"], tex["has_alpha"],
            tex["avg_r"], tex["avg_g"], tex["avg_b"], tex["size"], tex["path"],
            tex["sha256"], tex["ahash"], tex["width"], tex["height"],
            tex["avg_r"], tex["avg_g"], tex["avg_b"], limit
        )).fetchall()

    def begin(self) -> None:
        self.db.execute("BEGIN")

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.close()
