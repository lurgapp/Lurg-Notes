import re
from functools import lru_cache

CURRENT_PATTERNS = re.compile(r"\b(сегодня|сейчас|последн\w*|актуальн\w*|текущ\w*|новост\w*|курс\w*|цен\w*|верси\w*|погод\w*|202[6-9])\b", re.I)
PERSONAL_PATTERNS = re.compile(r"\b(мой|моя|моё|мое|мои|моих|моем|моём|обо мне|про меня|у меня|я начал|я изучаю|мои цели|мои навыки|мой graph|мой граф|что ты знаешь обо мне|расскажи обо мне|помнишь ли ты.*обо мне)\b", re.I)
EXPLICIT_WEB_PATTERNS = re.compile(r"\b(найди.*интернет|в интернете|интернет-поиск|web search|поищи|найди последние|последние новости|что произошло сегодня|сейчас стоит|актуальн\w* верси\w*)\b", re.I)
HYBRID_PATTERNS = re.compile(r"\b(на основе моих|исходя из моих|с учетом моих|с учётом моих).*\b(актуальн\w*|найди|ваканси\w*|технолог\w*|проект\w*|новост\w*|рын\w*|тренд\w*)\b", re.I)
GREETING_PREFIX = re.compile(r"^\s*(привет|здравствуй|здравствуйте|добрый день|доброе утро|добрый вечер|хай|hello|hi)[,!.\s-]+", re.I)
NO_WEB_PATTERNS = re.compile(r"\b(не используй интернет|без интернета|не ищи в интернете|только без web|не используй web)\b", re.I)
ONLY_GRAPH_PATTERNS = re.compile(r"\b(ответь только по моему graph|ответь только по моему граф|только по моему graph|только по моему граф)\b", re.I)
NO_GRAPH_PATTERNS = re.compile(r"\b(не используй мои данные|без моих данных|не используй graph|не используй граф)\b", re.I)
COMPLEX_PATTERNS = re.compile(r"\b(сравни|проанализ|стратег|почему|план)\b", re.I)

def normalize_query(query: str) -> str:
    value = query.strip()
    for _ in range(2):
        next_value = GREETING_PREFIX.sub("", value).strip()
        if next_value == value:
            break
        value = next_value
    return value or query.strip()

@lru_cache(maxsize=2048)
def classify_intent(query: str, use_graph: bool | None = None, use_internet: bool | None = None) -> str:
    normalized = normalize_query(query)
    no_web = bool(NO_WEB_PATTERNS.search(normalized))
    only_graph = bool(ONLY_GRAPH_PATTERNS.search(normalized))
    no_graph = bool(NO_GRAPH_PATTERNS.search(normalized))
    graph_allowed = use_graph is not False and not no_graph
    web_allowed = use_internet is not False and not no_web and not only_graph
    graph = graph_allowed and bool(PERSONAL_PATTERNS.search(normalized))
    web = web_allowed and bool(EXPLICIT_WEB_PATTERNS.search(normalized) or (CURRENT_PATTERNS.search(normalized) and not graph))
    if graph_allowed and web_allowed and HYBRID_PATTERNS.search(normalized):
        return "hybrid"
    if graph:
        return "graph"
    if web:
        return "web"
    return "general"

def is_complex(query: str) -> bool:
    return len(query) > 500 or bool(COMPLEX_PATTERNS.search(query))

def web_sources(results: list[dict]) -> list[dict]:
    from urllib.parse import urlparse
    return [{"type": "web", "title": item["title"], "url": item["url"], "domain": urlparse(item["url"]).hostname or "", "snippet": item["snippet"]} for item in results]

def graph_sources(nodes: list[dict]) -> list[dict]:
    return [{"type": "graph", "node_id": item["id"], "label": item["label"], "node_type": item.get("type"), "created_at": item["created_at"], "confidence": item["confidence"]} for item in nodes]
