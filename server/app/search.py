import asyncio
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException

from .config import settings
from .safe_fetch import PinnedAsyncTransport, resolve_public_url

_search_cache: dict[tuple[str, str, int], tuple[float, list[dict]]] = {}

class SearchUnavailable(Exception):
    pass

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}: self.skip += 1
    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.skip: self.skip -= 1
    def handle_data(self, data):
        if not self.skip and data.strip(): self.parts.append(data.strip())

async def validate_public_url(url: str) -> None:
    await resolve_public_url(url, min(5, settings.web_search_timeout_seconds))

class SearXNGSearchEngine:
    async def search(self, query: str, language: str = "ru", limit: int = 8) -> list[dict]:
        if not settings.web_search_enabled:
            raise SearchUnavailable("disabled")
        cache_key = (query.strip().casefold(), language, limit)
        cached = _search_cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return [dict(item, cache_hit=True) for item in cached[1]]
        raw: list[dict] | None = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=settings.web_search_timeout_seconds) as client:
                    response = await client.get(
                        f"{settings.searxng_base_url.rstrip('/')}/search",
                        params={"q": query[:500], "format": "json", "language": language, "safesearch": settings.web_search_safesearch},
                    )
                response.raise_for_status()
                raw = response.json().get("results", [])
                break
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.25 * (attempt + 1))
        if raw is None:
            raise SearchUnavailable("searxng unavailable") from last_error
        results, seen = [], set()
        for index, item in enumerate(raw):
            url = str(item.get("url") or "").strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or url in seen:
                continue
            seen.add(url)
            results.append({
                "title": str(item.get("title") or parsed.hostname)[:300], "url": url,
                "snippet": str(item.get("content") or "")[:1000], "published_at": item.get("publishedDate"),
                "engine": ",".join(item.get("engines") or [str(item.get("engine") or "searxng")])[:120],
                "score": float(item.get("score") or max(0.0, 1 - index * 0.1)),
            })
            if len(results) >= min(limit, settings.web_search_max_results): break
        _search_cache[cache_key] = (time.monotonic() + settings.web_search_cache_ttl_seconds, results)
        if len(_search_cache) > 1024:
            now = time.monotonic()
            for key in [key for key, value in _search_cache.items() if value[0] <= now]: _search_cache.pop(key, None)
        return results

    async def fetch_page(self, url: str) -> str:
        current = url
        for _ in range(4):
            hostname, port, addresses = await resolve_public_url(current, min(5, settings.web_search_timeout_seconds))
            # No ambient HTTP proxy or second DNS lookup can bypass the pinned
            # destination. Keep normal certificate and original TLS-host checks.
            async with httpx.AsyncClient(timeout=settings.web_search_timeout_seconds, follow_redirects=False, trust_env=False,
                    transport=PinnedAsyncTransport(hostname, port, addresses)) as client:
                async with client.stream("GET", current, headers={"User-Agent": "LurgBot/0.2 (+https://lurg.pro)"}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location: raise HTTPException(502, "Некорректный redirect.")
                        current = urljoin(current, location); continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";", 1)[0]
                    if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                        raise HTTPException(415, "Неподдерживаемый тип страницы.")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 1_000_000: break
                        chunks.append(chunk)
                    text = b"".join(chunks).decode(response.encoding or "utf-8", errors="ignore")
                    if content_type == "text/plain": return text[:20000]
                    parser = _TextExtractor(); parser.feed(text)
                    return "\n".join(parser.parts)[:20000]
        raise HTTPException(502, "Слишком много redirects.")

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                return (await client.get(f"{settings.searxng_base_url.rstrip('/')}/healthz")).status_code < 500
        except httpx.HTTPError:
            return False

class DisabledSearchEngine:
    async def search(self, query: str, language: str, limit: int): raise SearchUnavailable("disabled")
    async def fetch_page(self, url: str): raise SearchUnavailable("disabled")
    async def health(self): return False

def get_search_engine():
    return SearXNGSearchEngine() if settings.search_provider == "searxng" else DisabledSearchEngine()
