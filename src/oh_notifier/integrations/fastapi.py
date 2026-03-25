"""FastAPI/Starlette ASGI error middleware."""

from __future__ import annotations

import base64
import json
import logging
import traceback
import uuid
from typing import Any

from oh_notifier.context import reset_request_context, set_request_context
from oh_notifier.event import ErrorEvent, ErrorSource
from oh_notifier.masking import summarize_body
from oh_notifier.notifier import TelegramNotifier

logger = logging.getLogger("oh_notifier.fastapi")


class ErrorMiddleware:
    """Raw ASGI middleware that catches unhandled exceptions and sends to Telegram.

    Features:
    - Captures request_id, client_ip, user_agent, request body
    - Auto-extracts user context from JWT Bearer token (best-effort)
    - Skips configurable paths (health checks, metrics)
    """

    def __init__(
        self,
        app: Any,
        exclude_paths: set[str] | None = None,
    ) -> None:
        self.app = app
        self.exclude_paths = exclude_paths or {"/health", "/metrics"}

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            await self._handle_http(scope, receive, send)
        elif scope["type"] == "websocket":
            await self._handle_websocket(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    async def _handle_http(self, scope: dict, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if path in self.exclude_paths:
            await self.app(scope, receive, send)
            return

        reset_request_context()

        request_id = uuid.uuid4().hex[:12]
        set_request_context(request_id=request_id)

        # Parse headers
        headers_dict: dict[str, str] = {}
        for header_name, header_value in scope.get("headers", []):
            name = header_name.decode("latin-1", errors="replace").lower()
            val = header_value.decode("latin-1", errors="replace")
            headers_dict[name] = val

        # Client IP
        client_ip = ""
        forwarded_for = headers_dict.get("x-forwarded-for")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        elif scope.get("client"):
            client_ip = scope["client"][0]

        user_agent = headers_dict.get("user-agent", "")

        if client_ip:
            set_request_context(client_ip=client_ip)
        if user_agent:
            set_request_context(user_agent=user_agent[:200])

        # Auto JWT context extraction (best-effort)
        _extract_jwt_context(headers_dict)

        # Capture request body for POST/PUT/PATCH
        method = scope.get("method", "")
        body_chunks: list[bytes] = []

        async def receive_wrapper() -> dict:
            message = await receive()
            if method in ("POST", "PUT", "PATCH") and message.get("type") == "http.request":
                body_chunks.append(message.get("body", b""))
            return message

        status_code = 0

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception as exc:
            if body_chunks:
                body = b"".join(body_chunks)
                if body:
                    set_request_context(request_body=summarize_body(body))

            self._capture_exception(exc, scope, source=ErrorSource.HTTP, status_code=500)
            raise

    async def _handle_websocket(self, scope: dict, receive: Any, send: Any) -> None:
        reset_request_context()
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            self._capture_exception(exc, scope, source=ErrorSource.WEBSOCKET)
            raise

    def _capture_exception(
        self,
        exc: Exception,
        scope: dict,
        source: ErrorSource = ErrorSource.HTTP,
        status_code: int = 0,
    ) -> None:
        try:
            notifier = TelegramNotifier.get_instance()
            if not notifier:
                return

            event = ErrorEvent(
                service_name=notifier.service_name,
                error_type=type(exc).__name__,
                error_message=str(exc),
                traceback_text=traceback.format_exc(),
                endpoint=scope.get("path", ""),
                method=scope.get("method", ""),
                status_code=status_code,
                source=source,
            )
            notifier.capture(event)
        except Exception:
            pass


def _extract_jwt_context(headers: dict[str, str]) -> None:
    """Extract user_id/phone/role from JWT Bearer token (no signature verification)."""
    try:
        auth_header = headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return

        token = auth_header[7:]
        parts = token.split(".")
        if len(parts) != 3:
            return

        # Decode payload (middle part)
        payload_b64 = parts[1]
        # Add padding
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))

        user_id = payload.get("sub")
        phone = payload.get("phone")
        role = payload.get("role")

        if user_id:
            set_request_context(user_id=user_id)
        if phone:
            set_request_context(phone=phone)
        if role:
            set_request_context(role=role)
    except Exception:
        pass
