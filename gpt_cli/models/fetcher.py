from __future__ import annotations

import logging
from typing import Any
from curl_cffi.requests import AsyncSession

from ..config import defaults
from ..core.types import TokenBundle
from ..transport.conduit import ConduitHandshake
from .schemas import ModelDefinition

logger = logging.getLogger("gpt_cli.models")


def parse_upstream_models_response(data: dict[str, Any], include_work_mode: bool = False) -> list[ModelDefinition]:
    """Parse raw /backend-api/models JSON payload into a list of ModelDefinition."""
    raw_models = data.get("models") or []
    results: list[ModelDefinition] = []

    for item in raw_models:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not slug or not isinstance(slug, str):
            continue

        # Ignore work-mode models for standard ChatGPT Web chat
        if not include_work_mode:
            if item.get("is_work_mode_model") is True:
                continue
            if slug.endswith("-wm") or "-wm-" in slug:
                continue

        title = item.get("title") or slug
        desc = item.get("description") or ""
        reasoning_type = item.get("reasoning_type") or "none"
        conf_effort = bool(item.get("configurable_thinking_effort", False))
        
        raw_efforts = item.get("thinking_efforts") or []
        efforts: list[str] = []
        for e in raw_efforts:
            if isinstance(e, dict) and "thinking_effort" in e:
                effort_name = str(e["thinking_effort"])
                if effort_name and effort_name not in efforts:
                    efforts.append(effort_name)
            elif isinstance(e, str) and e not in efforts:
                efforts.append(e)

        supports_thinking = conf_effort or (reasoning_type == "reasoning") or bool(efforts)
        default_effort = "extended" if "extended" in efforts else ("standard" if "standard" in efforts else "extended")

        model_def = ModelDefinition(
            slug=slug,
            display_name=str(title),
            default_effort=default_effort,
            supports_thinking=supports_thinking,
            thinking_efforts=tuple(efforts),
            reasoning_type=str(reasoning_type),
            description=str(desc),
        )
        results.append(model_def)

    return results


async def fetch_upstream_models(
    bundle: TokenBundle,
    session: AsyncSession | None = None,
    timeout: float = 15.0,
) -> list[ModelDefinition]:
    """Fetch live supported models from ChatGPT Web backend (/backend-api/models)."""
    headers = ConduitHandshake.build_integrity_headers(
        bundle=bundle,
        turn_session_id="model-sync",
        turn_trace_id="model-sync-trace",
        target_path="/backend-api/models",
    )
    cookies = dict(bundle.cookies)
    if bundle.cf_clearance:
        cookies["cf_clearance"] = bundle.cf_clearance

    close_session = False
    if session is None:
        session = AsyncSession(impersonate="chrome146")
        close_session = True

    try:
        resp = await session.get(
            defaults.MODELS_URL,
            headers=headers,
            cookies=cookies,
            timeout=timeout,
        )
        if resp.status_code != 200:
            err_msg = f"Failed to fetch models from {defaults.MODELS_URL}: HTTP {resp.status_code}"
            logger.error(err_msg)
            raise RuntimeError(err_msg)

        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected JSON shape from models endpoint: {type(data)}")

        return parse_upstream_models_response(data)
    finally:
        if close_session:
            await session.close()
