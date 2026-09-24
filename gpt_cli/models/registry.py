from typing import ClassVar
from .schemas import ModelDefinition

class ModelRegistry:
    """Decoupled model registry: maps aliases to canonical model definitions."""
    
    DEFAULT_SLUG: ClassVar[str] = "gpt-5-6-thinking"

    _MODELS: ClassVar[dict[str, ModelDefinition]] = {
        "gpt-5-6-thinking": ModelDefinition(
            slug="gpt-5-6-thinking",
            display_name="GPT-5.6 Sol Thinking",
            default_effort="extended",
            supports_thinking=True
        ),
        "gpt-5-thinking": ModelDefinition(
            slug="gpt-5-thinking",
            display_name="GPT-5 Thinking",
            default_effort="extended",
            supports_thinking=True
        ),
    }

    _ALIASES: ClassVar[dict[str, str]] = {
        "5.6": "gpt-5-6-thinking",
        "5.6-thinking": "gpt-5-6-thinking",
        "sol": "gpt-5-6-thinking",
        "thinking": "gpt-5-6-thinking",
        "default": "gpt-5-6-thinking",
        "5": "gpt-5-thinking",
        "gpt-5": "gpt-5-thinking",
    }

    @classmethod
    def resolve(cls, name_or_alias: str | None = None) -> ModelDefinition:
        if not name_or_alias:
            return cls._MODELS[cls.DEFAULT_SLUG]
        normalized = name_or_alias.strip().lower()
        slug = cls._ALIASES.get(normalized, normalized)
        if slug in cls._MODELS:
            return cls._MODELS[slug]
        # Fallback to default flagship if unknown
        return cls._MODELS[cls.DEFAULT_SLUG]

    @classmethod
    def register_model(cls, definition: ModelDefinition, aliases: list[str] | None = None) -> None:
        cls._MODELS[definition.slug] = definition
        if aliases:
            for a in aliases:
                cls._ALIASES[a.strip().lower()] = definition.slug
