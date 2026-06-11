from pathlib import Path
from typing import Iterable

import httpx
from fastapi import HTTPException


UPLOAD_POST_URL = "https://api.upload-post.com/api/upload"
SUCCESS_CODES = {200, 201, 202}


def upload_video(
    *,
    file_path: str,
    api_key: str,
    user_id: str,
    platforms: Iterable[str],
    title: str,
    description: str,
    scheduled_date: str | None = None,
    timezone: str | None = "UTC",
) -> dict:
    path = Path(file_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Video file not found")

    selected_platforms = list(dict.fromkeys(platforms))
    if not selected_platforms:
        raise HTTPException(status_code=400, detail="Select at least one platform")

    payload: dict[str, object] = {
        "user": user_id,
        "title": title,
        "platform[]": selected_platforms,
        "async_upload": "true",
    }
    if scheduled_date:
        payload["scheduled_date"] = scheduled_date
        if timezone:
            payload["timezone"] = timezone
    if "tiktok" in selected_platforms:
        payload["tiktok_title"] = description
    if "instagram" in selected_platforms:
        payload["instagram_title"] = description
        payload["media_type"] = "REELS"
    if "youtube" in selected_platforms:
        payload["youtube_title"] = title
        payload["youtube_description"] = description
        payload["privacyStatus"] = "public"

    headers = {"Authorization": f"Apikey {api_key}"}
    with path.open("rb") as video_file:
        files = {"video": (path.name, video_file, "video/mp4")}
        with httpx.Client(timeout=600.0) as client:
            response = client.post(
                UPLOAD_POST_URL, headers=headers, data=payload, files=files
            )

    if response.status_code not in SUCCESS_CODES:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Upload-Post API error: {response.text}",
        )
    return response.json()
