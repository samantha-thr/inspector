from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from pathlib import Path

from config import PREFIX_HASH_SIZE, PREFIX_LARGE_HASH_SIZE, SAMPLE_STRING_LIMIT, SUFFIX_HASH_SIZE

PRINTABLE_RE = re.compile(rb"[\x20-\x7e]{4,}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest() if data else ""


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest() if data else ""


def byte_entropy(data: bytes) -> float:
    if not data:
        return 0.0

    counts = Counter(data)
    total = len(data)
    entropy = 0.0

    for count in counts.values():
        p = count / total
        entropy -= p * math.log2(p)

    return entropy


def printable_ratio(data: bytes) -> float:
    if not data:
        return 0.0

    printable = sum(1 for b in data if 32 <= b <= 126)
    return printable / len(data)


def zero_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    return data.count(0) / len(data)


def extract_ascii_strings(data: bytes, limit: int = SAMPLE_STRING_LIMIT) -> list[str]:
    strings: list[str] = []
    for match in PRINTABLE_RE.finditer(data):
        text = match.group(0).decode("ascii", errors="ignore").strip()
        if text:
            strings.append(text)
        if len(strings) >= limit:
            break
    return strings


def analyze_binary(path: Path) -> dict:
    try:
        data = path.read_bytes()
    except OSError:
        return {
            "entropy": 0.0,
            "printable_ratio": 0.0,
            "zero_ratio": 0.0,
            "prefix_256_sha256": "",
            "prefix_4k_sha256": "",
            "suffix_4k_sha256": "",
            "middle_4k_sha256": "",
            "full_strings_count": 0,
            "sample_strings": "",
            "first_64_hex": "",
            "first_256_sha256": "",
            "header": "",
            "som_version": "",
        }

    size = len(data)
    prefix_256 = data[:PREFIX_HASH_SIZE]
    prefix_4k = data[:PREFIX_LARGE_HASH_SIZE]
    suffix_4k = data[-SUFFIX_HASH_SIZE:] if data else b""

    if size > PREFIX_LARGE_HASH_SIZE * 2:
        mid = size // 2
        middle_4k = data[max(0, mid - 2048): min(size, mid + 2048)]
    else:
        middle_4k = b""

    strings = extract_ascii_strings(data, SAMPLE_STRING_LIMIT)
    all_string_count = len(PRINTABLE_RE.findall(data))

    header = "SOM" if data.startswith(b"SOM") or any("SOM" in s for s in strings[:10]) else ""
    som_version = ""

    for s in strings[:25]:
        low = s.lower()
        if "version" in low:
            digits = "".join(ch for ch in s if ch.isdigit())
            if digits:
                som_version = digits
                break

    return {
        "entropy": byte_entropy(data),
        "printable_ratio": printable_ratio(data),
        "zero_ratio": zero_ratio(data),
        "prefix_256_sha256": sha256_bytes(prefix_256),
        "prefix_4k_sha256": sha256_bytes(prefix_4k),
        "suffix_4k_sha256": sha256_bytes(suffix_4k),
        "middle_4k_sha256": sha256_bytes(middle_4k),
        "full_strings_count": all_string_count,
        "sample_strings": "\\n".join(strings),
        "first_64_hex": data[:64].hex(" "),
        "first_256_sha256": sha256_bytes(prefix_256),
        "header": header,
        "som_version": som_version,
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
    return "\\n".join(lines)
