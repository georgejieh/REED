"""LLM provider abstraction shared by all concrete provider classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.providers.tools import Tool

ToolChoice = Literal["auto", "any", "none"]


@dataclass
class ProviderResult:
    """Uniform return type for all provider generate() calls."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    raw: Any = None


class LLMProvider(ABC):
    """Base class for every LLM provider integration."""

    name: str

    def __init__(self, *, model: str, api_key: str | None, base_url: str | None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    @abstractmethod
    def supports_tools(self) -> bool:
        """Whether the configured model supports native tool calling."""
        ...

    @abstractmethod
    def supports_json_mode(self) -> bool:
        """Whether the configured model supports json_object response mode."""
        ...

    @abstractmethod
    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice = "auto",
        json_mode: bool = False,
        max_turns: int = 1,
        model: str | None = None,
    ) -> ProviderResult:
        """Run a generation. Implementations may be multi-turn when tools are present.

        The `model` parameter overrides the provider's configured default
        for this single call (per-call override).
        """
        ...
