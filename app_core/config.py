import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads")).resolve()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output")).resolve()
STATE_DB_PATH = Path(
    os.getenv("STATE_DB_PATH", OUTPUT_DIR / "lambadaclips-state.sqlite3")
).resolve()

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_csv(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return default
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


MAX_CONCURRENT_JOBS = max(1, int(os.getenv("MAX_CONCURRENT_JOBS", "5")))
MAX_FILE_SIZE_MB = max(1, int(os.getenv("MAX_FILE_SIZE_MB", "2048")))
JOB_RETENTION_SECONDS = max(300, int(os.getenv("JOB_RETENTION_SECONDS", "3600")))
DISABLE_YOUTUBE_URL = env_bool("DISABLE_YOUTUBE_URL")

API_AUTH_REQUIRED = env_bool("API_AUTH_REQUIRED", True)
API_ACCESS_KEY = os.getenv("API_ACCESS_KEY", "")
API_RATE_LIMIT_REQUESTS = max(1, int(os.getenv("API_RATE_LIMIT_REQUESTS", "30")))
API_RATE_LIMIT_WINDOW_SECONDS = max(
    1, int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60"))
)

CORS_ORIGINS = env_csv(
    "CORS_ORIGINS",
    [
        "https://clips.lambada.my.id",
        "http://clips.lambada.my.id",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
)

GEMINI_INPUT_PRICE_PER_MILLION = float(
    os.getenv("GEMINI_INPUT_PRICE_PER_MILLION", "0")
)
GEMINI_OUTPUT_PRICE_PER_MILLION = float(
    os.getenv("GEMINI_OUTPUT_PRICE_PER_MILLION", "0")
)
GEMINI_PRICING_LABEL = os.getenv(
    "GEMINI_PRICING_LABEL", "Configure current pricing via environment variables"
)

GEMINI_MODEL_FAST = os.getenv("GEMINI_MODEL_FAST", "gemini-2.5-flash")
GEMINI_MODEL_LITE = os.getenv("GEMINI_MODEL_LITE", "gemini-2.5-flash-lite")
