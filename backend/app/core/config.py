"""Runtime settings, read once from environment variables."""

import os
from pathlib import Path

from pydantic import BaseModel

DEFAULT_CORS = "http://localhost:5173,http://127.0.0.1:5173"
DEFAULT_AUDIT_PATH = Path(__file__).resolve().parents[2] / ".runtime" / "audit.jsonl"


class Settings(BaseModel):
    cors_origins: list[str]
    audit_path: Path
    log_level: str


def load_settings() -> Settings:
    return Settings(
        cors_origins=[
            o.strip() for o in os.environ.get("AIROPS_CORS_ORIGINS", DEFAULT_CORS).split(",") if o.strip()
        ],
        audit_path=Path(os.environ.get("AIROPS_AUDIT_PATH", str(DEFAULT_AUDIT_PATH))),
        log_level=os.environ.get("AIROPS_LOG_LEVEL", "INFO"),
    )
