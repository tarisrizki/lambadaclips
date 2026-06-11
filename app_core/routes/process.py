import os
import time
import glob
import json
import uuid
import shutil
import contextlib
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Form, Request
from pydantic import BaseModel

from app_core.globals import jobs, job_runtime, job_queue, api_security
from app_core.executors import cpu_executor, io_executor
from app_core.config import MAX_FILE_SIZE_MB, DISABLE_YOUTUBE_URL, UPLOAD_DIR
from app_core.paths import safe_join, safe_upload_suffix, validate_uuid
from app_core.utils import validate_youtube_url, _job_dir, _video_url
from app_core.s3_uploader import upload_job_artifacts

router = APIRouter()

class ProcessRequest(BaseModel):
    url: str

class MainArgs:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

def execute_pipeline_sync(job_id, main_args, env_vars, output_dir):
    def log_callback(message: str):
        msg = message.strip()
        if msg:
            print(f"📝 [Job Output] {msg}")
            if job_id in jobs:
                jobs[job_id]['logs'].append(msg)
            
    from main import run_pipeline
    
    args = MainArgs(**main_args)
    try:
        run_pipeline(args, env_override=env_vars, log_callback=log_callback)
        return 0
    except RuntimeError as e:
        log_callback(f"Pipeline error: {e}")
        return 1
    except Exception as e:
        log_callback(f"Pipeline failed: {e}")
        return 1

async def run_job(job_id, job_data):
    """Executes the pipeline for a specific job."""
    runtime = job_runtime.get(job_id)
    if not runtime:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["logs"].append("Runtime data is unavailable.")
        return

    main_args = runtime["main_args"]
    env = runtime["env"]
    output_dir = job_data['output_dir']
    
    jobs[job_id]['status'] = 'processing'
    jobs[job_id]['logs'].append("Job started by worker.")
    print(f"🎬 [run_job] Executing pipeline for {job_id}")
    
    # Run in a separate thread so it doesn't block the event loop
    returncode = await asyncio.to_thread(execute_pipeline_sync, job_id, main_args, env, output_dir)
    
    # After it finishes, collect results
    try:
        json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))
        if json_files:
            target_json = json_files[0]
            if os.path.getsize(target_json) > 0:
                with open(target_json, 'r') as f:
                    data = json.load(f)
                    
                base_name = os.path.basename(target_json).replace('_metadata.json', '')
                clips = data.get('shorts', [])
                cost_analysis = data.get('cost_analysis')
                
                ready_clips = []
                for i, clip in enumerate(clips):
                     clip_filename = f"{base_name}_clip_{i+1}.mp4"
                     clip_path = os.path.join(output_dir, clip_filename)
                     if os.path.exists(clip_path) and os.path.getsize(clip_path) > 0:
                         clip['video_url'] = _video_url(job_id, clip_filename)
                         ready_clips.append(clip)
                
                if ready_clips:
                     jobs[job_id]['result'] = {'clips': ready_clips, 'cost_analysis': cost_analysis}
    except Exception as exc:
        print(f"Error reading metadata for job {job_id}: {exc}")

    if returncode == 0:
        jobs[job_id]['status'] = 'completed'
        jobs[job_id]['logs'].append("Process finished successfully.")
        
        # Start S3 upload in background (silent, non-blocking)
        loop = asyncio.get_running_loop()
        loop.run_in_executor(io_executor, upload_job_artifacts, output_dir, job_id)
    else:
        jobs[job_id]['status'] = 'failed'
        jobs[job_id]['logs'].append(f"Process failed with exit code {returncode}.")

    # Prevent API key leak by removing runtime data when job finishes
    if job_id in job_runtime:
        del job_runtime[job_id]


@router.post("/api/process")
async def process_endpoint(
    request: Request,
    url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    prompt_template: Optional[str] = Form(None),
    acknowledged: Optional[str] = Form(None),
    x_gemini_key: Optional[str] = Header(None, alias="X-Gemini-Key")
):
    api_key = x_gemini_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=400, detail="Missing Gemini API Key (Header or Environment)")

    if not url and not file:
        raise HTTPException(status_code=400, detail="Must provide URL or File")

    if str(acknowledged).lower() != "true":
        raise HTTPException(status_code=400, detail="Content ownership acknowledgement is required")

    if url and DISABLE_YOUTUBE_URL:
        raise HTTPException(status_code=403, detail="YouTube URL ingest is disabled on this deployment. Please upload a file you own.")

    if url:
        validate_youtube_url(url)

    client_ip = request.client.host if request.client else "unknown"
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        client_ip = fwd.split(",")[0].strip()
    user_agent = request.headers.get("user-agent", "")
    attestation = {
        "acknowledged": True,
        "ip": client_ip,
        "user_agent": user_agent,
        "timestamp": time.time(),
        "source": "url" if url else "file",
    }

    job_id = str(uuid.uuid4())
    job_output_dir = _job_dir(job_id)
    os.makedirs(job_output_dir, exist_ok=True)

    main_args = {
        "input": None,
        "url": None,
        "output": job_output_dir,
        "keep_original": False,
        "skip_analysis": False,
        "enhance": False,
        "start": None,
        "end": None
    }
    
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = api_key
    if prompt_template:
        if len(prompt_template) > 20_000:
            raise HTTPException(status_code=400, detail="Prompt template is too large")
        env["GEMINI_PROMPT_TEMPLATE_OVERRIDE"] = prompt_template

    if url:
        main_args["url"] = url
    else:
        input_path = safe_join(UPLOAD_DIR, f"{job_id}{safe_upload_suffix(file.filename)}")
        size = 0
        limit_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
        try:
            with open(input_path, "wb") as buffer:
                while content := await file.read(1024 * 1024):
                    size += len(content)
                    if size > limit_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"File too large. Max size {MAX_FILE_SIZE_MB}MB",
                        )
                    buffer.write(content)
        except Exception:
            with contextlib.suppress(OSError):
                os.remove(input_path)
            shutil.rmtree(job_output_dir, ignore_errors=True)
            raise
        main_args["input"] = input_path

    print(f"[attestation] job={job_id} ip={attestation['ip']} source={attestation['source']} ack=true")

    jobs[job_id] = {
        'status': 'queued',
        'logs': [f"Job {job_id} queued."],
        'output_dir': job_output_dir,
        'attestation': attestation
    }
    job_runtime[job_id] = {"main_args": main_args, "env": env}

    await job_queue.put(job_id)

    return {"job_id": job_id, "status": "queued"}

@router.get("/api/status/{job_id}")
async def get_status(job_id: str):
    job_id = validate_uuid(job_id, "job ID")
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    return {
        "status": job['status'],
        "logs": job['logs'],
        "result": job.get('result')
    }
