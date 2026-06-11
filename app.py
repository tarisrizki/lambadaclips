import os
import shutil
import time
import asyncio
import logging
import contextlib
from dotenv import load_dotenv

load_dotenv()

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app_core.config import (
    CORS_ORIGINS,
    DISABLE_YOUTUBE_URL,
    JOB_RETENTION_SECONDS,
    MAX_CONCURRENT_JOBS,
    OUTPUT_DIR as OUTPUT_PATH,
    UPLOAD_DIR as UPLOAD_PATH,
)

logger = logging.getLogger("lambadaclips")

UPLOAD_DIR = os.fspath(UPLOAD_PATH)
OUTPUT_DIR = os.fspath(OUTPUT_PATH)

from app_core.globals import (
    state_db, job_queue, jobs, publish_jobs,
    enhance_jobs, saas_jobs, job_runtime, concurrency_semaphore, api_security
)


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



async def cleanup_jobs():
    """Background task to remove old jobs and files."""
    logger.info("🧹 Cleanup task started.")
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
                        logger.info("🧹 Purging old job: %s", job_id)
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
            logger.warning("Cleanup error: %s", e)

async def process_queue():
    """Background worker to process jobs from the queue with concurrency limit."""
    logger.info("🚀 Job Queue Worker started with %d concurrent slots.", MAX_CONCURRENT_JOBS)
    while True:
        try:
            # Wait for a job
            job_id = await job_queue.get()
            
            # Acquire semaphore slot (waits if max jobs are running)
            await concurrency_semaphore.acquire()
            logger.info("🔄 Acquired slot for job: %s", job_id)

            # Process in background task to not block the loop (allowing other slots to fill)
            asyncio.create_task(run_job_wrapper(job_id))
            
        except Exception as e:
            logger.error("Queue dispatch error: %s", e)
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
         logger.error("Job wrapper error %s: %s", job_id, e)
    finally:
        # Always release semaphore and mark queue task done
        concurrency_semaphore.release()
        job_queue.task_done()
        logger.info("✅ Released slot for job: %s", job_id)

@asynccontextmanager
async def lifespan(app: FastAPI):
    _mark_interrupted_jobs()
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
