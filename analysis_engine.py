from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from database import Database


def clean_stem(filename: str) -> str:
    stem = Path(filename).stem.lower()
    for suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem


def texture_base_slot(filename: str) -> tuple[str, str]:
    stem = clean_stem(filename)
    m = re.match(r"^(.+?)_(\d+)$", stem)
    return (m.group(1), m.group(2)) if m else (stem, "")


def rebuild_links(callback: Callable[[dict], None] | None = None) -> dict:
    db = Database()
    started = time.time()
    db.clear_relationships()
    models = db.all_models()
    links = checked = 0
    status_counts = {}
    db.begin()
    for i, m in enumerate(models, 1):
        base = Path(m["filename"]).stem.lower()
        model_links = 0
        status = "needs_som_parse"
        if base.isdigit():
            textures = db.textures_for_pid(m["folder"], base)
            checked += len(textures)
            for t in textures:
                tb, slot = texture_base_slot(t["filename"])
                if tb == base:
                    db.add_link(m["path"], t["path"], 100, "numeric_pid", f"PID match slot {slot or '?'}")
                    links += 1; model_links += 1
            status = "linked_external_dds" if model_links else "no_texture_found"
        else:
            textures = db.textures_for_name(m["folder"], base)
            checked += len(textures)
            for t in textures:
                tb, slot = texture_base_slot(t["filename"])
                score = 0; reasons = []
                if tb == base:
                    score += 95; reasons.append("exact base match")
                elif tb.startswith(base):
                    score += 80; reasons.append("texture starts with model base")
                elif base in tb:
                    score += 60; reasons.append("model base in texture name")
                if m["folder"] == t["folder"]:
                    score += 5
                if score >= 35:
                    db.add_link(m["path"], t["path"], min(score, 100), "named_fallback", "; ".join(reasons))
                    links += 1; model_links += 1
            status = "possible_external_dds" if model_links else "likely_baked_texture"
        db.set_model_texture_status(m["path"], status, model_links, "There-aware resource naming")
        status_counts[status] = status_counts.get(status, 0) + 1
        if i % 1000 == 0:
            db.commit(); db.begin()
        if callback:
            elapsed = max(time.time() - started, .001)
            callback({"index": i, "total": len(models), "relative_path": m["relative_path"], "links": links, "status": status, "speed": i / elapsed})
    db.commit(); db.close()
    return {"models": len(models), "links": links, "checked": checked, "status_counts": status_counts, "elapsed": time.time() - started}


def rebuild_families(callback: Callable[[dict], None] | None = None) -> dict:
    db = Database()
    started = time.time()
    db.clear_families()
    methods = [
        ("exact_sha256", "sha256", 100),
        ("prefix_suffix", "prefix_4k_sha256 || ':' || suffix_4k_sha256", 85),
        ("prefix_middle", "prefix_4k_sha256 || ':' || middle_4k_sha256", 75),
    ]
    assigned = set()
    families = members = 0
    for mi, (name, expr, confidence) in enumerate(methods, 1):
        for g in db.family_groups(expr):
            group_members = [m for m in db.family_members_for(expr, g["key_value"]) if m["path"] not in assigned]
            if len(group_members) < 2:
                continue
            db.create_family(f"{name}_{families+1:05d}", name, confidence, group_members)
            for m in group_members:
                assigned.add(m["path"])
            families += 1; members += len(group_members)
            if families % 500 == 0:
                db.commit()
        if callback:
            callback({"index": mi, "total": len(methods), "method": name, "families": families, "members": members})
    db.commit(); db.close()
    return {"families": families, "members": members, "elapsed": time.time() - started}
