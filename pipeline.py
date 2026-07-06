from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Any

from analysis_engine import rebuild_evidence, rebuild_families, rebuild_links, rebuild_texture_evidence, rebuild_texture_families
from config import DEFAULT_SCAN_PATH
from intelligence_engine import rebuild_asset_intelligence
from scanners import scan_models, scan_textures


ProgressCallback = Callable[[dict], None]


@dataclass
class PipelineStep:
    key: str
    label: str
    function: Callable[..., dict]
    args: tuple = ()
    kwargs: dict | None = None


def full_analysis_steps(full_rescan: bool = False) -> list[PipelineStep]:
    return [
        PipelineStep("model_scan", "Model Scan", scan_models, (DEFAULT_SCAN_PATH, full_rescan)),
        PipelineStep("texture_scan", "Texture Scan", scan_textures, (DEFAULT_SCAN_PATH, full_rescan)),
        PipelineStep("links", "Model ↔ Texture Links", rebuild_links),
        PipelineStep("model_families", "Model Families", rebuild_families),
        PipelineStep("texture_families", "Texture Families", rebuild_texture_families),
        PipelineStep("model_evidence", "Model Evidence Pairs", rebuild_evidence),
        PipelineStep("texture_evidence", "Texture Evidence Pairs", rebuild_texture_evidence),
        PipelineStep("asset_intelligence", "Asset Intelligence", rebuild_asset_intelligence),
    ]


def run_step(step: PipelineStep, callback: ProgressCallback | None = None) -> dict[str, Any]:
    kwargs = dict(step.kwargs or {})
    if callback:
        kwargs["callback"] = callback
    started = time.time()
    result = step.function(*step.args, **kwargs)
    result.setdefault("elapsed", time.time() - started)
    result["_step_key"] = step.key
    result["_step_label"] = step.label
    return result
