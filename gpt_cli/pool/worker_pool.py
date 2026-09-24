import asyncio
from typing import Any, Callable, Coroutine
from ..config import defaults

class WorkerPool:
    """Smart async worker pool enforcing strict concurrency bounding (cap=5)."""

    def __init__(self, max_workers: int | None = None):
        self.max_workers = max_workers or defaults.DEFAULT_MAX_WORKERS
        self.semaphore = asyncio.Semaphore(self.max_workers)
        self._active_count = 0
        self._lock = asyncio.Lock()

    @property
    def active_workers(self) -> int:
        return self._active_count

    async def execute(self, coro_func: Callable[..., Coroutine[Any, Any, Any]], *args: Any, **kwargs: Any) -> Any:
        """Executes a coroutine function bounded by the pool semaphore."""
        async with self.semaphore:
            async with self._lock:
                self._active_count += 1
            try:
                return await coro_func(*args, **kwargs)
            finally:
                async with self._lock:
                    self._active_count -= 1
