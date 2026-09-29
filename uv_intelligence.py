from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from there_model_decoder import decode_model


UV_CACHE_VERSION = 1
UV_CACHE_DIR = Path("cache/uv_intelligence")


def _q(value: float, places: int = 5) -> float:
    return round(float(value), places)


def _mesh_triangles(mesh) -> list[tuple[tuple[float, float], ...]]:
    """Return canonical UV0 triangles for a decoded mesh."""
    out = []
    verts = mesh.vertices
    for i in range(0, len(mesh.indices) - 2, 3):
        ids = mesh.indices[i:i + 3]
        if any(idx >= len(verts) or verts[idx].uv0 is None for idx in ids):
            continue
        tri = tuple(sorted((_q(verts[idx].uv0[0]), _q(verts[idx].uv0[1])) for idx in ids))
        out.append(tri)
    return sorted(out)


def uv_fingerprint(model_or_path, lod_index: int = 0) -> dict[str, Any]:
    """Build an artwork-independent fingerprint from decoded model UV topology.

    The digest is based on quantized UV triangle coordinates and material
    assignment, not on rendered pixels or texture artwork.
    """
    model = decode_model(model_or_path) if isinstance(model_or_path, (str, Path)) else model_or_path
    if lod_index < 0 or lod_index >= len(model.lods):
        raise ValueError(f"LOD{lod_index} is not present in {Path(model.path).name}")

    lod = model.lods[lod_index]
    material_groups: dict[int, list] = {}
    uv_vertex_count = 0
    for mesh in lod.meshes:
        tris = _mesh_triangles(mesh)
        if not tris:
            continue
        uv_vertex_count += sum(1 for v in mesh.vertices if v.uv0 is not None)
        material_groups.setdefault(mesh.material_index, []).extend(tris)

    canonical_groups = []
    for material_index in sorted(material_groups):
        triangles = sorted(material_groups[material_index])
        canonical_groups.append({"material": material_index, "triangles": triangles})

    topology_payload = json.dumps(canonical_groups, separators=(",", ":"), sort_keys=True)
    topology_sha256 = hashlib.sha256(topology_payload.encode("utf-8")).hexdigest()

    # A second digest ignores material assignment. This is useful when two
    # models use the same UV layout but split the layout into materials differently.
    all_triangles = sorted(tri for group in canonical_groups for tri in group["triangles"])
    geometry_payload = json.dumps(all_triangles, separators=(",", ":"))
    layout_sha256 = hashlib.sha256(geometry_payload.encode("utf-8")).hexdigest()

    unique_points = sorted({point for tri in all_triangles for point in tri})
    if unique_points:
        us = [p[0] for p in unique_points]
        vs = [p[1] for p in unique_points]
        bounds = [_q(min(us)), _q(min(vs)), _q(max(us)), _q(max(vs))]
    else:
        bounds = None

    return {
        "version": UV_CACHE_VERSION,
        "model": str(Path(model.path).resolve()),
        "lod": lod_index,
        "mesh_count": len(lod.meshes),
        "material_groups": len(canonical_groups),
        "uv_vertex_count": uv_vertex_count,
        "triangle_count": len(all_triangles),
        "unique_uv_points": len(unique_points),
        "bounds": bounds,
        "topology_sha256": topology_sha256,
        "layout_sha256": layout_sha256,
        "materials": [
            {
                "index": group["material"],
                "name": model.materials[group["material"]].name
                if 0 <= group["material"] < len(model.materials)
                else f"Material_{group['material']}",
                "triangle_count": len(group["triangles"]),
                "sha256": hashlib.sha256(
                    json.dumps(group["triangles"], separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            }
            for group in canonical_groups
        ],
    }


def render_uv_wireframe(model_or_path, output_path: str | Path, lod_index: int = 0,
                        size: int = 512) -> dict[str, Any]:
    """Render UV0 triangles in the same green/white diagnostic spirit as There templates."""
    model = decode_model(model_or_path) if isinstance(model_or_path, (str, Path)) else model_or_path
    if lod_index < 0 or lod_index >= len(model.lods):
        raise ValueError(f"LOD{lod_index} is not present in {Path(model.path).name}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(image)

    triangle_count = 0
    for mesh in model.lods[lod_index].meshes:
        for tri in _mesh_triangles(mesh):
            points = []
            for u, v in tri:
                # Decoder UVs are Blender-style (V up); images are Y down.
                x = int(round(u * (size - 1)))
                y = int(round((1.0 - v) * (size - 1)))
                points.append((x, y))
            draw.line(points + [points[0]], fill=(0, 255, 80), width=1)
            triangle_count += 1

    image.save(output_path, "PNG")
    return {"output": str(output_path), "triangles": triangle_count, "size": size, "lod": lod_index}


def analyze_model_uv(model_path: str | Path, lod_index: int = 0, force: bool = False) -> dict[str, Any]:
    """Generate a cached wireframe + JSON fingerprint for one There model."""
    model_path = Path(model_path)
    stat = model_path.stat()
    key_payload = f"{model_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|lod={lod_index}|v={UV_CACHE_VERSION}"
    key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()[:24]
    folder = UV_CACHE_DIR / key
    png = folder / f"LOD{lod_index}_uv.png"
    meta = folder / f"LOD{lod_index}_uv.json"

    if not force and png.exists() and meta.exists():
        data = json.loads(meta.read_text(encoding="utf-8"))
        data["wireframe"] = str(png)
        data["cached"] = True
        return data

    model = decode_model(model_path)
    fp = uv_fingerprint(model, lod_index)
    rendered = render_uv_wireframe(model, png, lod_index)
    data = {**fp, "wireframe": str(png), "rendered_triangles": rendered["triangles"], "cached": False}
    folder.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def compare_uv_fingerprints(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Exact UV-family evidence. No fuzzy artwork similarity is involved."""
    same_layout = bool(a.get("layout_sha256") and a.get("layout_sha256") == b.get("layout_sha256"))
    same_topology = bool(a.get("topology_sha256") and a.get("topology_sha256") == b.get("topology_sha256"))
    return {
        "same_layout": same_layout,
        "same_material_topology": same_topology,
        "triangle_count_equal": a.get("triangle_count") == b.get("triangle_count"),
        "unique_uv_points_equal": a.get("unique_uv_points") == b.get("unique_uv_points"),
    }
