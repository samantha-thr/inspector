from __future__ import annotations

import hashlib
import math
import zlib
from pathlib import Path
from typing import Any

from config import HASH_CHUNK_SIZE

try:
    from PIL import Image
except Exception:
    Image = None


def file_hashes(path: Path) -> tuple[str, str, int]:
    sha = hashlib.sha256()
    md5 = hashlib.md5()
    crc = 0

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_SIZE), b""):
            sha.update(chunk)
            md5.update(chunk)
            crc = zlib.crc32(chunk, crc)

    return sha.hexdigest(), md5.hexdigest(), crc & 0xFFFFFFFF


def _blank_result(path: Path, sha256: str, md5: str, crc32: int) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "filename": path.name,
        "folder": path.parent.name,
        "extension": path.suffix.lower(),
        "size": path.stat().st_size,
        "mtime": path.stat().st_mtime,
        "sha256": sha256,
        "md5": md5,
        "crc32": crc32,
        "width": 0,
        "height": 0,
        "mode": "",
        "has_alpha": 0,
        "avg_r": 0.0,
        "avg_g": 0.0,
        "avg_b": 0.0,
        "avg_a": 255.0,
        "ahash": "",
        "analysis_status": "hash_only",
        "last_scanned": 0.0,
    }


def analyze_texture(path: Path) -> dict[str, Any]:
    sha256, md5, crc32 = file_hashes(path)
    result = _blank_result(path, sha256, md5, crc32)

    if Image is None:
        result["analysis_status"] = "pillow_not_installed"
        return result

    try:
        with Image.open(path) as img:
            img.load()
            result["width"] = int(img.width)
            result["height"] = int(img.height)
            result["mode"] = str(img.mode)
            result["has_alpha"] = 1 if ("A" in img.getbands()) else 0

            rgba = img.convert("RGBA")
            small = rgba.resize((1, 1))
            r, g, b, a = small.getpixel((0, 0))
            result["avg_r"] = float(r)
            result["avg_g"] = float(g)
            result["avg_b"] = float(b)
            result["avg_a"] = float(a)

            gray = img.convert("L").resize((8, 8))
            pixels = list(gray.getdata())
            avg = sum(pixels) / len(pixels)
            bits = ["1" if p >= avg else "0" for p in pixels]
            result["ahash"] = hex(int("".join(bits), 2))[2:].zfill(16)
            result["analysis_status"] = "ok"

    except Exception as exc:
        result["analysis_status"] = f"image_error:{type(exc).__name__}"

    return result


def hamming_hex(a: str, b: str) -> int:
    if not a or not b:
        return 64
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 64


def color_distance(row_a, row_b) -> float:
    dr = float(row_a["avg_r"]) - float(row_b["avg_r"])
    dg = float(row_a["avg_g"]) - float(row_b["avg_g"])
    db = float(row_a["avg_b"]) - float(row_b["avg_b"])
    return math.sqrt(dr * dr + dg * dg + db * db)


def texture_similarity_score(row_a, row_b) -> int:
    if row_a["sha256"] and row_a["sha256"] == row_b["sha256"]:
        return 100

    score = 0

    if row_a["width"] and row_a["width"] == row_b["width"]:
        score += 10
    if row_a["height"] and row_a["height"] == row_b["height"]:
        score += 10
    if row_a["mode"] and row_a["mode"] == row_b["mode"]:
        score += 5
    if int(row_a["has_alpha"]) == int(row_b["has_alpha"]):
        score += 5

    hdist = hamming_hex(row_a["ahash"], row_b["ahash"])
    if hdist <= 2:
        score += 35
    elif hdist <= 6:
        score += 25
    elif hdist <= 12:
        score += 15
    elif hdist <= 20:
        score += 5

    cdist = color_distance(row_a, row_b)
    if cdist <= 5:
        score += 20
    elif cdist <= 15:
        score += 12
    elif cdist <= 35:
        score += 6

    size_a = max(int(row_a["size"]), 1)
    size_b = max(int(row_b["size"]), 1)
    ratio = min(size_a, size_b) / max(size_a, size_b)
    if ratio > 0.95:
        score += 10
    elif ratio > 0.80:
        score += 5

    return min(score, 99)
