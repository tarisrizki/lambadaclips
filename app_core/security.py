import hashlib
import hmac
import time
from collections import defaultdict, deque
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import (
    API_ACCESS_KEY,
    API_AUTH_REQUIRED,
    API_RATE_LIMIT_REQUESTS,
    API_RATE_LIMIT_WINDOW_SECONDS,
)


PUBLIC_API_PATHS = {
    "/api/health",
    "/api/config",
    "/api/saasshorts/gallery",
    "/api/saasshorts/actor-gallery",
}
RATE_LIMITED_PREFIXES = (
    "/api/process",
    "/api/thumbnail/upload",
    "/api/thumbnail/analyze",
    "/api/thumbnail/generate",
    "/api/saasshorts/generate",
    "/api/saasshorts/actor-upload",
    "/api/saasshorts/actor-options",
)


def sign_media_url(path: str, expires_in: int = 3600) -> str:
    if not API_AUTH_REQUIRED or not API_ACCESS_KEY:
        return path
    expires = int(time.time()) + expires_in
    message = f"{path}:{expires}".encode("utf-8")
    signature = hmac.new(
        API_ACCESS_KEY.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()
    return f"{path}?{urlencode({'expires': expires, 'signature': signature})}"


def verify_media_request(request: Request) -> bool:
    if not API_AUTH_REQUIRED:
        return True
    if not API_ACCESS_KEY:
        return False
    try:
        expires = int(request.query_params.get("expires", ""))
    except ValueError:
        return False
    if expires < int(time.time()):
        return False
    signature = request.query_params.get("signature", "")
    message = f"{request.url.path}:{expires}".encode("utf-8")
    expected = hmac.new(
        API_ACCESS_KEY.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


class ApiSecurity:
    def __init__(self) -> None:
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def _client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        return request.client.host if request.client else "unknown"

    @staticmethod
    def _provided_key(request: Request) -> str:
        header_key = request.headers.get("x-api-key", "")
        if header_key:
            return header_key
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return ""

    def _authenticated(self, request: Request) -> bool:
        if not API_AUTH_REQUIRED:
            return True
        if not API_ACCESS_KEY:
            return False
        return hmac.compare_digest(self._provided_key(request), API_ACCESS_KEY)

    def _rate_limited(self, request: Request) -> bool:
        if not request.url.path.startswith(RATE_LIMITED_PREFIXES):
            return False
        now = time.monotonic()
        cutoff = now - API_RATE_LIMIT_WINDOW_SECONDS
        bucket = self.requests[self._client_ip(request)]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= API_RATE_LIMIT_REQUESTS:
            return True
        bucket.append(now)
        return False

    async def middleware(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        if path.startswith(("/videos/", "/thumbnails/")):
            if not verify_media_request(request):
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            return await call_next(request)
        if path.startswith("/api") and path not in PUBLIC_API_PATHS:
            if API_AUTH_REQUIRED and not API_ACCESS_KEY:
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "API authentication is enabled but API_ACCESS_KEY is not configured"
                    },
                )
            if not self._authenticated(request):
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            if self._rate_limited(request):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Try again later."},
                    headers={"Retry-After": str(API_RATE_LIMIT_WINDOW_SECONDS)},
                )
        return await call_next(request)
