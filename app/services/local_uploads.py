from __future__ import annotations

from pathlib import Path
from uuid import uuid4
from uuid import UUID

from fastapi import UploadFile


def _safe_filename(filename: str | None, fallback: str) -> str:
    if not filename:
        return fallback
    return Path(filename).name or fallback


async def save_upload(
    file: UploadFile,
    base_dir: Path,
    subdir: Path,
    allowed_extensions: set[str] | None = None,
) -> tuple[str, str]:
    base_dir = base_dir.resolve()
    dest_dir = (base_dir / subdir).resolve()
    if base_dir not in dest_dir.parents and base_dir != dest_dir:
        raise ValueError("Invalid upload path")
    dest_dir.mkdir(parents=True, exist_ok=True)

    original_name = _safe_filename(file.filename, "upload.bin")
    ext = Path(original_name).suffix.lower()

    if allowed_extensions:
        # Normalize allowed extensions to lowercase and ensure dot prefix
        normalized_allowed = {e if e.startswith(".") else f".{e}" for e in allowed_extensions}
        # Special handling for jpeg/jpg
        if ".jpeg" in normalized_allowed:
            normalized_allowed.add(".jpg")
        if ".jpg" in normalized_allowed:
            normalized_allowed.add(".jpeg")

        if ext not in normalized_allowed:
            raise ValueError(
                f"File type not allowed. Allowed extensions: {', '.join(sorted(normalized_allowed))}"
            )

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


def org_templates_subdir(org_id: str, folder_id: UUID | None) -> Path:
    folder_segment = str(folder_id) if folder_id else "unassigned"
    return Path("orgs") / org_id / "templates" / folder_segment


def loan_documents_subdir(org_id: str, loan_id: UUID) -> Path:
    return Path("orgs") / org_id / "loans" / str(loan_id) / "documents"


def loan_repayments_subdir(org_id: str, loan_id: UUID) -> Path:
    return Path("orgs") / org_id / "loans" / str(loan_id) / "repayments"


def profile_pictures_subdir(org_id: str, user_id: UUID) -> Path:
    return Path("orgs") / org_id / "display-pictures" / str(user_id)


def generate_storage_key(subdir: Path, filename: str | None) -> tuple[str, str]:
    original_name = _safe_filename(filename, "upload.bin")
    ext = Path(original_name).suffix.lower()
    dest_name = f"{uuid4().hex}{ext}"
    return (subdir / dest_name).as_posix(), original_name


def ensure_org_scoped_key(org_id: str, object_key: str) -> None:
    expected_prefix = f"orgs/{org_id}/"
    if not object_key.startswith(expected_prefix):
        raise ValueError("Storage key is not scoped to org")
