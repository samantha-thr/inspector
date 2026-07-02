from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from config import TEXTURE_MIN_LINK_SCORE
from database import Database


TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def clean_stem(filename: str) -> str:
    stem = Path(filename).stem.lower()

    # There texture filenames often look like:
    # 26962001_1.jpg.dds
    # Path.stem removes only ".dds", so remove the embedded image extension too.
    for suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]

    return stem


def model_base(filename: str) -> str:
    return Path(filename).stem.lower()


def texture_base_and_slot(filename: str) -> tuple[str, str]:
    stem = clean_stem(filename)
    m = re.match(r"^(?P<base>.+?)_(?P<slot>\d+)$", stem)
    if m:
        return m.group("base"), m.group("slot")
    return stem, ""


def stem_tokens(name: str) -> set[str]:
    return {t for t in TOKEN_RE.findall(clean_stem(name)) if len(t) >= 2}


def score_official_model_texture(model, texture) -> tuple[int, str, str]:
    """Fallback scoring for named/official assets.

    Official There models may have baked textures, but this still checks for
    external DDS candidates where names/folders line up.
    """
    score = 0
    reasons: list[str] = []

    mb = model_base(model["filename"])
    tb, slot = texture_base_and_slot(texture["filename"])

    if model["folder"] == texture["folder"]:
        score += 20
        reasons.append("same folder")

    if tb == mb:
        score += 75
        reasons.append("exact base-name match")
    elif tb.startswith(mb + "_") or tb.startswith(mb):
        score += 60
        reasons.append("texture base starts with model base")
    elif mb in tb:
        score += 45
        reasons.append("model base inside texture base")

    shared = stem_tokens(model["filename"]) & stem_tokens(texture["filename"])
    if shared:
        score += min(30, len(shared) * 10)
        reasons.append("shared tokens: " + ",".join(sorted(shared)[:5]))

    if slot:
        reasons.append(f"texture slot {slot}")

    if score >= 85:
        status = "linked_external_dds"
    elif score >= TEXTURE_MIN_LINK_SCORE:
        status = "possible_external_dds"
    else:
        status = "needs_som_parse"

    return min(score, 100), "; ".join(reasons), status


def rebuild_model_texture_links(progress_callback: Callable[[dict], None] | None = None) -> dict:
    """Build model→texture candidate links using There-aware rules.

    v1.7.2 fix:
    The previous version called db.begin() after DELETE statements had already
    opened SQLite's implicit transaction. This caused:
    sqlite3.OperationalError: cannot start a transaction within a transaction

    This version commits the table-clearing step before starting the batched
    insert transaction.
    """
    db = Database()
    started = time.time()

    db.clear_model_texture_links()
    db.clear_model_texture_status()
    db.commit()  # Important: close implicit transaction before db.begin().

    models = db.all_models_for_linking()
    total = len(models)

    links = 0
    status_counts: dict[str, int] = {}
    checked_candidates = 0

    db.begin()

    for index, model in enumerate(models, 1):
        mb = model_base(model["filename"])
        status = "needs_som_parse"
        model_links = 0

        if mb.isdigit():
            # Fast There product convention:
            # 26962001.model -> 26962001_1.jpg.dds, 26962001_2.jpg.dds, etc.
            textures = db.textures_for_base_prefix(mb, model["folder"], 250)
            checked_candidates += len(textures)

            for tex in textures:
                tb, slot = texture_base_and_slot(tex["filename"])

                if tb == mb:
                    score = 100
                    reason = f"numeric PID match; texture slot {slot or '?'}"
                else:
                    score = 70
                    reason = "numeric PID fallback prefix match"

                db.upsert_model_texture_link(
                    model_path=model["path"],
                    texture_path=tex["path"],
                    score=score,
                    reason=reason,
                    method="numeric_pid",
                )
                links += 1
                model_links += 1

            status = "linked_external_dds" if model_links else "no_texture_found"

        else:
            # Official/named models may be baked, but still check for matching DDS.
            textures = db.textures_for_named_model(mb, model["folder"], 250)
            checked_candidates += len(textures)

            for tex in textures:
                score, reason, link_status = score_official_model_texture(model, tex)

                if score >= TEXTURE_MIN_LINK_SCORE:
                    db.upsert_model_texture_link(
                        model_path=model["path"],
                        texture_path=tex["path"],
                        score=score,
                        reason=reason,
                        method="named_model_fallback",
                    )
                    links += 1
                    model_links += 1

                    if link_status == "linked_external_dds":
                        status = link_status
                    elif status != "linked_external_dds":
                        status = link_status

            if model_links == 0:
                status = "likely_baked_texture"

        db.upsert_model_texture_status(
            model_path=model["path"],
            status=status,
            link_count=model_links,
            note="numeric PID external DDS" if mb.isdigit() else "named/official fallback; may be baked",
        )

        status_counts[status] = status_counts.get(status, 0) + 1

        if index % 1000 == 0:
            db.commit()
            db.begin()

        if progress_callback:
            elapsed = max(time.time() - started, 0.001)
            progress_callback({
                "index": index,
                "total": total,
                "model": model["relative_path"],
                "links": links,
                "status": status,
                "speed": index / elapsed,
            })

    db.commit()
    db.close()

    return {
        "elapsed": time.time() - started,
        "models": total,
        "links": links,
        "checked": checked_candidates,
        "status_counts": status_counts,
    }


def rebuild_model_families(progress_callback: Callable[[dict], None] | None = None) -> dict:
    db = Database()
    started = time.time()

    db.clear_model_families()
    db.commit()  # Same implicit-transaction safety as relationship rebuild.

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
    total_methods = len(grouping_methods)

    db.begin()

    for method_index, (method, expression, confidence) in enumerate(grouping_methods, 1):
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

            if groups_created % 500 == 0:
                db.commit()
                db.begin()

        if progress_callback:
            elapsed = max(time.time() - started, 0.001)
            progress_callback({
                "index": method_index,
                "total": total_methods,
                "method": method,
                "families": groups_created,
                "members": members_added,
                "speed": groups_created / elapsed,
            })

    db.commit()
    db.close()

    return {
        "elapsed": time.time() - started,
        "families": groups_created,
        "members": members_added,
    }
