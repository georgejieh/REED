from __future__ import annotations

import html
import re


def extract_article_text(body: bytes, *, max_characters: int = 10000) -> str:
    if max_characters < 1:
        raise ValueError("article character limit must be positive")
    text = body.decode("utf-8", errors="replace")
    text = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]*>", " ", text)
    return " ".join(html.unescape(text).split())[:max_characters]
