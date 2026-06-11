import os
import uuid
import subprocess
import threading
import json
import shutil
import glob
import time
import asyncio
import logging
import contextlib
import functools
import httpx
import sys
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

from typing import Dict, Optional, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from app_core.config import (
    CORS_ORIGINS,
    DISABLE_YOUTUBE_URL,
    JOB_RETENTION_SECONDS,
    MAX_CONCURRENT_JOBS,
    MAX_FILE_SIZE_MB,
    OUTPUT_DIR as OUTPUT_PATH,
    STATE_DB_PATH,
    UPLOAD_DIR as UPLOAD_PATH,
)
from app_core.paths import (
    job_directory,
    job_media_path,
    safe_filename,
    safe_join,
    safe_upload_suffix,
    validate_prefixed_uuid,
    validate_uuid,
)
from app_core.security import ApiSecurity, sign_media_url
from app_core.social import upload_video
from app_core.state import SQLiteState
from editor import VideoEditor
from hooks import add_hook_to_video
from s3_uploader import upload_job_artifacts, list_all_clips, upload_actor_to_s3, list_actor_gallery, upload_video_to_gallery, list_video_gallery
from saasshorts import (
    DEFAULT_VOICES,
    analyze_saas,
    generate_full_video,
    generate_scripts,
    research_saas_online,
    scrape_website,
)
from subtitles import generate_srt, burn_subtitles, generate_srt_from_video
from thumbnail import analyze_video_for_titles, refine_titles, generate_thumbnail, generate_youtube_description
# translation removed

logger = logging.getLogger("lambadaclips")

UPLOAD_DIR = os.fspath(UPLOAD_PATH)
OUTPUT_DIR = os.fspath(OUTPUT_PATH)

from app_core.globals import (
    state_db, job_queue, jobs, thumbnail_sessions, publish_jobs,
    enhance_jobs, saas_jobs, job_runtime, concurrency_semaphore, api_security
)


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
    safe_join(THUMBNAILS_DIR if "THUMBNAILS_DIR" in globals() else OUTPUT_DIR, *safe_parts)
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


def _persist_state() -> None:
    state_db.flush_all()


def _mark_interrupted_jobs() -> None:
    for store in (jobs, enhance_jobs, publish_jobs, saas_jobs):
        for record in store.values():
            if record.get("status") in {"queued", "processing", "uploading"}:
                record["status"] = "failed"
                record["error"] = "Server restarted before the job completed"
                record.setdefault("logs", []).append(record["error"])
    _persist_state()


_mark_interrupted_jobs()

def _relocate_root_job_artifacts(job_id: str, job_output_dir: str) -> bool:
    """
    Backward-compat rescue:
    If main.py accidentally wrote metadata/clips into OUTPUT_DIR root (e.g. output/<jobid>_...),
    move them into output/<job_id>/ so the API can find and serve them.
    """
    try:
        os.makedirs(job_output_dir, exist_ok=True)
        root = OUTPUT_DIR
        pattern = os.path.join(root, f"{job_id}_*_metadata.json")
        meta_candidates = sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p), reverse=True)
        if not meta_candidates:
            return False

        # Move the newest metadata and its associated clips.
        metadata_path = meta_candidates[0]
        base_name = os.path.basename(metadata_path).replace("_metadata.json", "")

        # Move metadata
        dest_metadata = os.path.join(job_output_dir, os.path.basename(metadata_path))
        if os.path.abspath(metadata_path) != os.path.abspath(dest_metadata):
            shutil.move(metadata_path, dest_metadata)

        # Move any clips that match the same base_name into the job folder
        clip_pattern = os.path.join(root, f"{base_name}_clip_*.mp4")
        for clip_path in glob.glob(clip_pattern):
            dest_clip = os.path.join(job_output_dir, os.path.basename(clip_path))
            if os.path.abspath(clip_path) != os.path.abspath(dest_clip):
                shutil.move(clip_path, dest_clip)

        # Also move any temp_ clips that might remain
        temp_clip_pattern = os.path.join(root, f"temp_{base_name}_clip_*.mp4")
        for clip_path in glob.glob(temp_clip_pattern):
            dest_clip = os.path.join(job_output_dir, os.path.basename(clip_path))
            if os.path.abspath(clip_path) != os.path.abspath(dest_clip):
                shutil.move(clip_path, dest_clip)

        return True
    except Exception:
        return False

async def cleanup_jobs():
    """Background task to remove old jobs and files."""
    print("🧹 Cleanup task started.")
    while True:
        try:
            await asyncio.sleep(300) # Check every 5 minutes
            now = time.time()
            
            # Simple directory cleanup based on modification time
            # Check OUTPUT_DIR
            for job_id in os.listdir(OUTPUT_DIR):
                job_path = os.path.join(OUTPUT_DIR, job_id)
                if os.path.isdir(job_path):
                    if now - os.path.getmtime(job_path) > JOB_RETENTION_SECONDS:
                        print(f"🧹 Purging old job: {job_id}")
                        shutil.rmtree(job_path, ignore_errors=True)
                        if job_id in jobs:
                            del jobs[job_id]

            saas_expired = [
                jid for jid, jdata in list(saas_jobs.items())
                if jdata.get("status") in ("completed", "failed")
                and jdata.get("output_dir")
                and os.path.isdir(jdata["output_dir"])
                and now - os.path.getmtime(jdata["output_dir"]) > JOB_RETENTION_SECONDS
            ]
            for jid in saas_expired:
                del saas_jobs[jid]

            # Cleanup Uploads
            for filename in os.listdir(UPLOAD_DIR):
                file_path = os.path.join(UPLOAD_DIR, filename)
                try:
                    if now - os.path.getmtime(file_path) > JOB_RETENTION_SECONDS:
                         os.remove(file_path)
                except OSError as exc:
                    logger.warning("Could not remove expired upload %s: %s", file_path, exc)

        except Exception as e:
            print(f"⚠️ Cleanup error: {e}")

async def process_queue():
    """Background worker to process jobs from the queue with concurrency limit."""
    print(f"🚀 Job Queue Worker started with {MAX_CONCURRENT_JOBS} concurrent slots.")
    while True:
        try:
            # Wait for a job
            job_id = await job_queue.get()
            
            # Acquire semaphore slot (waits if max jobs are running)
            await concurrency_semaphore.acquire()
            print(f"🔄 Acquired slot for job: {job_id}")

            # Process in background task to not block the loop (allowing other slots to fill)
            asyncio.create_task(run_job_wrapper(job_id))
            
        except Exception as e:
            print(f"❌ Queue dispatch error: {e}")
            await asyncio.sleep(1)


async def persist_state_periodically():
    while True:
        await asyncio.sleep(2)
        await asyncio.to_thread(_persist_state)


async def run_job_wrapper(job_id):
    """Wrapper to run job and release semaphore"""
    try:
        job = jobs.get(job_id)
        if job:
            await run_job(job_id, job)
    except Exception as e:
         print(f"❌ Job wrapper error {job_id}: {e}")
    finally:
        # Always release semaphore and mark queue task done
        concurrency_semaphore.release()
        job_queue.task_done()
        print(f"✅ Released slot for job: {job_id}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    from app_core.routes.saasshorts import process_saas_queue
    worker_task = asyncio.create_task(process_queue())
    saas_task = asyncio.create_task(process_saas_queue())
    cleanup_task = asyncio.create_task(cleanup_jobs())
    persistence_task = asyncio.create_task(persist_state_periodically())
    try:
        yield
    finally:
        for task in (worker_task, saas_task, cleanup_task, persistence_task):
            task.cancel()
        for task in (worker_task, saas_task, cleanup_task, persistence_task):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await asyncio.to_thread(_persist_state)

app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def secure_api(request: Request, call_next):
    return await api_security.middleware(request, call_next)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for serving videos
app.mount("/videos", StaticFiles(directory=OUTPUT_DIR), name="videos")

# Mount static files for serving thumbnails
THUMBNAILS_DIR = os.path.join(OUTPUT_DIR, "thumbnails")
os.makedirs(THUMBNAILS_DIR, exist_ok=True)
app.mount("/thumbnails", StaticFiles(directory=THUMBNAILS_DIR), name="thumbnails")

from app_core.routes.process import run_job

@app.get("/api/config")
async def get_config():
    return {"youtubeUrlEnabled": not DISABLE_YOUTUBE_URL}


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

from app_core.routes.process import router as process_router
from app_core.routes.editor import router as editor_router
from app_core.routes.subtitle import router as subtitle_router
from app_core.routes.social import router as social_router
from app_core.routes.thumbnails import router as thumbnails_router
from app_core.routes.saasshorts import router as saasshorts_router, process_saas_queue

app.include_router(process_router)
app.include_router(editor_router)
app.include_router(subtitle_router)
app.include_router(social_router)
app.include_router(thumbnails_router)
app.include_router(saasshorts_router)

@app.get('/api/voices')
async def get_voices():
    from saasshorts import DEFAULT_VOICES
    return {
        'voices': [
            {'voice_id': vid, 'name': name, 'category': 'default'}
            for name, vid in DEFAULT_VOICES.items()
        ],
        'source': 'defaults',
    }

if __name__ == '__main__':
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
