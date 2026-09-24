from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class ModelDefinition:
    slug: str
    display_name: str
    default_effort: str = "extended"
    supports_thinking: bool = True
