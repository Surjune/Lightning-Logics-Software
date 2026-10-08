"""Append-only audit trail of every event, proposal and decision."""

import json
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app.core.exceptions import AppError
from app.core.logging import get_logger, request_id_var

logger = get_logger(__name__)
RECENT_ENTRIES = 200


class AuditEntry(BaseModel):
    at_utc: str
    sim_min: int
    actor: str
    action: str
    detail: str
    request_id: str


class AuditWriteError(AppError):
    code = "AUDIT_WRITE_FAILED"
    status_code = 500


class AuditLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._recent: deque[AuditEntry] = deque(maxlen=RECENT_ENTRIES)
        self._lock = threading.Lock()

    def record(self, sim_min: int, actor: str, action: str, detail: str) -> AuditEntry:
        entry = AuditEntry(
            at_utc=datetime.now(UTC).isoformat(timespec="seconds"),
            sim_min=sim_min,
            actor=actor,
            action=action,
            detail=detail,
            request_id=request_id_var.get(),
        )
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry.model_dump()) + "\n")
            except OSError as exc:
                raise AuditWriteError(f"Could not write audit trail: {exc}") from exc
            self._recent.appendleft(entry)
        logger.info("audit", extra={"fields": entry.model_dump()})
        return entry

    def recent(self) -> list[AuditEntry]:
        with self._lock:
            return list(self._recent)
