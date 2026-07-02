from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterable

from config import (
    TEXTURE_MIN_LINK_SCORE,
    TEXTURE_NEARBY_NAME_SCORE,
    TEXTURE_NUMERIC_TOKEN_SCORE,
    TEXTURE_SAME_FOLDER_SCORE,
    TEXTURE_SHARED_TOKEN_SCORE,
)
from database import Database


TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def stem_tokens(name: str) -> set[str]:
    stem = Path(name).stem.lower()
    # DDS files often look like "123_1.png.dds"; strip intermediate image extensions.
    for suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return {t for t in TOKEN_RE.findall(stem) if len(t) >= 2}


def numeric_tokens(tokens: Iterable[str]) -> set[str]:
    return {t for t in tokens if t.isdigit() and len(t) >= 4}


def score_model_texture(model, texture) -> tuple[int, str]:
    score = 0
    reasons: list[str] = []

    model_tokens = stem_tokens(model["filename"])
    texture_tokens = stem_tokens(texture["filename"])
    shared = model_tokens & texture_tokens

    model_nums = numeric_tokens(model_tokens)
    texture_nums = numeric_tokens(texture_tokens)
    shared_nums = model_nums & texture_nums

    if model["folder"] == texture["folder"]:
        score += TEXTURE_SAME_FOLDER_SCORE
        reasons.append("same folder")

    if model["filename"].split(".")[0].lower() in texture["filename"].lower():
        score += TEXTURE_NEARBY_NAME_SCORE
        reasons.append("model stem inside texture name")

    if shared:
        token_score = min(len(shared) * TEXTURE_SHARED_TOKEN_SCORE, 45)
        score += token_score
        reasons.append("shared tokens: " + ",".join(sorted(shared)[:5]))

    if shared_nums:
        score += TEXTURE_NUMERIC_TOKEN_SCORE
        reasons.append("shared numeric id: " + ",".join(sorted(shared_nums)[:3]))

    if model["relative_path"].split("\\")[0:1] == texture["relative_path"].split("\\")[0:1]:
        # Mild boost for same top-level resource group.
        score += 5
        reasons.append("same top-level resource group")

    return min(score, 100), "; ".join(reasons)


def rebuild_model_texture_links(limit_textures_per_folder: int = 5000) -> dict:
    """Infer model→texture links from folder/name proximity.

    This does not claim a texture is actually used by a model. It creates candidate
    relationships for review until SOM texture references are decoded.
    """
    db = Database()
    started = time.time()
    db.clear_model_texture_links()

    folders = [row["folder"] for row in db.folder_counts(100000)]
    links = 0
    checked = 0

    for folder in folders:
        models = db.models_in_folder(folder, 100000)
        textures = db.textures_in_folder(folder, limit_textures_per_folder)

        if not models or not textures:
            continue

        for model in models:
            candidates = []
            for texture in textures:
                checked += 1
                score, reason = score_model_texture(model, texture)
                if score >= TEXTURE_MIN_LINK_SCORE:
                    candidates.append((score, texture, reason))

            candidates.sort(key=lambda x: x[0], reverse=True)
            for score, texture, reason in candidates[:25]:
                db.upsert_model_texture_link(
                    model_path=model["path"],
                    texture_path=texture["path"],
                    score=score,
                    reason=reason,
                    method="folder_name_heuristic",
                )
                links += 1

    db.commit()
    db.close()
    return {
        "elapsed": time.time() - started,
        "folders": len(folders),
        "links": links,
        "checked": checked,
    }


def rebuild_model_families() -> dict:
    """Build simple model families from exact hash and high-value binary fingerprints."""
    db = Database()
    started = time.time()
    db.clear_model_families()

    family_id = 1
    groups_created = 0
    members_added = 0

    grouping_methods = [
        ("exact_sha256", "sha256", 100),
        ("same_prefix_suffix", "prefix_4k_sha256 || ':' || suffix_4k_sha256", 85),
        ("same_prefix_middle", "prefix_4k_sha256 || ':' || middle_4k_sha256", 75),
        ("same_size_prefix", "CAST(size AS TEXT) || ':' || prefix_4k_sha256", 65),
    ]

    assigned_paths: set[str] = set()

    for method, expression, confidence in grouping_methods:
        rows = db.family_candidate_groups(expression)
        for group in rows:
            members = db.family_members_for_expression(expression, group["key_value"])
            members = [m for m in members if m["path"] not in assigned_paths]
            if len(members) < 2:
                continue

            fam_name = f"{method}_{family_id:05d}"
            db.create_model_family(fam_name, method, confidence, len(members))
            current_family_id = db.last_insert_id()

            for m in members:
                db.add_model_family_member(current_family_id, m["path"], confidence)
                assigned_paths.add(m["path"])
                members_added += 1

            family_id += 1
            groups_created += 1

    db.commit()
    db.close()

    return {
        "elapsed": time.time() - started,
        "families": groups_created,
        "members": members_added,
    }
