"""Agent run loop.

The runner drives the model through up to `max_turns` tool-calling
turns, executing each Tool via its callable. After the model returns
without tool calls, the final text is returned and (optionally)
parsed as JSON. A pre-fetched market snapshot can be merged into
the user prompt as a plain-text block so the model sees real numbers
rather than its own memory.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.providers.base import LLMProvider, ProviderResult
from app.providers.tools import Tool

logger = logging.getLogger(__name__)


@dataclass
class AgentRunResult:
    """Outcome of one agent run."""

    final_text: str
    parsed_json: dict[str, Any] | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    duration_ms: int = 0
    fallback_used: bool = False
    warning: str | None = None


def _safe_call(tool: Tool, **kwargs: Any) -> dict[str, Any]:
    """Execute a Tool, returning a dict the model can read."""
    try:
        result = tool.fn(**kwargs)
    except Exception as exc:
        logger.warning("tool %s raised: %s", tool.name, exc)
        return {"ok": False, "error": str(exc)}
    if isinstance(result, dict):
        return result
    return {"ok": True, "result": result}


def _build_user_prompt(user_prompt: str, snapshot: dict[str, Any] | None) -> str:
    """Append the market snapshot to the user prompt as plain text."""
    if not snapshot:
        return user_prompt
    lines = [user_prompt, "", "Live market snapshot (use these, do not invent):"]
    for symbol, value in snapshot.items():
        if isinstance(value, dict):
            lines.append(
                f"  - {symbol}: {value.get('value', '?')} "
                f"(change_pct={value.get('change_pct', '?')}, "
                f"as_of={value.get('as_of', '?')})"
            )
        else:
            lines.append(f"  - {symbol}: {value}")
    return "\n".join(lines)


def _coerce_tool_calls(result: ProviderResult) -> list[dict[str, Any]]:
    """Extract tool calls from a ProviderResult in a uniform shape."""
    out: list[dict[str, Any]] = []
    for tc in result.tool_calls:
        if isinstance(tc, dict):
            out.append(tc)
            continue
        name = getattr(tc, "name", None) or getattr(tc, "tool_name", None) or ""
        args = getattr(tc, "arguments", None) or getattr(tc, "args", None) or {}
        out.append({"name": name, "arguments": args})
    return out


def run_agent(
    *,
    provider: LLMProvider,
    tools: Sequence[Tool],
    system_prompt: str,
    user_prompt: str,
    market_snapshot: dict[str, Any] | None = None,
    max_turns: int = 6,
    json_mode: bool = True,
) -> AgentRunResult:
    """Drive the model through up to `max_turns` tool-calling turns.

    Returns the final AgentRunResult. If the model fails to produce
    valid JSON after one retry, `parsed_json` is None and
    `warning` is set.
    """
    start = time.monotonic()
    user_with_snapshot = _build_user_prompt(user_prompt, market_snapshot)
    pending_turns = max_turns
    last_result: ProviderResult | None = None
    tool_history: list[dict[str, Any]] = []
    fallback_used = False
    warning: str | None = None

    while pending_turns > 0:
        pending_turns -= 1
        try:
            last_result = provider.generate(
                system_prompt=system_prompt,
                user_prompt=user_with_snapshot,
                tools=list(tools),
                tool_choice="auto",
                json_mode=json_mode,
                max_turns=1,
            )
        except Exception as exc:
            logger.warning("provider.generate failed: %s", exc)
            warning = f"provider error: {exc}"
            fallback_used = True
            break

        calls = _coerce_tool_calls(last_result)
        if not calls:
            break

        for call in calls:
            name = call.get("name", "")
            args = call.get("arguments", {}) or {}
            tool = next((t for t in tools if t.name == name), None)
            if tool is None:
                logger.warning("model called unknown tool %r", name)
                tool_history.append(
                    {"name": name, "arguments": args, "error": "unknown tool"}
                )
                continue
            observation = _safe_call(tool, **args)
            tool_history.append(
                {
                    "name": name,
                    "arguments": args,
                    "observation": observation,
                }
            )
            if tool.parallel_safe:
                user_with_snapshot += (
                    f"\n\nTool {name} returned:\n{json.dumps(observation)[:4000]}"
                )
        else:
            continue

    if last_result is None:
        warning = warning or "no provider result"
        fallback_used = True
        text = ""
    else:
        text = last_result.text

    parsed: dict[str, Any] | None = None
    if json_mode and text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            logger.info("model output not JSON, retrying with corrective prompt")
            try:
                retry = provider.generate(
                    system_prompt=(
                        "Your previous response was not valid JSON. "
                        "Reply with only the JSON object, no commentary."
                    ),
                    user_prompt=text,
                    tools=[],
                    json_mode=True,
                    max_turns=1,
                )
                text = retry.text
                parsed = json.loads(text)
            except (json.JSONDecodeError, Exception) as exc:
                logger.warning("retry still not JSON: %s", exc)
                warning = (warning or "") + " | could not parse JSON after retry"
                parsed = None

    duration_ms = int((time.monotonic() - start) * 1000)
    return AgentRunResult(
        final_text=text,
        parsed_json=parsed,
        tool_calls=tool_history,
        turns=max_turns - pending_turns,
        duration_ms=duration_ms,
        fallback_used=fallback_used,
        warning=warning,
    )
