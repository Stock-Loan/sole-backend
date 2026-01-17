from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile


def _safe_filename(filename: str | None, fallback: str) -> str:
    if not filename:
        return fallback
    return Path(filename).name or fallback


async def save_upload(
    file: UploadFile,
    base_dir: Path,
    subdir: Path,
) -> tuple[str, str]:
    base_dir = base_dir.resolve()
    dest_dir = (base_dir / subdir).resolve()
    if base_dir not in dest_dir.parents and base_dir != dest_dir:
        raise ValueError("Invalid upload path")
    dest_dir.mkdir(parents=True, exist_ok=True)

    original_name = _safe_filename(file.filename, "upload.bin")
    ext = Path(original_name).suffix
    dest_name = f"{uuid4().hex}{ext}"
    dest_path = dest_dir / dest_name

    with dest_path.open("wb") as handle:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    await file.close()

    relative_path = dest_path.relative_to(base_dir).as_posix()
    return relative_path, original_name


def resolve_local_path(base_dir: Path, relative_path: str) -> Path:
    base_dir = base_dir.resolve()
    candidate = (base_dir / relative_path).resolve()
    if base_dir not in candidate.parents and candidate != base_dir:
        raise ValueError("Invalid document path")
    return candidate
