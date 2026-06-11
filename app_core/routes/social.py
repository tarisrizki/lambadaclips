import os
import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
import httpx

from app_core.globals import jobs, _job_media
from app_core.paths import safe_filename
from api_social import upload_video

router = APIRouter()

class SocialPostRequest(BaseModel):
    job_id: str
    clip_index: int
    api_key: str
    user_id: str
    platforms: List[str]
    title: Optional[str] = None
    description: Optional[str] = None
    scheduled_date: Optional[str] = None
    timezone: Optional[str] = "UTC"

@router.post("/api/social/post")
async def post_to_socials(req: SocialPostRequest):
    if req.job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[req.job_id]
    if 'result' not in job or 'clips' not in job['result']:
        raise HTTPException(status_code=400, detail="Job result not available")

    try:
        clip = job['result']['clips'][req.clip_index]
        filename = safe_filename(clip['video_url'].split('/')[-1])
        file_path = _job_media(req.job_id, filename)
        final_title = (
            req.title
            or clip.get('video_title_for_youtube_short')
            or clip.get('title', 'Viral Short')
        )
        final_description = req.description or clip.get('video_description_for_instagram') or clip.get('video_description_for_tiktok') or "Check this out!"
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
    except IndexError as exc:
        raise HTTPException(status_code=400, detail="Invalid clip index") from exc
    except HTTPException:
        raise
    except Exception as exc:
        print(f"Social post failed: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

@router.get("/api/social/user")
async def get_social_user(api_key: str = Header(..., alias="X-Upload-Post-Key")):
    if not api_key:
         raise HTTPException(status_code=400, detail="Missing X-Upload-Post-Key header")
         
    url = "https://api.upload-post.com/api/uploadposts/users"
    print(f"🔍 Fetching User ID from: {url}")
    headers = {"Authorization": f"Apikey {api_key}"}
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                print(f"❌ Upload-Post User Fetch Error: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=f"Failed to fetch user: {resp.text}")
            
            data = resp.json()
            print(f"🔍 Upload-Post User Response: {data}")
            
            user_id = None
            profiles_list = []
            if isinstance(data, dict):
                 raw_profiles = data.get('profiles', [])
                 if isinstance(raw_profiles, list):
                     for p in raw_profiles:
                         username = p.get('username')
                         if username:
                             socials = p.get('social_accounts', {})
                             connected = []
                             for platform in ['tiktok', 'instagram', 'youtube']:
                                 account_info = socials.get(platform)
                                 if isinstance(account_info, dict):
                                     connected.append(platform)
                             
                             profiles_list.append({
                                 "username": username,
                                 "connected": connected
                             })
            
            if not profiles_list:
                return {"profiles": [], "error": "No profiles found"}
                
            return {"profiles": profiles_list}
            
        except Exception as e:
             raise HTTPException(status_code=500, detail=str(e))
