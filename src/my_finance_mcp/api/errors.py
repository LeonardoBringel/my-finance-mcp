"""Single error format: {"error": {"code": str, "message": str}}."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _error_response(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_body(code, message),
        headers=headers,
    )


async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return _error_response(exc.status_code, exc.code, exc.message)


async def _handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    if exc.status_code == 404:
        return _error_response(404, "NOT_FOUND", "Recurso não encontrado.")
    if exc.status_code == 405:
        # Allow must survive so clients learn which methods the route accepts
        return _error_response(
            405,
            "METHOD_NOT_ALLOWED",
            "Método não permitido.",
            headers=dict(exc.headers or {}),
        )
    return _error_response(exc.status_code, "HTTP_ERROR", "Erro na requisição.")


async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _error_response(422, "INVALID_REQUEST", "Requisição inválida.")


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled error on %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return _error_response(500, "INTERNAL", "Erro interno.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected)
