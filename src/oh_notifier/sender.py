from __future__ import annotations

import asyncio
import html
import re
import sys
import time
from dataclasses import dataclass

import httpx

_TAG_RE = re.compile(r"<[^>]*>")

#: Telegram refuses anything past this; keep a little headroom for the
#: "(plain text fallback)" note we may prepend.
_HARD_LIMIT = 4096


@dataclass
class SendStats:
    sent: int = 0
    failed: int = 0
    retried: int = 0
    html_rejected: int = 0
    last_error: str = ""

    def snapshot_and_reset_failures(self) -> tuple[int, str]:
        failed, err = self.failed, self.last_error
        self.failed = 0
        self.last_error = ""
        return failed, err


@dataclass
class _Backoff:
    base: float = 0.5
    factor: float = 2.0
    ceiling: float = 8.0

    def delay(self, attempt: int) -> float:
        return min(self.base * (self.factor ** attempt), self.ceiling)


def to_plain_text(html_text: str) -> str:
    return html.unescape(_TAG_RE.sub("", html_text))


class TelegramSender:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        rate_limit: float = 1.0,
        timeout: float = 10.0,
        max_attempts: int = 3,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._rate_limit = rate_limit
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        self._client: httpx.AsyncClient | None = None
        self._last_send_time = 0.0
        self._backoff = _Backoff()
        self.stats = SendStats()

    @property
    def configured(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)

    async def stop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass

    @property
    def _url(self) -> str:
        return f"https://api.telegram.org/bot{self._bot_token}/sendMessage"

    async def _respect_rate_limit(self) -> None:
        # monotonic(), not loop.time(): the value must stay comparable even
        # if this sender is ever driven from more than one loop.
        elapsed = time.monotonic() - self._last_send_time
        if elapsed < self._rate_limit:
            await asyncio.sleep(self._rate_limit - elapsed)

    async def send(self, html_text: str) -> bool:
        """Deliver one message. Returns True if Telegram accepted it."""
        if self._client is None or not self.configured:
            return False

        payload = {
            "chat_id": self._chat_id,
            "text": html_text[:_HARD_LIMIT],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        for attempt in range(self._max_attempts):
            await self._respect_rate_limit()
            try:
                resp = await self._client.post(self._url, json=payload)
            except Exception as exc:  # transport: DNS, connect, timeout, TLS
                self._note_failure(f"{type(exc).__name__}: {exc}")
                if attempt + 1 < self._max_attempts:
                    self.stats.retried += 1
                    await asyncio.sleep(self._backoff.delay(attempt))
                    continue
                return False
            finally:
                self._last_send_time = time.monotonic()

            if resp.status_code == 200:
                self.stats.sent += 1
                return True

            if resp.status_code == 429:
                await asyncio.sleep(self._retry_after(resp))
                self.stats.retried += 1
                continue

            if resp.status_code == 400 and payload["parse_mode"] == "HTML":
                # Almost always our own malformed markup. Re-send as plain
                # text rather than lose the alert entirely.
                self.stats.html_rejected += 1
                payload = {
                    "chat_id": self._chat_id,
                    "text": to_plain_text(html_text)[:_HARD_LIMIT],
                    "disable_web_page_preview": True,
                }
                self.stats.retried += 1
                continue

            if resp.status_code >= 500:
                self._note_failure(f"telegram {resp.status_code}")
                if attempt + 1 < self._max_attempts:
                    self.stats.retried += 1
                    await asyncio.sleep(self._backoff.delay(attempt))
                    continue
                return False

            # 401/403/404 — bad token or chat id. Retrying cannot help.
            self._note_failure(f"telegram {resp.status_code}: {resp.text[:200]}")
            return False

        self._note_failure("exhausted send attempts")
        return False

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float:
        try:
            return float(resp.json().get("parameters", {}).get("retry_after", 5))
        except Exception:
            return 5.0

    def _note_failure(self, reason: str) -> None:
        self.stats.failed += 1
        self.stats.last_error = reason
        # stderr, never `logging` — the logging handler feeds this module and
        # routing a delivery failure back through it would recurse.
        print(f"[oh-notifier] telegram delivery failed: {reason}", file=sys.stderr)
