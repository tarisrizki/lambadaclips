import os
import glob
import json
import uuid
import contextlib
from urllib.parse import urlparse
from fastapi import HTTPException, UploadFile

from app_core.config import OUTPUT_DIR, JOB_RETENTION_SECONDS
from app_core.paths import job_directory, job_media_path, safe_filename, safe_join, validate_uuid
from app_core.security import sign_media_url
from app_core.globals import jobs

def _job_dir(job_id: str) -> str:
    return job_directory(OUTPUT_DIR, job_id)

def _job_media(job_id: str, filename: str) -> str:
    return job_media_path(OUTPUT_DIR, job_id, filename)

def _video_url(job_id: str, filename: str) -> str:
    safe_job_id = validate_uuid(job_id, "job ID")
    name = safe_filename(filename)
    return sign_media_url(f"/videos/{safe_job_id}/{name}", JOB_RETENTION_SECONDS)

def _named_video_url(directory: str, filename: str) -> str:
    safe_directory = safe_filename(directory)
    s_filename = safe_filename(filename)
    return sign_media_url(
        f"/videos/{safe_directory}/{s_filename}",
        JOB_RETENTION_SECONDS,
    )

def validate_youtube_url(url: str) -> str:
    parsed_url = urlparse(url)
    allowed_hosts = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
    if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname not in allowed_hosts:
        raise HTTPException(status_code=400, detail="Only YouTube URLs are supported")
    return url

def _thumbnail_url(relative_path: str) -> str:
    parts = [part for part in relative_path.replace("\\", "/").split("/") if part]
    if not parts:
        raise HTTPException(status_code=400, detail="Invalid thumbnail path")
    safe_parts = [safe_filename(part) for part in parts]
    thumb_dir = os.path.join(OUTPUT_DIR, "thumbnails")
    # Apply safe_join result to ensure path traversal protection
    safe_path = safe_join(thumb_dir, *safe_parts)
    return sign_media_url(
        f"/thumbnails/{'/'.join(safe_parts)}", JOB_RETENTION_SECONDS
    )

def update_clip_url(job_id: str, clip_index: int, filename: str) -> str:
    video_url = _video_url(job_id, filename)
    job = jobs.get(job_id)
    if job:
        clips = job.get("result", {}).get("clips", [])
        if 0 <= clip_index < len(clips):
            clips[clip_index]["video_url"] = video_url

    metadata_files = glob.glob(os.path.join(_job_dir(job_id), "*_metadata.json"))
    if metadata_files:
        metadata_path = metadata_files[0]
        with open(metadata_path, "r", encoding="utf-8") as metadata_file:
            data = json.load(metadata_file)
        disk_clips = data.get("shorts", [])
        if 0 <= clip_index < len(disk_clips):
            disk_clips[clip_index]["video_url"] = video_url
            data["shorts"] = disk_clips
            temp_path = f"{metadata_path}.{uuid.uuid4().hex}.tmp"
            try:
                with open(temp_path, "w", encoding="utf-8") as metadata_file:
                    json.dump(data, metadata_file, indent=2, ensure_ascii=False)
                os.replace(temp_path, metadata_path)
            finally:
                with contextlib.suppress(OSError):
                    os.remove(temp_path)
    return video_url

async def save_upload_limited(
    upload: UploadFile,
    destination: str,
    *,
    max_bytes: int,
) -> int:
    written = 0
    try:
        with open(destination, "wb") as output_file:
            while chunk := await upload.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail="Uploaded file is too large")
                output_file.write(chunk)
    except Exception:
        with contextlib.suppress(OSError):
            os.remove(destination)
        raise
    return written
