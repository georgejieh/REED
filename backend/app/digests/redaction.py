"""Focused redaction for persisted diagnostic warnings.

Warnings are stored inside ``Generation.warning`` and are intentionally
excluded from the public API (see ``app.api.digests.GenerationPublic``).
Before persistence they are stripped of ANSI terminal escape codes and
sensitive values (bearer tokens, URL query strings) and bounded to a
maximum length so they cannot bloat the store or leak credentials.
"""

from __future__ import annotations

import re

# ANSI CSI/OSC escape sequences.
_ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\-_]|\[[0-?]*[ -/]*[@-~])",
    flags=re.ASCII,
)

# Bearer-token headers.  Matches "Authorization: Bearer <token>" and
# similar forms, replacing the token value with a placeholder.
_BEARER_RE = re.compile(
    r"(Authorization:\s*Bearer\s+)\S+",
    flags=re.IGNORECASE,
)

# HTTP(S) URLs that carry a query string.  The query string is stripped
# entirely because it commonly contains API keys or session tokens.
_URL_QUERY_RE = re.compile(
    r"(https?://[^\s?]+)\?[^\s]*",
    flags=re.IGNORECASE,
)

_DEFAULT_MAX_LEN = 500


def redact_warning(text: str | None, max_length: int = _DEFAULT_MAX_LEN) -> str | None:
    """Return a bounded, sanitized copy of a diagnostic warning.

    * ANSI escape codes are removed.
    * ``Authorization: Bearer <token>`` is replaced with
      ``Authorization: Bearer [redacted]``.
    * Query strings are stripped from ``http://`` / ``https://`` URLs.
    * The result is truncated to ``max_length`` characters.

    ``None`` input returns ``None`` so callers can keep the field absent
    when there is no warning.
    """
    if text is None:
        return None
    text = _ANSI_RE.sub("", text)
    text = _BEARER_RE.sub(r"\1[redacted]", text)
    text = _URL_QUERY_RE.sub(r"\1", text)
    # Collapse multiple spaces left behind by redaction.
    text = " ".join(text.split())
    return text[:max_length]
