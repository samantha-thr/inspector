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
from there_model_decoder import ThereModelDecodeError, decode_model, export_obj, inspect_model

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

    def export_obj(self, model_path: Path, output_path: Path, textures: list[Path]) -> dict:
        raise NotImplementedError


class NativeThereModelDecoder(GeometryDecoder):
    name = "there_som_v10"

    def can_decode(self, model_path: Path) -> bool:
        try:
            with model_path.open("rb") as f:
                header = f.read(8)
            return len(header) >= 8 and header[:4] == b"SOM " and int.from_bytes(header[4:8], "big") == 10
        except OSError:
            return False

    def export_obj(self, model_path: Path, output_path: Path, textures: list[Path]) -> dict:
        decoded = decode_model(model_path)
        return export_obj(decoded, output_path, textures)

    def inspect(self, model_path: Path) -> dict:
        return inspect_model(model_path)


class SidecarGeometryDecoder(GeometryDecoder):
    name = "sidecar_interchange"

    def find_sidecar(self, model_path: Path) -> Path | None:
        for ext in (".obj", ".glb", ".gltf"):
            p = model_path.with_suffix(ext)
            if p.exists():
                return p
        return None

    def can_decode(self, model_path: Path) -> bool:
        return self.find_sidecar(model_path) is not None

    def export_obj(self, model_path: Path, output_path: Path, textures: list[Path]) -> dict:
        source = self.find_sidecar(model_path)
        if source is None or source.suffix.lower() != ".obj":
            raise RuntimeError("Sidecar decoder requires an OBJ sidecar for direct OBJ conversion.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output_path)
        mtl = source.with_suffix(".mtl")
        if mtl.exists():
            shutil.copy2(mtl, output_path.with_suffix(".mtl"))
        return {"source": str(source), "output": str(output_path), "copied": True}


# Native decoding is preferred over sidecars.
DECODERS: list[GeometryDecoder] = [NativeThereModelDecoder(), SidecarGeometryDecoder()]


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
    decoder = active_decoder(Path(sample_model)) if sample_model else NativeThereModelDecoder()
    notes = [
        "Native SOM v10 geometry decoding is enabled.",
        "OBJ can be generated directly without Blender.",
        "BLEND / GLB / GLTF use Blender background mode with explicit Blender Z-up axis handling.",
        "LOD components are consolidated to one Blender object per actual LOD; UV0, normals, material groups, collision geometry, nodes and LOD distances are decoded.",
        "Texture assignment is best-effort: Inspector-linked DDS files are preferred over embedded placeholder paths.",
    ]
    if not blender:
        notes.append("Blender was not detected, so BLEND/GLB/GLTF conversion will be unavailable until Blender is installed or BLENDER_EXE is set.")

    return ConversionReadiness(
        blender_found=bool(blender),
        blender_path=blender,
        geometry_decoder_available=decoder is not None,
        geometry_decoder_name=decoder.name if decoder else "none",
        supported_outputs=SUPPORTED_OUTPUTS,
        notes=notes,
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
                p = Path(link["texture_path"])
                if p.exists():
                    result.append(p)
        return result
    finally:
        db.close()


def inspect_conversion_source(model_path: str | Path) -> dict:
    model_path = Path(model_path).resolve()
    decoder = active_decoder(model_path)
    if decoder is None:
        return {"success": False, "decoder": "none", "message": "No decoder supports this model."}

    if isinstance(decoder, NativeThereModelDecoder):
        try:
            result = decoder.inspect(model_path)
            result.update({"success": True, "decoder": decoder.name})
            return result
        except Exception as exc:
            return {
                "success": False,
                "decoder": decoder.name,
                "message": f"{type(exc).__name__}: {exc}",
            }

    return {
        "success": True,
        "decoder": decoder.name,
        "message": "Sidecar conversion source is available.",
    }


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

    status = "prepared" if decoder else "unsupported_model"
    if not decoder:
        notes.append("No available decoder supports this .model file.")

    if output_format in ("blend", "gltf", "glb") and not blender:
        status = "blender_not_found"
        notes.append("Blender executable was not found. Install Blender or set BLENDER_EXE.")

    job = ConversionJob(
        source_model=str(model_path),
        relative_path=rel,
        output_format=output_format,
        output_path=str(target),
        status=status,
        decoder=decoder.name if decoder else "none",
        blender_path=blender,
        linked_textures=[str(x) for x in _linked_textures(model_path)],
        notes=notes,
    )

    job_file = JOBS_DIR / f"{model_path.stem}_{int(job.created)}.json"
    job_file.write_text(json.dumps(asdict(job), indent=2), encoding="utf-8")
    return job


def prepare_folder_jobs(
    folder: str | Path,
    output_format: str = "blend",
    recursive: bool = True,
    limit: int = 0,
) -> dict:
    folder = Path(folder)
    if not folder.exists():
        raise FileNotFoundError(folder)

    files = sorted(folder.rglob("*.model") if recursive else folder.glob("*.model"))
    if limit > 0:
        files = files[:limit]

    prepared = unsupported = errors = 0

    for path in files:
        try:
            job = prepare_conversion_job(path, output_format)
            prepared += 1
            unsupported += int(job.status == "unsupported_model")
        except Exception:
            errors += 1

    return {
        "models_found": len(files),
        "jobs_prepared": prepared,
        "unsupported_models": unsupported,
        "errors": errors,
    }


def _write_blender_bridge_script(
    source_obj: Path,
    output_path: Path,
    output_format: str,
    lod_distances: list[float] | None = None,
) -> Path:
    """Create Blender bridge script with clean There-style hierarchy."""
    _ensure_dirs()
    script = JOBS_DIR / f"blender_bridge_{int(time.time() * 1000)}.py"
    lod_distances = list(lod_distances or [])

    code = "\n".join([
        "import bpy",
        "from pathlib import Path",
        f"source = Path({str(source_obj)!r})",
        f"out = Path({str(output_path)!r})",
        f"fmt = {output_format!r}",
        f"lod_distances = {lod_distances!r}",
        "",
        "bpy.ops.object.select_all(action='SELECT')",
        "bpy.ops.object.delete(use_global=False)",
        "",
        "# Decoded OBJ is already Blender Y-forward / Z-up.",
        "try:",
        "    bpy.ops.wm.obj_import(filepath=str(source), forward_axis='Y', up_axis='Z')",
        "except Exception:",
        "    bpy.ops.import_scene.obj(filepath=str(source), axis_forward='Y', axis_up='Z')",
        "",
        "imported = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']",
        "",
        "# Dedicated collection keeps the converted model clean in the Outliner.",
        "model_collection = bpy.data.collections.get('ThereModel')",
        "if model_collection is None:",
        "    model_collection = bpy.data.collections.new('ThereModel')",
        "    bpy.context.scene.collection.children.link(model_collection)",
        "",
        "master = bpy.data.objects.get('master')",
        "if master is None:",
        "    master = bpy.data.objects.new('master', None)",
        "if master not in model_collection.objects[:]:",
        "    model_collection.objects.link(master)",
        "",
        "for col in list(master.users_collection):",
        "    if col is not model_collection:",
        "        col.objects.unlink(master)",
        "",
        "for i, distance in enumerate(lod_distances):",
        "    master[f'LOD{i}'] = float(distance)",
        "",
        "for obj in imported:",
        "    base = obj.name.split('.')[0]",
        "    if base.startswith('LOD'):",
        "        digits = ''.join(ch for ch in base[3:] if ch.isdigit())",
        "        if digits:",
        "            idx = int(digits)",
        "            obj.name = f'LOD{idx}'",
        "            if idx < len(lod_distances):",
        "                obj['LOD_DISTANCE'] = float(lod_distances[idx])",
        "    elif base == 'COL':",
        "        obj.name = 'COL'",
        "",
        "    world = obj.matrix_world.copy()",
        "    obj.parent = master",
        "    obj.matrix_world = world",
        "    obj.rotation_euler = (0.0, 0.0, 0.0)",
        "",
        "    # Unlink every imported object from its import/default collection and",
        "    # keep it only in ThereModel.",
        "    if obj not in model_collection.objects[:]:",
        "        model_collection.objects.link(obj)",
        "    for col in list(obj.users_collection):",
        "        if col is not model_collection:",
        "            col.objects.unlink(obj)",
        "",
        "# Remove empty import/default collections.",
        "for col in list(bpy.data.collections):",
        "    if col is model_collection:",
        "        continue",
        "    if len(col.objects) == 0 and len(col.children) == 0:",
        "        try:",
        "            bpy.data.collections.remove(col)",
        "        except Exception:",
        "            pass",
        "",
        "# Final hierarchy safety.",
        "for obj in model_collection.objects:",
        "    if obj is not master and obj.parent is None:",
        "        obj.parent = master",
        "",
        "out.parent.mkdir(parents=True, exist_ok=True)",
        "if fmt == 'blend':",
        "    bpy.ops.wm.save_as_mainfile(filepath=str(out))",
        "elif fmt == 'glb':",
        "    bpy.ops.export_scene.gltf(filepath=str(out), export_format='GLB')",
        "elif fmt == 'gltf':",
        "    bpy.ops.export_scene.gltf(filepath=str(out), export_format='GLTF_SEPARATE')",
        "else:",
        "    raise RuntimeError('Unsupported Blender output format: ' + fmt)",
    ])

    script.write_text(code, encoding="utf-8")
    return script


def execute_conversion_job(job: ConversionJob) -> dict:
    _ensure_dirs()
    model_path = Path(job.source_model)
    decoder = active_decoder(model_path)

    if decoder is None:
        return {
            "success": False,
            "status": "unsupported_model",
            "message": "No decoder supports this .model file.",
        }

    target = Path(job.output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    textures = [Path(p) for p in job.linked_textures if Path(p).exists()]

    # Direct OBJ path
    if job.output_format == "obj":
        try:
            result = decoder.export_obj(model_path, target, textures)
            return {
                "success": target.exists(),
                "status": "complete" if target.exists() else "failed",
                "output": str(target),
                "decoder": decoder.name,
                **result,
            }
        except Exception as exc:
            return {
                "success": False,
                "status": "decode_failed",
                "decoder": decoder.name,
                "message": f"{type(exc).__name__}: {exc}",
            }

    # BLEND / GLB / GLTF: decode to OBJ then let Blender transcode it.
    blender = job.blender_path or find_blender()
    if not blender:
        return {
            "success": False,
            "status": "blender_not_found",
            "message": "Blender executable not found.",
        }

    temp_obj = target.with_suffix(".decoder.obj")

    try:
        decode_result = decoder.export_obj(model_path, temp_obj, textures)
    except Exception as exc:
        return {
            "success": False,
            "status": "decode_failed",
            "decoder": decoder.name,
            "message": f"{type(exc).__name__}: {exc}",
        }

    lod_distances = []
    try:
        decoded_for_metadata = decode_model(model_path)
        lod_distances = [float(lod.distance) for lod in decoded_for_metadata.lods]
    except Exception:
        pass

    script = _write_blender_bridge_script(
        temp_obj,
        target,
        job.output_format,
        lod_distances=lod_distances,
    )
    proc = subprocess.run(
        [blender, "--background", "--python", str(script)],
        capture_output=True,
        text=True,
    )

    log = LOG_DIR / f"{model_path.stem}_{int(time.time())}.log"
    log.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")

    success = proc.returncode == 0 and target.exists()

    return {
        "success": success,
        "status": "complete" if success else "blender_failed",
        "output": str(target),
        "log": str(log),
        "returncode": proc.returncode,
        "decoder": decoder.name,
        "decoded_obj": str(temp_obj),
        **decode_result,
    }


def conversion_jobs(limit: int = 100) -> list[dict]:
    _ensure_dirs()
    rows = []
    for p in sorted(
        JOBS_DIR.glob("*.json"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )[:limit]:
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
            row["job_file"] = str(p)
            rows.append(row)
        except Exception:
            continue
    return rows
