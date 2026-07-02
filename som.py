from __future__ import annotations

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
        }

    strings = extract_ascii_strings(data)
    header = ""
    som_version = ""

    joined = "\n".join(strings[:10])
    if "SOM" in joined:
        header = "SOM"

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
    }
