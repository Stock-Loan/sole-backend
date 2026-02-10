from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send


MAX_JSON_BODY_BYTES = 1 * 1024 * 1024


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


def _decode_headers(raw_headers: list[tuple[bytes, bytes]]) -> dict[str, list[str]]:
    headers: dict[str, list[str]] = {}
    for key, value in raw_headers:
        text_key = key.decode("latin-1")
        text_value = value.decode("latin-1")
        headers.setdefault(text_key, []).append(text_value)
    return headers


def _is_json_response(headers: dict[str, list[str]]) -> bool:
    content_type_values = headers.get("content-type") or headers.get("Content-Type") or []
    if not content_type_values:
        return False
    content_type = content_type_values[0].lower()
    return content_type.startswith("application/json")


class ResponseEnvelopeMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status_code: int | None = None
        raw_headers: list[tuple[bytes, bytes]] = []
        body_chunks: list[bytes] = []
        buffered_bytes = 0
        saw_start = False
        passthrough = False

        async def _start_passthrough_with_buffered(current_message: Message) -> None:
            nonlocal passthrough
            if passthrough:
                await send(current_message)
                return
            passthrough = True
            await send({"type": "http.response.start", "status": status_code, "headers": raw_headers})
            if body_chunks:
                await send({"type": "http.response.body", "body": b"".join(body_chunks), "more_body": True})
                body_chunks.clear()
            await send(current_message)

        async def capture_send(message: Message) -> None:
            nonlocal status_code, raw_headers, saw_start, passthrough, buffered_bytes
            if message["type"] == "http.response.start":
                saw_start = True
                status_code = message["status"]
                raw_headers = list(message.get("headers", []))
                headers = _decode_headers(raw_headers)
                should_wrap = 200 <= status_code < 300 and (
                    status_code == 204 or _is_json_response(headers)
                )
                if should_wrap and status_code != 204:
                    content_length_values = headers.get("content-length") or headers.get("Content-Length") or []
                    if content_length_values:
                        try:
                            content_length = int(content_length_values[0])
                        except ValueError:
                            content_length = None
                        if content_length is not None and content_length > MAX_JSON_BODY_BYTES:
                            should_wrap = False
                if not should_wrap:
                    passthrough = True
                    await send(message)
                return
            if message["type"] == "http.response.body":
                if passthrough:
                    await send(message)
                    return
                chunk = message.get("body", b"")
                next_size = buffered_bytes + len(chunk)
                if next_size > MAX_JSON_BODY_BYTES:
                    await _start_passthrough_with_buffered(message)
                    return
                buffered_bytes = next_size
                body_chunks.append(chunk)
                return
            await send(message)

        await self.app(scope, receive, capture_send)

        if passthrough:
            return

        if not saw_start or status_code is None:
            return

        headers = _decode_headers(raw_headers)
        body = b"".join(body_chunks)

        should_wrap = 200 <= status_code < 300
        if not should_wrap:
            await send({"type": "http.response.start", "status": status_code, "headers": raw_headers})
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return

        if status_code == 204:
            wrapped_content = _build_success_envelope(None, 200)
            response = JSONResponse(status_code=200, content=wrapped_content)
            for key, values in headers.items():
                lowered = key.lower()
                if lowered in {"content-length", "content-type"}:
                    continue
                for value in values:
                    if lowered == "set-cookie":
                        response.headers.append(key, value)
                    else:
                        response.headers[key] = value
            await response(scope, receive, send)
            return

        if not _is_json_response(headers):
            await send({"type": "http.response.start", "status": status_code, "headers": raw_headers})
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return

        try:
            raw_body = body.decode("utf-8")
            payload = json.loads(raw_body) if raw_body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            await send({"type": "http.response.start", "status": status_code, "headers": raw_headers})
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return

        if _is_enveloped(payload):
            normalized = _normalize_envelope(payload, status_code)
            if normalized == payload:
                await send({"type": "http.response.start", "status": status_code, "headers": raw_headers})
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return
            response = JSONResponse(status_code=status_code, content=normalized)
        else:
            wrapped = _build_success_envelope(payload, status_code)
            response = JSONResponse(status_code=status_code, content=wrapped)

        for key, values in headers.items():
            lowered = key.lower()
            if lowered in {"content-length", "content-type"}:
                continue
            for value in values:
                if lowered == "set-cookie":
                    response.headers.append(key, value)
                else:
                    response.headers[key] = value

        await response(scope, receive, send)


def register_response_envelope(app) -> None:
    app.add_middleware(ResponseEnvelopeMiddleware)
