from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from config import DEFAULT_SCAN_PATH
from database import Database

CONVERSION_DIR = Path("conversions")
JOBS_DIR = CONVERSION_DIR / "jobs"
OUTPUT_DIR = CONVERSION_DIR / "output"
LOG_DIR = CONVERSION_DIR / "logs"
SUPPORTED_OUTPUTS = ("obj", "gltf", "glb", "blend")

@dataclass
class ConversionJob:
    source_model: str
    relative_path: str
    output_format: str
    output_path: str
    status: str = "prepared"
    decoder: str = "none"
    blender_path: str = ""
    linked_textures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)

@dataclass
class ConversionReadiness:
    blender_found: bool
    blender_path: str
    geometry_decoder_available: bool
    geometry_decoder_name: str
    supported_outputs: tuple[str, ...]
    notes: list[str]

def _ensure_dirs() -> None:
    for p in (CONVERSION_DIR, JOBS_DIR, OUTPUT_DIR, LOG_DIR):
        p.mkdir(parents=True, exist_ok=True)

def find_blender() -> str:
    candidates: list[Path] = []
    env = os.environ.get("BLENDER_EXE")
    if env:
        candidates.append(Path(env))
    which = shutil.which("blender") or shutil.which("blender.exe")
    if which:
        candidates.append(Path(which))
    for base in [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]:
        if not base:
            continue
        root = Path(base) / "Blender Foundation"
        if root.exists():
            candidates.extend(root.glob("Blender */blender.exe"))
    candidates += [
        Path(r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe"),
        Path(r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe"),
    ]
    for candidate in candidates:
        try:
            if candidate.exists():
                return str(candidate)
        except OSError:
            pass
    return ""

class GeometryDecoder:
    name = "none"
    def can_decode(self, model_path: Path) -> bool:
        return False
    def export_interchange(self, model_path: Path, output_path: Path, textures: list[Path]) -> dict:
        raise NotImplementedError

class SidecarGeometryDecoder(GeometryDecoder):
    name = "sidecar_interchange"
    def find_sidecar(self, model_path: Path) -> Path | None:
        for ext in (".glb", ".gltf", ".obj"):
            p = model_path.with_suffix(ext)
            if p.exists():
                return p
        return None
    def can_decode(self, model_path: Path) -> bool:
        return self.find_sidecar(model_path) is not None
    def export_interchange(self, model_path: Path, output_path: Path, textures: list[Path]) -> dict:
        source = self.find_sidecar(model_path)
        if not source:
            raise RuntimeError("No matching OBJ/GLTF/GLB sidecar was found.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output_path)
        return {"source": str(source), "interchange": str(output_path), "copied": True}

DECODERS: list[GeometryDecoder] = [SidecarGeometryDecoder()]

def active_decoder(model_path: Path) -> GeometryDecoder | None:
    for decoder in DECODERS:
        try:
            if decoder.can_decode(model_path):
                return decoder
        except Exception:
            continue
    return None

def conversion_readiness(sample_model: str | Path | None = None) -> ConversionReadiness:
    blender = find_blender()
    decoder = active_decoder(Path(sample_model)) if sample_model else None
    return ConversionReadiness(
        bool(blender), blender, decoder is not None, decoder.name if decoder else "none", SUPPORTED_OUTPUTS,
        [
            "Native There.com .model geometry decoding is not implemented yet.",
            "2.7.3 installs the conversion pipeline and Blender bridge so a native decoder can plug in cleanly.",
            "A same-name OBJ/GLTF/GLB sidecar can be used now to test the pipeline end-to-end.",
        ],
    )

def _linked_textures(model_path: Path) -> list[Path]:
    db = Database()
    try:
        row = db.model_by_query(str(model_path)) or db.model_by_query(model_path.name)
        if not row:
            return []
        result = []
        for link in db.links_for_model(row["path"], 500):
            if "texture_path" in link.keys() and link["texture_path"]:
                result.append(Path(link["texture_path"]))
        return result
    finally:
        db.close()

def prepare_conversion_job(model_path: str | Path, output_format: str = "blend") -> ConversionJob:
    _ensure_dirs()
    model_path = Path(model_path).resolve()
    if not model_path.exists():
        raise FileNotFoundError(model_path)
    if model_path.suffix.lower() != ".model":
        raise ValueError("Source must be a .model file")
    output_format = output_format.lower().lstrip(".")
    if output_format not in SUPPORTED_OUTPUTS:
        raise ValueError(f"Unsupported output format: {output_format}")
    try:
        rel = str(model_path.relative_to(Path(DEFAULT_SCAN_PATH)))
    except Exception:
        rel = model_path.name
    target = OUTPUT_DIR / Path(rel).parent / f"{model_path.stem}.{output_format}"
    decoder = active_decoder(model_path)
    blender = find_blender()
    notes = []
    status = "prepared" if decoder else "waiting_for_geometry_decoder"
    if not decoder:
        notes.append("No native There.com .model geometry decoder is available yet.")
    if output_format == "blend" and not blender:
        notes.append("Blender executable was not found. Install Blender or set BLENDER_EXE.")
    job = ConversionJob(
        source_model=str(model_path), relative_path=rel, output_format=output_format,
        output_path=str(target), status=status, decoder=decoder.name if decoder else "none",
        blender_path=blender, linked_textures=[str(x) for x in _linked_textures(model_path)], notes=notes,
    )
    job_file = JOBS_DIR / f"{model_path.stem}_{int(job.created)}.json"
    job_file.write_text(json.dumps(asdict(job), indent=2), encoding="utf-8")
    return job

def prepare_folder_jobs(folder: str | Path, output_format: str = "blend", recursive: bool = True, limit: int = 0) -> dict:
    folder = Path(folder)
    if not folder.exists():
        raise FileNotFoundError(folder)
    files = sorted(folder.rglob("*.model") if recursive else folder.glob("*.model"))
    if limit > 0:
        files = files[:limit]
    prepared = waiting = errors = 0
    for path in files:
        try:
            job = prepare_conversion_job(path, output_format)
            prepared += 1
            waiting += int(job.status != "prepared")
        except Exception:
            errors += 1
    return {"models_found": len(files), "jobs_prepared": prepared, "waiting_for_decoder": waiting, "errors": errors}

def _write_blender_bridge_script(source_path: Path, output_blend: Path) -> Path:
    _ensure_dirs()
    script = JOBS_DIR / f"blender_bridge_{int(time.time()*1000)}.py"
    code = "\n".join([
        "import bpy",
        "from pathlib import Path",
        f"source = Path({str(source_path)!r})",
        f"out = Path({str(output_blend)!r})",
        "bpy.ops.object.select_all(action='SELECT')",
        "bpy.ops.object.delete(use_global=False)",
        "ext = source.suffix.lower()",
        "if ext == '.obj':",
        "    try:",
        "        bpy.ops.wm.obj_import(filepath=str(source))",
        "    except Exception:",
        "        bpy.ops.import_scene.obj(filepath=str(source))",
        "elif ext in ('.gltf', '.glb'):",
        "    bpy.ops.import_scene.gltf(filepath=str(source))",
        "else:",
        "    raise RuntimeError('Unsupported interchange format: ' + ext)",
        "out.parent.mkdir(parents=True, exist_ok=True)",
        "bpy.ops.wm.save_as_mainfile(filepath=str(out))",
    ])
    script.write_text(code, encoding="utf-8")
    return script

def execute_conversion_job(job: ConversionJob) -> dict:
    model_path = Path(job.source_model)
    decoder = active_decoder(model_path)
    if decoder is None:
        return {"success": False, "status": "waiting_for_geometry_decoder", "message": "Native .model geometry decoding is not implemented yet."}
    target = Path(job.output_path)
    textures = [Path(p) for p in job.linked_textures]
    if job.output_format == "blend":
        blender = job.blender_path or find_blender()
        if not blender:
            return {"success": False, "status": "blender_not_found", "message": "Blender executable not found."}
        sidecar = decoder.find_sidecar(model_path) if isinstance(decoder, SidecarGeometryDecoder) else None
        temp = target.with_suffix(sidecar.suffix.lower() if sidecar else ".obj")
        decoder.export_interchange(model_path, temp, textures)
        script = _write_blender_bridge_script(temp, target)
        proc = subprocess.run([blender, "--background", "--python", str(script)], capture_output=True, text=True)
        log = LOG_DIR / f"{model_path.stem}_{int(time.time())}.log"
        log.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")
        return {"success": proc.returncode == 0 and target.exists(), "status": "complete" if proc.returncode == 0 and target.exists() else "blender_failed", "output": str(target), "log": str(log), "returncode": proc.returncode}
    sidecar = decoder.find_sidecar(model_path) if isinstance(decoder, SidecarGeometryDecoder) else None
    desired = "." + job.output_format
    if sidecar and sidecar.suffix.lower() != desired:
        return {"success": False, "status": "format_transcode_requires_blender", "message": f"Available source is {sidecar.suffix}; requested {desired}."}
    decoder.export_interchange(model_path, target, textures)
    return {"success": target.exists(), "status": "complete" if target.exists() else "failed", "output": str(target)}

def conversion_jobs(limit: int = 100) -> list[dict]:
    _ensure_dirs()
    rows = []
    for p in sorted(JOBS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
            row["job_file"] = str(p)
            rows.append(row)
        except Exception:
            continue
    return rows
