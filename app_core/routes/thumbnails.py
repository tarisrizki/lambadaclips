import os
import json
import uuid
import asyncio
from typing import Optional
from urllib.parse import urlparse
import httpx

from fastapi import APIRouter, HTTPException, Header, Request, Form, File, UploadFile, BackgroundTasks
from pydantic import BaseModel

from app_core.globals import thumbnail_sessions, publish_jobs, thumbnail_sessions_runtime
from app_core.executors import cpu_executor, io_executor
from app_core.config import DISABLE_YOUTUBE_URL, UPLOAD_DIR, MAX_FILE_SIZE_MB, OUTPUT_DIR, THUMBNAILS_DIR
from app_core.paths import safe_join, safe_upload_suffix, validate_uuid
from app_core.utils import validate_youtube_url, save_upload_limited, _thumbnail_url

from thumbnail import analyze_video_for_titles, refine_titles, generate_thumbnail, generate_youtube_description

router = APIRouter()

@router.post("/api/thumbnail/upload")
async def thumbnail_upload(
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
):
    if not url and not file:
        raise HTTPException(status_code=400, detail="Must provide URL or File")
    if url:
        if DISABLE_YOUTUBE_URL:
            raise HTTPException(status_code=403, detail="YouTube URL ingest is disabled")
        validate_youtube_url(url)

    session_id = str(uuid.uuid4())
    transcript_event = asyncio.Event()

    video_path = None
    if file:
        video_path = safe_join(
            UPLOAD_DIR, f"thumb_{session_id}{safe_upload_suffix(file.filename)}"
        )
        await save_upload_limited(
            file,
            video_path,
            max_bytes=MAX_FILE_SIZE_MB * 1024 * 1024,
        )

    thumbnail_sessions_runtime[session_id] = transcript_event

    thumbnail_sessions[session_id] = {
        "video_path": video_path,
        "transcript_ready": False,
        "transcript": None,
        "transcript_segments": [],
        "video_duration": 0,
        "language": "en",
        "context": "",
        "titles": [],
        "conversation": [],
        "_url": url,
    }

    async def run_background_whisper():
        try:
            vpath = video_path
            if not vpath and url:
                from main import download_youtube_video
                loop = asyncio.get_running_loop()
                vpath, _ = await loop.run_in_executor(io_executor, download_youtube_video, url, UPLOAD_DIR)
                thumbnail_sessions[session_id]["video_path"] = vpath

            from main import transcribe_video
            loop = asyncio.get_running_loop()
            transcript = await loop.run_in_executor(cpu_executor, transcribe_video, vpath)
            segments = transcript.get("segments", [])
            duration = segments[-1]["end"] if segments else 0

            thumbnail_sessions[session_id].update({
                "transcript_ready": True,
                "transcript": transcript,
                "transcript_segments": segments,
                "video_duration": duration,
                "language": transcript.get("language", "en"),
            })
            print(f"✅ [Thumbnail] Background Whisper complete for session {session_id}")
        except Exception as e:
            print(f"❌ [Thumbnail] Background Whisper failed: {e}")
            thumbnail_sessions[session_id]["transcript_error"] = str(e)
        finally:
            transcript_event.set()

    asyncio.create_task(run_background_whisper())

    return {"session_id": session_id}


@router.post("/api/thumbnail/analyze")
async def thumbnail_analyze(
    request: Request,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    x_gemini_key: Optional[str] = Header(None, alias="X-Gemini-Key")
):
    api_key = x_gemini_key
    if not api_key:
        raise HTTPException(status_code=400, detail="Missing X-Gemini-Key header")

    pre_transcript = None

    if session_id and session_id in thumbnail_sessions:
        session = thumbnail_sessions[session_id]

        transcript_event = thumbnail_sessions_runtime.get(session_id)
        if transcript_event:
            print(f"⏳ [Thumbnail] Waiting for background Whisper to finish...")
            await transcript_event.wait()

        if session.get("transcript_error"):
            raise HTTPException(status_code=500, detail=f"Transcription failed: {session['transcript_error']}")

        video_path = session["video_path"]
        if not video_path or not os.path.exists(video_path):
            raise HTTPException(status_code=404, detail="Video file not found in session")

        if session.get("transcript_ready"):
            pre_transcript = session["transcript"]
    else:
        if not url and not file:
            raise HTTPException(status_code=400, detail="Must provide URL, File, or session_id")

        session_id = str(uuid.uuid4())

        if url:
            if DISABLE_YOUTUBE_URL:
                raise HTTPException(status_code=403, detail="YouTube URL ingest is disabled")
            validate_youtube_url(url)
            from main import download_youtube_video
            video_path, _ = await loop.run_in_executor(io_executor, download_youtube_video, url, UPLOAD_DIR)
        else:
            video_path = safe_join(
                UPLOAD_DIR, f"thumb_{session_id}{safe_upload_suffix(file.filename)}"
            )
            await save_upload_limited(
                file,
                video_path,
                max_bytes=MAX_FILE_SIZE_MB * 1024 * 1024,
            )

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(cpu_executor, analyze_video_for_titles, api_key, video_path, pre_transcript)

        if session_id not in thumbnail_sessions:
            thumbnail_sessions[session_id] = {}

        thumbnail_sessions[session_id].update({
            "context": result.get("transcript_summary", ""),
            "titles": result.get("titles", []),
            "language": result.get("language", "en"),
            "conversation": thumbnail_sessions[session_id].get("conversation", []),
            "video_path": video_path,
            "transcript_segments": result.get("segments", []),
            "video_duration": result.get("video_duration", 0)
        })

        return {
            "session_id": session_id,
            "titles": result.get("titles", []),
            "context": result.get("transcript_summary", ""),
            "language": result.get("language", "en"),
            "recommended": result.get("recommended", [])
        }

    except Exception as e:
        print(f"❌ Thumbnail Analyze Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ThumbnailTitlesRequest(BaseModel):
    session_id: Optional[str] = None
    message: Optional[str] = None
    title: Optional[str] = None

@router.post("/api/thumbnail/titles")
async def thumbnail_titles(
    req: ThumbnailTitlesRequest,
    x_gemini_key: Optional[str] = Header(None, alias="X-Gemini-Key")
):
    api_key = x_gemini_key
    if not api_key:
        raise HTTPException(status_code=400, detail="Missing X-Gemini-Key header")

    if req.title:
        session_id = req.session_id or str(uuid.uuid4())
        if session_id not in thumbnail_sessions:
            thumbnail_sessions[session_id] = {
                "context": "",
                "titles": [req.title],
                "language": "en",
                "conversation": []
            }
        return {"session_id": session_id, "titles": [req.title]}

    if not req.session_id or req.session_id not in thumbnail_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    if not req.message:
        raise HTTPException(status_code=400, detail="Must provide message or title")

    session = thumbnail_sessions[req.session_id]
    session["conversation"].append({"role": "user", "content": req.message})

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            cpu_executor,
            refine_titles,
            api_key,
            session["context"],
            req.message,
            session["conversation"]
        )

        new_titles = result.get("titles", [])
        session["titles"] = new_titles
        session["conversation"].append({"role": "assistant", "content": json.dumps(new_titles)})

        return {"titles": new_titles}

    except Exception as e:
        print(f"❌ Thumbnail Titles Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/thumbnail/generate")
async def thumbnail_generate(
    request: Request,
    session_id: str = Form(...),
    title: str = Form(...),
    extra_prompt: str = Form(""),
    count: int = Form(3),
    face: Optional[UploadFile] = File(None),
    background: Optional[UploadFile] = File(None),
    x_gemini_key: Optional[str] = Header(None, alias="X-Gemini-Key")
):
    api_key = x_gemini_key
    if not api_key:
        raise HTTPException(status_code=400, detail="Missing X-Gemini-Key header")

    count = min(max(1, count), 6)

    face_path = None
    bg_path = None
    session_id = validate_uuid(session_id, "session ID")
    thumb_upload_dir = safe_join(UPLOAD_DIR, f"thumb_{session_id}")
    os.makedirs(thumb_upload_dir, exist_ok=True)

    try:
        if face and face.filename:
            face_path = safe_join(
                thumb_upload_dir, f"face{safe_upload_suffix(face.filename)}"
            )
            await save_upload_limited(face, face_path, max_bytes=20 * 1024 * 1024)

        if background and background.filename:
            bg_path = safe_join(
                thumb_upload_dir, f"background{safe_upload_suffix(background.filename)}"
            )
            await save_upload_limited(
                background, bg_path, max_bytes=20 * 1024 * 1024
            )

        video_context = ""
        if session_id in thumbnail_sessions:
            video_context = thumbnail_sessions[session_id].get("context", "")

        loop = asyncio.get_running_loop()
        thumbnails = await loop.run_in_executor(
            cpu_executor,
            generate_thumbnail,
            api_key,
            title,
            session_id,
            face_path,
            bg_path,
            extra_prompt,
            count,
            video_context
        )

        if not thumbnails:
            raise HTTPException(status_code=500, detail="Thumbnail generation failed.")

        signed_thumbnails = [
            _thumbnail_url(urlparse(url).path.removeprefix("/thumbnails/"))
            for url in thumbnails
        ]
        return {"thumbnails": signed_thumbnails}

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Thumbnail Generate Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ThumbnailDescribeRequest(BaseModel):
    session_id: str
    title: str

@router.post("/api/thumbnail/describe")
async def thumbnail_describe(
    req: ThumbnailDescribeRequest,
    x_gemini_key: Optional[str] = Header(None, alias="X-Gemini-Key")
):
    api_key = x_gemini_key
    if not api_key:
        raise HTTPException(status_code=400, detail="Missing X-Gemini-Key header")

    if req.session_id not in thumbnail_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = thumbnail_sessions[req.session_id]
    segments = session.get("transcript_segments", [])
    if not segments:
        raise HTTPException(status_code=400, detail="No transcript segments available. Please analyze a video first.")

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            cpu_executor,
            generate_youtube_description,
            api_key,
            req.title,
            segments,
            session.get("language", "en"),
            session.get("video_duration", 0)
        )
        return {"description": result.get("description", "")}

    except Exception as e:
        print(f"❌ Thumbnail Describe Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/thumbnail/publish")
async def thumbnail_publish(
    background_tasks: BackgroundTasks,
    session_id: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    thumbnail_url: str = Form(...),
    api_key: str = Form(...),
    user_id: str = Form(...),
):
    if session_id not in thumbnail_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = thumbnail_sessions[session_id]
    video_path = session.get("video_path")
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="Original video file not found")

    thumb_relative = urlparse(thumbnail_url).path.lstrip("/")
    if thumb_relative.startswith("thumbnails/"):
        thumb_path = safe_join(OUTPUT_DIR, *thumb_relative.split("/"))
    else:
        thumb_path = safe_join(THUMBNAILS_DIR, *thumb_relative.split("/"))

    if not os.path.exists(thumb_path):
        raise HTTPException(status_code=404, detail=f"Thumbnail file not found: {thumb_path}")

    publish_id = str(uuid.uuid4())
    publish_jobs[publish_id] = {"status": "uploading", "result": None, "error": None}

    def do_upload():
        try:
            upload_url = "https://api.upload-post.com/api/upload"
            headers = {"Authorization": f"Apikey {api_key}"}
            data_payload = {
                "user": user_id,
                "platform[]": ["youtube"],
                "title": title,          
                "async_upload": "true",
                "youtube_title": title,
                "youtube_description": description,
                "privacyStatus": "public",
            }
            video_filename = os.path.basename(video_path)
            thumb_filename = os.path.basename(thumb_path)

            print(f"📡 [Thumbnail] Publishing to YouTube via Upload-Post... (publish_id={publish_id})")
            with open(video_path, "rb") as vf, open(thumb_path, "rb") as tf:
                files = {
                    "video": (video_filename, vf.read(), "video/mp4"),
                    "thumbnail": (thumb_filename, tf.read(), "image/jpeg"),
                }

            with httpx.Client(timeout=600.0) as client:
                response = client.post(upload_url, headers=headers, data=data_payload, files=files)

            if response.status_code not in [200, 201, 202]:
                err = f"Upload-Post API Error ({response.status_code}): {response.text}"
                print(f"❌ {err}")
                publish_jobs[publish_id]["status"] = "failed"
                publish_jobs[publish_id]["error"] = err
            else:
                print(f"✅ [Thumbnail] Published successfully (publish_id={publish_id})")
                publish_jobs[publish_id]["status"] = "done"
                publish_jobs[publish_id]["result"] = response.json()

        except Exception as e:
            err = str(e)
            print(f"❌ Thumbnail Publish Background Error: {err}")
            publish_jobs[publish_id]["status"] = "failed"
            publish_jobs[publish_id]["error"] = err

    background_tasks.add_task(do_upload)
    return {"publish_id": publish_id, "status": "uploading"}

@router.get("/api/thumbnail/publish/status/{publish_id}")
async def thumbnail_publish_status(publish_id: str):
    if publish_id not in publish_jobs:
        raise HTTPException(status_code=404, detail="Publish job not found")
    return publish_jobs[publish_id]
