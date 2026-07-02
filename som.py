from __future__ import annotations

from pathlib import Path

from binary_analysis import analyze_binary, extract_ascii_strings, hex_preview


def inspect_model_header(path: Path) -> dict:
    return analyze_binary(path)
