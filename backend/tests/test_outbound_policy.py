from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.intake.policy import (
    OutboundUrlPolicy,
    SafeOutboundTransport,
    UnsafeOutboundUrl,
)


class RecordingResolver:
    def __init__(self, answers: dict[str, Sequence[str]]) -> None:
        self.answers = answers
        self.queries: list[tuple[str, int]] = []

    def resolve(self, hostname: str, port: int) -> Sequence[str]:
        self.queries.append((hostname, port))
        return self.answers[hostname]


@pytest.mark.parametrize(
    "url",
    [
        "ftp://feeds.example.com/market.xml",
        "https://user:password@feeds.example.com/market.xml",
        "https://127.0.0.1/market.xml",
        "https://10.2.3.4/market.xml",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/market.xml",
        "https://feeds.example.com/market.xml#fragment",
    ],
)
def test_url_policy_rejects_unsafe_destinations(url: str) -> None:
    with pytest.raises(UnsafeOutboundUrl):
        OutboundUrlPolicy().parse(url)


def test_transport_pins_dns_and_preserves_tls_hostname() -> None:
    resolver = RecordingResolver({"feeds.example.com": ["93.184.216.34"]})
    transport = SafeOutboundTransport(resolver=resolver)

    target = transport.prepare("https://feeds.example.com/market.xml")

    assert target.connect_host == "93.184.216.34"
    assert target.port == 443
    assert target.tls_server_name == "feeds.example.com"
    assert target.host_header == "feeds.example.com"
    assert target.trust_env is False
    assert resolver.queries == [("feeds.example.com", 443)]


def test_transport_rejects_hostname_resolving_to_private_address() -> None:
    resolver = RecordingResolver({"feeds.example.com": ["192.168.1.20"]})

    with pytest.raises(UnsafeOutboundUrl, match="address"):
        SafeOutboundTransport(resolver=resolver).prepare(
            "https://feeds.example.com/market.xml"
        )


def test_redirect_is_reparsed_reresolved_and_repinned() -> None:
    resolver = RecordingResolver(
        {
            "feeds.example.com": ["93.184.216.34"],
            "cdn.example.com": ["93.184.216.35"],
        }
    )
    transport = SafeOutboundTransport(resolver=resolver)
    first = transport.prepare("https://feeds.example.com/market.xml")

    redirected = transport.prepare_redirect(first, "https://cdn.example.com/feed.xml")

    assert redirected.connect_host == "93.184.216.35"
    assert redirected.tls_server_name == "cdn.example.com"
    assert resolver.queries == [
        ("feeds.example.com", 443),
        ("cdn.example.com", 443),
    ]


def test_redirect_to_metadata_address_is_rejected() -> None:
    resolver = RecordingResolver({"feeds.example.com": ["93.184.216.34"]})
    transport = SafeOutboundTransport(resolver=resolver)
    first = transport.prepare("https://feeds.example.com/market.xml")

    with pytest.raises(UnsafeOutboundUrl):
        transport.prepare_redirect(first, "http://169.254.169.254/latest")


def test_redirect_limit_is_bounded() -> None:
    resolver = RecordingResolver({"feeds.example.com": ["93.184.216.34"]})
    transport = SafeOutboundTransport(resolver=resolver, max_redirects=1)
    first = transport.prepare("https://feeds.example.com/market.xml")
    redirected = transport.prepare_redirect(first, "/second.xml")

    with pytest.raises(UnsafeOutboundUrl, match="redirect"):
        transport.prepare_redirect(redirected, "/third.xml")


def test_retry_resolves_and_pins_again() -> None:
    resolver = RecordingResolver({"feeds.example.com": ["93.184.216.34"]})
    transport = SafeOutboundTransport(resolver=resolver)
    first = transport.prepare("https://feeds.example.com/market.xml")

    retried = transport.prepare_retry(first)

    assert retried.connect_host == first.connect_host
    assert resolver.queries == [
        ("feeds.example.com", 443),
        ("feeds.example.com", 443),
    ]
