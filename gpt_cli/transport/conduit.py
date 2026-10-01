import asyncio
import os
import uuid
import time
import calendar
from typing import Any
from curl_cffi.requests import AsyncSession
from ..core.types import TokenBundle
from ..config import defaults
from .pow import solve_sentinel_pow, bootstrap_proof_token, PROOF_TOKEN_PREFIX

class ConduitHandshake:
    """Performs Sentinel chat-requirements check and Conduit turn preparation."""
    
    @staticmethod
    def build_integrity_headers(
        bundle: TokenBundle,
        turn_session_id: str,
        turn_trace_id: str,
        user_agent: str | None = None,
        requirements_token: str | None = None,
        proof_token: str | None = None,
        conduit_token: str | None = None,
        use_prepare_header: bool = True,
        is_stream: bool = False,
        target_path: str | None = "/backend-api/f/conversation",
    ) -> dict[str, str]:
        ua = user_agent or defaults.DEFAULT_USER_AGENT
        cookies = dict(bundle.cookies)
        if bundle.cf_clearance:
            cookies["cf_clearance"] = bundle.cf_clearance

        headers = {
            "Accept": "text/event-stream" if is_stream else "application/json",
            "Authorization": f"Bearer {bundle.access_token}",
            "Content-Type": "application/json",
            "Origin": defaults.CHATGPT_BASE_URL,
            "Referer": f"{defaults.CHATGPT_BASE_URL}/",
            "oai-language": "en-US",
            "User-Agent": ua,
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "oai-device-id": bundle.oai_device_id or "",
            "oai-session-id": turn_session_id,
            "x-oai-turn-trace-id": turn_trace_id,
            "oai-client-version": f"prod-{defaults.DEFAULT_BUILD_HASH}",
            "oai-client-build-number": "10145581",
            "oai-genui-client-actions": "open_entity_detail",
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": '"146.0.7680.177"',
        }
        if target_path:
            headers["x-openai-target-path"] = target_path
            headers["x-openai-target-route"] = target_path
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        if bundle.chatgpt_account_id:
            headers["ChatGPT-Account-ID"] = bundle.chatgpt_account_id
            
        if requirements_token:
            req_header = (
                "openai-sentinel-chat-requirements-prepare-token"
                if use_prepare_header
                else "openai-sentinel-chat-requirements-token"
            )
            headers[req_header] = requirements_token
            
        if proof_token:
            headers["openai-sentinel-proof-token"] = proof_token
            
        if conduit_token:
            headers["X-Conduit-Token"] = conduit_token
            
        return headers

    @staticmethod
    async def prepare_turn(
        session: AsyncSession,
        bundle: TokenBundle,
        model_slug: str,
        user_agent: str | None = None,
        conversation_id: str | None = None,
        parent_id: str | None = None,
        system_hints: list[str] | None = None,
    ) -> tuple[dict[str, str], str, str, str | None]:
        ua = user_agent or defaults.DEFAULT_USER_AGENT
        turn_session_id = str(uuid.uuid4())
        turn_trace_id = str(uuid.uuid4())
        device_id = bundle.oai_device_id or ""

        # Step 1: Sentinel chat-requirements with bootstrap proof
        bootstrap = bootstrap_proof_token(ua, device_id)
        headers = ConduitHandshake.build_integrity_headers(
            bundle, turn_session_id, turn_trace_id, ua, target_path=None
        )
        resp = await session.post(
            defaults.SENTINEL_PREPARE_URL,
            headers=headers,
            json={"p": bootstrap},
            timeout=15.0
        )
        is_prepare_stage = False
        if resp.status_code != 200:
            resp = await session.post(
                defaults.SENTINEL_CLASSIC_URL,
                headers=headers,
                json={"p": bootstrap},
                timeout=15.0
            )
            
        data = resp.json() if resp.status_code == 200 else {}
        token = data.get("token") or ""
        prepare_token = data.get("prepare_token") or data.get("prepareToken") or ""

        proof_token: str | None = None
        challenge = data.get("proofofwork") or data.get("proof_challenge")
        if isinstance(challenge, dict) and challenge.get("required"):
            seed = challenge.get("seed", "")
            diff = challenge.get("difficulty", "")
            if seed and diff:
                pow_val = await asyncio.to_thread(
                    solve_sentinel_pow, seed, diff, ua, device_id
                )
                proof_token = PROOF_TOKEN_PREFIX + pow_val

        # Step 1.5: Only a prepare-stage token needs finalization. A final
        # requirements token must never be sent to this endpoint again.
        if not token and prepare_token:
            fin_resp = await session.post(
                defaults.SENTINEL_FINALIZE_URL,
                headers=headers,
                json={"prepare_token": prepare_token},
                timeout=15.0,
            )
            if fin_resp.status_code != 200:
                raise RuntimeError(
                    f"Sentinel finalize returned HTTP {fin_resp.status_code}"
                )
            fin_data = fin_resp.json()
            token = fin_data.get("token") if isinstance(fin_data, dict) else None
            if not isinstance(token, str) or not token:
                raise RuntimeError("Sentinel finalize returned no requirements token")

        if not token:
            raise RuntimeError("Sentinel requirements returned no usable token")

        # Step 2: Conduit Prepare
        conduit_headers = ConduitHandshake.build_integrity_headers(
            bundle, turn_session_id, turn_trace_id, ua,
            requirements_token=token, proof_token=proof_token,
            use_prepare_header=False,
            target_path="/backend-api/f/conversation/prepare",
        )
        conduit_headers["X-Conduit-Token"] = "no-token"

        ts = time.time()
        local = time.localtime(ts)
        tz_offset = round((calendar.timegm(local) - int(ts)) / 60.0)

        prep_body: dict[str, Any] = {
            "action": "next",
            "client_contextual_info": {
                "app_name": "chatgpt.com",
                "has_web_push_capabilities": False,
                "web_push_notification_permission": "default",
            },
            "client_prepare_dispatch": "conversation",
            "client_prepare_source": "chatgpt_web_client",
            "client_prepare_state": "none",
            "conversation_mode": {"kind": "primary_assistant"},
            "local_function_names": [],
            "model": model_slug,
            "parent_message_id": parent_id or "client-created-root",
            "supported_encodings": ["v1"],
            "supports_buffering": True,
            "system_hints": list(system_hints or []),
            "timezone": "Asia/Ho_Chi_Minh",
            "timezone_offset_min": tz_offset,
        }
        if conversation_id and parent_id and parent_id != "client-created-root":
            prep_body["conversation_id"] = conversation_id
        else:
            prep_body["parent_message_id"] = "client-created-root"

        prep_resp = await session.post(
            defaults.FCONV_PREPARE_URL,
            headers=conduit_headers,
            json=prep_body,
            timeout=15.0
        )
        conduit_token: str | None = None
        if prep_resp.status_code == 200:
            conduit_data = prep_resp.json()
            conduit_token = conduit_data.get("conduit_token") or conduit_data.get("conduitToken")

        # Step 3: Stream Headers
        final_headers = ConduitHandshake.build_integrity_headers(
            bundle, turn_session_id, turn_trace_id, ua,
            requirements_token=token, proof_token=proof_token,
            conduit_token=conduit_token, use_prepare_header=False,
            is_stream=True, target_path="/backend-api/f/conversation",
        )
            
        return final_headers, turn_session_id, turn_trace_id, conduit_token
