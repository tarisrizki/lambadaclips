import os
import time
import glob
import json
import uuid
import shutil
import asyncio
import subprocess
import httpx
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel

from app_core.executors import cpu_executor
from app_core.globals import jobs, enhance_jobs
from app_core.utils import _job_dir, _video_url, _job_media, update_clip_url
from app_core.paths import safe_filename
from editor import VideoEditor
from subtitles import add_hook_to_video


router = APIRouter()

class EditRequest(BaseModel):
    job_id: str
    clip_index: int
    api_key: Optional[str] = None
    input_filename: Optional[str] = None

@router.post("/api/edit")
async def edit_clip(
    req: EditRequest,
    x_gemini_key: Optional[str] = Header(None, alias="X-Gemini-Key")
):
    final_api_key = req.api_key or x_gemini_key or os.environ.get("GEMINI_API_KEY")
    if not final_api_key:
        raise HTTPException(status_code=400, detail="Missing Gemini API Key (Header or Body)")

    if req.job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[req.job_id]
    if 'result' not in job or 'clips' not in job['result']:
        raise HTTPException(status_code=400, detail="Job result not available")
        
    try:
        if req.input_filename:
            safe_name = safe_filename(req.input_filename)
            input_path = _job_media(req.job_id, safe_name)
            filename = safe_name
        else:
            clip = job['result']['clips'][req.clip_index]
            filename = clip['video_url'].split('/')[-1].split('?')[0]
            input_path = _job_media(req.job_id, filename)
        
        if not os.path.exists(input_path):
             raise HTTPException(status_code=404, detail=f"Video file not found: {input_path}")

        edited_filename = f"edited_{filename}"
        output_path = _job_media(req.job_id, edited_filename)
        
        def run_edit():
            editor = VideoEditor(api_key=final_api_key)
            safe_filename = f"temp_input_{req.job_id}.mp4"
            safe_input_path = _job_media(req.job_id, safe_filename)
            shutil.copy(input_path, safe_input_path)
            
            try:
                vid_file = editor.upload_video(safe_input_path)
                
                import cv2
                cap = cv2.VideoCapture(safe_input_path)
                fps = cap.get(cv2.CAP_PROP_FPS)
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                duration = frame_count / fps if fps else 0
                cap.release()
                
                transcript = None
                try:
                    meta_files = glob.glob(os.path.join(_job_dir(req.job_id), "*_metadata.json"))
                    if meta_files:
                        with open(meta_files[0], 'r') as f:
                            data = json.load(f)
                            transcript = data.get('transcript')
                except Exception as e:
                    print(f"⚠️ Could not load transcript for editing context: {e}")

                filter_data = editor.get_ffmpeg_filter(vid_file, duration, fps=fps, width=width, height=height, transcript=transcript)
                
                safe_output_path = _job_media(req.job_id, f"temp_output_{req.job_id}.mp4")
                editor.apply_edits(safe_input_path, safe_output_path, filter_data)
                
                if os.path.exists(safe_output_path):
                    shutil.move(safe_output_path, output_path)
                
                return filter_data
            finally:
                if os.path.exists(safe_input_path):
                    os.remove(safe_input_path)

        loop = asyncio.get_running_loop()
        plan = await loop.run_in_executor(cpu_executor, run_edit)
        new_video_url = update_clip_url(req.job_id, req.clip_index, edited_filename)
        
        return {
            "success": True, 
            "new_video_url": new_video_url,
            "edit_plan": plan
        }

    except Exception as e:
        print(f"❌ Edit Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class EnhanceRequest(BaseModel):
    job_id: str
    clip_index: int

class EnhanceResponse(BaseModel):
    enhance_id: str
    status: str

@router.post("/api/enhance", response_model=EnhanceResponse)
async def enhance_clip(req: EnhanceRequest):
    if req.job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
        
    job = jobs[req.job_id]
    output_dir = job.get('output_dir')
    if not output_dir or not os.path.exists(output_dir):
        raise HTTPException(status_code=404, detail="Job output directory not found")
        
    json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))
    if not json_files:
        raise HTTPException(status_code=404, detail="Metadata not found")
        
    target_json = json_files[0]
    with open(target_json, 'r') as f:
        data = json.load(f)
        
    clips = data.get('shorts', [])
    if req.clip_index < 0 or req.clip_index >= len(clips):
        raise HTTPException(status_code=400, detail="Invalid clip index")
        
    clip = clips[req.clip_index]
    original_video = data.get('original_video')
    
    if not original_video or not os.path.exists(original_video):
        all_videos = glob.glob(os.path.join(output_dir, "*.mp4")) + glob.glob(os.path.join(output_dir, "*.webm"))
        original_video = None
        for v in all_videos:
            if "_clip_" not in v and "_vertical" not in v and "_fullhd" not in v:
                original_video = v
                break
                
    if not original_video or not os.path.exists(original_video):
        raise HTTPException(status_code=404, detail="Original video not found, cannot enhance")

    base_name = os.path.basename(target_json).replace('_metadata.json', '')
    clip_filename = f"{base_name}_clip_{req.clip_index+1}.mp4"
    clip_path = os.path.join(output_dir, clip_filename)
    
    enhance_id = str(uuid.uuid4())
    enhance_jobs[enhance_id] = {'status': 'processing'}
    
    def run_enhance():
        try:
            print(f"🚀 Starting enhance for job {req.job_id} clip {req.clip_index}")
            from main import run_pipeline
            from app_core.routes.process import MainArgs
            
            args = MainArgs(
                input=original_video,
                url=None,
                output=clip_path,
                keep_original=False,
                skip_analysis=False,
                enhance=True,
                start=clip['start'],
                end=clip['end']
            )
            run_pipeline(args)
            
            enhance_jobs[enhance_id]['status'] = 'completed'
            enhance_jobs[enhance_id]['clip_url'] = _video_url(req.job_id, clip_filename)
        except Exception as e:
            enhance_jobs[enhance_id]['status'] = 'failed'
            enhance_jobs[enhance_id]['error'] = str(e)
            print(f"❌ Enhance exception: {e}")

    loop = asyncio.get_running_loop()
    loop.run_in_executor(cpu_executor, run_enhance)
    
    return EnhanceResponse(enhance_id=enhance_id, status="processing")

@router.get("/api/enhance/status/{enhance_id}")
async def get_enhance_status(enhance_id: str):
    if enhance_id not in enhance_jobs:
        raise HTTPException(status_code=404, detail="Enhance job not found")
    return enhance_jobs[enhance_id]

class HookRequest(BaseModel):
    job_id: str
    clip_index: int
    text: str
    input_filename: Optional[str] = None
    position: Optional[str] = "top"
    size: Optional[str] = "M"

@router.post("/api/hook")
async def add_hook(req: HookRequest):
    if req.job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[req.job_id]
    output_dir = _job_dir(req.job_id)
    json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))
    
    if not json_files:
        raise HTTPException(status_code=404, detail="Metadata not found")
        
    with open(json_files[0], 'r') as f:
        data = json.load(f)
        
    clips = data.get('shorts', [])
    if req.clip_index >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
        
    clip_data = clips[req.clip_index]
    
    if req.input_filename:
        filename = safe_filename(req.input_filename)
    else:
        filename = clip_data.get('video_url', '').split('/')[-1].split('?')[0]
        if not filename:
             base_name = os.path.basename(json_files[0]).replace('_metadata.json', '')
             filename = f"{base_name}_clip_{req.clip_index+1}.mp4"
         
    input_path = os.path.join(output_dir, filename)
    if not os.path.exists(input_path):
        raise HTTPException(status_code=404, detail=f"Video file not found: {input_path}")
        
    output_filename = f"hook_{filename}"
    output_path = os.path.join(output_dir, output_filename)
    
    size_map = {"S": 0.8, "M": 1.0, "L": 1.3}
    font_scale = size_map.get(req.size, 1.0)
    
    try:
        def run_hook():
             add_hook_to_video(input_path, req.text, output_path, position=req.position, font_scale=font_scale)
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(cpu_executor, run_hook)
        
    except Exception as e:
        print(f"❌ Hook Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
    try:
        new_video_url = update_clip_url(req.job_id, req.clip_index, output_filename)
    except (OSError, json.JSONDecodeError):
        new_video_url = _video_url(req.job_id, output_filename)

    return {"success": True, "new_video_url": new_video_url}

class TranslateRequest(BaseModel):
    job_id: str
    clip_index: int
    target_language: str
    source_language: Optional[str] = None
    input_filename: Optional[str] = None

@router.get("/api/translate/languages")
async def get_languages():
    return {"languages": []}

@router.post("/api/translate")
async def translate_clip(
    req: TranslateRequest,
):
    # Translation feature is currently disabled
    raise HTTPException(status_code=501, detail="Translation feature is not available. This feature is currently disabled.")

class EffectsGenerateRequest(BaseModel):
    job_id: str
    clip_index: int
    input_filename: Optional[str] = None

@router.post("/api/effects/generate")
async def generate_effects_config(
    req: EffectsGenerateRequest,
    x_gemini_key: Optional[str] = Header(None, alias="X-Gemini-Key")
):
    final_api_key = x_gemini_key or os.environ.get("GEMINI_API_KEY")

    if not final_api_key:
        raise HTTPException(status_code=400, detail="Missing Gemini API Key (Header)")

    if req.job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[req.job_id]
    if 'result' not in job or 'clips' not in job['result']:
        raise HTTPException(status_code=400, detail="Job result not available")

    try:
        if req.input_filename:
            safe_name = safe_filename(req.input_filename)
            input_path = _job_media(req.job_id, safe_name)
        else:
            clip = job['result']['clips'][req.clip_index]
            filename = clip['video_url'].split('/')[-1]
            input_path = _job_media(req.job_id, filename)

        if not os.path.exists(input_path):
            raise HTTPException(status_code=404, detail=f"Video file not found: {input_path}")

        def run_effects_generation():
            editor = VideoEditor(api_key=final_api_key)

            safe_filename = f"temp_effects_{req.job_id}.mp4"
            safe_input_path = _job_media(req.job_id, safe_filename)
            shutil.copy(input_path, safe_input_path)

            try:
                vid_file = editor.upload_video(safe_input_path)

                probe_cmd = [
                    'ffprobe', '-v', 'error',
                    '-select_streams', 'v:0',
                    '-show_entries', 'stream=width,height,r_frame_rate,duration',
                    '-show_entries', 'format=duration',
                    '-of', 'json',
                    safe_input_path
                ]
                probe_result = subprocess.check_output(probe_cmd).decode().strip()
                probe_data = json.loads(probe_result)

                stream = probe_data.get('streams', [{}])[0]
                width = int(stream.get('width', 1080))
                height = int(stream.get('height', 1920))

                r_frame_rate = stream.get('r_frame_rate', '30/1')
                num, den = r_frame_rate.split('/')
                fps = round(int(num) / int(den), 2)

                duration = float(stream.get('duration', 0))
                if duration == 0:
                    duration = float(probe_data.get('format', {}).get('duration', 0))

                transcript = None
                try:
                    meta_files = glob.glob(os.path.join(_job_dir(req.job_id), "*_metadata.json"))
                    if meta_files:
                        with open(meta_files[0], 'r') as f:
                            data = json.load(f)
                            transcript = data.get('transcript')
                except Exception as e:
                    print(f"⚠️ Could not load transcript for effects config: {e}")

                effects_config = editor.get_effects_config(
                    vid_file, duration, fps=fps, width=width, height=height, transcript=transcript
                )

                return effects_config
            finally:
                if os.path.exists(safe_input_path):
                    os.remove(safe_input_path)

        loop = asyncio.get_running_loop()
        effects_config = await loop.run_in_executor(cpu_executor, run_effects_generation)

        if effects_config is None:
            raise HTTPException(status_code=500, detail="Failed to generate effects config from Gemini")

        return {"effects": effects_config}

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Effects Generation Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

RENDER_SERVICE_URL = os.getenv("RENDER_SERVICE_URL", "http://renderer:3100")

@router.post("/api/render")
async def proxy_render(request: Request):
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{RENDER_SERVICE_URL}/render", json=body)
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Render service unavailable: {e}")

@router.get("/api/render/{render_id}")
async def proxy_render_status(render_id: str):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{RENDER_SERVICE_URL}/render/{render_id}")
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Render service unavailable: {e}")
