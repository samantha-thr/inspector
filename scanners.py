from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from binary_analysis import analyze_model_binary
from config import BATCH_SIZE, MODEL_EXTENSION, TEXTURE_EXTENSIONS
from database import Database
from texture_analysis import analyze_texture
from utils import file_hashes, safe_relative_path


def discover(root: Path, extensions: tuple[str, ...]) -> list[Path]:
    found = []
    for ext in extensions:
        found.extend(root.rglob(f"*{ext}"))
        found.extend(root.rglob(f"*{ext.upper()}"))
    return sorted(set(found), key=lambda p: str(p).lower())


def classify_filename(path: Path) -> str:
    return "Numeric Product ID" if path.stem.isdigit() else "Named Asset"


def scan_models(root: str | Path, full: bool, callback: Callable[[dict], None] | None = None) -> dict:
    root = Path(root).resolve()
    started = time.time()
    db = Database()
    files = discover(root, (MODEL_EXTENSION,))
    existing = db.existing_models()
    scanned = skipped = errors = batch = 0
    db.begin()
    for i, path in enumerate(files, 1):
        try:
            stat = path.stat()
            key = str(path.resolve())
            old = existing.get(key)
            if not full and old and old["size"] == stat.st_size and old["mtime"] == stat.st_mtime:
                skipped += 1
            else:
                sha, md5, crc = file_hashes(path)
                info = analyze_model_binary(path)
                db.upsert_model({
                    "path": key, "root": str(root), "relative_path": safe_relative_path(path, root),
                    "filename": path.name, "folder": path.parent.name, "size": stat.st_size,
                    "mtime": stat.st_mtime, "sha256": sha, "md5": md5, "crc32": crc,
                    "filename_type": classify_filename(path), "last_scanned": time.time(), **info
                })
                scanned += 1; batch += 1
                if batch >= BATCH_SIZE:
                    db.commit(); db.begin(); batch = 0
        except Exception:
            errors += 1
        if callback:
            elapsed = max(time.time() - started, .001)
            callback({"index": i, "total": len(files), "relative_path": safe_relative_path(path, root), "scanned": scanned, "skipped": skipped, "errors": errors, "speed": i / elapsed})
    db.commit()
    db.add_scan_history(str(root), started, len(files), scanned, skipped, errors, "model_full" if full else "model_incremental")
    db.close()
    return {"root": str(root), "found": len(files), "scanned": scanned, "skipped": skipped, "errors": errors, "elapsed": time.time() - started, "scan_type": "Full Model Rescan" if full else "Incremental Model Scan"}


def scan_textures(root: str | Path, full: bool, callback: Callable[[dict], None] | None = None) -> dict:
    root = Path(root).resolve()
    started = time.time()
    db = Database()
    files = discover(root, TEXTURE_EXTENSIONS)
    existing = db.existing_textures()
    scanned = skipped = errors = batch = 0
    db.begin()
    for i, path in enumerate(files, 1):
        try:
            stat = path.stat()
            key = str(path.resolve())
            old = existing.get(key)
            if not full and old and old["size"] == stat.st_size and old["mtime"] == stat.st_mtime:
                skipped += 1
            else:
                row = analyze_texture(path)
                row.update({"root": str(root), "relative_path": safe_relative_path(path, root), "last_scanned": time.time()})
                db.upsert_texture(row)
                scanned += 1; batch += 1
                if batch >= BATCH_SIZE:
                    db.commit(); db.begin(); batch = 0
        except Exception:
            errors += 1
        if callback:
            elapsed = max(time.time() - started, .001)
            callback({"index": i, "total": len(files), "relative_path": safe_relative_path(path, root), "scanned": scanned, "skipped": skipped, "errors": errors, "speed": i / elapsed})
    db.commit()
    db.add_scan_history(str(root), started, len(files), scanned, skipped, errors, "texture_full" if full else "texture_incremental")
    db.close()
    return {"root": str(root), "found": len(files), "scanned": scanned, "skipped": skipped, "errors": errors, "elapsed": time.time() - started, "scan_type": "Full Texture Rescan" if full else "Incremental Texture Scan"}
