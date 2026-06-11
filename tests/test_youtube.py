import pytest
from fastapi import HTTPException
from app_core.utils import validate_youtube_url

def test_validate_youtube_url_valid():
    valid_urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    ]
    for url in valid_urls:
        try:
            validate_youtube_url(url)
        except Exception as e:
            pytest.fail(f"Valid URL {url} raised exception: {e}")

def test_validate_youtube_url_invalid():
    invalid_urls = [
        "https://vimeo.com/123456789",
        "https://www.example.com",
        "not a url",
        "https://www.youtube.com/watch?v=" # Missing ID
    ]
    for url in invalid_urls:
        with pytest.raises(HTTPException):
            validate_youtube_url(url)
