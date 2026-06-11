import os
import uuid
import contextlib
import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Header, UploadFile, File
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app_core.globals import saas_jobs, saas_queue, saas_concurrency_semaphore
from app_core.config import OUTPUT_DIR
from urllib.parse import urlparse
from app_core.paths import safe_join, validate_uuid, safe_filename
from app_core.utils import _named_video_url, save_upload_limited

from saasshorts import (
    scrape_website,
    research_saas_online,
    analyze_saas,
    generate_scripts,
    generate_actor_images,
    generate_saas_video,
)
from api_social import upload_video, upload_actor_to_s3, list_video_gallery, list_actor_gallery

router = APIRouter()

class SaaSAnalyzeRequest(BaseModel):
    url: Optional[str] = None
    description: Optional[str] = None
    num_scripts: int = 3
    style: str = "ugc"
    language: str = "en"
    actor_gender: str = "female"


@router.post("/api/saasshorts/analyze")
async def saasshorts_analyze(
    req: SaaSAnalyzeRequest,
    x_gemini_key: Optional[str] = Header(None, alias="X-Gemini-Key"),
):
    gemini_key = x_gemini_key or os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        raise HTTPException(status_code=400, detail="Missing Gemini API Key")

    if not req.url and not req.description:
        raise HTTPException(status_code=400, detail="Provide a URL or a product description")

    try:
        loop = asyncio.get_running_loop()

        def run_analysis():
            web_research = None

            if req.url and req.url.strip():
                scraped = scrape_website(req.url)
                web_research = research_saas_online(req.url, gemini_key)
                analysis = analyze_saas(scraped, gemini_key, web_research=web_research)
            else:
                analysis = {
                    "product_name": req.description.split(",")[0].strip()[:60] if req.description else "Product",
                    "description": req.description,
                    "value_proposition": req.description,
                    "target_audience": "general audience",
                    "key_features": [req.description],
                    "pain_points": [],
                    "tone": "casual and authentic",
                }

            scripts = generate_scripts(analysis, gemini_key, req.num_scripts, req.style, req.language, req.actor_gender)
            return {
                "analysis": analysis,
                "scripts": scripts,
                "web_research": web_research,
            }

        result = await loop.run_in_executor(None, run_analysis)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SaaSActorRequest(BaseModel):
    actor_description: str
    num_options: int = 3
    product_description: Optional[str] = None


@router.post("/api/saasshorts/actor-upload")
async def saasshorts_actor_upload(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    try:
        upload_id = uuid.uuid4().hex[:8]
        upload_dir = os.path.join(OUTPUT_DIR, "actor_uploads")
        os.makedirs(upload_dir, exist_ok=True)
        filename = f"custom_{upload_id}.png"
        file_path = safe_join(upload_dir, filename)
        size = await save_upload_limited(file, file_path, max_bytes=20 * 1024 * 1024)
        if size < 1000:
            with contextlib.suppress(OSError):
                os.remove(file_path)
            raise HTTPException(status_code=400, detail="File too small to be a valid image")

        return {"url": _named_video_url("actor_uploads", filename)}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/saasshorts/actor-options")
async def saasshorts_actor_options(
    req: SaaSActorRequest,
):
    try:
        job_id = str(uuid.uuid4())
        out_dir = os.path.join(OUTPUT_DIR, f"saas_actors_{job_id}")
        os.makedirs(out_dir, exist_ok=True)

        loop = asyncio.get_running_loop()
        import functools
        paths = await loop.run_in_executor(
            None,
            functools.partial(
                generate_actor_images,
                req.actor_description, out_dir, "actor", req.num_options,
                product_description=req.product_description,
            ),
        )

        desc = req.actor_description
        if req.product_description:
            desc += f" (holding {req.product_description})"
        urls = []
        for p in paths:
            s3_url = upload_actor_to_s3(p, description=desc)
            if s3_url:
                urls.append(s3_url)
            else:
                urls.append(
                    _named_video_url(f"saas_actors_{job_id}", os.path.basename(p))
                )

        return {"images": urls}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/saasshorts/gallery")
async def saasshorts_video_gallery(limit: int = 50):
    try:
        loop = asyncio.get_running_loop()
        videos = await loop.run_in_executor(None, list_video_gallery, limit)
        return {"videos": videos, "total": len(videos)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SaaSPostRequest(BaseModel):
    job_id: str
    api_key: str
    user_id: str
    platforms: List[str]
    title: Optional[str] = None
    description: Optional[str] = None
    scheduled_date: Optional[str] = None
    timezone: Optional[str] = "UTC"


@router.post("/api/saasshorts/post")
async def saasshorts_post_to_socials(req: SaaSPostRequest):
    if req.job_id not in saas_jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = saas_jobs[req.job_id]
    result = job.get("result")
    if not result or not result.get("video_url"):
        raise HTTPException(status_code=400, detail="No video available for this job")

    try:
        validated_job_id = validate_uuid(req.job_id, "job ID")
        filename = safe_filename(result["video_url"].split("/")[-1])
        file_path = safe_join(OUTPUT_DIR, f"saas_{validated_job_id}", filename)
        script = result.get("script", {})
        final_title = req.title or script.get("title", "AI Short")
        final_description = req.description or script.get("caption", "")
        if not final_description:
            final_description = script.get("full_narration", "Check this out!")

        return await asyncio.to_thread(
            upload_video,
            file_path=file_path,
            api_key=req.api_key,
            user_id=req.user_id,
            platforms=req.platforms,
            title=final_title,
            description=final_description,
            scheduled_date=req.scheduled_date,
            timezone=req.timezone,
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/gallery", response_class=HTMLResponse)
async def gallery_html_page():
    import html as html_mod
    loop = asyncio.get_running_loop()
    videos = await loop.run_in_executor(None, list_video_gallery, 100)

    cards_html = ""
    ld_items = []
    for i, v in enumerate(videos):
        title = html_mod.escape(v.get("title", "Untitled"))
        video_url = v.get("video_url", "")
        actor_url = v.get("actor_url", "")
        video_id = v.get("video_id", "")
        duration = v.get("duration", 0)
        mode = v.get("video_mode", "")
        product = html_mod.escape(v.get("product_name", ""))
        caption = html_mod.escape(v.get("caption", "")[:120])

        mode_badge = '<span style="background:#22c55e;color:#000;padding:2px 8px;border-radius:9999px;font-size:10px;font-weight:700">LOW COST</span>' if mode == "lowcost" else '<span style="background:#8b5cf6;color:#fff;padding:2px 8px;border-radius:9999px;font-size:10px;font-weight:700">PREMIUM</span>'

        cards_html += f'''
        <a href="/video/{video_id}" style="text-decoration:none;color:inherit">
          <div style="background:#18181b;border-radius:16px;overflow:hidden;border:1px solid #27272a;transition:transform 0.2s" onmouseover="this.style.transform='scale(1.02)'" onmouseout="this.style.transform='scale(1)'">
            <div style="position:relative;aspect-ratio:9/16;background:#000">
              <video src="{video_url}" poster="{actor_url}" muted playsinline preload="metadata"
                     onmouseenter="this.play()" onmouseleave="this.pause();this.currentTime=0"
                     style="width:100%;height:100%;object-fit:cover"></video>
              <div style="position:absolute;top:8px;right:8px">{mode_badge}</div>
            </div>
            <div style="padding:12px">
              <h2 style="font-size:14px;font-weight:600;margin:0 0 4px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{title}</h2>
              <p style="font-size:11px;color:#71717a;margin:0">{duration:.0f}s · {product}</p>
            </div>
          </div>
        </a>'''

        ld_items.append(f'{{"@type":"ListItem","position":{i+1},"url":"https://lambadaclips.app/video/{video_id}","name":"{title}"}}')

    ld_json = f'{{"@context":"https://schema.org","@type":"CollectionPage","name":"AI UGC Video Gallery","mainEntity":{{"@type":"ItemList","numberOfItems":{len(videos)},"itemListElement":[{",".join(ld_items)}]}}}}'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI UGC Video Gallery | LambadaClips</title>
<meta name="description" content="Browse {len(videos)} AI-generated UGC marketing videos. Create viral TikTok and Instagram Reels for your SaaS product.">
<meta name="robots" content="index, follow">
<meta property="og:title" content="AI UGC Video Gallery | LambadaClips">
<meta property="og:type" content="website">
<meta property="og:description" content="Browse AI-generated UGC marketing videos for SaaS products.">
<script type="application/ld+json">{ld_json}</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0a0c;color:#e4e4e7;font-family:-apple-system,BlinkMacSystemFont,sans-serif}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:20px;padding:20px;max-width:1400px;margin:0 auto}}
nav{{padding:20px 40px;border-bottom:1px solid #27272a;display:flex;align-items:center;justify-content:space-between}}
h1{{font-size:28px;font-weight:700;padding:40px 20px 0;text-align:center}}
.subtitle{{text-align:center;color:#71717a;font-size:14px;padding:8px 20px 20px}}
.cta{{display:inline-block;background:#8b5cf6;color:#fff;padding:10px 24px;border-radius:12px;text-decoration:none;font-weight:600;font-size:14px}}
</style>
</head>
<body>
<nav><strong style="font-size:18px">LambadaClips</strong><a href="/" class="cta">Create Your Video</a></nav>
<h1>AI-Generated UGC Videos</h1>
<p class="subtitle">{len(videos)} videos generated · Low Cost & Premium modes</p>
<div class="grid">{cards_html}</div>
<div style="text-align:center;padding:40px"><a href="/" class="cta">Create Your Own UGC Video</a></div>
</body></html>'''


@router.get("/video/{video_id}", response_class=HTMLResponse)
async def video_html_page(video_id: str):
    import html as html_mod
    loop = asyncio.get_running_loop()
    videos = await loop.run_in_executor(None, list_video_gallery, 200)
    meta = next((v for v in videos if v.get("video_id") == video_id), None)
    if not meta:
        raise HTTPException(status_code=404, detail="Video not found")

    title = html_mod.escape(meta.get("title", "Untitled"))
    caption = html_mod.escape(meta.get("caption", ""))
    narration = html_mod.escape(meta.get("full_narration", ""))
    video_url = meta.get("video_url", "")
    actor_url = meta.get("actor_url", "")
    duration = meta.get("duration", 0)
    mode = meta.get("video_mode", "")
    product = html_mod.escape(meta.get("product_name", ""))
    product_url = html_mod.escape(meta.get("product_url", ""))
    language = meta.get("language", "en")
    hashtags = " ".join(meta.get("hashtags", []))
    cost = meta.get("cost_estimate", {}).get("total", 0)
    created = meta.get("created_at", "")
    actor_desc = html_mod.escape(meta.get("actor_description", ""))

    ld_json = f'{{"@context":"https://schema.org","@type":"VideoObject","name":"{title}","description":"{caption}","thumbnailUrl":"{actor_url}","contentUrl":"{video_url}","uploadDate":"{created}","duration":"PT{int(duration)}S","width":1080,"height":1920,"inLanguage":"{language}"}}'

    mode_label = "Low Cost" if mode == "lowcost" else "Premium"

    return f'''<!DOCTYPE html>
<html lang="{language}">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - AI UGC Video | LambadaClips</title>
<meta name="description" content="{caption} {hashtags}">
<meta property="og:type" content="video.other">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{caption}">
<meta property="og:video" content="{video_url}">
<meta property="og:video:type" content="video/mp4">
<meta property="og:video:width" content="1080">
<meta property="og:video:height" content="1920">
<meta property="og:image" content="{actor_url}">
<meta name="twitter:card" content="player">
<meta name="twitter:title" content="{title}">
<meta name="twitter:image" content="{actor_url}">
<script type="application/ld+json">{ld_json}</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0a0c;color:#e4e4e7;font-family:-apple-system,BlinkMacSystemFont,sans-serif}}
nav{{padding:20px 40px;border-bottom:1px solid #27272a;display:flex;align-items:center;gap:16px}}
nav a{{color:#a1a1aa;text-decoration:none;font-size:14px}}
.container{{max-width:1000px;margin:0 auto;padding:40px 20px;display:grid;grid-template-columns:1fr 1fr;gap:40px}}
@media(max-width:768px){{.container{{grid-template-columns:1fr}}}}
video{{width:100%;border-radius:16px;background:#000}}
h1{{font-size:22px;font-weight:700;margin-bottom:8px}}
.meta{{color:#71717a;font-size:13px;margin-bottom:20px}}
.section{{margin-bottom:20px}}
.section h2{{font-size:13px;color:#71717a;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}}
.section p{{font-size:14px;line-height:1.6}}
.badge{{display:inline-block;padding:3px 10px;border-radius:9999px;font-size:11px;font-weight:700}}
.cta{{display:inline-block;background:#8b5cf6;color:#fff;padding:10px 24px;border-radius:12px;text-decoration:none;font-weight:600;font-size:14px;margin-top:20px}}
</style>
</head>
<body>
<nav><strong>LambadaClips</strong><a href="/gallery">Gallery</a><span style="color:#3f3f46">›</span><span style="color:#e4e4e7;font-size:14px">{title}</span></nav>
<div class="container">
<div><video src="{video_url}" poster="{actor_url}" controls autoplay playsinline style="aspect-ratio:9/16;object-fit:cover"></video></div>
<div>
<h1>{title}</h1>
<p class="meta">{duration:.0f}s · {mode_label} · ${cost:.2f} · {product}</p>
<div class="section"><h2>Caption</h2><p>{caption}</p><p style="color:#8b5cf6;margin-top:4px">{hashtags}</p></div>
<div class="section"><h2>Script</h2><p>{narration}</p></div>
<div class="section"><h2>Actor</h2><p>{actor_desc}</p></div>
{f'<div class="section"><h2>Product</h2><p><a href="{product_url}" style="color:#8b5cf6" target="_blank">{product}</a></p></div>' if product_url else ''}
<a href="/gallery">← Back to Gallery</a>
<br><a href="/" class="cta">Create Your Own</a>
</div>
</div>
</body></html>'''


@router.get("/api/saasshorts/actor-gallery")
async def saasshorts_actor_gallery():
    try:
        loop = asyncio.get_running_loop()
        images = await loop.run_in_executor(None, list_actor_gallery)
        return {"images": images}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SaaSGenerateRequest(BaseModel):
    script: dict
    voice_id: Optional[str] = None
    actor_description: Optional[str] = None
    selected_actor_url: Optional[str] = None 
    retry_job_id: Optional[str] = None
    video_mode: str = "lowcost"
    f5tts_url: Optional[str] = None
    f5tts_ref_text: Optional[str] = None
    f5tts_ref_audio: Optional[str] = None
    colab_url: Optional[str] = None
    sdxl_cloud_url: Optional[str] = None
    broll_cloud_url: Optional[str] = None
    lipsync_cloud_url: Optional[str] = None


@router.post("/api/saasshorts/generate")
async def saasshorts_generate(
    req: SaaSGenerateRequest,
    x_hf_token: Optional[str] = Header(None, alias="X-HF-Token"),
):
    hf_token = x_hf_token or os.environ.get("HF_TOKEN")

    if req.retry_job_id:
        job_id = req.retry_job_id
        if job_id not in saas_jobs:
            raise HTTPException(status_code=404, detail="Retry Job not found")
        saas_jobs[job_id]["status"] = "queued"
        saas_jobs[job_id]["logs"] = [f"Retrying Job {job_id} queued."]
        saas_jobs[job_id]["error"] = None
        saas_jobs[job_id]["result"] = None
        saas_jobs[job_id]["req"] = req.dict()
        saas_jobs[job_id]["hf_token"] = hf_token
    else:
        job_id = str(uuid.uuid4())
        saas_jobs[job_id] = {
            "status": "queued",
            "logs": [f"Job {job_id} queued."],
            "req": req.dict(),
            "hf_token": hf_token,
            "error": None,
            "result": None,
        }

    await saas_queue.put(job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/api/saasshorts/status/{job_id}")
async def saasshorts_status(job_id: str):
    if job_id not in saas_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = saas_jobs[job_id]
    return {
        "status": job["status"],
        "logs": job.get("logs", []),
        "result": job.get("result"),
        "error": job.get("error"),
        "video_mode": job.get("req", {}).get("video_mode", "lowcost")
    }

async def process_saas_job(job_id: str):
    job = saas_jobs.get(job_id)
    if not job:
        return

    req_data = job["req"]
    hf_token = job["hf_token"]

    job["status"] = "processing"
    job["logs"].append("Worker started processing video generation.")

    try:
        out_dir = os.path.join(OUTPUT_DIR, f"saas_{job_id}")
        os.makedirs(out_dir, exist_ok=True)

        def log_cb(msg):
            job["logs"].append(msg)
            print(f"[SaaS Worker {job_id}] {msg}")

        loop = asyncio.get_running_loop()
        video_path = await loop.run_in_executor(
            None,
            generate_saas_video,
            req_data["script"],
            req_data["voice_id"],
            req_data.get("actor_description"),
            req_data.get("selected_actor_url"),
            out_dir,
            hf_token,
            log_cb,
            req_data.get("video_mode", "lowcost"),
            req_data.get("f5tts_url"),
            req_data.get("f5tts_ref_text"),
            req_data.get("f5tts_ref_audio"),
            req_data.get("colab_url"),
            req_data.get("sdxl_cloud_url"),
            req_data.get("broll_cloud_url"),
            req_data.get("lipsync_cloud_url"),
        )

        filename = os.path.basename(video_path)
        video_url = _named_video_url(f"saas_{job_id}", filename)

        job["result"] = {"video_url": video_url, "script": req_data["script"]}
        job["status"] = "completed"
        job["logs"].append("Video generation complete.")

    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        job["logs"].append(f"Worker Error: {e}")
        print(f"❌ SaaS Worker error for {job_id}: {e}")

async def process_saas_queue():
    while True:
        await saas_concurrency_semaphore.acquire()
        try:
            job_id = await saas_queue.get()
            job = saas_jobs.get(job_id)
            if job:
                await process_saas_job(job_id)
        except Exception as e:
            print(f"❌ Error in saas_queue processing: {e}")
        finally:
            saas_queue.task_done()
            saas_concurrency_semaphore.release()

