from __future__ import annotations

import hashlib
import math
import struct
import zlib
from pathlib import Path
from typing import Any

from config import HASH_CHUNK_SIZE

try:
    from PIL import Image
except Exception:
    Image = None


DDS_PIXELFORMAT_FLAGS = {
    0x00000001: "ALPHAPIXELS",
    0x00000002: "ALPHA",
    0x00000004: "FOURCC",
    0x00000040: "RGB",
    0x00000200: "YUV",
    0x00020000: "LUMINANCE",
}


DDS_CAPS2_FLAGS = {
    0x00000200: "CUBEMAP",
    0x00000400: "CUBEMAP_POSITIVEX",
    0x00000800: "CUBEMAP_NEGATIVEX",
    0x00001000: "CUBEMAP_POSITIVEY",
    0x00002000: "CUBEMAP_NEGATIVEY",
    0x00004000: "CUBEMAP_POSITIVEZ",
    0x00008000: "CUBEMAP_NEGATIVEZ",
    0x00200000: "VOLUME",
}


FOURCC_FORMATS = {
    "DXT1": ("DXT1 / BC1", 8),
    "DXT2": ("DXT2", 16),
    "DXT3": ("DXT3 / BC2", 16),
    "DXT4": ("DXT4", 16),
    "DXT5": ("DXT5 / BC3", 16),
    "ATI1": ("ATI1 / BC4", 8),
    "BC4U": ("BC4U", 8),
    "BC4S": ("BC4S", 8),
    "ATI2": ("ATI2 / BC5", 16),
    "BC5U": ("BC5U", 16),
    "BC5S": ("BC5S", 16),
    "DX10": ("DX10 Extended", 0),
}


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


def parse_dds_header(path: Path) -> dict[str, Any]:
    result = {
        "is_dds": 0,
        "dds_width": 0,
        "dds_height": 0,
        "dds_mipmaps": 0,
        "dds_fourcc": "",
        "dds_format": "",
        "dds_rgb_bits": 0,
        "dds_has_alpha": 0,
        "dds_is_cubemap": 0,
        "dds_is_volume": 0,
        "dds_header_flags": "",
        "dds_caps2": "",
        "dds_estimated_vram": 0,
        "dds_header_status": "",
    }

    try:
        with path.open("rb") as f:
            data = f.read(148)
    except OSError:
        result["dds_header_status"] = "read_error"
        return result

    if len(data) < 128 or data[:4] != b"DDS ":
        result["dds_header_status"] = "not_dds"
        return result

    result["is_dds"] = 1

    try:
        header = data[4:128]
        (
            size,
            flags,
            height,
            width,
            pitch_or_linear_size,
            depth,
            mipmap_count,
        ) = struct.unpack_from("<7I", header, 0)

        pf_offset = 76
        (
            pf_size,
            pf_flags,
            pf_fourcc_raw,
            pf_rgb_bit_count,
            pf_r_mask,
            pf_g_mask,
            pf_b_mask,
            pf_a_mask,
        ) = struct.unpack_from("<II4sIIIII", header, pf_offset)

        caps_offset = 104
        caps, caps2, caps3, caps4, reserved2 = struct.unpack_from("<5I", header, caps_offset)

        fourcc = pf_fourcc_raw.decode("ascii", errors="ignore").strip("\x00 ").strip()
        format_name = ""
        block_size = 0

        if fourcc in FOURCC_FORMATS:
            format_name, block_size = FOURCC_FORMATS[fourcc]
        elif pf_flags & 0x40:
            format_name = f"Uncompressed RGB/RGBA {pf_rgb_bit_count}-bit"
        elif fourcc:
            format_name = f"FOURCC {fourcc}"
        else:
            format_name = "Unknown DDS pixel format"

        has_alpha = 1 if (pf_flags & 0x1 or pf_a_mask != 0 or fourcc in ("DXT3", "DXT5", "DXT2", "DXT4")) else 0

        if block_size:
            blocks_w = max(1, (width + 3) // 4)
            blocks_h = max(1, (height + 3) // 4)
            estimated_vram = blocks_w * blocks_h * block_size
        elif pf_rgb_bit_count:
            estimated_vram = width * height * max(1, pf_rgb_bit_count // 8)
        else:
            estimated_vram = int(pitch_or_linear_size)

        if mipmap_count and mipmap_count > 1:
            # Rough estimate for full mip chain. Good enough for comparison.
            estimated_vram = int(estimated_vram * 1.333)

        caps2_names = [name for bit, name in DDS_CAPS2_FLAGS.items() if caps2 & bit]
        pf_flag_names = [name for bit, name in DDS_PIXELFORMAT_FLAGS.items() if pf_flags & bit]

        result.update({
            "dds_width": int(width),
            "dds_height": int(height),
            "dds_mipmaps": int(mipmap_count or 1),
            "dds_fourcc": fourcc,
            "dds_format": format_name,
            "dds_rgb_bits": int(pf_rgb_bit_count),
            "dds_has_alpha": int(has_alpha),
            "dds_is_cubemap": 1 if caps2 & 0x00000200 else 0,
            "dds_is_volume": 1 if caps2 & 0x00200000 else 0,
            "dds_header_flags": ",".join(pf_flag_names),
            "dds_caps2": ",".join(caps2_names),
            "dds_estimated_vram": int(estimated_vram),
            "dds_header_status": "ok",
        })

    except Exception as exc:
        result["dds_header_status"] = f"parse_error:{type(exc).__name__}"

    return result


def _blank_result(path: Path, sha256: str, md5: str, crc32: int) -> dict[str, Any]:
    dds = parse_dds_header(path) if path.suffix.lower() == ".dds" else parse_dds_header(path)
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
        "width": dds.get("dds_width", 0),
        "height": dds.get("dds_height", 0),
        "mode": "",
        "has_alpha": dds.get("dds_has_alpha", 0),
        "avg_r": 0.0,
        "avg_g": 0.0,
        "avg_b": 0.0,
        "avg_a": 255.0,
        "ahash": "",
        "analysis_status": "hash_only",
        "last_scanned": 0.0,
        **dds,
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
            result["has_alpha"] = 1 if ("A" in img.getbands() or result.get("dds_has_alpha", 0)) else 0

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
        # DDS header data is still useful even when Pillow cannot decode the pixel data.
        if result.get("is_dds"):
            result["analysis_status"] = f"dds_header_only:{type(exc).__name__}"
        else:
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
    if row_a["dds_format"] and row_a["dds_format"] == row_b["dds_format"]:
        score += 10
    if row_a["dds_mipmaps"] and row_a["dds_mipmaps"] == row_b["dds_mipmaps"]:
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
