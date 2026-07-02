from __future__ import annotations

import hashlib
import re
from pathlib import Path


PRINTABLE_RE = re.compile(rb"[\x20-\x7e]{4,}")


def extract_ascii_strings(data: bytes, limit: int = 1000) -> list[str]:
    strings: list[str] = []
    for match in PRINTABLE_RE.finditer(data):
        try:
            strings.append(match.group(0).decode("ascii", errors="ignore"))
        except UnicodeDecodeError:
            continue
        if len(strings) >= limit:
            break
    return strings


def inspect_model_header(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            data = f.read(4096)
    except OSError:
        return {
            "header": "",
            "som_version": "",
            "string_count": 0,
            "strings": [],
            "first_64_hex": "",
            "first_256_sha256": "",
        }

    strings = extract_ascii_strings(data)
    header = "SOM" if data.startswith(b"SOM") or any("SOM" in s for s in strings[:10]) else ""
    som_version = ""

    for s in strings[:20]:
        low = s.lower()
        if "version" in low:
            digits = "".join(ch for ch in s if ch.isdigit())
            if digits:
                som_version = digits
                break

    return {
        "header": header,
        "som_version": som_version,
        "string_count": len(strings),
        "strings": strings,
        "first_64_hex": data[:64].hex(" "),
        "first_256_sha256": hashlib.sha256(data[:256]).hexdigest() if data else "",
    }


def hex_preview(path: Path, bytes_to_read: int = 256) -> str:
    try:
        data = path.read_bytes()[:bytes_to_read]
    except OSError:
        return ""

    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
        lines.append(f"{offset:08X}  {hex_part:<47}  {ascii_part}")
    return "\n".join(lines)
