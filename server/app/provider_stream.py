"""Bounded SSE framing and public errors shared by external AI providers."""
from collections.abc import AsyncIterator

from fastapi import HTTPException

MAX_SSE_EVENT_CHARS = 1_048_576


def public_provider_error(status: int) -> HTTPException:
    if status in {400, 401, 403, 404}:
        return HTTPException(503, "ИИ временно недоступен. Проверьте конфигурацию AI-провайдера.")
    if status == 402:
        return HTTPException(503, "На сервере закончился баланс AI-провайдера.")
    if status == 429:
        return HTTPException(429, "Слишком много запросов к ИИ. Попробуйте немного позже.")
    return HTTPException(502, "ИИ временно недоступен. Попробуйте ещё раз.")


def upstream_error_status(error: object) -> int:
    # A provider message can contain request data or credentials; never forward it.
    code = error.get("code") if isinstance(error, dict) else None
    try:
        return int(code)
    except (TypeError, ValueError):
        return 502


async def sse_data(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    """Join multi-line data fields; ignore comments, event names, IDs and retries."""
    fields: list[str] = []
    size = 0
    async for line in lines:
        if not line:
            if fields:
                yield "\n".join(fields)
            fields, size = [], 0
            continue
        if line.startswith(":") or not line.startswith("data:"):
            continue
        value = line[5:]
        if value.startswith(" "):
            value = value[1:]
        size += len(value) + 1
        if size > MAX_SSE_EVENT_CHARS:
            raise HTTPException(502, "ИИ вернул слишком большой фрагмент ответа.")
        fields.append(value)
    # Some gateways omit the final empty line. Terminal semantics remain the
    # responsibility of the provider adapter, rather than treating EOF as success.
    if fields:
        yield "\n".join(fields)
