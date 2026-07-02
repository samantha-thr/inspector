from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from pathlib import Path

PRINTABLE_RE = re.compile(rb"[\x20-\x7e]{4,}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest() if data else ""


def byte_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    total = len(data)
    counts = Counter(data)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log2(p)
    return entropy


def ratio(data: bytes, predicate) -> float:
    if not data:
        return 0.0
    return sum(1 for b in data if predicate(b)) / len(data)


def extract_strings(data: bytes, limit: int = 25) -> tuple[int, str]:
    matches = PRINTABLE_RE.findall(data)
    sample = []
    for m in matches[:limit]:
        sample.append(m.decode("ascii", errors="ignore").strip())
    return len(matches), "\n".join(s for s in sample if s)


def analyze_model_binary(path: Path) -> dict:
    try:
        data = path.read_bytes()
    except OSError:
        return {}

    size = len(data)
    prefix_256 = data[:256]
    prefix_4k = data[:4096]
    suffix_4k = data[-4096:] if data else b""
    if size > 8192:
        mid = size // 2
        middle_4k = data[max(0, mid - 2048):min(size, mid + 2048)]
    else:
        middle_4k = b""

    string_count, sample_strings = extract_strings(data)
    header = "SOM" if data.startswith(b"SOM") or b"SOM" in data[:256] else ""

    return {
        "header": header,
        "string_count": string_count,
        "sample_strings": sample_strings,
        "first_64_hex": data[:64].hex(" "),
        "first_256_sha256": sha256_bytes(prefix_256),
        "prefix_4k_sha256": sha256_bytes(prefix_4k),
        "middle_4k_sha256": sha256_bytes(middle_4k),
        "suffix_4k_sha256": sha256_bytes(suffix_4k),
        "entropy": byte_entropy(data),
        "printable_ratio": ratio(data, lambda b: 32 <= b <= 126),
        "zero_ratio": ratio(data, lambda b: b == 0),
        "som_version": "",
    }
