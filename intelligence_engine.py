from __future__ import annotations
import time
from collections import Counter
from typing import Callable
from database import Database


def ensure_intelligence_schema(db: Database) -> None:
    db.db.executescript("""
    CREATE TABLE IF NOT EXISTS asset_intelligence(
        asset_path TEXT PRIMARY KEY,
        asset_type TEXT,
        relative_path TEXT,
        folder TEXT,
        filename TEXT,
        classification TEXT,
        family_count INTEGER DEFAULT 0,
        linked_asset_count INTEGER DEFAULT 0,
        evidence_count INTEGER DEFAULT 0,
        max_evidence_score INTEGER DEFAULT 0,
        avg_evidence_score REAL DEFAULT 0,
        duplicate_count INTEGER DEFAULT 0,
        reuse_score INTEGER DEFAULT 0,
        suspicion_score INTEGER DEFAULT 0,
        fingerprint_score INTEGER DEFAULT 0,
        summary TEXT,
        flags TEXT,
        updated REAL
    );
    CREATE TABLE IF NOT EXISTS folder_intelligence(
        folder TEXT PRIMARY KEY,
        model_count INTEGER DEFAULT 0,
        texture_count INTEGER DEFAULT 0,
        model_family_count INTEGER DEFAULT 0,
        texture_family_count INTEGER DEFAULT 0,
        model_evidence_count INTEGER DEFAULT 0,
        texture_evidence_count INTEGER DEFAULT 0,
        avg_model_size REAL DEFAULT 0,
        avg_texture_size REAL DEFAULT 0,
        avg_suspicion_score REAL DEFAULT 0,
        top_flags TEXT,
        updated REAL
    );
    CREATE INDEX IF NOT EXISTS idx_asset_intel_type ON asset_intelligence(asset_type);
    CREATE INDEX IF NOT EXISTS idx_asset_intel_suspicion ON asset_intelligence(suspicion_score);
    CREATE INDEX IF NOT EXISTS idx_asset_intel_reuse ON asset_intelligence(reuse_score);
    CREATE INDEX IF NOT EXISTS idx_asset_intel_folder ON asset_intelligence(folder);
    CREATE INDEX IF NOT EXISTS idx_folder_intel_suspicion ON folder_intelligence(avg_suspicion_score);
    """)
    db.commit()


def clamp(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def classify_model(m) -> str:
    return "player_product_model" if m["filename_type"] == "Numeric Product ID" else "named_or_official_model"


def classify_texture(t) -> str:
    flags = []
    if t["is_probable_normal"]:
        flags.append("normal_map")
    if t["is_grayscale"]:
        flags.append("grayscale")
    if t["alpha_coverage"] and t["alpha_coverage"] > 0.01:
        flags.append("alpha_texture")
    if t["dds_format"]:
        flags.append("dds")
    return ",".join(flags) if flags else "texture"


def evidence_stats_for_model(db, path):
    return dict(db.db.execute("""
        SELECT COUNT(*) evidence_count, COALESCE(MAX(overall_score),0) max_score, COALESCE(AVG(overall_score),0) avg_score
        FROM evidence_pairs WHERE model_a=? OR model_b=?
    """, (path, path)).fetchone())


def evidence_stats_for_texture(db, path):
    return dict(db.db.execute("""
        SELECT COUNT(*) evidence_count, COALESCE(MAX(overall_score),0) max_score, COALESCE(AVG(overall_score),0) avg_score
        FROM texture_evidence_pairs WHERE texture_a=? OR texture_b=?
    """, (path, path)).fetchone())


def upsert_asset_intelligence(db, row):
    db.db.execute("""
        INSERT OR REPLACE INTO asset_intelligence(
            asset_path, asset_type, relative_path, folder, filename, classification,
            family_count, linked_asset_count, evidence_count, max_evidence_score,
            avg_evidence_score, duplicate_count, reuse_score, suspicion_score,
            fingerprint_score, summary, flags, updated
        ) VALUES(
            :asset_path, :asset_type, :relative_path, :folder, :filename, :classification,
            :family_count, :linked_asset_count, :evidence_count, :max_evidence_score,
            :avg_evidence_score, :duplicate_count, :reuse_score, :suspicion_score,
            :fingerprint_score, :summary, :flags, :updated
        )
    """, row)


def rebuild_asset_intelligence(callback: Callable[[dict], None] | None = None) -> dict:
    started = time.time()
    db = Database()
    ensure_intelligence_schema(db)
    db.db.execute("DELETE FROM asset_intelligence")
    db.db.execute("DELETE FROM folder_intelligence")
    db.commit()

    models = db.all_models()
    textures = db.db.execute("SELECT * FROM textures ORDER BY folder,filename").fetchall()
    total = len(models) + len(textures)
    model_sha_counts = {r["sha256"]: r["c"] for r in db.db.execute("SELECT sha256, COUNT(*) c FROM models WHERE sha256!='' GROUP BY sha256")}
    texture_sha_counts = {r["sha256"]: r["c"] for r in db.db.execute("SELECT sha256, COUNT(*) c FROM textures WHERE sha256!='' GROUP BY sha256")}

    db.begin()
    for i, m in enumerate(models, 1):
        ev = evidence_stats_for_model(db, m["path"])
        linked = len(db.links_for_model(m["path"], 500))
        family_count = db.db.execute("SELECT COUNT(*) FROM model_family_members WHERE model_path=?", (m["path"],)).fetchone()[0]
        dup_count = max(0, model_sha_counts.get(m["sha256"], 0) - 1)
        flags = []
        if dup_count:
            flags.append("exact_duplicate")
        if ev["max_score"] >= 90:
            flags.append("high_confidence_model_match")
        elif ev["max_score"] >= 75:
            flags.append("likely_derivative_model")
        flags.append("linked_textures" if linked else "no_external_texture_link")
        flags.append("numeric_product_id" if m["filename_type"] == "Numeric Product ID" else "named_asset")
        reuse_score = clamp(dup_count*10 + family_count*12 + linked*4 + ev["evidence_count"]*2 + ev["max_score"]*.35)
        suspicion_score = clamp(ev["max_score"]*.55 + ev["evidence_count"]*2.5 + dup_count*5 + (15 if not linked and m["filename_type"] == "Numeric Product ID" else 0))
        fingerprint_score = clamp((20 if m["sha256"] else 0)+(15 if m["prefix_4k_sha256"] else 0)+(15 if m["suffix_4k_sha256"] else 0)+(15 if m["middle_4k_sha256"] else 0)+(10 if m["string_fingerprint"] else 0)+min(25, linked*5))
        summary = f"{classify_model(m)}; evidence={ev['evidence_count']}; max_score={ev['max_score']}; families={family_count}; linked_textures={linked}; duplicates={dup_count}"
        upsert_asset_intelligence(db, {
            "asset_path":m["path"],"asset_type":"model","relative_path":m["relative_path"],"folder":m["folder"],"filename":m["filename"],"classification":classify_model(m),
            "family_count":family_count,"linked_asset_count":linked,"evidence_count":ev["evidence_count"],"max_evidence_score":ev["max_score"],"avg_evidence_score":ev["avg_score"],
            "duplicate_count":dup_count,"reuse_score":reuse_score,"suspicion_score":suspicion_score,"fingerprint_score":fingerprint_score,"summary":summary,"flags":",".join(flags),"updated":time.time(),
        })
        if i % 1000 == 0:
            db.commit(); db.begin()
            if callback:
                elapsed=max(time.time()-started,.001); callback({"index":i,"total":total,"relative_path":"models: "+m["relative_path"],"links":i,"speed":i/elapsed})
    db.commit()

    db.begin()
    for i, t in enumerate(textures, 1):
        ev = evidence_stats_for_texture(db, t["path"])
        linked = len(db.links_for_texture(t["path"], 500))
        family_count = db.db.execute("SELECT COUNT(*) FROM texture_family_members WHERE texture_path=?", (t["path"],)).fetchone()[0]
        dup_count = max(0, texture_sha_counts.get(t["sha256"], 0) - 1)
        flags=[]
        if dup_count:
            flags.append("exact_duplicate")
        if ev["max_score"] >= 90:
            flags.append("high_confidence_texture_match")
        elif ev["max_score"] >= 75:
            flags.append("likely_derived_texture")
        if linked:
            flags.append("used_by_models")
        if t["is_probable_normal"]:
            flags.append("probable_normal_map")
        if t["is_grayscale"]:
            flags.append("grayscale")
        if t["alpha_coverage"] and t["alpha_coverage"] > 0.01:
            flags.append("has_alpha")
        reuse_score=clamp(dup_count*10+family_count*14+linked*4+ev["evidence_count"]*2+ev["max_score"]*.30)
        suspicion_score=clamp(ev["max_score"]*.60+ev["evidence_count"]*2.5+dup_count*5+(8 if t["histogram_hash"] and ev["max_score"]>=80 else 0))
        fingerprint_score=clamp((20 if t["sha256"] else 0)+(15 if t["ahash"] else 0)+(15 if t["histogram_hash"] else 0)+(10 if t["dds_format"] else 0)+(10 if (t["width"] or t["dds_width"]) else 0)+(10 if t["analysis_status"]=="ok" else 0)+min(20,linked*4))
        summary=f"{classify_texture(t)}; evidence={ev['evidence_count']}; max_score={ev['max_score']}; families={family_count}; used_by={linked}; duplicates={dup_count}"
        upsert_asset_intelligence(db, {
            "asset_path":t["path"],"asset_type":"texture","relative_path":t["relative_path"],"folder":t["folder"],"filename":t["filename"],"classification":classify_texture(t),
            "family_count":family_count,"linked_asset_count":linked,"evidence_count":ev["evidence_count"],"max_evidence_score":ev["max_score"],"avg_evidence_score":ev["avg_score"],
            "duplicate_count":dup_count,"reuse_score":reuse_score,"suspicion_score":suspicion_score,"fingerprint_score":fingerprint_score,"summary":summary,"flags":",".join(flags),"updated":time.time(),
        })
        if i % 1000 == 0:
            db.commit(); db.begin()
            if callback:
                processed=len(models)+i; elapsed=max(time.time()-started,.001); callback({"index":processed,"total":total,"relative_path":"textures: "+t["relative_path"],"links":processed,"speed":processed/elapsed})
    db.commit()
    build_folder_intelligence(db)
    db.commit()
    count = db.db.execute("SELECT COUNT(*) FROM asset_intelligence").fetchone()[0]
    db.close()
    return {"models_profiled":len(models),"textures_profiled":len(textures),"intelligence_records":count,"elapsed":time.time()-started}


def build_folder_intelligence(db: Database):
    folders=set([r["folder"] for r in db.db.execute("SELECT DISTINCT folder FROM models")]) | set([r["folder"] for r in db.db.execute("SELECT DISTINCT folder FROM textures")])
    for folder in sorted(folders):
        ms=db.db.execute("SELECT COUNT(*) c, COALESCE(AVG(size),0) avg_size FROM models WHERE folder=?",(folder,)).fetchone()
        ts=db.db.execute("SELECT COUNT(*) c, COALESCE(AVG(size),0) avg_size FROM textures WHERE folder=?",(folder,)).fetchone()
        intel=db.db.execute("""SELECT COALESCE(AVG(suspicion_score),0) avg_suspicion, SUM(CASE WHEN asset_type='model' THEN evidence_count ELSE 0 END) model_evidence, SUM(CASE WHEN asset_type='texture' THEN evidence_count ELSE 0 END) texture_evidence FROM asset_intelligence WHERE folder=?""",(folder,)).fetchone()
        flags=[]
        for row in db.db.execute("SELECT flags FROM asset_intelligence WHERE folder=? AND flags!='' LIMIT 250",(folder,)):
            flags.extend([f for f in row["flags"].split(",") if f])
        top_flags=",".join(flag for flag,_ in Counter(flags).most_common(8))
        mfc=db.db.execute("SELECT COUNT(DISTINCT fm.family_id) c FROM model_family_members fm JOIN models m ON m.path=fm.model_path WHERE m.folder=?",(folder,)).fetchone()["c"]
        tfc=db.db.execute("SELECT COUNT(DISTINCT tfm.family_id) c FROM texture_family_members tfm JOIN textures t ON t.path=tfm.texture_path WHERE t.folder=?",(folder,)).fetchone()["c"]
        db.db.execute("""INSERT OR REPLACE INTO folder_intelligence(folder,model_count,texture_count,model_family_count,texture_family_count,model_evidence_count,texture_evidence_count,avg_model_size,avg_texture_size,avg_suspicion_score,top_flags,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (folder,ms["c"],ts["c"],mfc,tfc,intel["model_evidence"] or 0,intel["texture_evidence"] or 0,ms["avg_size"],ts["avg_size"],intel["avg_suspicion"] or 0,top_flags,time.time()))
