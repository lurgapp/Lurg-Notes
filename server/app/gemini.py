import asyncio
import json
import logging
from contextlib import aclosing
import httpx
from fastapi import HTTPException
from .config import settings
from .prompts import current_time_context
from .provider_stream import public_provider_error, sse_data, upstream_error_status

BASE = "https://generativelanguage.googleapis.com/v1beta"
logger = logging.getLogger("lurg.ai.gemini")
_client: httpx.AsyncClient | None = None

def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=45,
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16, keepalive_expiry=120),
        )
    return _client

async def _post(path: str, payload: dict) -> dict:
    for attempt in range(3):
        try:
            response = await client().post(f"{BASE}/{path}", headers={"x-goog-api-key": settings.gemini_api_key}, json=payload)
            if not response.is_error:
                return response.json()
            if response.status_code not in {429, 500, 502, 503, 504}:
                logger.warning("Gemini request failed status=%s", response.status_code)
                break
        except httpx.RequestError:
            logger.warning("Gemini request error attempt=%s", attempt + 1)
            if attempt == 2:
                break
        await asyncio.sleep(0.5 * (2 ** attempt))
    raise HTTPException(502, "Сервис анализа временно недоступен")

async def analyze(text: str) -> dict:
    prompt = """Сначала пойми смысл последнего сообщения человека, затем реши, относится ли оно к памяти.
Верни только JSON с ключами memory_disposition, operation, nodes и edges.
memory_disposition: ignore, conversation_only, temporary или graph_candidate.
graph_candidate — только явный устойчивый факт о пользователе: навык, предпочтение, проект, цель, занятие, связь или устойчивое место.
temporary — планы с датой, текущее самочувствие, погода, пробки, новости, доставка, разовое или истекающее событие. Для temporary nodes и edges всегда пустые.
conversation_only — обычная беседа; ignore — команда или вопрос без нового личного факта. Не выбирай класс по отдельным словам, определи смысл высказывания целиком.
operation: create, update или deactivate.
nodes: [{key,type,label,properties,confidence}].
Допустимые type: profile, age, occupation, education, person, interest, skill, goal, project, organization, place, fact.
edges: [{source,target,relation}], где source/target — key узлов. Добавь узел key=user,type=person,label=Пользователь только когда найден хотя бы один явный персональный факт.
Если memory_disposition не graph_candidate, верни пустые nodes и edges. Не выдумывай факты. Используй update для замены/актуализации, deactivate для отрицаний вроде "больше не". Не утверждай, что Graph сохранен. relation — короткий русский глагол. Текст: """ + text
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
    result = await _post(f"models/{settings.gemini_model}:generateContent", body)
    try:
        data = json.loads(result["candidates"][0]["content"]["parts"][0]["text"])
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list) or not isinstance(data.get("edges"), list):
            raise ValueError
        return data
    except (KeyError, ValueError, json.JSONDecodeError):
        raise HTTPException(502, "ИИ вернул некорректную структуру графа")

async def embedding(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    result = await _post(f"models/{settings.gemini_embedding_model}:embedContent", {"content": {"parts": [{"text": text}]}, "taskType": task_type})
    return result["embedding"]["values"]

async def repository_intelligence(text: str) -> dict:
    prompt = """Ты анализируешь безопасный summary публичного GitHub репозитория.
Не извлекай secrets, токены, ключи или приватные данные. Верни только JSON:
{
  "project_summary": "краткое описание проекта",
  "technologies": [],
  "frameworks": [],
  "dependencies": [],
  "skills": [],
  "topics": [],
  "confidence": 0.0
}
Используй только переданный summary/excerpt, не выдумывай. Текст: """ + text[:200000]
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
    result = await _post(f"models/{settings.gemini_model}:generateContent", body)
    try:
        data = json.loads(result["candidates"][0]["content"]["parts"][0]["text"])
        for key in ["technologies", "frameworks", "dependencies", "skills", "topics"]:
            if not isinstance(data.get(key), list):
                data[key] = []
        data["project_summary"] = str(data.get("project_summary") or "")[:1000]
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence") or 0.75)))
        return data
    except (KeyError, ValueError, json.JSONDecodeError):
        raise HTTPException(502, "ИИ вернул некорректную структуру repository intelligence")

async def classify_personal_query(content: str) -> dict:
    prompt = """Классифицируй только последнее сообщение пользователя из JSON ниже. Не отвечай пользователю и не придумывай персональные значения.
Верни JSON {kind,category,relation,requested_scope,confidence}.
kind: personal_fact_query, graph_overview, graph_search, general, web или unknown.
category: name, age, residence, origin, occupation, education, skills, interests, goals, projects, organizations, services, timeline или unknown.
requested_scope: single, many, overview или unknown.
Вход: """ + content[:12000]
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    result = await _post(f"models/{settings.gemini_model}:generateContent", body)
    try:
        data = json.loads(result["candidates"][0]["content"]["parts"][0]["text"])
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (KeyError, ValueError, json.JSONDecodeError):
        raise HTTPException(502, "ИИ вернул некорректную классификацию запроса")

async def answer(question: str, context: list[dict]) -> str:
    prompt = f"""Ответь по-русски только на основании узлов персонального графа ниже.
created_at — точная дата и время первого добавления информации, updated_at — последнего обновления.
Если вопрос относится к дате или времени, укажи их явно и не подменяй временем события.
Если данных недостаточно, прямо скажи об этом. Вопрос: {question}
Контекст: {json.dumps(context, ensure_ascii=False)}"""
    result = await _post(f"models/{settings.gemini_model}:generateContent", {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}})
    return result["candidates"][0]["content"]["parts"][0]["text"]

async def stream_answer(question: str, context: dict, history: list[dict[str, str]] | None = None, use_internet: bool = False):
    history_text = "\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')[:4000]}"
        for item in (history or [])[-20:]
    )
    prompt = f"""Ты Lurg AI, официальный клиент Lurg Core.
Отвечай по-русски, точно и полезно. Учитывай историю текущего диалога и релевантный Lurg Graph.
Не выдумывай данные Graph. Если контекста недостаточно, скажи это явно.
{current_time_context()}

История:
{history_text}

Lurg Graph context:
{json.dumps(context, ensure_ascii=False)}

Вопрос пользователя: {question}"""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.25},
    }
    if use_internet:
        payload["tools"] = [{"google_search": {}}]
    if not settings.gemini_api_key:
        raise public_provider_error(401)
    finished = False
    has_content = False
    try:
        async with client().stream(
            "POST", f"{BASE}/models/{settings.gemini_model}:streamGenerateContent",
            headers={"x-goog-api-key": settings.gemini_api_key}, params={"alt": "sse"}, json=payload,
        ) as response:
            if response.is_error:
                logger.warning("Gemini streaming failed status=%s", response.status_code)
                raise public_provider_error(response.status_code)
            async for raw in sse_data(response.aiter_lines()):
                if not raw or raw == "[DONE]":
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise HTTPException(502, "ИИ вернул некорректный фрагмент ответа.") from exc
                if not isinstance(data, dict):
                    raise HTTPException(502, "ИИ вернул некорректный фрагмент ответа.")
                if data.get("error"):
                    raise public_provider_error(upstream_error_status(data["error"]))
                if (data.get("promptFeedback") or {}).get("blockReason"):
                    raise HTTPException(502, "ИИ не смог сформировать ответ. Попробуйте изменить запрос.")
                for candidate in data.get("candidates") or []:
                    reason = candidate.get("finishReason")
                    if reason:
                        if reason not in {"STOP", "MAX_TOKENS"}:
                            raise HTTPException(502, "ИИ не смог сформировать ответ. Попробуйте изменить запрос.")
                        finished = True
                    for part in ((candidate.get("content") or {}).get("parts") or []):
                        chunk = part.get("text")
                        if chunk and not part.get("thought"):
                            if not isinstance(chunk, str):
                                raise HTTPException(502, "ИИ вернул некорректный фрагмент ответа.")
                            has_content = has_content or bool(chunk.strip())
                            yield chunk
    except httpx.RequestError as exc:
        raise public_provider_error(502) from exc
    if not finished:
        raise HTTPException(502, "Ответ ИИ прервался. Попробуйте ещё раз.")
    if not has_content:
        raise HTTPException(502, "ИИ вернул пустой ответ. Попробуйте ещё раз.")

class GeminiAIEngine:
    provider_name = "gemini"

    @property
    def text_model(self) -> str:
        return settings.gemini_model

    @property
    def embedding_model(self) -> str:
        return settings.gemini_embedding_model

    async def extract_graph(self, content: str) -> dict:
        return await analyze(content)

    async def classify_personal_query(self, content: str) -> dict:
        return await classify_personal_query(content)

    async def extract_repository_intelligence(self, content: str) -> dict:
        return await repository_intelligence(content)

    async def structured_json(self, instruction: str, content: str, *, max_tokens: int = 1000, temperature: float = 0.2) -> dict:
        raise HTTPException(503, "Notes AI требует OpenRouter provider.")

    async def embed(self, content: str, purpose: str) -> list[float]:
        return await embedding(content, purpose)

    async def answer(self, question: str, context: list[dict]) -> str:
        return await answer(question, context)

    async def stream_answer(self, question: str, context: dict, history: list[dict[str, str]] | None = None, *, mode: str = "general", complex_query: bool = False):
        async with aclosing(stream_answer(question, context, history, mode in {"web", "hybrid"})) as stream:
            async for chunk in stream:
                yield chunk

    def usage(self) -> dict:
        return {}

    def timings(self) -> dict:
        return {}
