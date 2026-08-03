"""Background delivery worker.

Alert delivery runs on a dedicated daemon thread with its own event loop, and
never on the host's. That is a deliberate trade of one thread for the removal
of an entire class of bugs the previous design had:

* ``capture()`` reached for ``asyncio.get_event_loop()``. Called from a
  worker thread — which is where a sync FastAPI endpoint and most logging
  actually run — that raises, the ``except RuntimeError: pass`` swallowed it,
  and the overflow flush silently never happened.
* ``sync_flush()`` built a *new* event loop and drove the existing
  ``httpx.AsyncClient`` on it. That client's connection pool belongs to the
  loop that created it; using it from another is undefined and in practice
  hangs or raises "Event loop is closed". It ran on every Celery task
  failure and blocked the task for up to the HTTP timeout.
* Sends shared the application's loop, so a slow Telegram call competed with
  real request handling.

With delivery isolated, the host only ever does a lock-protected dict update
(``ErrorBuffer.add``) and an ``Event.set`` — no I/O, no awaiting, no chance of
blocking a request, a Celery task or a consumer.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Awaitable, Callable

#: Bound on how long ``start()`` waits for the worker loop to come up. Only
#: hit if the interpreter is pathologically busy; we proceed regardless
#: rather than delay application startup for a monitoring tool.
_LOOP_READY_TIMEOUT = 5.0


class DeliveryWorker:
    """Runs an async flush routine on a private thread + event loop."""

    def __init__(
        self,
        flush: Callable[[], Awaitable[None]],
        setup: Callable[[], Awaitable[None]] | None = None,
        teardown: Callable[[], Awaitable[None]] | None = None,
        interval: float = 2.0,
    ) -> None:
        self._flush = flush
        self._setup = setup
        self._teardown = teardown
        self._interval = interval

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._loop_ready = threading.Event()
        self._stopping = False
        self._started = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._started and not self._stopping

    def start(self) -> None:
        """Spawn the worker thread. Safe to call twice."""
        if self._started:
            return
        self._started = True
        self._stopping = False
        self._loop_ready.clear()

        self._thread = threading.Thread(
            target=self._run,
            name="oh-notifier-delivery",
            daemon=True,  # must never hold up interpreter exit
        )
        self._thread.start()
        # Wait for the loop so an immediate wake() is not dropped.
        self._loop_ready.wait(timeout=_LOOP_READY_TIMEOUT)

    def wake(self) -> None:
        """Ask for an immediate flush. Thread-safe, never blocks, never raises."""
        loop, wake = self._loop, self._wake
        if loop is None or wake is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            # Loop shut down between the check and the call — the final
            # drain in _run() covers anything still buffered.
            pass

    def stop(self, timeout: float = 10.0) -> None:
        """Signal shutdown and wait for the final drain. Blocking; call from a thread."""
        if not self._started:
            return
        self._stopping = True
        self.wake()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._started = False
        self._thread = None
        self._loop = None
        self._wake = None

    async def astop(self, timeout: float = 10.0) -> None:
        """``stop()`` without blocking the caller's event loop."""
        if not self._started:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.stop, timeout)

    # -- worker ------------------------------------------------------------

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._main())
        except Exception:
            # A dead delivery thread must not take the service with it.
            pass
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _main(self) -> None:
        self._wake = asyncio.Event()
        self._loop_ready.set()

        if self._setup is not None:
            try:
                await self._setup()
            except Exception:
                pass

        try:
            while not self._stopping:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
                except asyncio.TimeoutError:
                    pass  # periodic flush
                except Exception:
                    pass
                self._wake.clear()

                try:
                    await self._flush()
                except Exception:
                    pass
        finally:
            # Final drain — buffered alerts must survive shutdown, which is
            # exactly when the interesting errors tend to happen.
            try:
                await self._flush()
            except Exception:
                pass
            if self._teardown is not None:
                try:
                    await self._teardown()
                except Exception:
                    pass
