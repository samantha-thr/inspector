from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from config import DATABASE_PATH, SCHEMA_VERSION


class Database:
    def __init__(self, path: Path = DATABASE_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.create_schema()

    def close(self):
        self.db.close()

    def commit(self):
        self.db.commit()

    def begin(self):
        self.db.execute("BEGIN")

    def set_setting(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))

    def columns(self, table):
        try:
            return {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
        except sqlite3.OperationalError:
            return set()

    def add_col(self, table, col, spec):
        if col not in self.columns(table):
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {spec}")

    def create_schema(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS models(
            path TEXT PRIMARY KEY, root TEXT, relative_path TEXT, filename TEXT, folder TEXT,
            size INTEGER, mtime REAL, sha256 TEXT, md5 TEXT, crc32 INTEGER, filename_type TEXT,
            som_version TEXT, header TEXT, string_count INTEGER, sample_strings TEXT,
            string_fingerprint TEXT, first_64_hex TEXT, first_256_sha256 TEXT,
            prefix_4k_sha256 TEXT, middle_4k_sha256 TEXT, suffix_4k_sha256 TEXT,
            entropy REAL, printable_ratio REAL, zero_ratio REAL, last_scanned REAL
        );

        CREATE TABLE IF NOT EXISTS textures(
            path TEXT PRIMARY KEY, root TEXT, relative_path TEXT, filename TEXT, folder TEXT,
            extension TEXT, size INTEGER, mtime REAL, sha256 TEXT, md5 TEXT, crc32 INTEGER,
            width INTEGER, height INTEGER, mode TEXT, has_alpha INTEGER,
            avg_r REAL, avg_g REAL, avg_b REAL, avg_a REAL, ahash TEXT, histogram_hash TEXT,
            alpha_coverage REAL, edge_density REAL, brightness REAL, saturation REAL,
            is_grayscale INTEGER, is_probable_normal INTEGER, analysis_status TEXT,
            is_dds INTEGER, dds_width INTEGER, dds_height INTEGER, dds_mipmaps INTEGER,
            dds_fourcc TEXT, dds_format TEXT, dds_rgb_bits INTEGER, dds_has_alpha INTEGER,
            dds_is_cubemap INTEGER, dds_is_volume INTEGER, dds_estimated_vram INTEGER,
            dds_header_status TEXT, last_scanned REAL
        );

        CREATE TABLE IF NOT EXISTS model_texture_links(
            model_path TEXT NOT NULL, texture_path TEXT NOT NULL, score INTEGER,
            method TEXT, reason TEXT, created REAL, PRIMARY KEY(model_path, texture_path)
        );

        CREATE TABLE IF NOT EXISTS model_texture_status(
            model_path TEXT PRIMARY KEY, status TEXT, link_count INTEGER, note TEXT, updated REAL
        );

        CREATE TABLE IF NOT EXISTS model_families(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, method TEXT, confidence INTEGER,
            member_count INTEGER, created REAL
        );

        CREATE TABLE IF NOT EXISTS model_family_members(
            family_id INTEGER, model_path TEXT, confidence INTEGER, PRIMARY KEY(family_id, model_path)
        );

        CREATE TABLE IF NOT EXISTS texture_families(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, method TEXT, confidence INTEGER,
            member_count INTEGER, created REAL
        );

        CREATE TABLE IF NOT EXISTS texture_family_members(
            family_id INTEGER, texture_path TEXT, confidence INTEGER, PRIMARY KEY(family_id, texture_path)
        );

        CREATE TABLE IF NOT EXISTS evidence_pairs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_a TEXT, model_b TEXT, overall_score INTEGER,
            binary_score INTEGER, texture_score INTEGER, string_score INTEGER,
            evidence_type TEXT, reasons TEXT, created REAL
        );

        CREATE TABLE IF NOT EXISTS texture_evidence_pairs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            texture_a TEXT, texture_b TEXT,
            overall_score INTEGER,
            exact_score INTEGER,
            perceptual_score INTEGER,
            histogram_score INTEGER,
            color_score INTEGER,
            alpha_score INTEGER,
            format_score INTEGER,
            size_score INTEGER,
            evidence_type TEXT,
            reasons TEXT,
            created REAL
        );

        CREATE TABLE IF NOT EXISTS scan_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL, finished REAL, root TEXT,
            found INTEGER, scanned INTEGER, skipped INTEGER, errors INTEGER, elapsed REAL, scan_type TEXT
        );
        """)

        # Migrations for existing v2 databases.
        for col, spec in {"string_fingerprint": "TEXT DEFAULT ''"}.items():
            self.add_col("models", col, spec)
        for col, spec in {
            "histogram_hash": "TEXT DEFAULT ''",
            "alpha_coverage": "REAL DEFAULT 0",
            "edge_density": "REAL DEFAULT 0",
            "brightness": "REAL DEFAULT 0",
            "saturation": "REAL DEFAULT 0",
            "is_grayscale": "INTEGER DEFAULT 0",
            "is_probable_normal": "INTEGER DEFAULT 0",
        }.items():
            self.add_col("textures", col, spec)

        self.db.executescript("""
        CREATE INDEX IF NOT EXISTS idx_models_folder ON models(folder);
        CREATE INDEX IF NOT EXISTS idx_models_filename ON models(filename);
        CREATE INDEX IF NOT EXISTS idx_models_sha256 ON models(sha256);
        CREATE INDEX IF NOT EXISTS idx_models_prefix ON models(prefix_4k_sha256);
        CREATE INDEX IF NOT EXISTS idx_models_suffix ON models(suffix_4k_sha256);
        CREATE INDEX IF NOT EXISTS idx_models_string_fp ON models(string_fingerprint);

        CREATE INDEX IF NOT EXISTS idx_textures_folder ON textures(folder);
        CREATE INDEX IF NOT EXISTS idx_textures_filename ON textures(filename);
        CREATE INDEX IF NOT EXISTS idx_textures_sha256 ON textures(sha256);
        CREATE INDEX IF NOT EXISTS idx_textures_ahash ON textures(ahash);
        CREATE INDEX IF NOT EXISTS idx_textures_hist ON textures(histogram_hash);
        CREATE INDEX IF NOT EXISTS idx_textures_format ON textures(dds_format);

        CREATE INDEX IF NOT EXISTS idx_links_model ON model_texture_links(model_path);
        CREATE INDEX IF NOT EXISTS idx_links_texture ON model_texture_links(texture_path);

        CREATE INDEX IF NOT EXISTS idx_evidence_score ON evidence_pairs(overall_score);
        CREATE INDEX IF NOT EXISTS idx_evidence_a ON evidence_pairs(model_a);
        CREATE INDEX IF NOT EXISTS idx_evidence_b ON evidence_pairs(model_b);

        CREATE INDEX IF NOT EXISTS idx_texture_evidence_score ON texture_evidence_pairs(overall_score);
        CREATE INDEX IF NOT EXISTS idx_texture_evidence_a ON texture_evidence_pairs(texture_a);
        CREATE INDEX IF NOT EXISTS idx_texture_evidence_b ON texture_evidence_pairs(texture_b);
        CREATE INDEX IF NOT EXISTS idx_texture_evidence_type ON texture_evidence_pairs(evidence_type);
        """)
        self.set_setting("schema_version", str(SCHEMA_VERSION))
        self.commit()

    # scan
    def add_scan_history(self, root, started, found, scanned, skipped, errors, scan_type):
        finished = time.time()
        self.db.execute(
            "INSERT INTO scan_history(started,finished,root,found,scanned,skipped,errors,elapsed,scan_type) VALUES(?,?,?,?,?,?,?,?,?)",
            (started, finished, root, found, scanned, skipped, errors, finished - started, scan_type),
        )
        self.commit()

    def existing_models(self):
        return {r["path"]: r for r in self.db.execute("SELECT path,size,mtime FROM models")}

    def existing_textures(self):
        return {r["path"]: r for r in self.db.execute("SELECT path,size,mtime FROM textures")}

    def upsert_model(self, row: dict[str, Any]):
        self.db.execute("""
            INSERT OR REPLACE INTO models(path,root,relative_path,filename,folder,size,mtime,sha256,md5,crc32,filename_type,
            som_version,header,string_count,sample_strings,string_fingerprint,first_64_hex,first_256_sha256,prefix_4k_sha256,middle_4k_sha256,suffix_4k_sha256,
            entropy,printable_ratio,zero_ratio,last_scanned)
            VALUES(:path,:root,:relative_path,:filename,:folder,:size,:mtime,:sha256,:md5,:crc32,:filename_type,
            :som_version,:header,:string_count,:sample_strings,:string_fingerprint,:first_64_hex,:first_256_sha256,:prefix_4k_sha256,:middle_4k_sha256,:suffix_4k_sha256,
            :entropy,:printable_ratio,:zero_ratio,:last_scanned)
        """, row)

    def upsert_texture(self, row: dict[str, Any]):
        self.db.execute("""
            INSERT OR REPLACE INTO textures(path,root,relative_path,filename,folder,extension,size,mtime,sha256,md5,crc32,
            width,height,mode,has_alpha,avg_r,avg_g,avg_b,avg_a,ahash,histogram_hash,alpha_coverage,edge_density,brightness,saturation,is_grayscale,is_probable_normal,
            analysis_status,is_dds,dds_width,dds_height,dds_mipmaps,dds_fourcc,dds_format,dds_rgb_bits,dds_has_alpha,dds_is_cubemap,dds_is_volume,dds_estimated_vram,dds_header_status,last_scanned)
            VALUES(:path,:root,:relative_path,:filename,:folder,:extension,:size,:mtime,:sha256,:md5,:crc32,
            :width,:height,:mode,:has_alpha,:avg_r,:avg_g,:avg_b,:avg_a,:ahash,:histogram_hash,:alpha_coverage,:edge_density,:brightness,:saturation,:is_grayscale,:is_probable_normal,
            :analysis_status,:is_dds,:dds_width,:dds_height,:dds_mipmaps,:dds_fourcc,:dds_format,:dds_rgb_bits,:dds_has_alpha,:dds_is_cubemap,:dds_is_volume,:dds_estimated_vram,:dds_header_status,:last_scanned)
        """, row)

    # stats
    def count_models(self):
        return self.db.execute("SELECT COUNT(*) FROM models").fetchone()[0]

    def count_textures(self):
        return self.db.execute("SELECT COUNT(*) FROM textures").fetchone()[0]

    def model_stats(self):
        return dict(self.db.execute("SELECT COUNT(*) total, COALESCE(SUM(size),0) total_size, COALESCE(AVG(size),0) avg_size, COUNT(DISTINCT sha256) unique_hashes FROM models").fetchone())

    def texture_stats(self):
        return dict(self.db.execute("SELECT COUNT(*) total, COALESCE(SUM(size),0) total_size, COALESCE(AVG(size),0) avg_size, COUNT(DISTINCT sha256) unique_hashes, SUM(CASE WHEN is_dds=1 THEN 1 ELSE 0 END) dds_count FROM textures").fetchone())

    def relationship_stats(self):
        links = dict(self.db.execute("SELECT COUNT(*) links, COUNT(DISTINCT model_path) linked_models, COUNT(DISTINCT texture_path) linked_textures FROM model_texture_links").fetchone())
        mf = dict(self.db.execute("SELECT COUNT(*) families, COALESCE(SUM(member_count),0) family_members FROM model_families").fetchone())
        tf = dict(self.db.execute("SELECT COUNT(*) texture_families, COALESCE(SUM(member_count),0) texture_family_members FROM texture_families").fetchone())
        ev = dict(self.db.execute("SELECT COUNT(*) evidence_pairs FROM evidence_pairs").fetchone())
        tev = dict(self.db.execute("SELECT COUNT(*) texture_evidence_pairs FROM texture_evidence_pairs").fetchone())
        return links | mf | tf | ev | tev

    def texture_format_counts(self):
        return self.db.execute("SELECT COALESCE(NULLIF(dds_format,''), extension) format, COUNT(*) count FROM textures GROUP BY format ORDER BY count DESC").fetchall()

    # explore/search
    def search_models(self, term, limit=100):
        like = f"%{term}%"
        return self.db.execute("SELECT * FROM models WHERE filename LIKE ? OR folder LIKE ? OR relative_path LIKE ? OR sha256 LIKE ? ORDER BY folder,filename LIMIT ?", (like, like, like, like, limit)).fetchall()

    def search_textures(self, term, limit=100):
        like = f"%{term}%"
        return self.db.execute("SELECT * FROM textures WHERE filename LIKE ? OR folder LIKE ? OR relative_path LIKE ? OR sha256 LIKE ? OR dds_format LIKE ? ORDER BY folder,filename LIMIT ?", (like, like, like, like, like, limit)).fetchall()

    def model_by_query(self, q):
        return self.db.execute("SELECT * FROM models WHERE path=? OR relative_path=? OR filename=? ORDER BY relative_path LIMIT 1", (q, q, q)).fetchone()

    def texture_by_query(self, q):
        return self.db.execute("SELECT * FROM textures WHERE path=? OR relative_path=? OR filename=? ORDER BY relative_path LIMIT 1", (q, q, q)).fetchone()

    def duplicates(self, table, limit=100):
        return self.db.execute(f"SELECT sha256, COUNT(*) count, SUM(size) total_size FROM {table} WHERE sha256!='' GROUP BY sha256 HAVING COUNT(*)>1 ORDER BY count DESC,total_size DESC LIMIT ?", (limit,)).fetchall()

    # relationships
    def clear_relationships(self):
        self.db.execute("DELETE FROM model_texture_links")
        self.db.execute("DELETE FROM model_texture_status")
        self.commit()

    def all_models(self):
        return self.db.execute("SELECT * FROM models ORDER BY folder,filename").fetchall()

    def textures_for_pid(self, folder, pid, limit=250):
        return self.db.execute("SELECT * FROM textures WHERE folder=? AND filename LIKE ? ORDER BY filename LIMIT ?", (folder, f"{pid}_%", limit)).fetchall()

    def textures_for_name(self, folder, base, limit=250):
        return self.db.execute("SELECT * FROM textures WHERE folder=? AND (filename LIKE ? OR filename LIKE ? OR filename LIKE ?) ORDER BY filename LIMIT ?", (folder, f"{base}_%", f"{base}.%", f"%{base}%", limit)).fetchall()

    def add_link(self, model_path, texture_path, score, method, reason):
        self.db.execute("INSERT OR REPLACE INTO model_texture_links(model_path,texture_path,score,method,reason,created) VALUES(?,?,?,?,?,?)", (model_path, texture_path, score, method, reason, time.time()))

    def set_model_texture_status(self, model_path, status, link_count, note):
        self.db.execute("INSERT OR REPLACE INTO model_texture_status(model_path,status,link_count,note,updated) VALUES(?,?,?,?,?)", (model_path, status, link_count, note, time.time()))

    def links_for_model(self, model_path, limit=100):
        return self.db.execute("""SELECT l.*, t.relative_path texture_relative_path, t.dds_format, t.width, t.height, t.dds_width, t.dds_height, t.sha256 texture_sha256, t.ahash
        FROM model_texture_links l JOIN textures t ON t.path=l.texture_path WHERE model_path=? ORDER BY score DESC LIMIT ?""", (model_path, limit)).fetchall()

    def links_for_texture(self, texture_path, limit=100):
        return self.db.execute("""SELECT l.*, m.relative_path model_relative_path, m.folder FROM model_texture_links l JOIN models m ON m.path=l.model_path WHERE texture_path=? ORDER BY score DESC LIMIT ?""", (texture_path, limit)).fetchall()

    # model families
    def clear_families(self):
        self.db.execute("DELETE FROM model_family_members")
        self.db.execute("DELETE FROM model_families")
        self.commit()

    def create_family(self, name, method, confidence, members):
        self.db.execute("INSERT INTO model_families(name,method,confidence,member_count,created) VALUES(?,?,?,?,?)", (name, method, confidence, len(members), time.time()))
        fid = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
        for m in members:
            self.db.execute("INSERT OR REPLACE INTO model_family_members(family_id,model_path,confidence) VALUES(?,?,?)", (fid, m["path"], confidence))

    def family_groups(self, expr):
        return self.db.execute(f"SELECT {expr} key_value, COUNT(*) count FROM models WHERE {expr}!='' GROUP BY key_value HAVING COUNT(*)>1 ORDER BY count DESC").fetchall()

    def family_members_for(self, expr, key):
        return self.db.execute(f"SELECT * FROM models WHERE {expr}=? ORDER BY folder,filename", (key,)).fetchall()

    def families(self, limit=100):
        return self.db.execute("SELECT * FROM model_families ORDER BY member_count DESC,confidence DESC LIMIT ?", (limit,)).fetchall()

    def family_members(self, fid, limit=500):
        return self.db.execute("SELECT fm.*, m.* FROM model_family_members fm JOIN models m ON m.path=fm.model_path WHERE family_id=? ORDER BY m.folder,m.filename LIMIT ?", (fid, limit)).fetchall()

    # texture families
    def clear_texture_families(self):
        self.db.execute("DELETE FROM texture_family_members")
        self.db.execute("DELETE FROM texture_families")
        self.commit()

    def texture_family_groups(self, expr):
        return self.db.execute(f"SELECT {expr} key_value, COUNT(*) count FROM textures WHERE {expr}!='' GROUP BY key_value HAVING COUNT(*)>1 ORDER BY count DESC").fetchall()

    def texture_family_members_for(self, expr, key):
        return self.db.execute(f"SELECT * FROM textures WHERE {expr}=? ORDER BY folder,filename", (key,)).fetchall()

    def create_texture_family(self, name, method, confidence, members):
        self.db.execute("INSERT INTO texture_families(name,method,confidence,member_count,created) VALUES(?,?,?,?,?)", (name, method, confidence, len(members), time.time()))
        fid = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
        for t in members:
            self.db.execute("INSERT OR REPLACE INTO texture_family_members(family_id,texture_path,confidence) VALUES(?,?,?)", (fid, t["path"], confidence))

    def texture_families(self, limit=100):
        return self.db.execute("SELECT * FROM texture_families ORDER BY member_count DESC,confidence DESC LIMIT ?", (limit,)).fetchall()

    def texture_family_members(self, fid, limit=500):
        return self.db.execute("SELECT tfm.*, t.* FROM texture_family_members tfm JOIN textures t ON t.path=tfm.texture_path WHERE family_id=? ORDER BY t.folder,t.filename LIMIT ?", (fid, limit)).fetchall()

    # model evidence
    def clear_evidence(self):
        self.db.execute("DELETE FROM evidence_pairs")
        self.commit()

    def add_evidence(self, model_a, model_b, overall, binary, texture, string, evidence_type, reasons):
        self.db.execute("INSERT INTO evidence_pairs(model_a,model_b,overall_score,binary_score,texture_score,string_score,evidence_type,reasons,created) VALUES(?,?,?,?,?,?,?,?,?)", (model_a, model_b, overall, binary, texture, string, evidence_type, reasons, time.time()))

    def top_evidence(self, limit=100):
        return self.db.execute("""SELECT e.*, a.relative_path path_a, b.relative_path path_b FROM evidence_pairs e JOIN models a ON a.path=e.model_a JOIN models b ON b.path=e.model_b ORDER BY overall_score DESC LIMIT ?""", (limit,)).fetchall()

    def evidence_for_model(self, model_path, limit=100):
        return self.db.execute("""SELECT e.*, a.relative_path path_a, b.relative_path path_b FROM evidence_pairs e JOIN models a ON a.path=e.model_a JOIN models b ON b.path=e.model_b WHERE model_a=? OR model_b=? ORDER BY overall_score DESC LIMIT ?""", (model_path, model_path, limit)).fetchall()

    # texture evidence
    def clear_texture_evidence(self):
        self.db.execute("DELETE FROM texture_evidence_pairs")
        self.commit()

    def add_texture_evidence(self, texture_a, texture_b, overall, exact, perceptual, histogram, color, alpha, fmt, size, evidence_type, reasons):
        self.db.execute("""INSERT INTO texture_evidence_pairs(texture_a,texture_b,overall_score,exact_score,perceptual_score,histogram_score,color_score,alpha_score,format_score,size_score,evidence_type,reasons,created)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (texture_a, texture_b, overall, exact, perceptual, histogram, color, alpha, fmt, size, evidence_type, reasons, time.time()))

    def top_texture_evidence(self, limit=100):
        return self.db.execute("""SELECT e.*, a.relative_path path_a, b.relative_path path_b,
            a.dds_format format_a, b.dds_format format_b, a.width width_a, a.height height_a, b.width width_b, b.height height_b
            FROM texture_evidence_pairs e JOIN textures a ON a.path=e.texture_a JOIN textures b ON b.path=e.texture_b
            ORDER BY overall_score DESC LIMIT ?""", (limit,)).fetchall()

    def texture_evidence_for_texture(self, texture_path, limit=100):
        return self.db.execute("""SELECT e.*, a.relative_path path_a, b.relative_path path_b
            FROM texture_evidence_pairs e JOIN textures a ON a.path=e.texture_a JOIN textures b ON b.path=e.texture_b
            WHERE texture_a=? OR texture_b=? ORDER BY overall_score DESC LIMIT ?""", (texture_path, texture_path, limit)).fetchall()

    def texture_evidence_candidate_groups(self):
        """Return candidate texture groups directly from texture fingerprints.

        Used by rebuild_texture_evidence(). This lets texture evidence build
        from SHA256 / perceptual hash / histogram groups without depending on
        texture_families being rebuilt first.
        """
        groups = []
        for method, expr, confidence in [
            ("exact_sha256", "sha256", 100),
            ("perceptual_ahash", "ahash", 80),
            ("color_histogram", "histogram_hash", 70),
        ]:
            rows = self.db.execute(f"""
                SELECT {expr} key_value, COUNT(*) count
                FROM textures
                WHERE {expr} IS NOT NULL AND {expr} != ''
                GROUP BY key_value
                HAVING COUNT(*) > 1
                ORDER BY count DESC
            """).fetchall()
            for row in rows:
                groups.append({
                    "method": method,
                    "expr": expr,
                    "key_value": row["key_value"],
                    "count": row["count"],
                    "confidence": confidence,
                })
        return groups

    def texture_members_for_group(self, expr, key, limit=500):
        """Return texture rows for one evidence candidate group."""
        return self.db.execute(
            f"SELECT * FROM textures WHERE {expr}=? ORDER BY folder,filename LIMIT ?",
            (key, limit),
        ).fetchall()
