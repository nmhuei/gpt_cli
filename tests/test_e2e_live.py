import asyncio
import os
import time
import pytest
from gpt_cli.core.token_cache import TokenCacheManager
from gpt_cli.transport.wire import WireTransport
from gpt_cli.pool.worker_pool import WorkerPool

TEST_PROMPTS = [
    "Viết 1 câu ngắn định nghĩa Định lý Pythagoras.",
    "Viết 1 câu ngắn định nghĩa Nguyên lý Archimedes.",
    "Viết 1 câu ngắn định nghĩa Định luật 1 Newton.",
]

@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("WEBGPT_RUN_LIVE_TESTS") != "1",
    reason="live upstream certification requires WEBGPT_RUN_LIVE_TESTS=1",
)
def test_live_3_concurrent_workers_certification():
    async def _run():
        cache_mgr = TokenCacheManager("~/.local/share/webgpt/profiles/personal")
        bundle = cache_mgr.load_disk_cache()
        assert bundle is not None, "Personal token cache must exist for live certification"

        pool = WorkerPool(max_workers=3)
        t0 = time.perf_counter()

        async def worker_job(worker_id: int, prompt: str):
            async def do_turn():
                async with WireTransport(bundle) as transport:
                    return await transport.send_turn(
                        prompt=prompt,
                        model_alias="gpt-5-6-thinking",
                        effort="extended"
                    )
            return await pool.execute(do_turn)

        tasks = [worker_job(i, prompt) for i, prompt in enumerate(TEST_PROMPTS)]
        results = await asyncio.gather(*tasks)
        total_time = time.perf_counter() - t0

        assert len(results) == 3
        for i, res in enumerate(results):
            assert res.model == "gpt-5-6-thinking", f"Worker {i} did not use gpt-5-6-thinking"
            assert len(res.text.strip()) > 5, f"Worker {i} returned empty response"
            assert res.conversation_id, f"Worker {i} missing conversation_id"

        # All 5 separate sessions
        conv_ids = [r.conversation_id for r in results]
        assert len(set(conv_ids)) == 5, "Workers must run on separate conversations"

    asyncio.run(_run())
