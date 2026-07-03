from __future__ import annotations
import re, time
from pathlib import Path
from typing import Callable
from database import Database

def clean_stem(filename: str) -> str:
    stem = Path(filename).stem.lower()
    for suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp"):
        if stem.endswith(suffix): stem = stem[:-len(suffix)]
    return stem

def texture_base_slot(filename: str) -> tuple[str, str]:
    stem = clean_stem(filename); m = re.match(r"^(.+?)_(\d+)$", stem)
    return (m.group(1), m.group(2)) if m else (stem, "")

def rebuild_links(callback: Callable[[dict], None] | None = None) -> dict:
    db = Database(); started = time.time(); db.clear_relationships()
    models = db.all_models(); links = checked = 0; status_counts = {}; db.begin()
    for i, m in enumerate(models, 1):
        base = Path(m["filename"]).stem.lower(); model_links = 0
        if base.isdigit():
            textures = db.textures_for_pid(m["folder"], base); checked += len(textures)
            for t in textures:
                tb, slot = texture_base_slot(t["filename"])
                if tb == base:
                    db.add_link(m["path"], t["path"], 100, "numeric_pid", f"PID match slot {slot or '?'}")
                    links += 1; model_links += 1
            status = "linked_external_dds" if model_links else "no_texture_found"
        else:
            textures = db.textures_for_name(m["folder"], base); checked += len(textures)
            for t in textures:
                tb, slot = texture_base_slot(t["filename"]); score = 0; reasons = []
                if tb == base: score += 95; reasons.append("exact base match")
                elif tb.startswith(base): score += 80; reasons.append("texture starts with model base")
                elif base in tb: score += 60; reasons.append("model base in texture name")
                if score >= 35:
                    db.add_link(m["path"], t["path"], min(score, 100), "named_fallback", "; ".join(reasons))
                    links += 1; model_links += 1
            status = "possible_external_dds" if model_links else "likely_baked_texture"
        db.set_model_texture_status(m["path"], status, model_links, "There-aware resource naming")
        status_counts[status] = status_counts.get(status, 0) + 1
        if i % 1000 == 0: db.commit(); db.begin()
        if callback:
            elapsed = max(time.time() - started, .001)
            callback({"index": i, "total": len(models), "relative_path": m["relative_path"], "links": links, "status": status, "speed": i / elapsed})
    db.commit(); db.close()
    return {"models": len(models), "links": links, "checked": checked, "status_counts": status_counts, "elapsed": time.time() - started}

def rebuild_families(callback: Callable[[dict], None] | None = None) -> dict:
    db = Database(); started = time.time(); db.clear_families()
    methods = [("exact_sha256", "sha256", 100), ("prefix_suffix", "prefix_4k_sha256 || ':' || suffix_4k_sha256", 85), ("prefix_middle", "prefix_4k_sha256 || ':' || middle_4k_sha256", 75)]
    assigned = set(); families = members = 0
    for mi, (name, expr, confidence) in enumerate(methods, 1):
        for g in db.family_groups(expr):
            group_members = [m for m in db.family_members_for(expr, g["key_value"]) if m["path"] not in assigned]
            if len(group_members) < 2: continue
            db.create_family(f"{name}_{families+1:05d}", name, confidence, group_members)
            for m in group_members: assigned.add(m["path"])
            families += 1; members += len(group_members)
            if families % 500 == 0: db.commit()
        if callback: callback({"index": mi, "total": len(methods), "method": name, "families": families, "members": members})
    db.commit(); db.close()
    return {"families": families, "members": members, "elapsed": time.time() - started}

def rebuild_texture_families(callback: Callable[[dict], None] | None = None) -> dict:
    db = Database(); started = time.time(); db.clear_texture_families()
    methods = [("exact_sha256", "sha256", 100), ("perceptual_ahash", "ahash", 80), ("color_histogram", "histogram_hash", 70)]
    assigned = set(); families = members = 0
    for mi, (name, expr, confidence) in enumerate(methods, 1):
        for g in db.texture_family_groups(expr):
            group_members = [t for t in db.texture_family_members_for(expr, g["key_value"]) if t["path"] not in assigned]
            if len(group_members) < 2: continue
            db.create_texture_family(f"{name}_{families+1:05d}", name, confidence, group_members)
            for t in group_members: assigned.add(t["path"])
            families += 1; members += len(group_members)
            if families % 500 == 0: db.commit()
        if callback: callback({"index": mi, "total": len(methods), "method": name, "families": families, "members": members})
    db.commit(); db.close()
    return {"texture_families": families, "texture_members": members, "elapsed": time.time() - started}

def score_pair(a, b, links_a, links_b) -> tuple[int, int, int, int, str, str]:
    reasons = []
    binary = 0
    if a["sha256"] and a["sha256"] == b["sha256"]: binary += 100; reasons.append("exact model SHA")
    if a["prefix_4k_sha256"] and a["prefix_4k_sha256"] == b["prefix_4k_sha256"]: binary += 30; reasons.append("same prefix")
    if a["suffix_4k_sha256"] and a["suffix_4k_sha256"] == b["suffix_4k_sha256"]: binary += 30; reasons.append("same suffix")
    if a["middle_4k_sha256"] and a["middle_4k_sha256"] == b["middle_4k_sha256"]: binary += 20; reasons.append("same middle")
    if abs((a["size"] or 0) - (b["size"] or 0)) < 64: binary += 10; reasons.append("near same size")
    binary = min(binary, 100)
    string = 0
    if a["string_fingerprint"] and a["string_fingerprint"] == b["string_fingerprint"]:
        string = 30; reasons.append("same string fingerprint")
    tex_a = {x["texture_sha256"] for x in links_a if x["texture_sha256"]}
    tex_b = {x["texture_sha256"] for x in links_b if x["texture_sha256"]}
    texture = 0
    if tex_a and tex_b:
        inter = len(tex_a & tex_b); union = len(tex_a | tex_b)
        texture = round((inter / max(union, 1)) * 100)
        if inter: reasons.append(f"shared textures {inter}/{union}")
    elif links_a or links_b:
        texture = 5
    overall = min(100, round(binary * 0.65 + texture * 0.25 + string * 0.10))
    if overall >= 90: etype = "likely_same_or_reexport"
    elif overall >= 75: etype = "likely_derivative"
    elif texture >= 60: etype = "shared_texture_set"
    elif overall >= 55: etype = "possible_relationship"
    else: etype = "weak"
    return overall, binary, texture, string, etype, "; ".join(reasons)

def rebuild_evidence(callback: Callable[[dict], None] | None = None, max_family_size: int = 75) -> dict:
    db = Database(); started = time.time(); db.clear_evidence()
    families = db.families(100000); pairs = 0; added = 0
    db.begin()
    for fi, fam in enumerate(families, 1):
        members = db.family_members(fam["id"], max_family_size)
        link_cache = {m["path"]: db.links_for_model(m["path"], 200) for m in members}
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                overall, binary, texture, string, etype, reasons = score_pair(a, b, link_cache[a["path"]], link_cache[b["path"]])
                pairs += 1
                if overall >= 55:
                    db.add_evidence(a["path"], b["path"], overall, binary, texture, string, etype, reasons)
                    added += 1
        if fi % 100 == 0: db.commit(); db.begin()
        if callback:
            elapsed = max(time.time() - started, .001)
            callback({"index": fi, "total": len(families), "relative_path": fam["name"], "links": added, "speed": fi / elapsed})
    db.commit(); db.close()
    return {"families_checked": len(families), "pairs_checked": pairs, "evidence_pairs": added, "elapsed": time.time() - started}
