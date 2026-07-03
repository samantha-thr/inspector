from __future__ import annotations
import hashlib, math, re
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
    return -sum((c/total) * math.log2(c/total) for c in counts.values())

def ratio(data: bytes, predicate) -> float:
    return 0.0 if not data else sum(1 for b in data if predicate(b)) / len(data)

def extract_strings(data: bytes, limit: int = 25) -> tuple[int, str, str]:
    matches = PRINTABLE_RE.findall(data)
    sample = [m.decode("ascii", errors="ignore").strip() for m in matches[:limit]]
    string_blob = "\n".join(s for s in sample if s)
    string_fingerprint = sha256_bytes("\n".join(sorted(set(sample))).encode("utf-8")) if sample else ""
    return len(matches), string_blob, string_fingerprint

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
    string_count, sample_strings, string_fingerprint = extract_strings(data)
    return {
        "header": "SOM" if data.startswith(b"SOM") or b"SOM" in data[:256] else "",
        "som_version": "",
        "string_count": string_count,
        "sample_strings": sample_strings,
        "string_fingerprint": string_fingerprint,
        "first_64_hex": data[:64].hex(" "),
        "first_256_sha256": sha256_bytes(prefix_256),
        "prefix_4k_sha256": sha256_bytes(prefix_4k),
        "middle_4k_sha256": sha256_bytes(middle_4k),
        "suffix_4k_sha256": sha256_bytes(suffix_4k),
        "entropy": byte_entropy(data),
        "printable_ratio": ratio(data, lambda b: 32 <= b <= 126),
        "zero_ratio": ratio(data, lambda b: b == 0),
    }
