"""APScheduler job error integration."""

from __future__ import annotations

import logging
from typing import Any

from oh_notifier.config import _get_settings_or_none
from oh_notifier.event import ErrorEvent, ErrorSeverity, ErrorSource
from oh_notifier.notifier import TelegramNotifier

logger = logging.getLogger("oh_notifier.apscheduler")


def setup_apscheduler_alerts(scheduler: Any) -> None:
    """Add error listener to an APScheduler scheduler instance."""
    try:
        from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
    except ImportError:
        logger.warning("apscheduler not installed, skipping integration")
        return

    def _listener(event: Any) -> None:
        try:
            notifier = TelegramNotifier.get_instance()
            if not notifier:
                return

            settings = _get_settings_or_none()
            job_id = getattr(event, "job_id", "unknown")

            if hasattr(event, "exception") and event.exception:
                tb_text = ""
                if hasattr(event, "traceback") and event.traceback:
                    tb_text = str(event.traceback)

                err_event = ErrorEvent(
                    service_name=settings.service_name if settings else "unknown",
                    error_type=type(event.exception).__name__,
                    error_message=str(event.exception),
                    traceback_text=tb_text,
                    source=ErrorSource.APSCHEDULER,
                    extras={"job_id": job_id},
                )
                notifier.capture(err_event)
            else:
                # Job missed
                err_event = ErrorEvent(
                    service_name=settings.service_name if settings else "unknown",
                    error_type="JobMissed",
                    error_message=f"Scheduled job '{job_id}' missed its run time",
                    source=ErrorSource.APSCHEDULER,
                    severity=ErrorSeverity.WARNING,
                    extras={"job_id": job_id},
                )
                notifier.capture(err_event)
        except Exception:
            pass

    scheduler.add_listener(_listener, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    logger.info("APScheduler alert listener connected")
