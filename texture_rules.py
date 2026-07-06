from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SLOT_RE = re.compile(r"^(?P<base>.+?)_(?P<slot>\d+)$")


def clean_stem(filename: str) -> str:
    stem = Path(filename).stem.lower()
    # There texture files often look like 123456_1.jpg.dds.
    for suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem


def texture_base_slot(filename: str) -> tuple[str, str]:
    stem = clean_stem(filename)
    m = SLOT_RE.match(stem)
    if not m:
        return stem, ""
    return m.group("base"), m.group("slot")


def is_numeric_product_texture(filename: str) -> bool:
    base, _slot = texture_base_slot(filename)
    return base.isdigit()


def is_probably_flat_texture(t: Any) -> bool:
    """Detect single-color or near-single-color textures that create false positives."""
    edge = float(t["edge_density"] or 0)
    saturation = float(t["saturation"] or 0)
    alpha = float(t["alpha_coverage"] or 0)
    brightness = float(t["brightness"] or 0)

    # True flat windows, masks, and simple material swatches generally have very low edges.
    if edge <= 0.006:
        return True

    # Low-edge, low-saturation grays/blacks/whites are usually utility/base materials.
    if edge <= 0.018 and saturation <= 0.08:
        return True

    # Mostly transparent overlays often match too broadly.
    if alpha >= 0.90 and edge <= 0.03:
        return True

    # Very dark or very bright low-detail textures are commonly masks/solid layers.
    if edge <= 0.012 and (brightness <= 0.04 or brightness >= 0.96):
        return True

    return False


def classify_texture_role(t: Any) -> dict[str, Any]:
    """Return a texture role and evidence behavior.

    Roles are intentionally conservative. They do not delete records; they only help
    evidence scoring down-weight common/base/template textures.
    """
    filename = t["filename"]
    folder = (t["folder"] or "").lower()
    base, slot = texture_base_slot(filename)
    numeric_base = base.isdigit()
    flat = is_probably_flat_texture(t)
    flags: list[str] = []

    # Known There convention supplied by Brian:
    # bg contains buggy models; _1 is body template; _2/_3 are window layers.
    if folder == "bg" and slot in {"2", "3"}:
        return {
            "role": "buggy_window_layer",
            "weight": 0.05,
            "max_score": 25,
            "exclude_from_suspicion": True,
            "flags": "bg_window_layer,false_positive_prone,downweighted",
            "reason": f"bg buggy window layer slot _{slot}",
        }

    if folder == "bg" and slot == "1":
        return {
            "role": "buggy_body_template",
            "weight": 1.0,
            "max_score": 100,
            "exclude_from_suspicion": False,
            "flags": "bg_body_template,high_value_texture",
            "reason": "bg buggy body texture slot _1",
        }

    if flat:
        return {
            "role": "common_flat_or_mask_texture",
            "weight": 0.15,
            "max_score": 40,
            "exclude_from_suspicion": True,
            "flags": "flat_or_mask,false_positive_prone,downweighted",
            "reason": "flat/low-detail texture likely common layer, mask, or material swatch",
        }

    # Named textures are usually tied to named/official/template assets rather than PID products.
    if not numeric_base:
        return {
            "role": "named_template_or_official_texture",
            "weight": 0.40,
            "max_score": 55,
            "exclude_from_suspicion": True,
            "flags": "named_template_or_official,downweighted",
            "reason": "named texture treated as likely template/base resource",
        }

    # Player PID _1 textures are usually the most valuable artwork for comparison.
    if numeric_base and slot == "1":
        return {
            "role": "pid_primary_texture",
            "weight": 1.0,
            "max_score": 100,
            "exclude_from_suspicion": False,
            "flags": "pid_primary_texture,high_value_texture",
            "reason": "numeric PID primary texture slot _1",
        }

    # Other PID slots may still matter, but are more likely to be overlays/windows/masks.
    if numeric_base and slot:
        return {
            "role": "pid_secondary_texture",
            "weight": 0.65,
            "max_score": 80,
            "exclude_from_suspicion": False,
            "flags": "pid_secondary_texture,moderate_value_texture",
            "reason": f"numeric PID secondary texture slot _{slot}",
        }

    return {
        "role": "unknown_texture",
        "weight": 0.75,
        "max_score": 80,
        "exclude_from_suspicion": False,
        "flags": "unknown_texture",
        "reason": "unclassified texture",
    }


def combined_texture_weight(a: Any, b: Any) -> dict[str, Any]:
    ra = classify_texture_role(a)
    rb = classify_texture_role(b)
    weight = min(float(ra["weight"]), float(rb["weight"]))
    max_score = min(int(ra["max_score"]), int(rb["max_score"]))
    exclude = bool(ra["exclude_from_suspicion"] or rb["exclude_from_suspicion"])
    return {
        "weight": weight,
        "max_score": max_score,
        "exclude_from_suspicion": exclude,
        "role_a": ra,
        "role_b": rb,
    }
