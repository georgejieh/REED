from __future__ import annotations

import ipaddress
import http.client
import socket
import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit


class UnsafeOutboundUrl(ValueError):
    pass


class DnsResolver(Protocol):
    def resolve(self, hostname: str, port: int) -> Sequence[str]: ...


class SystemDnsResolver:
    def resolve(self, hostname: str, port: int) -> Sequence[str]:
        addresses: list[str] = []
        for result in socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        ):
            address = result[4][0]
            if address not in addresses:
                addresses.append(address)
        return addresses


@dataclass(frozen=True)
class ValidatedUrl:
    url: str
    scheme: str
    hostname: str
    port: int


@dataclass(frozen=True)
class OutboundResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    final_url: str


@dataclass(frozen=True)
class PinnedTarget:
    url: str
    connect_host: str
    port: int
    tls_server_name: str | None
    host_header: str
    trust_env: bool = False
    redirect_count: int = 0
    allow_loopback: bool = False


class OutboundTransport(Protocol):
    def prepare(self, url: str) -> PinnedTarget: ...

    def prepare_redirect(
        self,
        previous: PinnedTarget,
        location: str,
    ) -> PinnedTarget: ...

    def prepare_retry(self, previous: PinnedTarget) -> PinnedTarget: ...

    def request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> OutboundResponse: ...


class OutboundUrlPolicy:
    _blocked_hostnames = {
        "metadata.google.internal",
        "metadata",
    }

    def __init__(self, allow_loopback: bool = False):
        self.allow_loopback = allow_loopback

    def parse(self, url: str) -> ValidatedUrl:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as error:
            raise UnsafeOutboundUrl("URL is not parseable") from error
        if parsed.scheme.lower() not in {"http", "https"}:
            raise UnsafeOutboundUrl("URL scheme is not allowed")
        if not parsed.hostname:
            raise UnsafeOutboundUrl("URL hostname is required")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeOutboundUrl("credential-bearing URLs are not allowed")
        if parsed.fragment:
            raise UnsafeOutboundUrl("URL fragments are not allowed")

        hostname = parsed.hostname.rstrip(".").lower()
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise UnsafeOutboundUrl("URL hostname is invalid") from error
        if hostname in self._blocked_hostnames:
            raise UnsafeOutboundUrl("metadata hostnames are not allowed")

        effective_port = port or (443 if parsed.scheme.lower() == "https" else 80)
        self._validate_literal_address(hostname)
        normalized = SplitResult(
            scheme=parsed.scheme.lower(),
            netloc=self._netloc(hostname, effective_port, parsed.scheme.lower()),
            path=parsed.path or "/",
            query=parsed.query,
            fragment="",
        )
        return ValidatedUrl(
            url=urlunsplit(normalized),
            scheme=normalized.scheme,
            hostname=hostname,
            port=effective_port,
        )

    @staticmethod
    def _netloc(hostname: str, port: int, scheme: str) -> str:
        formatted = f"[{hostname}]" if ":" in hostname else hostname
        default_port = 443 if scheme == "https" else 80
        return formatted if port == default_port else f"{formatted}:{port}"

    def validate_address(self, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise UnsafeOutboundUrl("DNS returned an invalid address") from error
        if not address.is_global and not (
            self.allow_loopback and address.is_loopback
        ):
            raise UnsafeOutboundUrl("destination address is not public")
        return address.compressed

    def _validate_literal_address(self, hostname: str) -> None:
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            return
        self.validate_address(hostname)


class SafeOutboundTransport:
    def __init__(
        self,
        resolver: DnsResolver | None = None,
        policy: OutboundUrlPolicy | None = None,
        max_redirects: int = 5,
    ):
        if max_redirects < 0:
            raise ValueError("maximum redirects must not be negative")
        self._resolver = resolver or SystemDnsResolver()
        self._policy = policy or OutboundUrlPolicy()
        self._max_redirects = max_redirects

    def prepare(
        self,
        url: str,
        *,
        allow_loopback: bool = False,
    ) -> PinnedTarget:
        return self._prepare(
            url,
            redirect_count=0,
            allow_loopback=allow_loopback,
        )

    def _prepare(
        self,
        url: str,
        redirect_count: int,
        allow_loopback: bool,
    ) -> PinnedTarget:
        policy = (
            OutboundUrlPolicy(allow_loopback=True)
            if allow_loopback
            else self._policy
        )
        validated = policy.parse(url)
        try:
            literal = ipaddress.ip_address(validated.hostname)
        except ValueError:
            addresses = self._resolver.resolve(
                validated.hostname,
                validated.port,
            )
        else:
            addresses = [literal.compressed]
        if not addresses:
            raise UnsafeOutboundUrl("hostname did not resolve")

        approved = [
            policy.validate_address(address) for address in addresses
        ]
        host_header = OutboundUrlPolicy._netloc(
            validated.hostname,
            validated.port,
            validated.scheme,
        )
        return PinnedTarget(
            url=validated.url,
            connect_host=approved[0],
            port=validated.port,
            tls_server_name=(
                validated.hostname if validated.scheme == "https" else None
            ),
            host_header=host_header,
            redirect_count=redirect_count,
            allow_loopback=allow_loopback,
        )

    def prepare_redirect(
        self,
        previous: PinnedTarget,
        location: str,
    ) -> PinnedTarget:
        if previous.redirect_count >= self._max_redirects:
            raise UnsafeOutboundUrl("redirect limit exceeded")
        return self._prepare(
            urljoin(previous.url, location),
            redirect_count=previous.redirect_count + 1,
            allow_loopback=previous.allow_loopback,
        )

    def prepare_retry(self, previous: PinnedTarget) -> PinnedTarget:
        return self._prepare(
            previous.url,
            redirect_count=previous.redirect_count,
            allow_loopback=previous.allow_loopback,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 8,
        max_bytes: int = 2 * 1024 * 1024,
        follow_redirects: bool = True,
        allow_loopback: bool = False,
    ) -> OutboundResponse:
        if max_bytes < 1:
            raise ValueError("maximum response bytes must be positive")
        target = self.prepare(url, allow_loopback=allow_loopback)
        request_method = method.upper()
        request_body = body
        for _ in range(self._max_redirects + 1):
            response = self._request_once(
                target,
                request_method,
                headers or {},
                request_body,
                timeout,
                max_bytes,
            )
            if (
                not follow_redirects
                or response.status_code not in {301, 302, 303, 307, 308}
            ):
                return response
            location = response.headers.get("location")
            if not location:
                return response
            target = self.prepare_redirect(target, location)
            if response.status_code == 303:
                request_method = "GET"
                request_body = None
        raise UnsafeOutboundUrl("redirect limit exceeded")

    @staticmethod
    def _request_once(
        target: PinnedTarget,
        method: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
        max_bytes: int,
    ) -> OutboundResponse:
        connection: http.client.HTTPConnection
        if target.tls_server_name is not None:
            connection = _PinnedHttpsConnection(
                target.connect_host,
                target.port,
                target.tls_server_name,
                timeout,
            )
        else:
            connection = http.client.HTTPConnection(
                target.connect_host,
                target.port,
                timeout=timeout,
            )
        request_headers = {key: value for key, value in headers.items()}
        request_headers["host"] = target.host_header
        try:
            connection.request(
                method,
                _request_target(target.url),
                body=body,
                headers=request_headers,
            )
            response = connection.getresponse()
            content_length = response.getheader("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > max_bytes:
                        raise ValueError("outbound response exceeded byte limit")
                except ValueError as error:
                    if "exceeded" in str(error):
                        raise
            response_body = response.read(max_bytes + 1)
            if len(response_body) > max_bytes:
                raise ValueError("outbound response exceeded byte limit")
            return OutboundResponse(
                status_code=response.status,
                headers={
                    key.lower(): value for key, value in response.getheaders()
                },
                body=response_body,
                final_url=target.url,
            )
        finally:
            connection.close()


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        connect_host: str,
        port: int,
        server_hostname: str,
        timeout: float,
    ):
        super().__init__(
            server_hostname,
            port,
            timeout=timeout,
            context=create_pinned_tls_context(),
        )
        self._connect_host = connect_host
        self._server_hostname = server_hostname

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._connect_host, self.port),
            self.timeout,
        )
        self.sock = self._context.wrap_socket(
            raw_socket,
            server_hostname=self._server_hostname,
        )


def create_pinned_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _request_target(url: str) -> str:
    parsed = urlsplit(url)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return target
