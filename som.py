from __future__ import annotations

from pathlib import Path

from binary_analysis import analyze_model_binary


def inspect_model_header(path: Path) -> dict:
    """Return the lightweight binary metadata used by the model scanner.

    The binary analysis module was renamed during the 2.x forensic work;
    keep this wrapper as the scanner's stable API.
    """
    return analyze_model_binary(path)
