import asyncio


async def _finish_sse_worker(queue: asyncio.Queue) -> None:
    if asyncio.current_task().cancelling():
        return
    await queue.put(None)
