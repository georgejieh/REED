from __future__ import annotations

from typing import Protocol


class Provider(Protocol):
    def generate(self, prompt: str) -> str: ...
