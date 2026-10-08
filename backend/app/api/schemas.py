"""Request bodies and the error envelope of the HTTP API."""

from typing import Annotated

from pydantic import BaseModel, Field

from app.core.constants import HORIZON_MIN


class AdvanceClockRequest(BaseModel):
    minutes: Annotated[int, Field(ge=1, le=HORIZON_MIN)]


class ResetRequest(BaseModel):
    seed: Annotated[int, Field(ge=0, le=2**31 - 1)] | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorBody
