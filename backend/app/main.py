"""FastAPI application factory. Run with: uvicorn app.main:create_app --factory"""

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.api.schemas import ErrorBody, ErrorEnvelope
from app.core.config import Settings, load_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger, log_fields, request_id_var
from app.services.audit import AuditLog
from app.services.container import build_services

logger = get_logger(__name__)
REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_LEN = 64


def _error(status: int, code: str, message: str) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorBody(code=code, message=message, request_id=request_id_var.get()))
    return JSONResponse(status_code=status, content=body.model_dump())


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="Lightning Logics - Air Operations Decision Support",
        version="0.1.0",
        description="Multi-source fusion, CP-SAT allocation and dynamic retasking of air operations.",
    )
    app.state.services = build_services(AuditLog(settings.audit_path))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )

    @app.middleware("http")
    async def correlate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")[:MAX_REQUEST_ID_LEN]
        token = request_id_var.set(incoming or uuid.uuid4().hex[:12])
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id_var.get()
            logger.info(
                "request",
                extra=log_fields(method=request.method, path=request.url.path, status=response.status_code),
            )
            return response
        finally:
            request_id_var.reset(token)

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        logger.warning("request failed", extra=log_fields(code=exc.code, message=exc.message))
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []))
        return _error(422, "VALIDATION_ERROR", f"{where}: {first.get('msg', 'invalid request')}")

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return _error(500, "INTERNAL_ERROR", "Unexpected server error; see logs for the request ID")

    app.include_router(router, prefix="/api")
    return app
