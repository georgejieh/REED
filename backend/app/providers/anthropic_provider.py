"""Anthropic provider using the official anthropic SDK."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import anthropic

from app.providers.base import LLMProvider, ProviderResult, ToolChoice
from app.providers.tools import Tool


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, *, model: str, api_key: str | None, base_url: str | None):
        super().__init__(model=model, api_key=api_key, base_url=base_url)
        self._client = anthropic.Anthropic(api_key=api_key, base_url=base_url)

    def supports_tools(self) -> bool:
        return True

    def supports_json_mode(self) -> bool:
        return True

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
        target_model = model or self.model
        effective_system = system_prompt
        if json_mode:
            effective_system = (
                f"{system_prompt}\n\n"
                "Respond with a single JSON object and nothing else. "
                "No prose, no markdown fences."
            )
        kwargs: dict[str, Any] = {
            "model": target_model,
            "system": effective_system,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": 4096,
        }
        if tools:
            kwargs["tools"] = [_tool_to_anthropic(t) for t in tools]
            kwargs["tool_choice"] = {"type": tool_choice}

        response = self._client.messages.create(**kwargs)
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    {"id": block.id, "name": block.name, "arguments": block.input}
                )
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        return ProviderResult(
            text="".join(text_parts), tool_calls=tool_calls, usage=usage, raw=response
        )


def _tool_to_anthropic(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters_schema,
    }
