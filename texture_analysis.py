from __future__ import annotations
import hashlib, struct
from pathlib import Path
from typing import Any
from utils import file_hashes

try:
    from PIL import Image, ImageFilter, ImageStat
except Exception:
    Image = None
    ImageFilter = None
    ImageStat = None

DDS_FORMATS = {
    "DXT1": ("DXT1 / BC1", 8), "DXT2": ("DXT2", 16), "DXT3": ("DXT3 / BC2", 16),
    "DXT4": ("DXT4", 16), "DXT5": ("DXT5 / BC3", 16), "ATI1": ("ATI1 / BC4", 8),
    "BC4U": ("BC4U", 8), "BC4S": ("BC4S", 8), "ATI2": ("ATI2 / BC5", 16),
    "BC5U": ("BC5U", 16), "BC5S": ("BC5S", 16), "DX10": ("DX10 Extended", 0),
}

def parse_dds(path: Path) -> dict[str, Any]:
    result = {
        "is_dds": 0, "dds_width": 0, "dds_height": 0, "dds_mipmaps": 0,
        "dds_fourcc": "", "dds_format": "", "dds_rgb_bits": 0, "dds_has_alpha": 0,
        "dds_is_cubemap": 0, "dds_is_volume": 0, "dds_estimated_vram": 0,
        "dds_header_status": "not_dds",
    }
    try:
        data = path.read_bytes()[:148]
    except OSError:
        result["dds_header_status"] = "read_error"
        return result
    if len(data) < 128 or data[:4] != b"DDS ":
        return result
    result["is_dds"] = 1
    try:
        header = data[4:128]
        _, _, height, width, pitch_or_linear, _, mipmaps = struct.unpack_from("<7I", header, 0)
        _, pf_flags, fourcc_raw, rgb_bits, _, _, _, amask = struct.unpack_from("<II4sIIIII", header, 76)
        _, caps2, _, _, _ = struct.unpack_from("<5I", header, 104)
        fourcc = fourcc_raw.decode("ascii", errors="ignore").strip("\x00 ").strip()
        fmt, block_size = DDS_FORMATS.get(fourcc, ("", 0))
        if not fmt:
            fmt = f"Uncompressed RGB/RGBA {rgb_bits}-bit" if pf_flags & 0x40 else (f"FOURCC {fourcc}" if fourcc else "Unknown DDS pixel format")
        has_alpha = 1 if (pf_flags & 0x1 or amask != 0 or fourcc in ("DXT2", "DXT3", "DXT4", "DXT5")) else 0
        if block_size:
            estimated = max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block_size
        elif rgb_bits:
            estimated = width * height * max(1, rgb_bits // 8)
        else:
            estimated = int(pitch_or_linear or 0)
        if mipmaps and mipmaps > 1:
            estimated = int(estimated * 1.333)
        result.update({
            "dds_width": int(width), "dds_height": int(height), "dds_mipmaps": int(mipmaps or 1),
            "dds_fourcc": fourcc, "dds_format": fmt, "dds_rgb_bits": int(rgb_bits),
            "dds_has_alpha": has_alpha, "dds_is_cubemap": 1 if caps2 & 0x200 else 0,
            "dds_is_volume": 1 if caps2 & 0x200000 else 0,
            "dds_estimated_vram": int(estimated), "dds_header_status": "ok",
        })
    except Exception as exc:
        result["dds_header_status"] = f"parse_error:{type(exc).__name__}"
    return result

def average_hash(img) -> str:
    gray = img.convert("L").resize((8, 8))
    pixels = list(gray.getdata())
    avg = sum(pixels) / len(pixels)
    bits = ["1" if p >= avg else "0" for p in pixels]
    return hex(int("".join(bits), 2))[2:].zfill(16)

def color_histogram_signature(img) -> str:
    rgb = img.convert("RGB").resize((32, 32))
    bins = [0] * 64
    for r, g, b in rgb.getdata():
        bins[(r // 64) * 16 + (g // 64) * 4 + (b // 64)] += 1
    total = max(sum(bins), 1)
    quantized = [str(round((v / total) * 1000)) for v in bins]
    return hashlib.sha256(",".join(quantized).encode()).hexdigest()

def analyze_texture(path: Path) -> dict[str, Any]:
    sha256, md5, crc32 = file_hashes(path)
    dds = parse_dds(path)
    stat = path.stat()
    result = {
        "path": str(path.resolve()), "filename": path.name, "folder": path.parent.name,
        "extension": path.suffix.lower(), "size": stat.st_size, "mtime": stat.st_mtime,
        "sha256": sha256, "md5": md5, "crc32": crc32,
        "width": dds["dds_width"], "height": dds["dds_height"], "mode": "",
        "has_alpha": dds["dds_has_alpha"], "avg_r": 0.0, "avg_g": 0.0, "avg_b": 0.0,
        "avg_a": 255.0, "ahash": "", "histogram_hash": "", "alpha_coverage": 0.0,
        "edge_density": 0.0, "brightness": 0.0, "saturation": 0.0,
        "is_grayscale": 0, "is_probable_normal": 0, "analysis_status": "hash_only",
        **dds,
    }
    if Image is None:
        result["analysis_status"] = "pillow_not_installed"
        return result
    try:
        with Image.open(path) as img:
            img.load()
            result["width"] = int(img.width)
            result["height"] = int(img.height)
            result["mode"] = str(img.mode)
            result["has_alpha"] = 1 if ("A" in img.getbands() or result["dds_has_alpha"]) else 0
            rgba = img.convert("RGBA")
            r, g, b, a = rgba.resize((1, 1)).getpixel((0, 0))
            result["avg_r"], result["avg_g"], result["avg_b"], result["avg_a"] = float(r), float(g), float(b), float(a)
            result["ahash"] = average_hash(img)
            result["histogram_hash"] = color_histogram_signature(img)
            small = rgba.resize((64, 64))
            alpha_pixels = [px[3] for px in small.getdata()]
            result["alpha_coverage"] = sum(1 for x in alpha_pixels if x < 250) / max(len(alpha_pixels), 1)
            hsv = img.convert("HSV").resize((64, 64))
            hsv_data = list(hsv.getdata())
            result["brightness"] = sum(px[2] for px in hsv_data) / (255 * max(len(hsv_data), 1))
            result["saturation"] = sum(px[1] for px in hsv_data) / (255 * max(len(hsv_data), 1))
            rgb = img.convert("RGB").resize((64, 64))
            stat_rgb = ImageStat.Stat(rgb)
            means = stat_rgb.mean
            result["is_grayscale"] = 1 if max(means) - min(means) < 6 else 0
            result["is_probable_normal"] = 1 if (means[2] > means[0] + 25 and means[2] > means[1] + 10) else 0
            edges = rgb.convert("L").filter(ImageFilter.FIND_EDGES)
            edge_pixels = list(edges.getdata())
            result["edge_density"] = sum(1 for x in edge_pixels if x > 32) / max(len(edge_pixels), 1)
            result["analysis_status"] = "ok"
    except Exception as exc:
        result["analysis_status"] = f"dds_header_only:{type(exc).__name__}" if result["is_dds"] else f"image_error:{type(exc).__name__}"
    return result
