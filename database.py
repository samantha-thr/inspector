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

    def close(self): self.db.close()
    def commit(self): self.db.commit()
    def begin(self): self.db.execute("BEGIN")
    def set_setting(self, key: str, value: str): self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    def get_setting(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def create_schema(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS models(
            path TEXT PRIMARY KEY, root TEXT, relative_path TEXT, filename TEXT, folder TEXT,
            size INTEGER, mtime REAL, sha256 TEXT, md5 TEXT, crc32 INTEGER, filename_type TEXT,
            som_version TEXT, header TEXT, string_count INTEGER, sample_strings TEXT,
            first_64_hex TEXT, first_256_sha256 TEXT, prefix_4k_sha256 TEXT,
            middle_4k_sha256 TEXT, suffix_4k_sha256 TEXT,
            entropy REAL, printable_ratio REAL, zero_ratio REAL, last_scanned REAL
        );

        CREATE TABLE IF NOT EXISTS textures(
            path TEXT PRIMARY KEY, root TEXT, relative_path TEXT, filename TEXT, folder TEXT,
            extension TEXT, size INTEGER, mtime REAL, sha256 TEXT, md5 TEXT, crc32 INTEGER,
            width INTEGER, height INTEGER, mode TEXT, has_alpha INTEGER,
            avg_r REAL, avg_g REAL, avg_b REAL, avg_a REAL, ahash TEXT, analysis_status TEXT,
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

        CREATE TABLE IF NOT EXISTS scan_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL, finished REAL, root TEXT,
            found INTEGER, scanned INTEGER, skipped INTEGER, errors INTEGER, elapsed REAL, scan_type TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_models_folder ON models(folder);
        CREATE INDEX IF NOT EXISTS idx_models_filename ON models(filename);
        CREATE INDEX IF NOT EXISTS idx_models_sha256 ON models(sha256);
        CREATE INDEX IF NOT EXISTS idx_models_prefix ON models(prefix_4k_sha256);
        CREATE INDEX IF NOT EXISTS idx_models_suffix ON models(suffix_4k_sha256);
        CREATE INDEX IF NOT EXISTS idx_models_middle ON models(middle_4k_sha256);

        CREATE INDEX IF NOT EXISTS idx_textures_folder ON textures(folder);
        CREATE INDEX IF NOT EXISTS idx_textures_filename ON textures(filename);
        CREATE INDEX IF NOT EXISTS idx_textures_sha256 ON textures(sha256);
        CREATE INDEX IF NOT EXISTS idx_textures_ahash ON textures(ahash);
        CREATE INDEX IF NOT EXISTS idx_textures_format ON textures(dds_format);

        CREATE INDEX IF NOT EXISTS idx_links_model ON model_texture_links(model_path);
        CREATE INDEX IF NOT EXISTS idx_links_texture ON model_texture_links(texture_path);
        CREATE INDEX IF NOT EXISTS idx_status_status ON model_texture_status(status);
        CREATE INDEX IF NOT EXISTS idx_fam_member_family ON model_family_members(family_id);
        CREATE INDEX IF NOT EXISTS idx_fam_member_model ON model_family_members(model_path);
        """)
        self.set_setting("schema_version", str(SCHEMA_VERSION))
        self.commit()

    # scans
    def add_scan_history(self, root: str, started: float, found: int, scanned: int, skipped: int, errors: int, scan_type: str):
        finished = time.time()
        self.db.execute("INSERT INTO scan_history(started,finished,root,found,scanned,skipped,errors,elapsed,scan_type) VALUES(?,?,?,?,?,?,?,?,?)",
                        (started, finished, root, found, scanned, skipped, errors, finished-started, scan_type))
        self.commit()

    def existing_models(self): return {r["path"]: r for r in self.db.execute("SELECT path,size,mtime FROM models")}
    def existing_textures(self): return {r["path"]: r for r in self.db.execute("SELECT path,size,mtime FROM textures")}
    def upsert_model(self, row: dict[str, Any]):
        self.db.execute("""INSERT OR REPLACE INTO models(path,root,relative_path,filename,folder,size,mtime,sha256,md5,crc32,filename_type,
            som_version,header,string_count,sample_strings,first_64_hex,first_256_sha256,prefix_4k_sha256,middle_4k_sha256,suffix_4k_sha256,
            entropy,printable_ratio,zero_ratio,last_scanned)
            VALUES(:path,:root,:relative_path,:filename,:folder,:size,:mtime,:sha256,:md5,:crc32,:filename_type,
            :som_version,:header,:string_count,:sample_strings,:first_64_hex,:first_256_sha256,:prefix_4k_sha256,:middle_4k_sha256,:suffix_4k_sha256,
            :entropy,:printable_ratio,:zero_ratio,:last_scanned)""", row)
    def upsert_texture(self, row: dict[str, Any]):
        self.db.execute("""INSERT OR REPLACE INTO textures(path,root,relative_path,filename,folder,extension,size,mtime,sha256,md5,crc32,
            width,height,mode,has_alpha,avg_r,avg_g,avg_b,avg_a,ahash,analysis_status,is_dds,dds_width,dds_height,dds_mipmaps,
            dds_fourcc,dds_format,dds_rgb_bits,dds_has_alpha,dds_is_cubemap,dds_is_volume,dds_estimated_vram,dds_header_status,last_scanned)
            VALUES(:path,:root,:relative_path,:filename,:folder,:extension,:size,:mtime,:sha256,:md5,:crc32,
            :width,:height,:mode,:has_alpha,:avg_r,:avg_g,:avg_b,:avg_a,:ahash,:analysis_status,:is_dds,:dds_width,:dds_height,:dds_mipmaps,
            :dds_fourcc,:dds_format,:dds_rgb_bits,:dds_has_alpha,:dds_is_cubemap,:dds_is_volume,:dds_estimated_vram,:dds_header_status,:last_scanned)""", row)

    # counts/stats
    def count_models(self): return self.db.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    def count_textures(self): return self.db.execute("SELECT COUNT(*) FROM textures").fetchone()[0]
    def relationship_stats(self):
        links = dict(self.db.execute("SELECT COUNT(*) links, COUNT(DISTINCT model_path) linked_models, COUNT(DISTINCT texture_path) linked_textures FROM model_texture_links").fetchone())
        fam = dict(self.db.execute("SELECT COUNT(*) families, COALESCE(SUM(member_count),0) family_members FROM model_families").fetchone())
        status = {r["status"]: r["count"] for r in self.db.execute("SELECT status, COUNT(*) count FROM model_texture_status GROUP BY status")}
        return links | fam | {"status_counts": status}
    def latest_scan(self):
        return self.db.execute("SELECT * FROM scan_history ORDER BY id DESC LIMIT 1").fetchone()
    def texture_stats(self):
        return dict(self.db.execute("SELECT COUNT(*) total, COALESCE(SUM(size),0) total_size, COALESCE(AVG(size),0) avg_size, COUNT(DISTINCT sha256) unique_hashes, SUM(CASE WHEN is_dds=1 THEN 1 ELSE 0 END) dds_count FROM textures").fetchone())
    def model_stats(self):
        return dict(self.db.execute("SELECT COUNT(*) total, COALESCE(SUM(size),0) total_size, COALESCE(AVG(size),0) avg_size, COUNT(DISTINCT sha256) unique_hashes FROM models").fetchone())
    def texture_format_counts(self):
        return self.db.execute("SELECT COALESCE(NULLIF(dds_format,''), extension) format, COUNT(*) count FROM textures GROUP BY format ORDER BY count DESC").fetchall()
    def folder_counts(self, limit=100):
        return self.db.execute("SELECT folder, COUNT(*) count FROM models GROUP BY folder ORDER BY count DESC LIMIT ?", (limit,)).fetchall()

    # search/explore
    def search_models(self, term: str, limit=100):
        like = f"%{term}%"
        return self.db.execute("SELECT * FROM models WHERE filename LIKE ? OR folder LIKE ? OR relative_path LIKE ? OR sha256 LIKE ? ORDER BY folder,filename LIMIT ?", (like,like,like,like,limit)).fetchall()
    def search_textures(self, term: str, limit=100):
        like = f"%{term}%"
        return self.db.execute("SELECT * FROM textures WHERE filename LIKE ? OR folder LIKE ? OR relative_path LIKE ? OR sha256 LIKE ? OR dds_format LIKE ? ORDER BY folder,filename LIMIT ?", (like,like,like,like,like,limit)).fetchall()
    def model_by_query(self, q: str):
        return self.db.execute("SELECT * FROM models WHERE path=? OR relative_path=? OR filename=? ORDER BY relative_path LIMIT 1", (q,q,q)).fetchone()
    def texture_by_query(self, q: str):
        return self.db.execute("SELECT * FROM textures WHERE path=? OR relative_path=? OR filename=? ORDER BY relative_path LIMIT 1", (q,q,q)).fetchone()
    def duplicates(self, table: str, limit=100):
        return self.db.execute(f"SELECT sha256, COUNT(*) count, SUM(size) total_size FROM {table} WHERE sha256!='' GROUP BY sha256 HAVING COUNT(*)>1 ORDER BY count DESC,total_size DESC LIMIT ?", (limit,)).fetchall()
    def rows_by_hash(self, table: str, sha: str):
        return self.db.execute(f"SELECT * FROM {table} WHERE sha256=? ORDER BY folder,filename", (sha,)).fetchall()

    # relationship
    def clear_relationships(self):
        self.db.execute("DELETE FROM model_texture_links")
        self.db.execute("DELETE FROM model_texture_status")
        self.commit()
    def all_models(self):
        return self.db.execute("SELECT * FROM models ORDER BY folder,filename").fetchall()
    def textures_for_pid(self, folder: str, pid: str, limit=250):
        return self.db.execute("SELECT * FROM textures WHERE folder=? AND filename LIKE ? ORDER BY filename LIMIT ?", (folder, f"{pid}_%", limit)).fetchall()
    def textures_for_name(self, folder: str, base: str, limit=250):
        return self.db.execute("SELECT * FROM textures WHERE folder=? AND (filename LIKE ? OR filename LIKE ? OR filename LIKE ?) ORDER BY filename LIMIT ?", (folder, f"{base}_%", f"{base}.%", f"%{base}%", limit)).fetchall()
    def add_link(self, model_path, texture_path, score, method, reason):
        self.db.execute("INSERT OR REPLACE INTO model_texture_links(model_path,texture_path,score,method,reason,created) VALUES(?,?,?,?,?,?)", (model_path,texture_path,score,method,reason,time.time()))
    def set_model_texture_status(self, model_path, status, link_count, note):
        self.db.execute("INSERT OR REPLACE INTO model_texture_status(model_path,status,link_count,note,updated) VALUES(?,?,?,?,?)", (model_path,status,link_count,note,time.time()))
    def links_for_model(self, model_path: str, limit=100):
        return self.db.execute("""SELECT l.*, t.relative_path texture_relative_path, t.dds_format, t.width, t.height, t.dds_width, t.dds_height
            FROM model_texture_links l JOIN textures t ON t.path=l.texture_path WHERE model_path=? ORDER BY score DESC LIMIT ?""", (model_path, limit)).fetchall()
    def links_for_texture(self, texture_path: str, limit=100):
        return self.db.execute("""SELECT l.*, m.relative_path model_relative_path, m.folder
            FROM model_texture_links l JOIN models m ON m.path=l.model_path WHERE texture_path=? ORDER BY score DESC LIMIT ?""", (texture_path, limit)).fetchall()

    # family
    def clear_families(self):
        self.db.execute("DELETE FROM model_family_members")
        self.db.execute("DELETE FROM model_families")
        self.commit()
    def create_family(self, name, method, confidence, members):
        self.db.execute("INSERT INTO model_families(name,method,confidence,member_count,created) VALUES(?,?,?,?,?)", (name,method,confidence,len(members),time.time()))
        fid = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
        for m in members:
            self.db.execute("INSERT OR REPLACE INTO model_family_members(family_id,model_path,confidence) VALUES(?,?,?)", (fid,m["path"],confidence))
    def family_groups(self, expr: str):
        return self.db.execute(f"SELECT {expr} key_value, COUNT(*) count FROM models WHERE {expr}!='' GROUP BY key_value HAVING COUNT(*)>1 ORDER BY count DESC").fetchall()
    def family_members_for(self, expr: str, key: str):
        return self.db.execute(f"SELECT * FROM models WHERE {expr}=? ORDER BY folder,filename", (key,)).fetchall()
    def families(self, limit=100):
        return self.db.execute("SELECT * FROM model_families ORDER BY member_count DESC,confidence DESC LIMIT ?", (limit,)).fetchall()
    def family_members(self, fid: int, limit=500):
        return self.db.execute("""SELECT fm.*, m.* FROM model_family_members fm JOIN models m ON m.path=fm.model_path WHERE family_id=? ORDER BY m.folder,m.filename LIMIT ?""", (fid,limit)).fetchall()
