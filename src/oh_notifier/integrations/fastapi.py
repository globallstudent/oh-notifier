"""FastAPI/Starlette ASGI error middleware."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import traceback
import uuid
from typing import Any

from oh_notifier.config import _get_settings_or_none
from oh_notifier.context import reset_request_context, set_request_context
from oh_notifier.event import ErrorEvent, ErrorSource
from oh_notifier.masking import summarize_body
from oh_notifier.notifier import TelegramNotifier

logger = logging.getLogger("oh_notifier.fastapi")


class ErrorMiddleware:
    """Raw ASGI middleware that reports failures to Telegram.

    Catches unhandled exceptions *and* error responses that never raised —
    an exception handler that turns a fault into a JSON 500 is still a
    fault, and the previous version reported none of those because it only
    saw exceptions propagating out of the app.
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

        settings = _get_settings_or_none()
        max_body = settings.max_body_bytes if settings else 16384

        reset_request_context()

        request_id = uuid.uuid4().hex[:12]

        headers_dict: dict[str, str] = {}
        for header_name, header_value in scope.get("headers", []):
            name = header_name.decode("latin-1", errors="replace").lower()
            val = header_value.decode("latin-1", errors="replace")
            headers_dict[name] = val

        client_ip = ""
        forwarded_for = headers_dict.get("x-forwarded-for")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        elif scope.get("client"):
            client_ip = scope["client"][0]

        user_agent = headers_dict.get("user-agent", "")

        # One contextvar write instead of four.
        context: dict[str, str] = {"request_id": request_id}
        if client_ip:
            context["client_ip"] = client_ip
        if user_agent:
            context["user_agent"] = user_agent[:200]
        context.update(_decode_jwt_claims(headers_dict))
        set_request_context(**context)

        method = scope.get("method", "")
        capture_body = method in ("POST", "PUT", "PATCH")
        body_chunks: list[bytes] = []
        body_size = 0

        async def receive_wrapper() -> dict:
            nonlocal body_size
            message = await receive()
            if capture_body and message.get("type") == "http.request":
                chunk = message.get("body", b"")
                # Keep only what an alert can show. Buffering an entire
                # upload just in case it fails is a memory risk on any
                # endpoint that accepts files.
                if chunk and body_size < max_body:
                    body_chunks.append(chunk[: max_body - body_size])
                    body_size += len(chunk)
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
            self._attach_body(body_chunks)
            self._capture_exception(
                exc, scope, source=ErrorSource.HTTP, status_code=status_code or 500
            )
            raise
        else:
            if self._should_report(status_code, settings):
                self._attach_body(body_chunks)
                self._capture_status(scope, status_code)

    @staticmethod
    def _should_report(status_code: int, settings: Any) -> bool:
        if not status_code:
            return False
        if status_code >= 500:
            return settings.capture_http_5xx if settings else True
        if 400 <= status_code < 500:
            return bool(settings and settings.capture_http_4xx)
        return False

    @staticmethod
    def _attach_body(body_chunks: list[bytes]) -> None:
        if not body_chunks:
            return
        body = b"".join(body_chunks)
        if body:
            set_request_context(request_body=summarize_body(body))

    async def _handle_websocket(self, scope: dict, receive: Any, send: Any) -> None:
        reset_request_context()
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            self._capture_exception(exc, scope, source=ErrorSource.WEBSOCKET)
            raise

    def _capture_status(self, scope: dict, status_code: int) -> None:
        """Report an error response that no exception accompanied."""
        try:
            notifier = TelegramNotifier.get_instance()
            if not notifier:
                return
            notifier.capture(
                ErrorEvent(
                    service_name=notifier.service_name,
                    error_type=f"HTTP{status_code}",
                    error_message=(
                        f"{scope.get('method', '')} {scope.get('path', '')} "
                        f"returned {status_code}"
                    ).strip(),
                    endpoint=scope.get("path", ""),
                    method=scope.get("method", ""),
                    status_code=status_code,
                    source=ErrorSource.HTTP,
                )
            )
        except Exception:
            pass

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


def _decode_jwt_claims(headers: dict[str, str]) -> dict[str, str]:
    """Read user_id/phone/role from a Bearer token.

    The signature is deliberately NOT verified: this is labelling for an
    alert, not authorisation, and the middleware has no access to the signing
    key. Returns the claims rather than writing context itself so the caller
    can apply a single contextvar write.
    """
    claims: dict[str, str] = {}
    try:
        auth_header = headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return claims

        parts = auth_header[7:].split(".")
        if len(parts) != 3:
            return claims

        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if not isinstance(payload, dict):
            return claims

        for claim, key in (("sub", "user_id"), ("phone", "phone"), ("role", "role")):
            value = payload.get(claim)
            if value:
                claims[key] = str(value)
    except (ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        pass
    except Exception:
        pass
    return claims
