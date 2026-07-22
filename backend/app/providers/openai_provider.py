"""OpenAI provider using the official openai SDK."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openai import OpenAI

from app.providers.base import LLMProvider, ProviderResult, ToolChoice
from app.providers.tools import Tool


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, *, model: str, api_key: str | None, base_url: str | None):
        super().__init__(model=model, api_key=api_key, base_url=base_url)
        self._client = OpenAI(api_key=api_key, base_url=base_url)

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
        kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = [_tool_to_openai(t) for t in tools]
            kwargs["tool_choice"] = tool_choice

        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        text = choice.message.content or ""
        tool_calls: list[dict[str, Any]] = []
        if choice.message.tool_calls:
            for call in choice.message.tool_calls:
                tool_calls.append(
                    {
                        "id": call.id,
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    }
                )
        usage = {
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
        }
        return ProviderResult(text=text, tool_calls=tool_calls, usage=usage, raw=response)


def _tool_to_openai(tool: Tool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_schema,
        },
    }
