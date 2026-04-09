from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    app_host: str
    app_port: int
    app_debug: bool
    gemini_api_key: str
    gemini_model: str
    gemini_embedding_model: str
    supabase_url: str
    supabase_key: str
    use_vertex_ai: bool = False
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"


def _to_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "RxPulse API"),
        app_env=os.getenv("APP_ENV", "development"),
        app_host=os.getenv("APP_HOST", "0.0.0.0"),
        app_port=int(os.getenv("APP_PORT", "8000")),
        app_debug=_to_bool(os.getenv("APP_DEBUG"), default=True),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        gemini_embedding_model=os.getenv(
            "GEMINI_EMBEDDING_MODEL", "text-embedding-004"
        ),
        supabase_url=os.getenv("SUPABASE_URL", "").strip(),
        supabase_key=os.getenv("SUPABASE_KEY", "").strip(),
        use_vertex_ai=_to_bool(os.getenv("USE_VERTEX_AI"), default=False),
        gcp_project_id=os.getenv("GCP_PROJECT_ID", "").strip(),
        gcp_region=os.getenv("GCP_REGION", "us-central1").strip(),
    )
