from __future__ import annotations

from http import HTTPStatus
from typing import Any, TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from app.api.deps import StepUpMfaRequired
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException


def _default_code(status_code: int) -> str:
    mapping = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "unprocessable_entity",
        429: "rate_limited",
    }
    return mapping.get(status_code, "http_error")


def _default_message(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Request failed"


def _normalize_details(details: Any) -> dict:
    if details is None:
        return {}
    if isinstance(details, dict):
        return details
    if isinstance(details, list):
        return {"errors": details}
    if isinstance(details, str):
        return {"detail": details}
    return {"detail": str(details)}


def _build_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    payload = {
        "code": code,
        "message": message,
        "data": None,
        "details": _normalize_details(details),
    }
    return JSONResponse(status_code=status_code, content=jsonable_encoder(payload))


def _parse_http_exception_detail(detail: Any, status_code: int) -> tuple[str, str, dict]:
    code = _default_code(status_code)
    message = _default_message(status_code)
    details: dict = {}

    if isinstance(detail, dict):
        code = detail.get("code") or code
        message = detail.get("message") or detail.get("detail") or detail.get("error") or message
        if "details" in detail:
            details = _normalize_details(detail.get("details"))
        else:
            remainder = {
                k: v for k, v in detail.items() if k not in {"code", "message", "detail", "error"}
            }
            details = remainder or {
                "detail": detail.get("detail") or detail.get("error") or message
            }
        return code, message, details

    if isinstance(detail, list):
        return code, message, {"errors": detail}

    if isinstance(detail, str):
        return code, detail, {"detail": detail}

    return code, message, {"detail": str(detail)}


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code, message, details = _parse_http_exception_detail(exc.detail, exc.status_code)
    return _build_response(exc.status_code, code, message, details)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    message = "Validation failed"
    if errors:
        first = errors[0] or {}
        loc = first.get("loc") or []
        msg = first.get("msg") or "Validation failed"
        # Drop the request section (body/query/path) from the location
        loc_parts = [str(part) for part in loc if part not in {"body", "query", "path"}]
        if loc_parts:
            message = f"{'.'.join(loc_parts)}: {msg}"
        else:
            message = str(msg)
    return _build_response(
        status_code=422,
        code="validation_error",
        message=message,
        details={"errors": errors, "body": exc.body},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return _build_response(
        status_code=500,
        code="internal_server_error",
        message="Internal server error",
        details={},
    )


def register_exception_handlers(app) -> None:
    from app.api.deps import StepUpMfaRequired

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)
    app.add_exception_handler(StepUpMfaRequired, step_up_mfa_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


async def step_up_mfa_exception_handler(request: Request, exc: "StepUpMfaRequired") -> JSONResponse:
    """Handle step-up MFA required exceptions with a structured response."""
    return JSONResponse(
        status_code=403,
        content={
            "code": "step_up_mfa_required",
            "message": "Step-up MFA verification required",
            "data": None,
            "details": {
                "step_up_required": True,
                "challenge_token": exc.challenge_token,
                "action": exc.action,
            },
        },
    )


async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    details = getattr(exc, "detail", None)
    response = _build_response(
        status_code=429,
        code="rate_limited",
        message=_default_message(429),
        details=details,
    )
    headers = getattr(exc, "headers", None)
    if isinstance(headers, dict):
        response.headers.update(headers)
    return response
