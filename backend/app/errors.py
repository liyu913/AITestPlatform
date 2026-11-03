import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, code: str, message: str, detail: str | None = None, status_code: int = 400):
        self.code = code
        self.message = message
        self.detail = detail
        self.status_code = status_code
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


async def app_error_handler(request: Request, exc: AppError):
    log.warning(
        "AppError %s %s status=%s code=%s message=%s detail=%s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.code,
        exc.message,
        exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "detail": exc.detail},
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": "http_error",
            "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            "detail": None,
        },
    )
