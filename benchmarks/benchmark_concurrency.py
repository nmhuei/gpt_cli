import asyncio
import os
import sys
import time
import json
from pathlib import Path

os.environ["WEBGPT_FCONV_PREPARE"] = "1"
os.environ["WEBGPT_SENTINEL_CACHE"] = "1"

from gpt.transport.token_manager import TokenManager
from gpt.transport.curl_transport import CurlCffiTransport
from gpt.types import SendRequest, ModelInfo

TEST_PROMPTS = [
    "Giải thích định lý Pythagoras trong đúng 1 câu ngắn gọn.",
    "Giải thích nguyên lý Archimedes trong đúng 1 câu ngắn gọn.",
    "Giải thích định luật vạn vật hấp dẫn của Newton trong đúng 1 câu ngắn gọn.",
    "Giải thích thuyết tương đối hẹp của Einstein trong đúng 1 câu ngắn gọn.",
    "Giải thích định luật bảo toàn năng lượng trong đúng 1 câu ngắn gọn."
]

async def run_worker(worker_id: int, prompt: str, transport: CurlCffiTransport, bundle):
    t_start = time.perf_counter()
    req = SendRequest(
        text=prompt,
        conversation_id=None, # Session mới hoàn toàn cho mỗi worker
        model=ModelInfo(id="gpt-5-6-thinking", label="5.6"),
        reasoning_effort="high",
        timeout_seconds=90.0
    )
    
    print(f"[{time.strftime('%X')}] [Worker #{worker_id}] Bắt đầu prepare và gửi prompt...")
    try:
        sentinel, s_id, t_id, conduit = await transport._prepare_fconv_turn(bundle, req)
        t_prepared = time.perf_counter()
        
        headers = transport._build_headers(
            bundle, sentinel, turn_session_id=s_id, turn_trace_id=t_id, conduit_token=conduit, fconv=True
        )
        payload = transport._build_conversation_payload(req)
        
        t_post = time.perf_counter()
        response = await transport._post_conversation(headers, payload, req)
        status_code = getattr(response, "status_code", 200)
        
        if status_code != 200:
            err_text = await transport._peek_body_sample(response)
            await transport._close_quietly(response)
            return {
                "worker_id": worker_id,
                "status": f"HTTP {status_code}",
                "error": err_text[:200] if err_text else "Unknown",
                "duration": time.perf_counter() - t_start
            }
            
        turn_result = await transport._stream_sse(response, req)
        await transport._close_quietly(response)
        t_done = time.perf_counter()
        
        return {
            "worker_id": worker_id,
            "status": "OK 200",
            "conv_id": turn_result.conversation_id,
            "model": str(turn_result.model),
            "prep_time": t_prepared - t_start,
            "stream_time": t_done - t_post,
            "total_time": t_done - t_start,
            "response_preview": turn_result.text[:90].replace("\n", " ")
        }
    except Exception as e:
        return {
            "worker_id": worker_id,
            "status": f"EXCEPTION: {type(e).__name__}",
            "error": str(e),
            "duration": time.perf_counter() - t_start
        }

async def run_batch(concurrency: int):
    print(f"\n{'='*75}")
    print(f">>> BẮT ĐẦU TEST {concurrency} WORKERS SONG SONG CÙNG MỘT THỜI ĐIỂM <<<")
    print(f"{'='*75}\n")
    
    profile_dir = Path("/home/light/.local/share/webgpt/profiles/personal")
    tm = TokenManager(page=None, cache_dir=str(profile_dir), refresh_interval=86400)
    bundle = tm._load_disk_cache()
    if not bundle:
        print("LỖI: Không tìm thấy TokenBundle!")
        return

    transport = CurlCffiTransport(tm)
    
    tasks = []
    t_global_start = time.perf_counter()
    for i in range(concurrency):
        prompt = TEST_PROMPTS[i % len(TEST_PROMPTS)]
        tasks.append(run_worker(i + 1, prompt, transport, bundle))
        
    results = await asyncio.gather(*tasks)
    total_batch_time = time.perf_counter() - t_global_start
    
    print(f"\n--- KẾT QUẢ TEST CONCURRENCY = {concurrency} (Tổng thời gian: {total_batch_time:.2f}s) ---")
    success_count = 0
    for r in results:
        w_id = r["worker_id"]
        status = r["status"]
        if status == "OK 200":
            success_count += 1
            print(f"✓ Worker #{w_id}: {status} | Prep: {r['prep_time']:.2f}s | Stream: {r['stream_time']:.2f}s | Total: {r['total_time']:.2f}s")
            print(f"    Model: {r['model']} | Session: {r['conv_id']}")
            print(f"    Preview: {r['response_preview']}...")
        else:
            print(f"✗ Worker #{w_id}: {status} | Error: {r.get('error')}")
            
    print(f"\n===> THÀNH CÔNG: {success_count}/{concurrency} workers ({success_count/concurrency*100:.0f}%)")
    return success_count == concurrency

async def main():
    concurrency_target = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    await run_batch(concurrency_target)

if __name__ == "__main__":
    asyncio.run(main())
