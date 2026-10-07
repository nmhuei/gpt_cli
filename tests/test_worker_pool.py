import asyncio
import json
import sys
import pytest
from gpt_cli.pool.worker_pool import WorkerPool

def test_worker_pool_concurrency_bound():
    async def _run():
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

    asyncio.run(_run())

def test_worker_pool_execute():
    async def _run():
        pool = WorkerPool(max_workers=5)

        async def add(a: int, b: int) -> int:
            await asyncio.sleep(0.01)
            return a + b

        results = await asyncio.gather(*[pool.execute(add, i, i * 2) for i in range(5)])
        assert results == [0, 3, 6, 9, 12]

    asyncio.run(_run())


def test_worker_pool_none_uses_default():
    pool = WorkerPool(max_workers=None)
    assert pool.max_workers == 3


def test_worker_pool_rejects_non_positive_limits():
    with pytest.raises(ValueError, match="max_workers must be a positive integer"):
        WorkerPool(max_workers=0)

    with pytest.raises(ValueError, match="max_workers must be a positive integer"):
        WorkerPool(max_workers=-1)


def test_worker_finish_reason_reflects_lifecycle(monkeypatch, tmp_path, capsys):
    from gpt_cli.core.types import TokenBundle, TurnResult
    from gpt_cli.harness.worker import run_worker

    monkeypatch.setattr(
        "gpt_cli.harness.worker.TokenCacheManager.load_disk_cache",
        lambda self: TokenBundle(access_token="fake"),
    )

    async def fake_send_turn(self, *args, **kwargs):
        return TurnResult(
            text="tool ended",
            conversation_id="conv_1",
            message_id="msg_1",
            model="gpt-5-6-thinking",
            lifecycle_status="tool_timeout",
        )

    monkeypatch.setattr("gpt_cli.harness.worker.WireTransport.send_turn", fake_send_turn)
    monkeypatch.setattr(
        sys,
        "argv",
        ["worker", "--session-root", str(tmp_path), "--no-bridge", "solve /tmp/challenge"],
    )
    assert asyncio.run(run_worker()) == 0

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["finish_reason"] == "tool_timeout"
