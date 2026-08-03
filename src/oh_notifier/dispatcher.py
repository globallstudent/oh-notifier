from __future__ import annotations

import asyncio
import threading
from typing import Awaitable, Callable

_LOOP_READY_TIMEOUT = 5.0


class DeliveryWorker:

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
        loop, wake = self._loop, self._wake
        if loop is None or wake is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            pass

    def stop(self, timeout: float = 10.0) -> None:
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
            try:
                await self._flush()
            except Exception:
                pass
            if self._teardown is not None:
                try:
                    await self._teardown()
                except Exception:
                    pass
