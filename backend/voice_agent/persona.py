"""Voice persona configuration loader."""
from dataclasses import dataclass


@dataclass(frozen=True)
class VoicePersona:
    name: str
    language: str
    tone: str  # professional, friendly, formal
    greeting_template: str


def load_persona(project_config: dict) -> VoicePersona:
    """Load persona from project configuration."""
    persona = project_config.get("voice_persona", {})
    return VoicePersona(
        name=persona.get("name", "Assistant"),
        language=persona.get("language", "en"),
        tone=persona.get("tone", "professional"),
        greeting_template=persona.get("greeting", "Hello {name}, this is {persona_name} calling from {project}."),
    )
