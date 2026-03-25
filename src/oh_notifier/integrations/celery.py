"""Celery task failure signal integration."""

from __future__ import annotations

import logging
from typing import Any

from oh_notifier.config import _get_settings_or_none
from oh_notifier.event import ErrorEvent, ErrorSource
from oh_notifier.notifier import TelegramNotifier
from oh_notifier.utils import sync_flush

logger = logging.getLogger("oh_notifier.celery")


def setup_celery_alerts(celery_app: Any = None) -> None:
    """Connect to Celery task_failure and task_retry signals."""
    try:
        from celery.signals import task_failure, task_retry
    except ImportError:
        logger.warning("celery not installed, skipping celery integration")
        return

    @task_failure.connect
    def _on_task_failure(
        sender: Any = None,
        task_id: str | None = None,
        exception: BaseException | None = None,
        traceback: Any = None,  # noqa: F811
        **kwargs: Any,
    ) -> None:
        try:
            notifier = TelegramNotifier.get_instance()
            if not notifier:
                return

            settings = _get_settings_or_none()
            tb_text = ""
            if traceback:
                import traceback as tb_module
                tb_text = "".join(tb_module.format_tb(traceback))  # noqa: F811

            task_name = getattr(sender, "name", str(sender)) if sender else "unknown"

            event = ErrorEvent(
                service_name=settings.service_name if settings else "unknown",
                error_type=type(exception).__name__ if exception else "TaskFailure",
                error_message=str(exception) if exception else "Task failed",
                traceback_text=tb_text,
                source=ErrorSource.CELERY,
                extras={
                    "task_name": task_name,
                    "task_id": task_id or "",
                },
            )
            notifier.capture(event)
            sync_flush()
        except Exception:
            pass

    @task_retry.connect
    def _on_task_retry(
        sender: Any = None,
        request: Any = None,
        reason: Any = None,
        **kwargs: Any,
    ) -> None:
        try:
            notifier = TelegramNotifier.get_instance()
            if not notifier:
                return

            task_name = getattr(sender, "name", str(sender)) if sender else "unknown"

            from oh_notifier import send_warning
            send_warning(
                f"Task {task_name} retrying: {reason}",
                error_type="TaskRetry",
                extras={
                    "task_name": task_name,
                    "task_id": getattr(request, "id", "") if request else "",
                    "reason": str(reason),
                },
            )
        except Exception:
            pass

    logger.info("Celery alert signals connected")
