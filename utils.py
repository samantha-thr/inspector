from __future__ import annotations
import hashlib
import zlib
from pathlib import Path
from config import HASH_CHUNK_SIZE

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

def safe_relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)

def format_bytes(value: int | float) -> str:
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"

def format_seconds(seconds: float) -> str:
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
