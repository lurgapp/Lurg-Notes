"""Public-page HTTP transport with DNS checked once and pinned for the socket.

Keep the URL/Host/TLS hostname intact. Only connect_tcp uses a numeric address;
the networking library can no longer resolve the hostname again after validation.
Each redirect uses a fresh pool and a new validated destination.
"""
import asyncio
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlparse

import httpcore
import httpx
from fastapi import HTTPException


def _public_address(address: str) -> bool:
    value = ipaddress.ip_address(address)
    return value.is_global and not (value.is_multicast or value.is_reserved or value.is_unspecified)


async def resolve_public_url(url: str, timeout: float = 5) -> tuple[str, int, tuple[str, ...]]:
    try:
        parsed = urlparse(url)
        normalized = httpx.URL(url)
        hostname = normalized.raw_host.decode("ascii")
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if parsed.scheme not in {"http", "https"} or not hostname or parsed.username is not None or parsed.password is not None or "%" in hostname or not 1 <= port <= 65535:
            raise ValueError
    except (ValueError, UnicodeError, httpx.InvalidURL) as exc:
        raise HTTPException(400, "Небезопасный URL.") from exc
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = (str(literal),)
    else:
        try:
            infos = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, hostname, port, type=socket.SOCK_STREAM), timeout)
        except (socket.gaierror, asyncio.TimeoutError) as exc:
            raise HTTPException(400, "Адрес страницы не найден.") from exc
        addresses = tuple(dict.fromkeys(str(ipaddress.ip_address(info[4][0])) for info in infos))
    # Check every answer before choosing addresses. A mixed public/private answer
    # must not make validation depend on resolver ordering or IPv4/IPv6 fallback.
    if not addresses or any(not _public_address(address) for address in addresses):
        raise HTTPException(400, "Доступ к локальным адресам запрещен.")
    return hostname, port, addresses[:16]


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, hostname: str, port: int, addresses: tuple[str, ...], backend=None):
        if not addresses or any(not _public_address(address) for address in addresses):
            raise ValueError("Only validated global addresses may be pinned")
        self.hostname, self.port, self.addresses = hostname, port, addresses
        self.backend = backend if backend is not None else httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host != self.hostname or port != self.port or local_address is not None:
            raise httpcore.ConnectError("Destination differs from validated origin")
        deadline = time.monotonic() + timeout if timeout is not None else None
        last_error = None
        for address in self.addresses:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise httpcore.ConnectTimeout("Pinned connection deadline exceeded")
            try:
                return await self.backend.connect_tcp(address, port, timeout=remaining, local_address=None, socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        raise last_error

    async def connect_unix_socket(self, *args, **kwargs):
        raise httpcore.ConnectError("Unix sockets are unavailable for public fetch")

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)


_NETWORK_ERRORS = (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError, httpcore.ProxyError, httpcore.UnsupportedProtocol)


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream, request):
        self.stream, self.request = stream, request

    async def __aiter__(self):
        try:
            async for part in self.stream:
                yield part
        except _NETWORK_ERRORS as exc:
            raise httpx.TransportError("Ошибка безопасного HTTP-соединения.", request=self.request) from exc

    async def aclose(self):
        await self.stream.aclose()


class PinnedAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, hostname: str, port: int, addresses: tuple[str, ...], backend=None):
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=PinnedNetworkBackend(hostname, port, addresses, backend),
            max_connections=1, max_keepalive_connections=0, retries=0,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host, port=request.url.port, target=request.url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions,
        )
        try:
            response = await self.pool.handle_async_request(core_request)
        except _NETWORK_ERRORS as exc:
            raise httpx.TransportError("Ошибка безопасного HTTP-соединения.", request=request) from exc
        return httpx.Response(response.status, headers=response.headers, stream=_ResponseStream(response.stream, request), extensions=response.extensions)

    async def aclose(self):
        await self.pool.aclose()
