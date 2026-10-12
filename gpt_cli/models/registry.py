from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import ClassVar

from ..config import defaults
from .schemas import ModelDefinition

logger = logging.getLogger("gpt_cli.models")


class ModelRegistry:
    """Decoupled model registry: maps aliases to canonical model definitions."""

    DEFAULT_SLUG: ClassVar[str] = "gpt-6-thinking"

    _BASE_MODELS: ClassVar[dict[str, ModelDefinition]] = {
        "gpt-6-thinking": ModelDefinition(
            slug="gpt-6-thinking",
            display_name="GPT-6",
            default_effort="extended",
            supports_thinking=True,
            thinking_efforts=("standard", "extended"),
            reasoning_type="reasoning",
        ),
        "gpt-5-6-thinking": ModelDefinition(
            slug="gpt-5-6-thinking",
            display_name="GPT-5.6 Sol Thinking",
            default_effort="extended",
            supports_thinking=True,
            thinking_efforts=("standard", "extended"),
            reasoning_type="reasoning",
        ),
        "gpt-5-thinking": ModelDefinition(
            slug="gpt-5-thinking",
            display_name="GPT-5 Thinking",
            default_effort="extended",
            supports_thinking=True,
            thinking_efforts=("standard", "extended"),
            reasoning_type="reasoning",
        ),
    }

    _BASE_ALIASES: ClassVar[dict[str, str]] = {
        "6": "gpt-6-thinking",
        "6-thinking": "gpt-6-thinking",
        "6t": "gpt-6-thinking",
        "gpt-6": "gpt-6-thinking",
        "gpt-6-thinking": "gpt-6-thinking",
        "5.6": "gpt-5-6-thinking",
        "5.6-thinking": "gpt-5-6-thinking",
        "sol": "gpt-5-6-thinking",
        "thinking": "gpt-6-thinking",
        "default": "gpt-6-thinking",
        "5": "gpt-5-thinking",
        "gpt-5": "gpt-5-thinking",
    }

    _MODELS: ClassVar[dict[str, ModelDefinition]] = dict(_BASE_MODELS)
    _ALIASES: ClassVar[dict[str, str]] = dict(_BASE_ALIASES)
    _CACHE_LOADED: ClassVar[bool] = False

    @classmethod
    def ensure_cache_loaded(cls) -> None:
        if not cls._CACHE_LOADED:
            cls.load_cache()

    @classmethod
    def clear_dynamic(cls) -> None:
        """Reset registry back to base static definitions (useful for testing)."""
        cls._MODELS = dict(cls._BASE_MODELS)
        cls._ALIASES = dict(cls._BASE_ALIASES)
        cls._CACHE_LOADED = False

    @classmethod
    def all_models(cls) -> list[ModelDefinition]:
        cls.ensure_cache_loaded()
        return list(cls._MODELS.values())

    @classmethod
    def resolve(cls, name_or_alias: str | None = None) -> ModelDefinition:
        cls.ensure_cache_loaded()
        if not name_or_alias:
            return cls._MODELS.get(cls.DEFAULT_SLUG, list(cls._MODELS.values())[0])
        normalized = name_or_alias.strip().lower()
        slug = cls._ALIASES.get(normalized, normalized)
        if slug in cls._MODELS:
            return cls._MODELS[slug]
        # Fallback to default flagship if unknown
        return cls._MODELS.get(cls.DEFAULT_SLUG, list(cls._MODELS.values())[0])

    @classmethod
    def generate_aliases_for_slug(cls, slug: str) -> list[str]:
        """Generate common shorthand aliases for a model slug."""
        normalized = slug.strip().lower()
        aliases: set[str] = {normalized}

        if normalized.startswith("gpt-"):
            stripped = normalized[4:]
            aliases.add(stripped)

            # 5-6 -> 5.6
            with_dots = re.sub(r"^(\d+)-(\d+)", r"\1.\2", stripped)
            if with_dots != stripped:
                aliases.add(with_dots)

            # thinking shorthand: e.g. 6-thinking -> 6t, gpt-6-thinking -> gpt-6t
            if normalized.endswith("-thinking"):
                prefix = normalized[:-9]
                aliases.add(f"{prefix}t")
                if prefix.startswith("gpt-"):
                    aliases.add(f"{prefix[4:]}t")
                    aliases.add(f"{with_dots[:-9]}t" if with_dots.endswith("-thinking") else f"{prefix[4:]}t")

            # instant shorthand: e.g. 6-instant -> 6i
            if normalized.endswith("-instant"):
                prefix = normalized[:-8]
                aliases.add(f"{prefix}i")
                if prefix.startswith("gpt-"):
                    aliases.add(f"{prefix[4:]}i")

        return list(aliases)

    @classmethod
    def register_model(cls, definition: ModelDefinition, aliases: list[str] | None = None) -> None:
        cls._MODELS[definition.slug] = definition
        auto_aliases = cls.generate_aliases_for_slug(definition.slug)
        for a in auto_aliases:
            cls._ALIASES[a.strip().lower()] = definition.slug
        if aliases:
            for a in aliases:
                cls._ALIASES[a.strip().lower()] = definition.slug

    @classmethod
    def save_cache(cls, models: list[ModelDefinition], cache_path: Path | str | None = None) -> None:
        """Persist model definitions to disk cache and register them in memory."""
        target_path = Path(cache_path or defaults.DEFAULT_MODELS_CACHE_FILE).expanduser()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        data = {"models": [m.to_dict() for m in models]}
        tmp_path = target_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(target_path)

        for m in models:
            cls.register_model(m)
        cls._CACHE_LOADED = True

    @classmethod
    def load_cache(cls, cache_path: Path | str | None = None) -> list[ModelDefinition]:
        """Load cached models from disk into in-memory registry."""
        target_path = Path(cache_path or defaults.DEFAULT_MODELS_CACHE_FILE).expanduser()
        cls._CACHE_LOADED = True
        if not target_path.is_file():
            return list(cls._MODELS.values())

        try:
            content = target_path.read_text(encoding="utf-8")
            data = json.loads(content)
            raw_models = data.get("models") or []
            loaded: list[ModelDefinition] = []
            for item in raw_models:
                if isinstance(item, dict) and "slug" in item:
                    model_def = ModelDefinition.from_dict(item)
                    cls.register_model(model_def)
                    loaded.append(model_def)
            return loaded or list(cls._MODELS.values())
        except Exception as exc:
            logger.warning(f"Could not load models cache from {target_path}: {exc}")
            return list(cls._MODELS.values())

    @classmethod
    def pick_flagship_model(cls, models: list[ModelDefinition] | None = None) -> ModelDefinition:
        """Score models and return the single most powerful reasoning model available."""
        pool = models if models is not None else cls.all_models()
        if not pool:
            return cls._MODELS[cls.DEFAULT_SLUG]

        def _score(m: ModelDefinition) -> tuple[int, int, int, float]:
            slug = m.slug.lower()
            non_wm = 0 if "-wm" in slug else 1
            non_mini = 0 if ("mini" in slug or "instant" in slug) else 1
            thinking_score = (
                2 if (m.supports_thinking and "extended" in m.thinking_efforts)
                else (1 if m.supports_thinking else 0)
            )

            major, minor = 0, 0
            m_ver = re.search(r"gpt-(\d+)(?:[.\-](\d+))?", slug)
            if m_ver:
                major = int(m_ver.group(1))
                if m_ver.group(2):
                    minor = int(m_ver.group(2))
            version = major + minor * 0.1

            return (non_wm, non_mini, thinking_score, version)

        sorted_models = sorted(pool, key=_score, reverse=True)
        return sorted_models[0]
