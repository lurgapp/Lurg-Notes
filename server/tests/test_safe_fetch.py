"""Exercise the actual HTTPCore TCP/TLS boundary without reaching any network."""
import asyncio
import gzip
import ipaddress
import socket
import ssl

import httpcore
import httpx
import pytest
from fastapi import HTTPException

from app.safe_fetch import PinnedAsyncTransport, PinnedNetworkBackend, resolve_public_url
from app.search import SearXNGSearchEngine


class RecordingStream(httpcore.AsyncMockStream):
    def __init__(self, response):
        super().__init__([response])
        self.writes, self.tls, self.closed = [], [], False

    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)
        await super().write(buffer, timeout=timeout)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED and ssl_context.check_hostname
        self.tls.append(server_hostname)
        return self

    async def aclose(self):
        self.closed = True
        await super().aclose()


class RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, response):
        self.stream, self.calls = RecordingStream(response), []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        # The network backend MUST only receive the validated numeric destination.
        assert ipaddress.ip_address(host).is_global
        self.calls.append((host, port, timeout))
        return self.stream


def dns_for(*addresses):
    return [(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443)) for ip in addresses]


@pytest.mark.parametrize('url', ['http://127.0.0.1/a','http://169.254.169.254/','http://10.1.2.3/','http://[::1]/','http://[fe80::1]/','http://224.0.0.1/','http://0.0.0.0/', 'http://user:password@example.com/', 'file:///etc/passwd', 'https://example.com:bad/', 'https://example.com:0/', 'https://example.com:65536/'])
def test_nonpublic_and_malformed_destinations_fail_before_connect(url):
    with pytest.raises(HTTPException) as error:
        asyncio.run(resolve_public_url(url))
    assert error.value.status_code == 400


def test_mixed_dns_answers_fail_closed(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: dns_for('1.1.1.1','127.0.0.1'))
    with pytest.raises(HTTPException):
        asyncio.run(resolve_public_url('https://mixed.example/'))


def test_rebinding_cannot_change_tcp_and_tls_host_is_preserved(monkeypatch):
    queries = []
    def changing_dns(*args, **kwargs):
        queries.append(args[0])
        return dns_for('1.1.1.1' if len(queries)==1 else '127.0.0.1')
    monkeypatch.setattr(socket, 'getaddrinfo', changing_dns)
    backend = RecordingBackend(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\nhello')
    async def run():
        hostname, port, addresses = await resolve_public_url('https://public.example/a?q=1')
        async with httpx.AsyncClient(transport=PinnedAsyncTransport(hostname, port, addresses, backend), trust_env=False) as client:
            response = await client.get('https://public.example/a?q=1')
            assert response.text == 'hello'
    asyncio.run(run())
    assert queries == ['public.example']
    assert backend.calls[0][:2] == ('1.1.1.1',443)
    assert backend.stream.tls == ['public.example']
    assert b'Host: public.example\r\n' in b''.join(backend.stream.writes)
    assert b'GET /a?q=1 HTTP/1.1' in b''.join(backend.stream.writes)
    assert backend.stream.closed


def test_changed_origin_and_unix_socket_cannot_reuse_pin():
    backend = RecordingBackend(b'')
    pin = PinnedNetworkBackend('public.example',443,('1.1.1.1',),backend)
    async def run():
        for host, port in [('other.example',443),('public.example',80)]:
            with pytest.raises(httpcore.ConnectError):
                await pin.connect_tcp(host,port)
        with pytest.raises(httpcore.ConnectError):
            await pin.connect_unix_socket('/var/run/docker.sock')
    asyncio.run(run())
    assert not backend.calls


def test_private_redirect_is_validated_and_never_connected(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:dns_for('1.1.1.1'))
    backend = RecordingBackend(b'HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/admin\r\nContent-Length: 0\r\n\r\n')
    monkeypatch.setattr(httpcore,'AnyIOBackend',lambda:backend)
    with pytest.raises(HTTPException) as error:
        asyncio.run(SearXNGSearchEngine().fetch_page('https://public.example/'))
    assert error.value.status_code == 400
    assert len(backend.calls)==1 and backend.stream.closed


def test_real_adapter_decodes_compressed_html_and_ignores_ambient_proxy(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:3128')
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:dns_for('1.1.1.1'))
    compressed=gzip.compress(b'<h1>Public title</h1><script>ignored</script><p>Text</p>')
    response=b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Encoding: gzip\r\nContent-Length: '+str(len(compressed)).encode()+b'\r\n\r\n'+compressed
    backend=RecordingBackend(response)
    monkeypatch.setattr(httpcore,'AnyIOBackend',lambda:backend)
    assert asyncio.run(SearXNGSearchEngine().fetch_page('https://public.example/'))=='Public title\nText'
    assert backend.calls[0][:2]==('1.1.1.1',443)
