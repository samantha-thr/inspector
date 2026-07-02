from __future__ import annotations

import hashlib
import time
import zlib
from pathlib import Path
from typing import Callable, Optional

from config import BATCH_SIZE, HASH_CHUNK_SIZE, MODEL_EXTENSION, TEXTURE_EXTENSIONS
from database import Database
from som import inspect_model_header
from texture_analysis import analyze_texture


def classify_filename(path: Path) -> str:
    stem = path.stem.strip()
    if stem.isdigit():
        return "Numeric Product ID"
    if "_" in stem or any(c.isalpha() for c in stem):
        return "Named Asset"
    return "Unknown"


def hash_file(path: Path) -> tuple[str, str, int]:
    sha = hashlib.sha256()
    md5 = hashlib.md5()
    crc = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_SIZE), b""):
            sha.update(chunk)
            md5.update(chunk)
            crc = zlib.crc32(chunk, crc)
    return sha.hexdigest(), md5.hexdigest(), crc & 0xFFFFFFFF


def discover_models(root: Path) -> list[Path]:
    return sorted(root.rglob(f"*{MODEL_EXTENSION}"), key=lambda p: str(p).lower())


def discover_textures(root: Path) -> list[Path]:
    files: list[Path] = []
    for ext in TEXTURE_EXTENSIONS:
        files.extend(root.rglob(f"*{ext}"))
        files.extend(root.rglob(f"*{ext.upper()}"))
    return sorted(set(files), key=lambda p: str(p).lower())


def safe_relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def scan_folder(
    root: str | Path,
    progress_callback: Optional[Callable[[dict], None]] = None,
    full_rescan: bool = False,
) -> dict:
    root = Path(root).resolve()
    started = time.time()
    db = Database()
    files = discover_models(root)
    total = len(files)
    existing = db.get_existing_map()
    scanned = skipped = errors = batch_count = 0
    db.begin()

    for index, file_path in enumerate(files, 1):
        status = "Unknown"
        try:
            stat = file_path.stat()
            path_key = str(file_path.resolve())
            existing_row = existing.get(path_key)

            if (
                not full_rescan
                and existing_row
                and int(existing_row["size"]) == stat.st_size
                and float(existing_row["mtime"]) == stat.st_mtime
            ):
                skipped += 1
                status = "Skipped"
            else:
                sha256, md5, crc32 = hash_file(file_path)
                info = inspect_model_header(file_path)
                db.upsert_model({
                    "path": path_key,
                    "root": str(root),
                    "relative_path": safe_relative_path(file_path, root),
                    "filename": file_path.name,
                    "folder": file_path.parent.name,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "sha256": sha256,
                    "md5": md5,
                    "crc32": crc32,
                    "filename_type": classify_filename(file_path),
                    "som_version": info.get("som_version", ""),
                    "header": info.get("header", ""),
                    "string_count": info.get("full_strings_count", info.get("string_count", 0)),
                    "first_64_hex": info.get("first_64_hex", ""),
                    "first_256_sha256": info.get("first_256_sha256", ""),
                    "prefix_4k_sha256": info.get("prefix_4k_sha256", ""),
                    "suffix_4k_sha256": info.get("suffix_4k_sha256", ""),
                    "middle_4k_sha256": info.get("middle_4k_sha256", ""),
                    "entropy": info.get("entropy", 0.0),
                    "printable_ratio": info.get("printable_ratio", 0.0),
                    "zero_ratio": info.get("zero_ratio", 0.0),
                    "sample_strings": info.get("sample_strings", ""),
                    "last_scanned": time.time(),
                })
                scanned += 1
                batch_count += 1
                status = "Hashed"
                if batch_count >= BATCH_SIZE:
                    db.commit()
                    db.begin()
                    batch_count = 0
        except Exception:
            errors += 1
            status = "Error"

        if progress_callback:
            elapsed = max(time.time() - started, 0.001)
            progress_callback({
                "index": index,
                "total": total,
                "file": file_path.name,
                "folder": file_path.parent.name,
                "relative_path": safe_relative_path(file_path, root),
                "scanned": scanned,
                "skipped": skipped,
                "errors": errors,
                "elapsed": elapsed,
                "speed": index / elapsed,
                "status": status,
            })

    db.commit()
    db.add_scan_history(str(root), started, total, scanned, skipped, errors, "model_full" if full_rescan else "model_incremental")
    summary = {
        "root": str(root),
        "found": total,
        "scanned": scanned,
        "skipped": skipped,
        "errors": errors,
        "elapsed": time.time() - started,
        "database_models": db.count_models(),
        "duplicate_hash_groups": db.duplicate_hash_count(),
        "filename_types": db.filename_type_counts(),
        "som_versions": db.som_version_counts(),
        "size_stats": db.size_stats(),
        "scan_mode": "Full Rescan" if full_rescan else "Incremental Scan",
    }
    db.close()
    return summary


def scan_textures(
    root: str | Path,
    progress_callback: Optional[Callable[[dict], None]] = None,
    full_rescan: bool = False,
) -> dict:
    root = Path(root).resolve()
    started = time.time()
    db = Database()
    files = discover_textures(root)
    total = len(files)
    existing = db.get_existing_texture_map()
    scanned = skipped = errors = batch_count = 0
    db.begin()

    for index, file_path in enumerate(files, 1):
        status = "Unknown"
        try:
            stat = file_path.stat()
            path_key = str(file_path.resolve())
            existing_row = existing.get(path_key)

            if (
                not full_rescan
                and existing_row
                and int(existing_row["size"]) == stat.st_size
                and float(existing_row["mtime"]) == stat.st_mtime
            ):
                skipped += 1
                status = "Skipped"
            else:
                row = analyze_texture(file_path)
                row.update({
                    "root": str(root),
                    "relative_path": safe_relative_path(file_path, root),
                    "last_scanned": time.time(),
                })
                db.upsert_texture(row)
                scanned += 1
                batch_count += 1
                status = row.get("analysis_status", "Scanned")
                if batch_count >= BATCH_SIZE:
                    db.commit()
                    db.begin()
                    batch_count = 0
        except Exception:
            errors += 1
            status = "Error"

        if progress_callback:
            elapsed = max(time.time() - started, 0.001)
            progress_callback({
                "index": index,
                "total": total,
                "file": file_path.name,
                "folder": file_path.parent.name,
                "relative_path": safe_relative_path(file_path, root),
                "scanned": scanned,
                "skipped": skipped,
                "errors": errors,
                "elapsed": elapsed,
                "speed": index / elapsed,
                "status": status,
            })

    db.commit()
    db.add_scan_history(str(root), started, total, scanned, skipped, errors, "texture_full" if full_rescan else "texture_incremental")
    stats = db.texture_stats()
    summary = {
        "root": str(root),
        "found": total,
        "scanned": scanned,
        "skipped": skipped,
        "errors": errors,
        "elapsed": time.time() - started,
        "database_textures": db.count_textures(),
        "texture_stats": stats,
        "scan_mode": "Full Texture Rescan" if full_rescan else "Incremental Texture Scan",
    }
    db.close()
    return summary
