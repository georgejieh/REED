"""Tool dataclass and the built-in scrape_url tool used by REED agents.

The scraper uses httpx with bounded redirects and a hard timeout, then
extracts the main article text via trafilatura. SSRF protection
rejects loopback, link-local, and private-IP destinations. Failures
return ScrapeResult(ok=False, error=<message>) rather than raising.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
import trafilatura

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 4.0
MAX_TOTAL_SECONDS = 8.0
MAX_REDIRECTS = 3
MAX_SCRAPE_CONCURRENCY = 5


@dataclass
class Tool:
    """A function-callable tool exposed to the model."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    fn: Callable[..., Any]
    parallel_safe: bool = True


@dataclass
class ScrapeResult:
    """Outcome of a scrape attempt."""

    url: str
    text: str
    ok: bool
    error: str | None = None


class ScrapeSemaphore:
    """Bounded concurrency gate for in-flight scrapes."""

    def __init__(self, limit: int = MAX_SCRAPE_CONCURRENCY):
        self._limit = limit
        self._in_flight = 0

    @property
    def available(self) -> int:
        return self._limit - self._in_flight

    def acquire(self) -> bool:
        if self._in_flight >= self._limit:
            return False
        self._in_flight += 1
        return True

    def release(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)


_GLOBAL_SEMAPHORE = ScrapeSemaphore()


def get_scrape_semaphore() -> ScrapeSemaphore:
    return _GLOBAL_SEMAPHORE


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Return (is_safe, reason). Rejects non-http(s) and private IPs."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return False, f"url parse error: {exc}"
    if parsed.scheme not in ("http", "https"):
        return False, f"scheme {parsed.scheme!r} not allowed (http/https only)"
    if not parsed.hostname:
        return False, "missing hostname"
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        return False, f"dns resolution failed: {exc}"
    for info in infos:
        sockaddr = info[4]
        try:
            ip = ip_address(sockaddr[0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return False, f"destination {ip} is not publicly routable"
    return True, ""


def _resolve_final_ip(url: str) -> str:
    """Resolve and return the IP that will be connected to (for logging)."""
    parsed = urlparse(url)
    if not parsed.hostname:
        return ""
    try:
        return socket.gethostbyname(parsed.hostname)
    except socket.gaierror:
        return ""


def scrape_url(url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> ScrapeResult:
    """Fetch the article text at `url` and return a populated ScrapeResult.

    Network errors, 4xx/5xx, redirect loops, SSRF rejections, and
    extraction failures all return ok=False with a descriptive error
    rather than raising. The agent loop treats these as data.
    """
    if not url:
        return ScrapeResult(url=url, text="", ok=False, error="empty url")

    safe, reason = _is_safe_url(url)
    if not safe:
        return ScrapeResult(url=url, text="", ok=False, error=f"blocked: {reason}")

    if not _GLOBAL_SEMAPHORE.acquire():
        return ScrapeResult(
            url=url, text="", ok=False, error="scraper at capacity, try again"
        )

    try:
        try:
            with httpx.Client(
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                timeout=httpx.Timeout(timeout, connect=timeout, read=timeout, write=timeout),
                headers={
                    "User-Agent": "REED/0.1 (+https://github.com/georgejieh/REED)",
                    "Accept": "text/html,application/xhtml+xml",
                },
            ) as client:
                response = client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("scrape http error for %s: %s", url, exc)
            return ScrapeResult(url=url, text="", ok=False, error=f"http error: {exc}")

        if response.status_code >= 400:
            return ScrapeResult(
                url=url,
                text="",
                ok=False,
                error=f"http {response.status_code}",
            )

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower() and "xml" not in content_type.lower():
            return ScrapeResult(
                url=url,
                text="",
                ok=False,
                error=f"unsupported content-type: {content_type or 'unknown'}",
            )

        html = response.text
        if not html:
            return ScrapeResult(url=url, text="", ok=False, error="empty response body")

        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
            with_metadata=False,
        ) or ""
        text = text.strip()
        if not text:
            return ScrapeResult(
                url=url, text="", ok=False, error="extraction returned no text"
            )
        return ScrapeResult(url=url, text=text, ok=True)
    finally:
        _GLOBAL_SEMAPHORE.release()


SCRAPE_TOOL = Tool(
    name="scrape_url",
    description="Fetch the full text of a URL. Returns the article text or an error.",
    parameters_schema={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Absolute URL to fetch"}},
        "required": ["url"],
    },
    fn=scrape_url,
)
