"""Telegram Bot API message dispatch with rate limiting."""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger("oh_notifier.sender")


class TelegramSender:
    """Sends messages to Telegram with rate limiting and 429 retry."""

    def __init__(self, bot_token: str, chat_id: str, rate_limit: float = 1.0) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._rate_limit = rate_limit
        self._client: httpx.AsyncClient | None = None
        self._last_send_time = 0.0

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)

    async def stop(self) -> None:
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    async def send(self, html_text: str) -> None:
        """Send HTML message to Telegram with rate limiting."""
        if not self._client:
            return

        # Rate limiting
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_send_time
        if elapsed < self._rate_limit:
            await asyncio.sleep(self._rate_limit - elapsed)

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            resp = await self._client.post(url, json=payload)
            self._last_send_time = asyncio.get_event_loop().time()

            if resp.status_code == 429:
                try:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                except Exception:
                    retry_after = 5
                await asyncio.sleep(retry_after)
                await self._client.post(url, json=payload)
                self._last_send_time = asyncio.get_event_loop().time()
        except Exception:
            pass
