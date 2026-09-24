import asyncio
import pytest
from gpt_cli.pool.worker_pool import WorkerPool

@pytest.mark.asyncio
async def test_worker_pool_concurrency_bound():
    pool = WorkerPool(max_workers=3)
    active_now = 0
    max_observed = 0
    
    async def dummy_job():
        nonlocal active_now, max_observed
        async with pool.semaphore:
            active_now += 1
            max_observed = max(max_observed, active_now)
            await asyncio.sleep(0.05)
            active_now -= 1
            
    await asyncio.gather(*[dummy_job() for _ in range(8)])
    assert max_observed <= 3
    assert pool.max_workers == 3

@pytest.mark.asyncio
async def test_worker_pool_execute():
    pool = WorkerPool(max_workers=5)
    
    async def add(a: int, b: int) -> int:
        await asyncio.sleep(0.01)
        return a + b
        
    results = await asyncio.gather(*[pool.execute(add, i, i * 2) for i in range(5)])
    assert results == [0, 3, 6, 9, 12]
