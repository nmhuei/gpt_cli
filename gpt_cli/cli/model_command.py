from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..config.settings import Settings
from ..core.token_cache import TokenCacheManager
from ..models.fetcher import fetch_upstream_models
from ..models.registry import ModelRegistry
from ..models.schemas import ModelDefinition


def build_suggested_command(flagship: ModelDefinition) -> tuple[str, str]:
    """Generate the suggested CLI run and chat command for the flagship model."""
    if flagship.supports_thinking and "extended" in flagship.thinking_efforts:
        run_cmd = f'gpt run -m {flagship.slug} -e extended -p "<prompt>"'
        chat_cmd = f"gpt chat -m {flagship.slug} -e extended"
    elif flagship.supports_thinking and "standard" in flagship.thinking_efforts:
        run_cmd = f'gpt run -m {flagship.slug} -e standard -p "<prompt>"'
        chat_cmd = f"gpt chat -m {flagship.slug} -e standard"
    else:
        run_cmd = f'gpt run -m {flagship.slug} -p "<prompt>"'
        chat_cmd = f"gpt chat -m {flagship.slug}"
    return run_cmd, chat_cmd


async def run_model_command(args: argparse.Namespace) -> int:
    """Handle 'gpt model' CLI command: sync/list models and display suggested flagship command."""
    settings = Settings()
    cache_mgr = TokenCacheManager(str(settings.cache_dir))
    bundle = cache_mgr.load_disk_cache()

    models: list[ModelDefinition] = []
    sync_source = "cache"

    if not getattr(args, "offline", False):
        if bundle:
            try:
                models = await fetch_upstream_models(bundle)
                if models:
                    ModelRegistry.save_cache(models, settings.models_cache_file)
                    sync_source = "upstream"
            except Exception as exc:
                if not getattr(args, "json", False):
                    sys.stderr.write(f"[Warning] Upstream sync failed ({exc}). Falling back to local cache.\n")
                models = ModelRegistry.all_models()
        else:
            if not getattr(args, "json", False):
                sys.stderr.write("[Warning] No active token cache found. Using local model registry.\n")
            models = ModelRegistry.all_models()
    else:
        models = ModelRegistry.all_models()

    if not models:
        models = ModelRegistry.all_models()

    flagship = ModelRegistry.pick_flagship_model(models)
    run_cmd, chat_cmd = build_suggested_command(flagship)

    if getattr(args, "json", False):
        output: dict[str, Any] = {
            "sync_source": sync_source,
            "total_models": len(models),
            "flagship": flagship.to_dict(),
            "suggested_command": run_cmd,
            "chat_command": chat_cmd,
            "models": [m.to_dict() for m in models],
        }
        sys.stdout.write(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
        return 0

    # Format human-friendly table
    sys.stdout.write(f"\nSupported ChatGPT Models (source: {sync_source}, total: {len(models)}):\n")
    sys.stdout.write("-" * 88 + "\n")
    sys.stdout.write(f"{'SLUG':<26} {'DISPLAY NAME':<24} {'THINKING':<12} {'EFFORTS'}\n")
    sys.stdout.write("-" * 88 + "\n")

    for m in models:
        efforts_str = ", ".join(m.thinking_efforts) if m.thinking_efforts else "-"
        thinking_disp = "yes" if m.supports_thinking else "no"
        if m.reasoning_type and m.reasoning_type not in ("reasoning", "none"):
            thinking_disp = m.reasoning_type
        sys.stdout.write(f"{m.slug:<26} {m.display_name:<24} {thinking_disp:<12} {efforts_str}\n")

    sys.stdout.write("-" * 88 + "\n\n")
    sys.stdout.write(f"⭐ Model mạnh nhất hiện tại: {flagship.slug} ({flagship.display_name})\n")
    sys.stdout.write(f"   Mức thinking: {', '.join(flagship.thinking_efforts) if flagship.thinking_efforts else 'none'}\n")
    sys.stdout.write("   Lệnh đề xuất để chạy:\n")
    sys.stdout.write(f"     $ {run_cmd}\n")
    sys.stdout.write(f"     $ {chat_cmd}\n\n")

    return 0
