from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Callable

from database import Database
from texture_rules import combined_texture_weight, texture_base_slot


def clean_stem(filename: str) -> str:
    stem = Path(filename).stem.lower()
    for suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
    return stem


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

        if base.isdigit():
            textures = db.textures_for_pid(m["folder"], base)
            checked += len(textures)
            for t in textures:
                tb, slot = texture_base_slot(t["filename"])
                if tb == base:
                    db.add_link(m["path"], t["path"], 100, "numeric_pid", f"PID match slot {slot or '?'}")
                    links += 1
                    model_links += 1
            status = "linked_external_dds" if model_links else "no_texture_found"
        else:
            textures = db.textures_for_name(m["folder"], base)
            checked += len(textures)
            for t in textures:
                tb, slot = texture_base_slot(t["filename"])
                score = 0
                reasons = []
                if tb == base:
                    score += 95
                    reasons.append("exact base match")
                elif tb.startswith(base):
                    score += 80
                    reasons.append("texture starts with model base")
                elif base in tb:
                    score += 60
                    reasons.append("model base in texture name")
                if score >= 35:
                    db.add_link(m["path"], t["path"], min(score, 100), "named_fallback", "; ".join(reasons))
                    links += 1
                    model_links += 1
            status = "possible_external_dds" if model_links else "likely_baked_texture"

        db.set_model_texture_status(m["path"], status, model_links, "There-aware resource naming")
        status_counts[status] = status_counts.get(status, 0) + 1

        if i % 1000 == 0:
            db.commit()
            db.begin()

        if callback:
            elapsed = max(time.time() - started, .001)
            callback({"index": i, "total": len(models), "relative_path": m["relative_path"], "links": links, "status": status, "speed": i / elapsed})

    db.commit()
    db.close()
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
            families += 1
            members += len(group_members)
            if families % 500 == 0:
                db.commit()
        if callback:
            callback({"index": mi, "total": len(methods), "method": name, "families": families, "members": members})

    db.commit()
    db.close()
    return {"families": families, "members": members, "elapsed": time.time() - started}


def rebuild_texture_families(callback: Callable[[dict], None] | None = None) -> dict:
    db = Database()
    started = time.time()
    db.clear_texture_families()
    methods = [
        ("exact_sha256", "sha256", 100),
        ("perceptual_ahash", "ahash", 80),
        ("color_histogram", "histogram_hash", 70),
    ]
    assigned = set()
    families = members = 0

    for mi, (name, expr, confidence) in enumerate(methods, 1):
        for g in db.texture_family_groups(expr):
            group_members = [t for t in db.texture_family_members_for(expr, g["key_value"]) if t["path"] not in assigned]
            if len(group_members) < 2:
                continue
            db.create_texture_family(f"{name}_{families+1:05d}", name, confidence, group_members)
            for t in group_members:
                assigned.add(t["path"])
            families += 1
            members += len(group_members)
            if families % 500 == 0:
                db.commit()
        if callback:
            callback({"index": mi, "total": len(methods), "method": name, "families": families, "members": members})

    db.commit()
    db.close()
    return {"texture_families": families, "texture_members": members, "elapsed": time.time() - started}


def score_pair(a, b, links_a, links_b) -> tuple[int, int, int, int, str, str]:
    reasons = []
    binary = 0
    if a["sha256"] and a["sha256"] == b["sha256"]:
        binary += 100
        reasons.append("exact model SHA")
    if a["prefix_4k_sha256"] and a["prefix_4k_sha256"] == b["prefix_4k_sha256"]:
        binary += 30
        reasons.append("same prefix")
    if a["suffix_4k_sha256"] and a["suffix_4k_sha256"] == b["suffix_4k_sha256"]:
        binary += 30
        reasons.append("same suffix")
    if a["middle_4k_sha256"] and a["middle_4k_sha256"] == b["middle_4k_sha256"]:
        binary += 20
        reasons.append("same middle")
    if abs((a["size"] or 0) - (b["size"] or 0)) < 64:
        binary += 10
        reasons.append("near same size")
    binary = min(binary, 100)

    string = 30 if a["string_fingerprint"] and a["string_fingerprint"] == b["string_fingerprint"] else 0
    if string:
        reasons.append("same string fingerprint")

    tex_a = {x["texture_sha256"] for x in links_a if x["texture_sha256"]}
    tex_b = {x["texture_sha256"] for x in links_b if x["texture_sha256"]}
    texture = 0
    if tex_a and tex_b:
        inter = len(tex_a & tex_b)
        union = len(tex_a | tex_b)
        texture = round((inter / max(union, 1)) * 100)
        if inter:
            reasons.append(f"shared textures {inter}/{union}")
    elif links_a or links_b:
        texture = 5

    overall = min(100, round(binary * 0.65 + texture * 0.25 + string * 0.10))
    if overall >= 90:
        etype = "likely_same_or_reexport"
    elif overall >= 75:
        etype = "likely_derivative"
    elif texture >= 60:
        etype = "shared_texture_set"
    elif overall >= 55:
        etype = "possible_relationship"
    else:
        etype = "weak"

    return overall, binary, texture, string, etype, "; ".join(reasons)


def rebuild_evidence(callback: Callable[[dict], None] | None = None, max_family_size: int = 75) -> dict:
    db = Database()
    started = time.time()
    db.clear_evidence()
    families = db.families(100000)
    pairs = added = 0
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
        if fi % 100 == 0:
            db.commit()
            db.begin()
        if callback:
            elapsed = max(time.time() - started, .001)
            callback({"index": fi, "total": len(families), "relative_path": fam["name"], "links": added, "speed": fi / elapsed})

    db.commit()
    db.close()
    return {"families_checked": len(families), "pairs_checked": pairs, "evidence_pairs": added, "elapsed": time.time() - started}


def hex_hamming(a: str, b: str) -> int:
    if not a or not b:
        return 64
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 64


def texture_pair_score(a, b):
    reasons = []

    exact = 100 if a["sha256"] and a["sha256"] == b["sha256"] else 0
    if exact:
        reasons.append("exact texture SHA")

    hdist = hex_hamming(a["ahash"], b["ahash"])
    perceptual = max(0, round(100 * (1 - hdist / 64))) if a["ahash"] and b["ahash"] else 0
    if perceptual >= 95:
        reasons.append("near-identical perceptual hash")
    elif perceptual >= 85:
        reasons.append("similar perceptual hash")

    histogram = 100 if a["histogram_hash"] and a["histogram_hash"] == b["histogram_hash"] else 0
    if histogram:
        reasons.append("same color histogram")

    color_dist = math.sqrt(
        (float(a["avg_r"] or 0) - float(b["avg_r"] or 0)) ** 2
        + (float(a["avg_g"] or 0) - float(b["avg_g"] or 0)) ** 2
        + (float(a["avg_b"] or 0) - float(b["avg_b"] or 0)) ** 2
    )
    color = max(0, round(100 - (color_dist / 441.7) * 100))
    if color >= 95:
        reasons.append("same average color")
    elif color >= 85:
        reasons.append("similar average color")

    alpha_diff = abs(float(a["alpha_coverage"] or 0) - float(b["alpha_coverage"] or 0))
    alpha = max(0, round(100 - alpha_diff * 100))
    if alpha >= 98 and (a["has_alpha"] or b["has_alpha"]):
        reasons.append("same alpha coverage")

    fmt = 100 if (a["dds_format"] and a["dds_format"] == b["dds_format"]) else 0
    if fmt:
        reasons.append("same DDS format")

    size_score = 0
    aw, ah = a["width"] or a["dds_width"], a["height"] or a["dds_height"]
    bw, bh = b["width"] or b["dds_width"], b["height"] or b["dds_height"]
    if aw and ah and bw and bh:
        if aw == bw and ah == bh:
            size_score = 100
            reasons.append("same resolution")
        elif abs((aw / ah) - (bw / bh)) < 0.02:
            size_score = 70
            reasons.append("same aspect ratio")

    if exact:
        raw_overall = 100
        etype = "identical_texture"
    else:
        raw_overall = round(perceptual * 0.32 + histogram * 0.25 + color * 0.18 + alpha * 0.10 + fmt * 0.05 + size_score * 0.10)
        brightness_delta = abs(float(a["brightness"] or 0) - float(b["brightness"] or 0))
        saturation_delta = abs(float(a["saturation"] or 0) - float(b["saturation"] or 0))

        if histogram >= 100 and perceptual < 95:
            etype = "likely_recolor_or_adjustment"
            reasons.append("histogram match with perceptual difference")
        elif perceptual >= 92 and color >= 90:
            etype = "near_identical_texture"
        elif perceptual >= 80 and (brightness_delta > .08 or saturation_delta > .08):
            etype = "likely_brightness_or_saturation_variant"
            reasons.append("brightness/saturation differs")
        elif raw_overall >= 75:
            etype = "likely_derived_texture"
        elif raw_overall >= 60:
            etype = "possible_texture_relationship"
        else:
            etype = "weak"

    rule = combined_texture_weight(a, b)
    overall = min(raw_overall, int(rule["max_score"]))
    if rule["weight"] < 1.0:
        overall = min(overall, round(raw_overall * float(rule["weight"])))
        reasons.append(f"downweighted: {rule['role_a']['role']} / {rule['role_b']['role']}")

    if rule["exclude_from_suspicion"]:
        etype = "downweighted_common_or_template_texture"

    if rule["role_a"]["role"] == "buggy_body_template" and rule["role_b"]["role"] == "buggy_body_template" and raw_overall >= 60:
        # bg _1 body templates should remain high-value even though bg contains other false-positive layers.
        overall = raw_overall
        etype = "buggy_body_texture_match" if raw_overall < 95 else "near_identical_buggy_body_texture"
        reasons.append("bg _1 body textures kept high-value")

    return overall, exact, perceptual, histogram, color, alpha, fmt, size_score, etype, "; ".join(reasons)


def rebuild_texture_evidence(callback: Callable[[dict], None] | None = None, max_group_size: int = 200) -> dict:
    """Build texture-only evidence directly from texture fingerprints.

    v2.6 adds common/base/template down-weighting, including bg _2/_3 buggy
    window layers and flat/single-color textures.
    """
    db = Database()
    started = time.time()
    db.clear_texture_evidence()

    groups = db.texture_evidence_candidate_groups()
    checked = added = skipped_downweighted = 0
    seen_pairs = set()
    role_skips: dict[str, int] = {}

    db.begin()

    for gi, group in enumerate(groups, 1):
        members = db.texture_members_for_group(group["expr"], group["key_value"], max_group_size)

        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                pair_key = tuple(sorted((a["path"], b["path"])))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                overall, exact, perceptual, histogram, color, alpha, fmt, size, etype, reasons = texture_pair_score(a, b)
                checked += 1

                if overall >= 55:
                    db.add_texture_evidence(a["path"], b["path"], overall, exact, perceptual, histogram, color, alpha, fmt, size, etype, reasons)
                    added += 1
                else:
                    if "downweighted" in reasons:
                        skipped_downweighted += 1
                        role_skips[etype] = role_skips.get(etype, 0) + 1

        if gi % 100 == 0:
            db.commit()
            db.begin()

        if callback:
            elapsed = max(time.time() - started, .001)
            callback({"index": gi, "total": len(groups), "relative_path": f"{group['method']} {group['count']} textures", "links": added, "speed": gi / elapsed})

    db.commit()
    db.close()
    return {
        "candidate_groups": len(groups),
        "pairs_checked": checked,
        "texture_evidence_pairs": added,
        "downweighted_skipped": skipped_downweighted,
        "role_skips": role_skips,
        "elapsed": time.time() - started,
    }
