import asyncio
from typing import Dict
from app_core.state import SQLiteState
from app_core.config import STATE_DB_PATH, MAX_CONCURRENT_JOBS
from app_core.security import ApiSecurity

state_db = SQLiteState(STATE_DB_PATH)
job_queue = asyncio.Queue()
jobs = state_db.namespace("jobs")
thumbnail_sessions = state_db.namespace("thumbnail_sessions")
publish_jobs = state_db.namespace("publish_jobs")
enhance_jobs = state_db.namespace("enhance_jobs")
saas_jobs = state_db.namespace("saas_jobs")
job_runtime: Dict[str, Dict] = {}

concurrency_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
api_security = ApiSecurity()
