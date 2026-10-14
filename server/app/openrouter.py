import json
from hashlib import sha256
import logging
from contextvars import ContextVar
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import HTTPException

from .config import settings
from .gemini import embedding
from .prompts import LURG_SYSTEM_PROMPT, current_time_context
from .provider_stream import public_provider_error, sse_data, upstream_error_status

logger = logging.getLogger("lurg.ai.openrouter")
_usage: ContextVar[dict[str, Any]] = ContextVar("openrouter_usage", default={})
_timings: ContextVar[dict[str, int]] = ContextVar("openrouter_timings", default={})
_embedding_cache: dict[tuple[str, str, str], list[float]] = {}
_client: httpx.AsyncClient | None = None

def ms(start: float) -> int:
    import time
    return round((time.monotonic() - start) * 1000)

def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=10),
            limits=httpx.Limits(max_keepalive_connections=16, max_connections=32, keepalive_expiry=120),
        )
    return _client

def parse_sse_line(line: str) -> tuple[str | None, dict[str, Any] | None]:
    if not line.startswith("data:"):
        return None, None
    raw = line[5:].strip()
    if raw == "[DONE]":
        return "done", None
    if not raw:
        return None, None
    try:
        return "data", json.loads(raw)
    except json.JSONDecodeError:
        return None, None

class OpenRouterAIEngine:
    provider_name = "openrouter"
    embedding_model = property(lambda self: settings.gemini_embedding_model)
    text_model = property(lambda self: settings.openrouter_model)

    def usage(self) -> dict[str, Any]:
        return _usage.get()

    def timings(self) -> dict[str, int]:
        return _timings.get()

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": settings.openrouter_http_referer,
            "X-Title": settings.openrouter_app_title,
        }

    async def _json(self, instruction: str, content: str, *, max_tokens: int = 1000, temperature: float = 0.2) -> dict[str, Any]:
        request_started = __import__("time").monotonic()
        payload = {
            "model": self.text_model,
            "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": content[:settings.ai_max_input_chars]}],
            "response_format": {"type": "json_object"}, "temperature": temperature, "max_tokens": max_tokens,
            "reasoning": {"enabled": False, "exclude": True},
        }
        _usage.set({})
        _timings.set({})
        try:
            response = await client().post(f"{settings.openrouter_base_url.rstrip('/')}/chat/completions", headers=self.headers, json=payload)
        except httpx.RequestError as exc:
            raise public_provider_error(502) from exc
        elapsed = ms(request_started)
        _timings.set({"openrouter_connection_ms": elapsed, "connection_ms": elapsed, "total_ms": elapsed})
        if response.is_error:
            logger.warning("OpenRouter JSON request failed status=%s", response.status_code)
            raise public_provider_error(response.status_code)
        try:
            raw = response.json()
            if not isinstance(raw, dict) or raw.get("error"):
                raise public_provider_error(upstream_error_status(raw.get("error") if isinstance(raw, dict) else None))
            raw_usage = raw.get("usage") or {}
            if raw_usage:
                _usage.set({
                    "input_tokens": raw_usage.get("prompt_tokens"),
                    "output_tokens": raw_usage.get("completion_tokens"),
                    "reasoning_tokens": (raw_usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
                    "cost": raw_usage.get("cost"),
                    "cost_estimated": raw_usage.get("cost") is None,
                })
            data = json.loads(raw["choices"][0]["message"]["content"])
            if not isinstance(data, dict):
                raise TypeError("structured JSON response must be an object")
            return data
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(502, "ИИ вернул некорректный структурированный ответ.") from exc

    async def structured_json(self, instruction: str, content: str, *, max_tokens: int = 1000, temperature: float = 0.2) -> dict[str, Any]:
        return await self._json(instruction, content, max_tokens=max_tokens, temperature=temperature)

    async def extract_graph(self, content: str) -> dict[str, Any]:
        return await self._json(
            "Understand the meaning of the latest user message before deciding whether any memory should be proposed. "
            "Return strict JSON: {memory_disposition:'ignore|conversation_only|temporary|graph_candidate',operation:'create|update|deactivate',nodes:[{key,type,label,properties,confidence}],edges:[{source,target,relation,confidence}]}. "
            "Use graph_candidate only for explicit, durable knowledge about the user: skills, preferences, projects, goals, occupation, relationships or stable places. "
            "Use temporary for dated plans, current feelings, traffic, weather, news, deliveries, one-off events and any expiring context; temporary must return empty graph nodes and edges. "
            "Use conversation_only for ordinary conversation and ignore for commands/questions unrelated to a personal fact. "
            "Do not choose a disposition from individual trigger words: infer it from the whole statement and its meaning. "
            "Allowed node types: profile, age, occupation, education, interest, skill, goal, project, organization, place, fact. "
            "Use update for replacements like 'now', 'already', moved, changed age; deactivate for negations like 'no longer'. "
            "For every disposition other than graph_candidate, return empty nodes and edges. "
            "Include user person node with key='user' only when a personal fact exists. Do not infer. Do not claim anything was saved.",
            content,
        )

    async def classify_personal_query(self, content: str) -> dict[str, Any]:
        return await self._json(
            "Classify the latest user message. Return only strict JSON object. "
            "Allowed kind: personal_fact_query, graph_overview, graph_search, general, web, unknown. "
            "Allowed category: name, age, residence, origin, occupation, education, skills, interests, goals, projects, organizations, services, timeline, unknown. "
            "Return schema: {kind:string,category:string,relation:null|string,requested_scope:'single|many|overview|unknown',confidence:number}. "
            "Do not answer the user. Do not include or invent personal values. Do not infer facts.",
            content,
            max_tokens=120,
            temperature=0,
        )

    async def extract_repository_intelligence(self, content: str) -> dict[str, Any]:
        return await self._json("Analyze the bounded public repository summary. Return JSON with project_summary, technologies, frameworks, dependencies, skills, topics, confidence. Never expose secrets.", content[:200000])

    async def embed(self, content: str, purpose: str) -> list[float]:
        if not settings.gemini_api_key:
            raise HTTPException(503, "Embedding provider не настроен.")
        cache_key = (settings.gemini_embedding_model, purpose, sha256(content.encode()).hexdigest())
        cached = _embedding_cache.get(cache_key)
        if cached is not None:
            return cached
        vector = await embedding(content, purpose)
        _embedding_cache[cache_key] = vector
        if len(_embedding_cache) > 4096:
            for key in list(_embedding_cache)[:512]:
                _embedding_cache.pop(key, None)
        return vector

    async def answer(self, question: str, context: list[dict[str, Any]]) -> str:
        chunks = []
        async for chunk in self.stream_answer(question, {"graph": context}, mode="graph" if context else "general"):
            chunks.append(chunk)
        return "".join(chunks)

    async def stream_answer(self, question: str, context: dict[str, Any], history=None, *, mode="general", complex_query=False) -> AsyncIterator[str]:
        if not settings.openrouter_api_key:
            raise HTTPException(503, "AI provider не настроен.")
        messages = [{"role": "system", "content": f"{LURG_SYSTEM_PROMPT}\n\n{current_time_context()}"}]
        messages.extend((history or [])[-10:])
        messages.append({"role": "user", "content": f"<mode>{mode}</mode>\n<context>{json.dumps(context, ensure_ascii=False)}</context>\n<question>{question}</question>"})
        max_tokens = 400 if len(question) < 240 and not complex_query else min(settings.ai_max_output_tokens, 1000)
        payload = {
            "model": self.text_model, "messages": messages, "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": 0.25, "max_tokens": max_tokens,
            "reasoning": {"enabled": False, "exclude": True},
        }
        # V4 Flash currently supports high/xhigh only. Keep reasoning off by default.
        if complex_query:
            payload["reasoning"] = {"effort": "high", "exclude": True}
        _usage.set({})
        _timings.set({})
        request_started = __import__("time").monotonic()
        first_token_seen = False
        done = False
        has_content = False
        try:
            async with client().stream("POST", f"{settings.openrouter_base_url.rstrip('/')}/chat/completions", headers=self.headers, json=payload) as response:
                timings = dict(_timings.get())
                connection_elapsed = ms(request_started)
                timings["openrouter_connection_ms"] = connection_elapsed
                timings["connection_ms"] = connection_elapsed
                _timings.set(timings)
                if response.is_error:
                    logger.warning("OpenRouter stream failed status=%s", response.status_code)
                    raise public_provider_error(response.status_code)
                async for raw in sse_data(response.aiter_lines()):
                    if raw.strip() == "[DONE]":
                        done = True
                        break
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise HTTPException(502, "ИИ вернул некорректный фрагмент ответа.") from exc
                    if not isinstance(data, dict):
                        raise HTTPException(502, "ИИ вернул некорректный фрагмент ответа.")
                    if data.get("error"):
                        raise public_provider_error(upstream_error_status(data["error"]))
                    raw_usage = data.get("usage")
                    if isinstance(raw_usage, dict):
                        _usage.set({
                            "input_tokens": raw_usage.get("prompt_tokens"),
                            "output_tokens": raw_usage.get("completion_tokens"),
                            "reasoning_tokens": (raw_usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
                            "cost": raw_usage.get("cost"),
                            "cost_estimated": raw_usage.get("cost") is None,
                        })
                    choices = data.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue  # Usage-only frames are valid; they contain no text.
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        raise HTTPException(502, "ИИ вернул некорректный фрагмент ответа.")
                    if choice.get("finish_reason") == "error":
                        raise public_provider_error(502)
                    if choice.get("finish_reason") in {"content_filter", "refusal"}:
                        raise HTTPException(502, "ИИ не смог сформировать ответ. Попробуйте изменить запрос.")
                    delta = choice.get("delta") or {}
                    if not isinstance(delta, dict):
                        raise HTTPException(502, "ИИ вернул некорректный фрагмент ответа.")
                    chunk = delta.get("content")
                    if chunk:
                        if not isinstance(chunk, str):
                            raise HTTPException(502, "ИИ вернул некорректный фрагмент ответа.")
                        has_content = has_content or bool(chunk.strip())
                        if not first_token_seen:
                            first_token_seen = True
                            timings = dict(_timings.get())
                            first_elapsed = ms(request_started)
                            timings["openrouter_first_token_ms"] = first_elapsed
                            timings["first_token_ms"] = first_elapsed
                            _timings.set(timings)
                        yield chunk
        except httpx.RequestError as exc:
            raise public_provider_error(502) from exc
        if not done:
            raise HTTPException(502, "Ответ ИИ прервался. Попробуйте ещё раз.")
        if not has_content:
            raise HTTPException(502, "ИИ вернул пустой ответ. Попробуйте ещё раз.")
