from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True, slots=True)
class ModelDefinition:
    slug: str
    display_name: str
    default_effort: str = "extended"
    supports_thinking: bool = True
    thinking_efforts: tuple[str, ...] = ("standard", "extended")
    reasoning_type: str = "reasoning"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "display_name": self.display_name,
            "default_effort": self.default_effort,
            "supports_thinking": self.supports_thinking,
            "thinking_efforts": list(self.thinking_efforts),
            "reasoning_type": self.reasoning_type,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelDefinition":
        return cls(
            slug=data["slug"],
            display_name=data.get("display_name", data["slug"]),
            default_effort=data.get("default_effort", "extended"),
            supports_thinking=data.get("supports_thinking", True),
            thinking_efforts=tuple(data.get("thinking_efforts") or ()),
            reasoning_type=data.get("reasoning_type", "reasoning"),
            description=data.get("description", ""),
        )
