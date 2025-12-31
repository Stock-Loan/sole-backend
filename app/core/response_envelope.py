from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


def _success_code(status_code: int) -> str:
    mapping = {
        200: "ok",
        201: "created",
        202: "accepted",
    }
    return mapping.get(status_code, "ok")


def _success_message(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Success"


def _build_success_envelope(data: Any, status_code: int) -> dict[str, Any]:
    return {
        "code": _success_code(status_code),
        "message": _success_message(status_code),
        "data": data,
        "details": {},
    }


def _is_enveloped(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if "code" in payload and "message" in payload:
        return "data" in payload or "details" in payload
    return False


def _normalize_envelope(payload: dict[str, Any], status_code: int) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("code", _success_code(status_code))
    normalized.setdefault("message", _success_message(status_code))
    normalized.setdefault("data", None)
    normalized.setdefault("details", {})
    return normalized


class ResponseEnvelopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next) -> Response:
        response = await call_next(request)

        if response.status_code < 200 or response.status_code >= 300:
            return response

        # Convert 204 to a 200 success envelope for frontend consistency
        if response.status_code == 204:
            new_response = JSONResponse(status_code=200, content=_build_success_envelope(None, 200))
            for key, value in response.headers.items():
                if key.lower() in {"content-length", "content-type"}:
                    continue
                new_response.headers[key] = value
            return new_response

        if not isinstance(response, JSONResponse):
            return response

        if response.body is None:
            return response

        try:
            raw_body = response.body.decode("utf-8")
            payload = json.loads(raw_body) if raw_body else None
        except Exception:
            return response

        if _is_enveloped(payload):
            normalized = _normalize_envelope(payload, response.status_code)
            if normalized == payload:
                return response
            new_response = JSONResponse(status_code=response.status_code, content=normalized)
            for key, value in response.headers.items():
                if key.lower() in {"content-length", "content-type"}:
                    continue
                new_response.headers[key] = value
            return new_response

        wrapped = _build_success_envelope(payload, response.status_code)
        new_response = JSONResponse(status_code=response.status_code, content=wrapped)
        for key, value in response.headers.items():
            if key.lower() in {"content-length", "content-type"}:
                continue
            new_response.headers[key] = value
        return new_response


def register_response_envelope(app) -> None:
    app.add_middleware(ResponseEnvelopeMiddleware)
