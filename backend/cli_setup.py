#!/usr/bin/env python3
"""REED operator setup wizard.

Detects provider keys in .env, lets the operator pick a provider and
model, then writes backend/settings.yaml deterministically. Safe to
re-run any time.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from app.config import (
    EnvSettings,
    MarketDataConfig,
    ProviderName,
    SchedulerConfig,
    SearchConfig,
    SearchProviderName,
    SessionsConfig,
    SettingsYaml,
    TriggerConfig,
)

logger = logging.getLogger(__name__)

PROVIDER_MODELS: dict[ProviderName, list[str]] = {
    ProviderName.OPENAI: ["gpt-4o-mini", "gpt-4o", "o4-mini"],
    ProviderName.ANTHROPIC: [
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
        "claude-opus-4-0",
    ],
    ProviderName.OPENROUTER: [
        "google/gemini-2.5-flash-lite",
        "anthropic/claude-3.5-sonnet",
        "openai/gpt-4o-mini",
    ],
    ProviderName.OLLAMA: ["llama3.2", "qwen2.5", "mistral"],
    ProviderName.OPENAI_COMPATIBLE: [],
}

PROVIDER_KEY_NAMES: dict[ProviderName, str] = {
    ProviderName.OPENAI: "openai_api_key",
    ProviderName.ANTHROPIC: "anthropic_api_key",
    ProviderName.OPENROUTER: "openrouter_api_key",
    ProviderName.OLLAMA: "ollama_api_key",
    ProviderName.OPENAI_COMPATIBLE: "openai_api_key",
}

PROVIDER_LABELS: dict[ProviderName, str] = {
    ProviderName.OPENAI: "OpenAI",
    ProviderName.ANTHROPIC: "Anthropic",
    ProviderName.OPENROUTER: "OpenRouter",
    ProviderName.OLLAMA: "Ollama",
    ProviderName.OPENAI_COMPATIBLE: "OpenAI-compatible (custom base URL)",
}


def detect_available_providers(env: EnvSettings) -> list[ProviderName]:
    """Return the providers whose API keys are present in the environment."""
    available = []
    if env.openai_api_key:
        available.append(ProviderName.OPENAI)
    if env.anthropic_api_key:
        available.append(ProviderName.ANTHROPIC)
    if env.openrouter_api_key:
        available.append(ProviderName.OPENROUTER)
    if env.ollama_host or env.ollama_api_key:
        available.append(ProviderName.OLLAMA)
    return available


def prompt_provider(available: list[ProviderName]) -> ProviderName:
    print("REED setup wizard")
    print("Detected provider keys in .env:")
    if not available:
        print("  (none detected)")
        print()
        print("OpenAI-compatible is always available; configure the base_url in step 2.")
        return ProviderName.OPENAI_COMPATIBLE

    for i, name in enumerate(available, start=1):
        print(f"  {i}. {PROVIDER_LABELS[name]}")
    print(f"  {len(available) + 1}. OpenAI-compatible (custom base URL)")

    while True:
        choice = input(f"Pick a provider [1-{len(available) + 1}]: ").strip()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(available) + 1:
                if idx == len(available) + 1:
                    return ProviderName.OPENAI_COMPATIBLE
                return available[idx - 1]
        print("Invalid choice. Try again.")


def prompt_model(provider: ProviderName) -> str:
    models = PROVIDER_MODELS.get(provider, [])
    if not models:
        return input("Enter the model name (e.g. my-model-3.5): ").strip()
    print(f"Available models for {PROVIDER_LABELS[provider]}:")
    for i, model in enumerate(models, start=1):
        print(f"  {i}. {model}")
    print(f"  {len(models) + 1}. Custom model name")

    while True:
        choice = input(f"Pick a model [1-{len(models) + 1}]: ").strip()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(models) + 1:
                if idx == len(models) + 1:
                    return input("Enter the model name: ").strip()
                return models[idx - 1]
        print("Invalid choice. Try again.")


def prompt_base_url(provider: ProviderName) -> str | None:
    if provider != ProviderName.OPENAI_COMPATIBLE:
        return None
    return input("Enter the OpenAI-compatible base URL: ").strip() or None


def write_settings(
    path: Path,
    *,
    provider: ProviderName,
    model: str,
    base_url: str | None,
) -> None:
    settings = SettingsYaml(
        provider=provider,
        model=model,
        base_url=base_url,
        sessions=SessionsConfig(),
        search=SearchConfig(provider=SearchProviderName.DDGS, rate_limit_per_minute=12),
        market_data=MarketDataConfig(),
        data_dir=Path("../data/digests"),
        scheduler=SchedulerConfig(),
        trigger=TriggerConfig(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            settings.model_dump(mode="json", exclude_none=True),
            f,
            sort_keys=False,
            default_flow_style=False,
        )
    print(f"Wrote {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="REED setup wizard")
    parser.add_argument(
        "--env",
        default=".env",
        help="Path to .env file (default: .env)",
    )
    parser.add_argument(
        "--out",
        default="../settings.yaml",
        help="Path to write settings.yaml (default: ../settings.yaml, repo root)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip prompts and fail if no provider keys are detected",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    env = EnvSettings(_env_file=args.env)
    available = detect_available_providers(env)

    if not available and args.non_interactive:
        print("No provider keys detected and --non-interactive set; exiting.", file=sys.stderr)
        return 1

    if args.non_interactive:
        provider = available[0]
        model = PROVIDER_MODELS[provider][0]
        base_url = None
        print(f"Non-interactive mode: provider={provider.value}, model={model}")
    else:
        provider = prompt_provider(available)
        model = prompt_model(provider)
        base_url = prompt_base_url(provider)

    write_settings(Path(args.out), provider=provider, model=model, base_url=base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
