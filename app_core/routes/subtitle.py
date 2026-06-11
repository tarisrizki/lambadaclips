import os
import time
import glob
import json
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app_core.globals import jobs, _job_dir, _video_url
from app_core.paths import safe_filename
from app_core.utils import update_clip_url
from subtitles import generate_srt, generate_srt_from_video, burn_subtitles

router = APIRouter()

class SubtitleRequest(BaseModel):
    job_id: str
    clip_index: int
    position: str = "bottom"
    font_size: int = 16
    font_name: str = "Verdana"
    font_color: str = "#FFFFFF"
    border_color: str = "#000000"
    border_width: int = 2
    bg_color: str = "#000000"
    bg_opacity: float = 0.0
    input_filename: Optional[str] = None

@router.get("/api/clip/{job_id}/{clip_index}/transcript")
async def get_clip_transcript(job_id: str, clip_index: int):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    output_dir = _job_dir(job_id)
    json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))

    if not json_files:
        raise HTTPException(status_code=404, detail="Metadata not found")

    with open(json_files[0], 'r') as f:
        data = json.load(f)

    transcript = data.get('transcript')
    if not transcript:
        raise HTTPException(status_code=400, detail="Transcript not found in metadata")

    clips = data.get('shorts', [])
    if clip_index >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")

    clip_data = clips[clip_index]
    clip_start = clip_data.get('start', 0)
    clip_end = clip_data.get('end', 0)

    captions = []
    for segment in transcript.get('segments', []):
        for word_info in segment.get('words', []):
            if word_info['end'] > clip_start and word_info['start'] < clip_end:
                captions.append({
                    "text": word_info.get('word', '').strip(),
                    "startMs": int((max(0, word_info['start'] - clip_start)) * 1000),
                    "endMs": int((max(0, word_info['end'] - clip_start)) * 1000),
                })

    duration_sec = clip_end - clip_start

    return {
        "captions": captions,
        "durationSec": duration_sec,
        "language": transcript.get('language', 'en'),
    }

@router.post("/api/subtitle")
async def add_subtitles(req: SubtitleRequest):
    if req.job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[req.job_id]
    output_dir = _job_dir(req.job_id)
    json_files = glob.glob(os.path.join(output_dir, "*_metadata.json"))
    
    if not json_files:
        raise HTTPException(status_code=404, detail="Metadata not found")
        
    with open(json_files[0], 'r') as f:
        data = json.load(f)
        
    transcript = data.get('transcript')
    if not transcript:
        raise HTTPException(status_code=400, detail="Transcript not found in metadata. Please process a new video.")
        
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
        
    srt_filename = f"subs_{req.clip_index}_{int(time.time())}.srt"
    srt_path = os.path.join(output_dir, srt_filename)
    
    output_filename = f"subtitled_{filename}"
    output_path = os.path.join(output_dir, output_filename)
    
    try:
        is_dubbed = filename.startswith("translated_")

        if is_dubbed:
            print(f"🎙️ Dubbed video detected, transcribing audio for subtitles...")
            def run_transcribe_srt():
                return generate_srt_from_video(input_path, srt_path)

            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(None, run_transcribe_srt)
        else:
            success = generate_srt(transcript, clip_data['start'], clip_data['end'], srt_path)

        if not success:
             raise HTTPException(status_code=400, detail="No words found for this clip range.")

        def run_burn():
             burn_subtitles(input_path, srt_path, output_path,
                           alignment=req.position, fontsize=req.font_size,
                           font_name=req.font_name, font_color=req.font_color,
                           border_color=req.border_color, border_width=req.border_width,
                           bg_color=req.bg_color, bg_opacity=req.bg_opacity)
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, run_burn)
        
    except Exception as e:
        print(f"❌ Subtitle Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
    try:
        new_video_url = update_clip_url(req.job_id, req.clip_index, output_filename)
    except (OSError, json.JSONDecodeError):
        new_video_url = _video_url(req.job_id, output_filename)

    return {"success": True, "new_video_url": new_video_url}
