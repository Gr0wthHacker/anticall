"""An asyncio event loop on a daemon thread, for the synchronous callback session code."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")
_background: set[asyncio.Future[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Future[Any]:
    """Fire-and-forget on the running loop, holding a strong reference until done."""
    task = asyncio.ensure_future(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


class LoopThread:
    def __init__(self, name: str = "callshield-asterisk-loop") -> None:
        self._name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("loop thread is not running")
        return self._loop

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
            self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("loop thread failed to start")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def submit(self, coro: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call_soon(self, fn: Callable[[], None]) -> None:
        self.loop.call_soon_threadsafe(fn)

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            loop, thread = self._loop, self._thread
            if loop is None or thread is None:
                return
            loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout)
        self._thread = None
        self._loop = None
