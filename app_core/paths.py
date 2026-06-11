import os
import re
import uuid
from pathlib import Path

from fastapi import HTTPException


SAFE_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


def validate_uuid(value: str, label: str = "ID") -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label}") from exc


def validate_prefixed_uuid(value: str, prefix: str, label: str = "ID") -> str:
    if not value.startswith(prefix):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")
    suffix = value[len(prefix) :]
    return f"{prefix}{validate_uuid(suffix, label)}"


def safe_filename(value: str, fallback: str = "upload.bin") -> str:
    normalized = (value or "").replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1].split("?", 1)[0].strip()
    if not name or name in {".", ".."}:
        return fallback
    return name


def safe_upload_suffix(filename: str | None) -> str:
    suffix = Path(safe_filename(filename or "")).suffix.lower()
    return suffix if SAFE_EXTENSION.fullmatch(suffix) else ".bin"


def safe_join(root: str | Path, *parts: str) -> str:
    root_path = Path(root).resolve()
    candidate = root_path.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc
    return os.fspath(candidate)


def job_directory(output_dir: str | Path, job_id: str) -> str:
    return safe_join(output_dir, validate_uuid(job_id, "job ID"))


def job_media_path(output_dir: str | Path, job_id: str, filename: str) -> str:
    return safe_join(job_directory(output_dir, job_id), safe_filename(filename))
