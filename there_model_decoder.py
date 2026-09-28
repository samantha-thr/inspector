from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ThereModelDecodeError(RuntimeError):
    pass


class BitReader:
    """MSB-first bit reader matching There's SOM writer."""

    def __init__(self, data: bytes):
        self.data = data
        self.byte = 0
        self.bit = 0

    def align(self) -> None:
        if self.bit:
            self.byte += 1
            self.bit = 0

    def tell(self) -> int:
        return self.byte

    def remaining(self) -> int:
        return max(0, len(self.data) - self.byte)

    def read_bits(self, width: int) -> int:
        if width < 0:
            raise ValueError(width)
        value = 0
        for _ in range(width):
            if self.byte >= len(self.data):
                raise EOFError(f"Unexpected EOF at byte {self.byte}")
            value = (value << 1) | ((self.data[self.byte] >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.byte += 1
                self.bit = 0
        return value

    def read_uint(self, width: int = 0, start: int = 0, end: int = 0, step: int = 1) -> int:
        if width == 0:
            width = int(math.ceil(math.log((float(end) - float(start) + 1.0) / float(step), 2.0)))
        return self.read_bits(width)

    def read_int(self, **kwargs) -> int:
        return self.read_uint(**kwargs)

    def read_float(self, width: int = 0, start: float = 0.0, end: float = 0.0, step: float = 1.0) -> float:
        if width == 0:
            width = int(math.ceil(math.log((end - start) / step, 2.0)))
        elif end > start:
            step = (end - start) / (math.pow(2.0, float(width)) - 1.0)
        raw = self.read_bits(width)
        return start + raw * step

    def read_text(self, width: int, length: int | None = None, end: int | None = None) -> str:
        if length is None:
            if end is None:
                raise ValueError("end is required when length is not fixed")
            length = self.read_int(end=end)
        raw = bytes(self.read_bits(width) for _ in range(length))
        self.align()
        return raw.decode("latin1", errors="replace").rstrip("\x00")

    def skip_bytes(self, count: int) -> None:
        self.align()
        self.byte += count
        if self.byte > len(self.data):
            raise EOFError("Skip passed end of file")


@dataclass
class DecodedMaterial:
    name: str
    index: int
    bool_mask: int
    float_mask: int
    color_mask: int
    map_mask: int
    bool_values: int
    textures: list[str] = field(default_factory=list)
    texture_maps: dict[int, str] = field(default_factory=dict)


@dataclass
class DecodedNode:
    name: str
    index: int
    parent_index: int | None
    position: tuple[float, float, float]
    orientation: tuple[float, float, float, float]


@dataclass
class DecodedVertex:
    position: tuple[float, float, float]
    normal: tuple[float, float, float] | None
    color: int | None
    uv0: tuple[float, float] | None
    uv1: tuple[float, float] | None
    tangent: tuple[float, float, float] | None
    bitangent: tuple[float, float, float] | None


@dataclass
class DecodedMesh:
    lod_index: int
    node_index: int
    material_index: int
    vertex_format: int
    vertices: list[DecodedVertex]
    indices: list[int]


@dataclass
class DecodedLOD:
    index: int
    scale_code: int
    scale_factor: float
    distance: float = 0.0
    meshes: list[DecodedMesh] = field(default_factory=list)


@dataclass
class DecodedCollision:
    center: tuple[float, float, float]
    vertices: list[tuple[float, float, float]]
    polygons: list[list[int]]


@dataclass
class DecodedModel:
    path: str
    version: int
    materials: list[DecodedMaterial]
    nodes: list[DecodedNode]
    lods: list[DecodedLOD]
    collision: DecodedCollision | None
    header_counts: dict[str, int]
    bytes_consumed: int
    file_size: int

    @property
    def vertex_count(self) -> int:
        return sum(len(m.vertices) for lod in self.lods for m in lod.meshes)

    @property
    def triangle_count(self) -> int:
        return sum(len(m.indices) // 3 for lod in self.lods for m in lod.meshes)


def _read_custom_half(reader: BitReader) -> float:
    sign = reader.read_uint(width=1)
    exponent = reader.read_uint(width=4)
    mantissa = reader.read_uint(width=11)

    if exponent == 0:
        value = (mantissa / 2048.0) * 0.5
        return -value if sign else value

    bits = (sign << 31) | ((exponent + 125) << 23) | (mantissa << 12)
    return struct.unpack(">f", struct.pack(">I", bits))[0]


def _there_to_blender_vec(v: tuple[float, float, float]) -> tuple[float, float, float]:
    # Exporter stores Blender XYZ as [-X, Z, Y].
    return (-v[0], v[2], v[1])


def _there_to_blender_uv(uv: tuple[float, float] | None) -> tuple[float, float] | None:
    # Exporter stores [U, 1-V].
    return None if uv is None else (uv[0], 1.0 - uv[1])


def _decode_orientation(reader: BitReader) -> tuple[float, float, float, float]:
    dropped_xyz_index = reader.read_int(width=2)
    values = [
        reader.read_float(width=24, start=-1.0, end=1.0),
        reader.read_float(width=23, start=-1.0, end=1.0),
        reader.read_float(width=23, start=-1.0, end=1.0),
    ]
    reader.align()

    missing = math.sqrt(max(0.0, 1.0 - sum(v * v for v in values)))
    result = []
    value_index = 0
    for quaternion_index in range(4):
        if quaternion_index == dropped_xyz_index + 1:
            result.append(missing)
        else:
            result.append(values[value_index])
            value_index += 1

    # There stores inverse of Blender's model quaternion.
    w, x, y, z = result
    return (w, -x, -y, -z)


def decode_model(path: str | Path) -> DecodedModel:
    path = Path(path)
    data = path.read_bytes()
    reader = BitReader(data)

    magic = reader.read_text(width=8, length=4)
    if magic != "SOM ":
        raise ThereModelDecodeError(f"{path.name}: unsupported signature {magic!r}")

    version = reader.read_uint(width=32)
    if version != 10:
        raise ThereModelDecodeError(f"{path.name}: SOM version {version} is not supported yet")

    reader.align()

    # Fixed version-10 header. Values are retained only where they describe
    # counts needed to decode the remaining stream.
    reader.read_uint(width=1)
    reader.read_uint(width=1)
    reader.read_int(end=8)
    reader.align()

    for _ in range(6):
        reader.read_float(width=32, start=-2000000.0, end=2000000.0)
    reader.align()

    reader.read_uint(width=32)
    reader.read_uint(width=32)
    reader.align()

    reader.read_int(width=32)
    reader.read_int(width=32)
    reader.align()

    reader.read_uint(width=32)
    reader.read_int(width=32)
    reader.read_float(width=32, start=0.0, end=360.0)
    reader.read_int(width=32)
    reader.read_uint(width=32)
    reader.read_int(width=8)
    reader.read_float(width=32, start=0.0, end=360.0)
    reader.read_float(width=32, start=0.0, end=360.0)
    reader.read_int(width=32)
    reader.read_uint(width=32)
    reader.read_int(width=8)
    reader.read_float(width=32, start=0.0, end=360.0)
    reader.read_int(end=32)
    reader.align()

    reader.read_uint(width=32)
    reader.align()
    reader.read_uint(width=32)
    reader.align()

    material_count = reader.read_int(end=256)
    lod_count = reader.read_int(width=32)
    lod_count_2 = reader.read_int(width=32)
    family_count = reader.read_int(width=32)
    non_root_node_count = reader.read_int(width=32)
    reader.align()

    if lod_count <= 0 or lod_count > 32:
        raise ThereModelDecodeError(f"{path.name}: implausible LOD count {lod_count}")
    if lod_count_2 != lod_count:
        raise ThereModelDecodeError(f"{path.name}: LOD count mismatch {lod_count}/{lod_count_2}")
    if material_count < 0 or material_count > 256:
        raise ThereModelDecodeError(f"{path.name}: implausible material count {material_count}")
    if non_root_node_count < 0 or non_root_node_count > 65535:
        raise ThereModelDecodeError(f"{path.name}: implausible node count {non_root_node_count}")

    # Materials begin with a 16-bit byte-swapped block length, followed by
    # one similarly prefixed record per material. We decode the records
    # sequentially; the lengths are not required for version-10 parsing.
    reader.skip_bytes(2)
    materials: list[DecodedMaterial] = []

    for material_index in range(material_count):
        reader.skip_bytes(2)
        name = reader.read_text(width=7, end=32)

        reader.read_uint(width=8)  # material record version-ish field
        reader.read_uint(width=8)

        bool_mask = reader.read_uint(width=16)
        float_mask = reader.read_uint(width=16)
        color_mask = reader.read_uint(width=16)
        map_mask = reader.read_uint(width=16)
        bool_values = reader.read_uint(width=16)

        if float_mask & (1 << 5):
            _read_custom_half(reader)
        if color_mask & (1 << 2):
            reader.read_uint(width=24)
        if color_mask & (1 << 4):
            reader.read_uint(width=24)

        textures: list[str] = []
        texture_maps: dict[int, str] = {}
        for map_bit in range(7):
            if map_mask & (1 << map_bit):
                tex = reader.read_text(width=7, end=48)
                textures.append(tex)
                texture_maps[map_bit] = tex

        # Current exporter writes zero here. Preserve forward compatibility by
        # recording it, but version 10 has no known payload associated with it.
        int_mask = reader.read_uint(width=16)
        if int_mask:
            raise ThereModelDecodeError(
                f"{path.name}: unsupported material integer mask 0x{int_mask:04x}"
            )
        reader.align()

        materials.append(
            DecodedMaterial(
                name=name or f"Material_{material_index}",
                index=material_index,
                bool_mask=bool_mask,
                float_mask=float_mask,
                color_mask=color_mask,
                map_mask=map_mask,
                bool_values=bool_values,
                textures=textures,
                texture_maps=texture_maps,
            )
        )

    total_nodes = non_root_node_count + 1

    positions_there: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
    for _ in range(non_root_node_count):
        positions_there.append(
            (
                reader.read_float(width=32, start=-2000000.0, end=2000000.0),
                reader.read_float(width=32, start=-2000000.0, end=2000000.0),
                reader.read_float(width=32, start=-2000000.0, end=2000000.0),
            )
        )
        reader.align()

    parents_raw = []
    for _ in range(total_nodes):
        parents_raw.append(reader.read_int(width=16))
        reader.align()

    orientations = [_decode_orientation(reader) for _ in range(total_nodes)]

    names = []
    for node_index in range(total_nodes):
        name = reader.read_text(width=7, end=40)
        reader.align()
        names.append(name or ("master" if node_index == 0 else f"Node_{node_index}"))

    nodes = []
    for index in range(total_nodes):
        parent = None if parents_raw[index] == 3001 else parents_raw[index]
        nodes.append(
            DecodedNode(
                name=names[index],
                index=index,
                parent_index=parent,
                position=_there_to_blender_vec(positions_there[index]),
                orientation=orientations[index],
            )
        )

    # Collision
    collision_version = reader.read_uint(width=16)
    has_collision = reader.read_uint(width=32)
    collision = None

    if collision_version != 1:
        raise ThereModelDecodeError(f"{path.name}: collision version {collision_version} unsupported")

    if has_collision:
        collision_vertex_count = reader.read_uint(width=16)
        collision_polygon_count = reader.read_uint(width=16)
        reader.read_uint(width=32)  # total polygon indices
        reader.read_uint(width=32)

        center_there = (
            reader.read_float(width=32, start=-2000000.0, end=2000000.0),
            reader.read_float(width=32, start=-2000000.0, end=2000000.0),
            reader.read_float(width=32, start=-2000000.0, end=2000000.0),
        )

        collision_vertices = []
        for _ in range(collision_vertex_count):
            v = (
                reader.read_float(width=32, start=-2000000.0, end=2000000.0),
                reader.read_float(width=32, start=-2000000.0, end=2000000.0),
                reader.read_float(width=32, start=-2000000.0, end=2000000.0),
            )
            collision_vertices.append(_there_to_blender_vec(v))

        collision_polygons = []
        for _ in range(collision_polygon_count):
            polygon_size = reader.read_uint(width=8)
            collision_polygons.append(
                [reader.read_uint(width=16) for _ in range(polygon_size)]
            )

        collision = DecodedCollision(
            center=_there_to_blender_vec(center_there),
            vertices=collision_vertices,
            polygons=collision_polygons,
        )

    reader.align()

    # Components / meshes
    lods: list[DecodedLOD] = []

    for lod_index in range(lod_count):
        mesh_count = reader.read_uint(width=32)
        scale_code = reader.read_uint(width=6)
        reader.align()

        if mesh_count > 100000:
            raise ThereModelDecodeError(f"{path.name}: implausible mesh count {mesh_count}")

        scale_factor = math.pow(2.0, scale_code - 32)
        lod = DecodedLOD(index=lod_index, scale_code=scale_code, scale_factor=scale_factor)

        for _ in range(mesh_count):
            component_type = reader.read_uint(end=8)
            component_version = reader.read_uint(end=8)
            node_index_minus_one = reader.read_uint(width=8)

            for _ in range(7):
                reader.read_uint(width=8)

            material_index = reader.read_uint(end=256)
            vertex_format = reader.read_uint(width=32)
            vertex_format_2 = reader.read_uint(width=32)
            component_flags = reader.read_uint(width=32)
            vertex_count = reader.read_uint(width=32)
            index_count = reader.read_uint(width=32)
            reader.align()

            if component_type != 1 or component_version != 8:
                raise ThereModelDecodeError(
                    f"{path.name}: component {component_type}/{component_version} unsupported"
                )
            if vertex_format != vertex_format_2:
                raise ThereModelDecodeError(f"{path.name}: vertex-format mismatch")
            if component_flags != 7:
                raise ThereModelDecodeError(
                    f"{path.name}: unsupported component flags {component_flags}"
                )
            if vertex_count > 2_000_000 or index_count > 6_000_000:
                raise ThereModelDecodeError(
                    f"{path.name}: implausible mesh size {vertex_count}/{index_count}"
                )

            vertices: list[DecodedVertex] = []

            for _ in range(vertex_count):
                position = None
                normal = None
                color = None
                uv0 = None
                uv1 = None
                tangent = None
                bitangent = None

                if vertex_format & (1 << 0):
                    p_there = (
                        reader.read_float(width=14, start=-1.024, end=1.023) * scale_factor,
                        reader.read_float(width=14, start=-1.024, end=1.023) * scale_factor,
                        reader.read_float(width=14, start=-1.024, end=1.023) * scale_factor,
                    )
                    position = _there_to_blender_vec(p_there)

                # Bits 1 and 2 are not emitted by the current exporter.
                if vertex_format & ((1 << 1) | (1 << 2)):
                    raise ThereModelDecodeError(
                        f"{path.name}: unsupported vertex format 0x{vertex_format:08x}"
                    )

                if vertex_format & (1 << 3):
                    n_there = (
                        reader.read_float(width=6, start=-1.0, end=1.0),
                        reader.read_float(width=6, start=-1.0, end=1.0),
                        reader.read_float(width=6, start=-1.0, end=1.0),
                    )
                    normal = _there_to_blender_vec(n_there)

                if vertex_format & (1 << 4):
                    color = reader.read_uint(width=32)

                if vertex_format & (1 << 5):
                    uv0 = _there_to_blender_uv(
                        (
                            reader.read_float(width=18, start=-256.0, end=255.998046875),
                            reader.read_float(width=18, start=-256.0, end=255.998046875),
                        )
                    )

                if vertex_format & (1 << 6):
                    uv1 = _there_to_blender_uv(
                        (
                            reader.read_float(width=18, start=-256.0, end=255.998046875),
                            reader.read_float(width=18, start=-256.0, end=255.998046875),
                        )
                    )

                if vertex_format & (1 << 7):
                    t_there = (
                        reader.read_float(width=6, start=-1.0, end=1.0),
                        reader.read_float(width=6, start=-1.0, end=1.0),
                        reader.read_float(width=6, start=-1.0, end=1.0),
                    )
                    tangent = _there_to_blender_vec(t_there)

                if vertex_format & (1 << 8):
                    b_there = (
                        reader.read_float(width=6, start=-1.0, end=1.0),
                        reader.read_float(width=6, start=-1.0, end=1.0),
                        reader.read_float(width=6, start=-1.0, end=1.0),
                    )
                    bitangent = _there_to_blender_vec(b_there)

                reader.align()

                if position is None:
                    raise ThereModelDecodeError(f"{path.name}: mesh is missing positions")

                vertices.append(
                    DecodedVertex(
                        position=position,
                        normal=normal,
                        color=color,
                        uv0=uv0,
                        uv1=uv1,
                        tangent=tangent,
                        bitangent=bitangent,
                    )
                )

            index_width = max(
                4,
                int(math.ceil(math.log(index_count, 2))) if index_count > 1 else 4,
            )

            repeated_index_count = reader.read_uint(width=index_width)
            reader.align()

            if repeated_index_count != index_count:
                raise ThereModelDecodeError(
                    f"{path.name}: repeated index count {repeated_index_count} != {index_count}"
                )

            indices = [reader.read_uint(width=index_width) for _ in range(index_count)]
            reader.align()

            if indices and max(indices) >= vertex_count:
                raise ThereModelDecodeError(
                    f"{path.name}: mesh index exceeds vertex count"
                )

            lod.meshes.append(
                DecodedMesh(
                    lod_index=lod_index,
                    node_index=node_index_minus_one + 1,
                    material_index=material_index,
                    vertex_format=vertex_format,
                    vertices=vertices,
                    indices=indices,
                )
            )

        lods.append(lod)

    # LOD family table
    for lod_index in range(lod_count):
        distance = reader.read_float(width=32, start=0.0, end=100000.0)
        family_lod_index = reader.read_uint(width=32)
        reader.align()
        if family_lod_index < len(lods):
            lods[family_lod_index].distance = distance

    if reader.tell() != len(data):
        # Version 10 produced by the current exporter consumes the stream exactly.
        # Permit zero padding only.
        tail = data[reader.tell():]
        if any(tail):
            raise ThereModelDecodeError(
                f"{path.name}: {len(tail)} unparsed non-zero bytes remain"
            )

    return DecodedModel(
        path=str(path.resolve()),
        version=version,
        materials=materials,
        nodes=nodes,
        lods=lods,
        collision=collision,
        header_counts={
            "material_count": material_count,
            "lod_count": lod_count,
            "family_count": family_count,
            "node_count": total_nodes,
        },
        bytes_consumed=reader.tell(),
        file_size=len(data),
    )


def _safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "")
    return value.strip("_") or "unnamed"


def _resolve_material_textures(
    model: DecodedModel,
    model_path: Path,
    linked_textures: list[Path],
) -> dict[int, Path | None]:
    """Best-effort material -> texture assignment.

    Player product models typically use external PID DDS files, while named
    assets may contain resource-relative texture references. We prefer linked
    textures supplied by There Inspector and fall back to embedded names.
    """

    linked = [Path(p) for p in linked_textures if Path(p).exists()]
    assignments: dict[int, Path | None] = {m.index: None for m in model.materials}

    if linked:
        # Preserve There product texture suffixes, but map them by the model's
        # actual There material semantics rather than by material ordinal.
        # There map bits: 0=color, 1=opacity, 2=cutout, 3=lighting/detail,
        # 4=gloss, 5=emission, 6=normal.
        import re
        product_slots = {}
        unslotted = []
        for p in linked:
            match = re.match(r"^\\d+_([1-9]\\d*)\\.", p.name, re.IGNORECASE)
            if match: product_slots[int(match.group(1))] = p
            else: unslotted.append(p)

        if product_slots:
            window_material = next((m for m in model.materials
                                    if (m.map_mask & (1 << 0)) and (m.map_mask & (1 << 1))), None)
            body_material = next((m for m in model.materials
                                  if (m.map_mask & (1 << 0)) and not (m.map_mask & ((1 << 1) | (1 << 2)))), None)
            if body_material is None and model.materials:
                body_material = model.materials[0]
            if 1 in product_slots and body_material:
                assignments[body_material.index] = product_slots[1]
            if 3 in product_slots and window_material:
                assignments[window_material.index] = product_slots[3]
            # Generic fallback for non-buggy product sets.
            for slot,p in product_slots.items():
                if slot in (1,3,4): continue
                idx=slot-1
                if idx in assignments and assignments[idx] is None: assignments[idx]=p
            # Older buggy models may not expose opacity semantics. Preserve
            # compatibility with the previous two-material convention.
            if 3 in product_slots and window_material is None and len(model.materials)>=2:
                assignments[model.materials[1].index]=product_slots[3]
        else:
            for material,p in zip(model.materials,sorted(unslotted,key=lambda x:x.name.lower())):
                assignments[material.index] = p

    resource_root = None
    parts = list(model_path.resolve().parts)
    try:
        resources_index = [p.lower() for p in parts].index("resources")
        resource_root = Path(*parts[: resources_index + 1])
    except ValueError:
        pass

    for material in model.materials:
        if assignments[material.index] is not None:
            continue

        for embedded in material.textures:
            candidates = []
            embedded_path = Path(embedded.replace("/", str(Path("/"))))
            if resource_root:
                candidates.extend([
                    resource_root / embedded_path,
                    resource_root / Path(str(embedded_path) + ".dds"),
                    resource_root / embedded_path.with_suffix(embedded_path.suffix + ".dds"),
                ])
            candidates.extend([
                model_path.parent / embedded_path.name,
                model_path.parent / (embedded_path.name + ".dds"),
                model_path.parent / (embedded_path.name.rsplit(".", 1)[0] + "_1.jpg.dds"),
            ])
            for candidate in candidates:
                if candidate.exists():
                    assignments[material.index] = candidate
                    break
            if assignments[material.index] is not None:
                break

    return assignments


def export_obj(
    model: DecodedModel,
    output_path: str | Path,
    linked_textures: list[str | Path] | None = None,
    include_collision: bool = True,
) -> dict[str, Any]:
    """Export a decoded There model as OBJ.

    v2.7.5 refinement:
    - one OBJ object/group per actual LOD instead of one object per component
    - component/material boundaries are retained through usemtl statements
    - geometry is already expressed in Blender Z-up coordinates
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    linked = [Path(p) for p in (linked_textures or [])]
    model_path = Path(model.path)
    texture_assignments = _resolve_material_textures(model, model_path, linked)

    mtl_path = output_path.with_suffix(".mtl")
    lines = [
        "# There Inspector native SOM v10 decode",
        f"# source: {model.path}",
        "# Coordinate system: Blender Z-up / Y-forward",
        "# Blender OBJ import: forward_axis='Y', up_axis='Z'",
        f"mtllib {mtl_path.name}",
    ]

    vertex_offset = 1
    uv_offset = 1
    normal_offset = 1

    for lod in model.lods:
        object_name = f"LOD{lod.index}"
        lines.append(f"o {object_name}")
        lines.append(f"g {object_name}")

        for mesh_index, mesh in enumerate(lod.meshes):
            material_name = (
                model.materials[mesh.material_index].name
                if 0 <= mesh.material_index < len(model.materials)
                else f"Material_{mesh.material_index}"
            )
            lines.append(f"usemtl {_safe_name(material_name)}")

            has_uv = all(v.uv0 is not None for v in mesh.vertices)
            has_normal = all(v.normal is not None for v in mesh.vertices)

            for vertex in mesh.vertices:
                x, y, z = vertex.position
                lines.append(f"v {x:.9g} {y:.9g} {z:.9g}")

            if has_uv:
                for vertex in mesh.vertices:
                    u, v = vertex.uv0
                    lines.append(f"vt {u:.9g} {v:.9g}")

            if has_normal:
                for vertex in mesh.vertices:
                    nx, ny, nz = vertex.normal
                    lines.append(f"vn {nx:.9g} {ny:.9g} {nz:.9g}")

            for i in range(0, len(mesh.indices) - 2, 3):
                face = mesh.indices[i:i + 3]
                refs = []
                for index in face:
                    vi = vertex_offset + index
                    if has_uv and has_normal:
                        refs.append(f"{vi}/{uv_offset + index}/{normal_offset + index}")
                    elif has_uv:
                        refs.append(f"{vi}/{uv_offset + index}")
                    elif has_normal:
                        refs.append(f"{vi}//{normal_offset + index}")
                    else:
                        refs.append(str(vi))
                lines.append("f " + " ".join(refs))

            vertex_offset += len(mesh.vertices)
            if has_uv:
                uv_offset += len(mesh.vertices)
            if has_normal:
                normal_offset += len(mesh.vertices)

    if include_collision and model.collision:
        lines.append("o COL")
        lines.append("g COL")
        collision_start = vertex_offset
        for x, y, z in model.collision.vertices:
            lines.append(f"v {x:.9g} {y:.9g} {z:.9g}")
        for polygon in model.collision.polygons:
            if len(polygon) >= 3:
                lines.append(
                    "f " + " ".join(str(collision_start + i) for i in polygon)
                )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    mtl_lines = ["# There Inspector generated material library"]
    for material in model.materials:
        name = _safe_name(material.name)
        mtl_lines.extend([
            f"newmtl {name}",
            "Ka 0.2 0.2 0.2",
            "Kd 1.0 1.0 1.0",
            "Ks 0.0 0.0 0.0",
            "d 1.0",
            "illum 2",
        ])
        texture = texture_assignments.get(material.index)
        if texture:
            mtl_lines.append(f"map_Kd {texture.resolve().as_posix()}")
        # Product _4 is the opacity companion for a material whose There
        # map mask explicitly contains both COLOR(bit 0) and OPACITY(bit 1).
        if (material.map_mask & 0x03) == 0x03 and linked:
            slot4 = next((p for p in linked if re.match(r"^\\d+_4\\.", p.name, re.IGNORECASE)), None)
            if slot4:
                mtl_lines.append(f"map_d {slot4.resolve().as_posix()}")
        mtl_lines.append("")

    mtl_path.write_text("\n".join(mtl_lines), encoding="utf-8")

    return {
        "format": "obj",
        "output": str(output_path),
        "material_library": str(mtl_path),
        "lods": len(model.lods),
        "lod_objects": len(model.lods),
        "components": sum(len(lod.meshes) for lod in model.lods),
        "vertices": model.vertex_count,
        "triangles": model.triangle_count,
        "collision": model.collision is not None,
        "materials": len(model.materials),
        "material_details": [{"index":m.index,"name":m.name,"bool_mask":m.bool_mask,"bool_values":m.bool_values,"map_mask":m.map_mask,"map_bits":[b for b in range(7) if m.map_mask & (1<<b)],"texture_maps":dict(m.texture_maps)} for m in model.materials],
        "nodes": len(model.nodes),
    }


def inspect_model(path: str | Path) -> dict[str, Any]:
    model = decode_model(path)
    return {
        "path": model.path,
        "version": model.version,
        "materials": len(model.materials),
        "nodes": len(model.nodes),
        "lods": len(model.lods),
        "meshes": sum(len(lod.meshes) for lod in model.lods),
        "vertices": model.vertex_count,
        "triangles": model.triangle_count,
        "collision_vertices": len(model.collision.vertices) if model.collision else 0,
        "collision_polygons": len(model.collision.polygons) if model.collision else 0,
        "lod_distances": [round(lod.distance, 4) for lod in model.lods],
        "bytes_consumed": model.bytes_consumed,
        "file_size": model.file_size,
    }
