import asyncio
import json
import logging
import time
import re
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...ai import get_ai_engine
from ...auth import current_user
from ...core.contracts import AIEngine
from ...db import Session, get_db
from ...runtime import classify_intent, graph_sources, is_complex, normalize_query, web_sources
from ...search import SearchUnavailable, get_search_engine
from ...schemas import AnalyzeRequest, ChatCreate, ChatMessageCreate, ChatUpdate, GraphChangeDecision
from ..graph.router import analyze_graph, apply_graph_candidate, load_pending_candidate
from ..graph.resolver import graph_canonical_key_for_type, resolve_entity, strip_conversational_noise
from .personal_facts import HIGH_CONFIDENCE, PersonalFactResolver, deterministic_personal_intent, personal_query_category

router = APIRouter(prefix="/chat", tags=["Lurg Chat"])
logger = logging.getLogger("lurg.chat")
GRAPH_SOURCE_THRESHOLD = 0.52
CONFIRM_PATTERNS = re.compile(r"^\s*(да|добавь|подтверждаю|запомни|да,\s*добавь|ок|хорошо)\s*[.!?]*\s*$", re.I)
MISSING_PENDING_CONFIRM_PATTERNS = re.compile(r"^\s*(да|добавь|подтверждаю|запомни|да,\s*добавь)\s*[.!?]*\s*$", re.I)
REJECT_PATTERNS = re.compile(r"^\s*(нет|отмена|отмени|не надо|не добавляй)\s*[.!?]*\s*$", re.I)
EDIT_PATTERNS = re.compile(r"^\s*(измени|исправь|редактировать)\b", re.I)
LOCAL_REPLY_PATTERNS = [
    (re.compile(r"^\s*(привет|здравствуй|здравствуйте|добрый\s+день|доброе\s+утро|добрый\s+вечер)\s*[.!?]*\s*$", re.I), "Привет."),
    (re.compile(r"^\s*(спасибо|благодарю|спс)\s*[.!?]*\s*$", re.I), "Пожалуйста."),
    (re.compile(r"^\s*(пока|до\s+свидания|увидимся|хорошего\s+дня)\s*[.!?]*\s*$", re.I), "До связи."),
    (re.compile(r"^\s*(ок|ладно|понял|поняла|ясно|хорошо)\s*[.!?]*\s*$", re.I), "Хорошо."),
]
ONBOARDING_FACT_HINT_PATTERNS = re.compile(
    r"\b(меня зовут|мо[её] имя|зовут меня|можно просто|мне \d{1,3}|исполнилось|лет|будет \d{1,3}|"
    r"я работаю|работаю в|работаю над|занимаюсь|учусь|живу|родом|переехал|переехала|я из|интересуюсь|интересен|интересна|интересно|интересны|интерес|увлекаюсь|увлекся|увлёкся|увлеклась|увлеклась|"
    r"люблю|больше не|теперь|умею|знаю|изучаю|изучаю|навык|цель|хочу|мечтаю|планирую|проект|экосистем)\b",
    re.I,
)
TEMPORARY_FACT_PATTERNS = re.compile(
    r"\b(?:я\s+)?(?:устал[а]?|голоден|голодна|болею|ем|пью|иду|еду|сплю|сижу|лежу|смотрю|слушаю|читаю|"
    r"посмотрел[а]?|послушал[а]?|прочитал[а]?|съел[а]?|выпил[а]?|сейчас\s+ем|сейчас\s+смотрю|сегодня|вчера|"
    r"завтра|прямо\s+сейчас|настроение)\b",
    re.I,
)
LONG_TERM_FACT_PATTERNS = re.compile(
    r"\b(?:меня\s+зовут|мо[её]\s+имя|зовут\s+меня|можно\s+просто|мне\s+\d{1,3}|исполнилось|лет|будет\s+\d{1,3}|"
    r"работаю|тружусь|место\s+работы|учусь|место\s+уч[её]бы|живу|родом|переехал|переехала|я\s+из|"
    r"интересуюсь|интересен|интересна|интересно|интересны|увлекаюсь|увлекся|увлёкся|увлеклась|увлеклась|занимаюсь|умею|знаю|владею|изучаю|навык|цель|хочу|мечтаю|планирую|"
    r"проект|экосистем|больше\s+не|теперь)\b",
    re.I,
)
LONG_TERM_INTEREST_LABEL_PATTERNS = re.compile(
    r"\b(?:ai|ии|искусственн\w+\s+интеллект\w*|авиац\w*|шахмат\w*|дизайн\w*|искусств\w*|музык\w*|"
    r"истор\w*|философ\w*|наук\w*|технолог\w*|программирован\w*|разработк\w*)\b",
    re.I,
)
TRANSIENT_INTEREST_LABEL_PATTERNS = re.compile(
    r"\b(?:этот|эта|это|фильм|сериал|ролик|видео|пицц\w*|кофе|чай|бургер\w*|суп\w*|песн\w*|трек\w*)\b",
    re.I,
)
COMMON_LABEL_ALIASES = {
    "мишей": "Миша",
}
AI_INTENT_CONFIDENCE_THRESHOLD = 0.75
AI_INTENT_CACHE_TTL_SECONDS = 3600
AI_INTENT_CACHE_MAX = 1024
AI_INTENT_ALLOWED_KINDS = {"personal_fact_query", "graph_overview", "graph_search", "general", "web", "unknown"}
AI_INTENT_ALLOWED_CATEGORIES = {
    "name", "age", "residence", "origin", "occupation", "education", "skills",
    "interests", "goals", "projects", "organizations", "services", "timeline", "unknown",
}
_ai_intent_cache: dict[str, tuple[float, dict]] = {}


@dataclass(frozen=True)
class MessageRoute:
    kind: str
    local_answer: str | None = None
    candidate_change: dict | None = None
    direct_lookup: dict | None = None

def elapsed_ms(start: float) -> int:
    return round((time.monotonic() - start) * 1000)

def clean_fact_label(value: str) -> str:
    cleaned = trim_next_fact_clause(strip_conversational_noise(value))
    return re.split(r"\s+(?:и|and)\s+", cleaned.strip(" .,!?:;"))[0].strip(" .,!?:;")

def normalize_fact_label(value: str, node_type: str) -> str:
    label = strip_conversational_noise(value).strip(" .,!?:;\"'")
    if node_type == "place":
        label = re.sub(r"^(?:городе|город|г\.|в|во|на)\s+", "", label, flags=re.I).strip(" .,!?:;")
        return resolve_entity("place", label)["label"]
    return COMMON_LABEL_ALIASES.get(label.casefold(), label)

def canonical_entity_key(label: str) -> str:
    return graph_canonical_key_for_type("interest", label) or re.sub(r"\s+", " ", label.strip().casefold())

def graph_node_key_label(label: str) -> str:
    normalized = re.sub(r"\s+", " ", label.strip().casefold())
    return graph_canonical_key_for_type("interest", label) or label

def personal_fact_lifetime(message: str) -> str:
    value = message.strip()
    lowered = value.casefold()
    if TEMPORARY_FACT_PATTERNS.search(value) and not LONG_TERM_FACT_PATTERNS.search(value):
        return "short_term"
    if LONG_TERM_FACT_PATTERNS.search(value):
        return "long_term"
    if re.search(r"\b(?:люблю|очень\s+люблю|мне\s+нравится)\s+(.{2,120})", value, re.I):
        label = re.search(r"\b(?:люблю|очень\s+люблю|мне\s+нравится)\s+(.{2,120})", value, re.I).group(1)
        if TRANSIENT_INTEREST_LABEL_PATTERNS.search(label):
            return "short_term"
        if LONG_TERM_INTEREST_LABEL_PATTERNS.search(label):
            return "long_term"
    if lowered.startswith(("запомни", "добавь в мой graph", "добавь в мой граф", "исправь", "удали факт")):
        return "long_term"
    return "unknown"

def local_chat_answer(message: str) -> str | None:
    value = normalize_query(message)
    for pattern, answer in LOCAL_REPLY_PATTERNS:
        if pattern.match(value):
            return answer
    return None

def missing_personal_fact_answer(category: str) -> str:
    messages = {
        "name": "В вашем Graph пока не указано имя.",
        "age": "В вашем Graph пока не указан возраст.",
        "residence": "В вашем Graph пока не указано место проживания.",
        "origin": "В вашем Graph пока не указано происхождение.",
        "skills": "В вашем Graph пока не указаны навыки.",
        "projects": "В вашем Graph пока не указаны проекты.",
        "interests": "В вашем Graph пока не указаны интересы.",
        "occupation": "В вашем Graph пока не указана работа.",
        "education": "В вашем Graph пока не указана учеба.",
        "goals": "В вашем Graph пока не указаны цели.",
    }
    return messages.get(category, "В вашем Graph пока нет подтвержденных данных по этому запросу.")

def should_ai_intent_fallback(message: str) -> bool:
    normalized = normalize_query(message).casefold().replace("ё", "е")
    return bool(re.search(r"\b(я|мне|меня|мой|моя|мое|моё|мои|у\s+меня|обо\s+мне|про\s+меня|graph|граф|сохранено|знаешь|напомни)\b", normalized))

def sanitize_recent_history(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    result = []
    for item in (history or [])[-3:]:
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        result.append({"role": role, "content": (item.get("content") or "")[:240]})
    return result

def validate_ai_personal_intent(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "unknown").strip()
    category = str(raw.get("category") or "unknown").strip()
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if kind not in AI_INTENT_ALLOWED_KINDS or category not in AI_INTENT_ALLOWED_CATEGORIES:
        return None
    return {
        "kind": kind,
        "category": category,
        "relation": raw.get("relation") if isinstance(raw.get("relation"), str) else None,
        "requested_scope": str(raw.get("requested_scope") or "unknown")[:40],
        "confidence": max(0.0, min(1.0, confidence)),
    }

def ai_intent_usage_telemetry(ai: AIEngine, direct_lookup: dict) -> dict:
    telemetry = (direct_lookup.get("telemetry") or {})
    return {
        "provider": ai.provider_name,
        "model": ai.text_model,
        "mode": "intent_classification",
        "usage": telemetry.get("classification_usage") or {},
        "total_latency_ms": (telemetry.get("classification_timings") or {}).get("total_ms"),
        "first_token_latency_ms": None,
        "cache_hit": bool(telemetry.get("classification_cache_hit")),
        "profile": {
            "classified_kind": telemetry.get("classified_kind"),
            "classified_category": telemetry.get("classified_category"),
            "classification_confidence": telemetry.get("classification_confidence"),
            "deterministic_hit": telemetry.get("deterministic_hit"),
        },
        "graph_nodes_used": direct_lookup.get("node_count"),
        "internet_results_used": 0,
    }

async def ai_personal_intent(ai: AIEngine, message: str, history: list[dict[str, str]] | None = None) -> dict | None:
    recent_history = sanitize_recent_history(history)
    cache_key = json.dumps({
        "message": normalize_query(message).casefold().replace("ё", "е"),
        "recent_context": recent_history,
    }, ensure_ascii=False, sort_keys=True)
    now = time.monotonic()
    cached = _ai_intent_cache.get(cache_key)
    if cached and now - cached[0] <= AI_INTENT_CACHE_TTL_SECONDS:
        return {**cached[1], "cache_hit": True}
    payload = {
        "message": message[:500],
        "language": "ru",
        "recent_context": recent_history,
        "allowed_kinds": sorted(AI_INTENT_ALLOWED_KINDS),
        "allowed_categories": sorted(AI_INTENT_ALLOWED_CATEGORIES),
    }
    try:
        raw = await ai.classify_personal_query(json.dumps(payload, ensure_ascii=False))
    except Exception:
        logger.warning("AI personal intent classification failed")
        return None
    intent = validate_ai_personal_intent(raw)
    if not intent:
        return None
    usage = getattr(ai, "usage", lambda: {})() or {}
    timings = getattr(ai, "timings", lambda: {})() or {}
    intent = {**intent, "source": "ai", "cache_hit": False, "usage": usage, "timings": timings}
    _ai_intent_cache[cache_key] = (now, intent)
    if len(_ai_intent_cache) > AI_INTENT_CACHE_MAX:
        for key in list(_ai_intent_cache)[:128]:
            _ai_intent_cache.pop(key, None)
    return intent

def normalize_natural_fact_text(message: str) -> str:
    value = strip_conversational_noise(message)
    value = re.sub(r"^\s*(?:привет|здравствуй|здравствуйте|добрый\s+день)\s*,?\s*", "", value, flags=re.I)
    value = re.sub(r"\b(?:я\s+)?сам(?:а)?\s+", "я ", value, flags=re.I)
    return value.strip(" .,!?:;")

def trim_next_fact_clause(value: str) -> str:
    return re.split(
        r"\s*,?\s+\b(?:меня\s+зовут|мо[её]\s+имя|мне\s+\d{1,3}|я\s+работаю|работаю\s+в|учусь\s+в|живу\s+в|я\s+из|"
        r"родом\s+из|сейчас\s+живу|моя\s+цель|цель|хочу|мечтаю|планирую|мой\s+проект|проект|работаю\s+над|делаю)\b",
        value,
        maxsplit=1,
        flags=re.I,
    )[0]

def split_fact_list(value: str) -> list[str]:
    cleaned = trim_next_fact_clause(value).strip(" .,!?:;")
    parts = re.split(r"\s*,\s*|\s+(?:и|and)\s+", cleaned)
    return [part.strip(" .,!?:;") for part in parts if part.strip(" .,!?:;")]

def add_candidate_fact(nodes: list[dict], edges: list[dict], seen: set[tuple[str, str]], node_type: str, label: str, relation: str, *, key: str | None = None, confidence: float = 0.9) -> None:
    label = normalize_fact_label(label, node_type)
    if not label:
        return
    canonical_key = graph_node_key_label(label)
    node_key = key or f"{node_type}:{canonical_key}"
    dedupe = (node_type, canonical_key.casefold())
    if dedupe in seen:
        return
    seen.add(dedupe)
    nodes.append({"key": node_key, "type": node_type, "label": label, "confidence": confidence})
    edges.append({"source": "user", "target": node_key, "relation": relation, "confidence": confidence})

def deterministic_onboarding_change(message: str) -> dict | None:
    value = normalize_natural_fact_text(message)
    if personal_fact_lifetime(value) == "short_term":
        return None
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    operation = "create"

    for pattern in [
        r"^\s*[Яя]\s+([А-ЯЁ][а-яё]{1,40})\s*[.!?]*\s*$",
        r"\b[Мм]ожно\s+просто\s+([А-ЯЁ][а-яё]{1,40})\b",
        r"\b[Мм]еня\s+([А-ЯЁ][а-яё]{1,40})\s+зовут\b",
    ]:
        for match in re.finditer(pattern, value):
            add_candidate_fact(nodes, edges, seen, "profile", match.group(1), "зовут", confidence=0.95)

    patterns = [
        (r"(?:меня\s+зовут|мо[её]\s+имя)\s+([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё .'-]{1,80})", "profile", "зовут", "Ваше имя"),
        (r"(?:все\s+)?зовут\s+меня\s+([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё .'-]{1,80})", "profile", "зовут", "Ваше имя"),
        (r"\bмне\s+(?:уже\s+)?(\d{1,3})\s*(?:год(?:ик(?:а|ов)?)?|года|лет)?\b", "age", "возраст", "Ваш возраст"),
        (r"\b(?:мне\s+)?(?:недавно\s+)?исполнилось\s+(\d{1,3})\b", "age", "возраст", "Ваш возраст"),
        (r"\bчерез\s+\w+\s+будет\s+(\d{1,3})\b", "age", "возраст", "Будущий возраст"),
        (r"\b(?:я\s+)?(?:работаю|тружусь)\s+(?!(?:в|во|над)\b)(?:как\s+|на\s+позиции\s+)?([A-Za-zА-Яа-яЁё0-9 ._+#-]{2,100})", "occupation", "занимается", "Род занятий"),
        (r"\b(?:вообще\s+)?я\s+((?:backend|frontend|fullstack|mobile|android|ios|web|data|ml|ai|devops)\s+)?(пилот(?:ом)?|разработчик(?:ом)?|инженер(?:ом)?|врач(?:ом)?|дизайнер(?:ом)?|аналитик(?:ом)?|предпринимател(?:ь|ем)|программист(?:ом)?|учител(?:ь|ем)|менеджер(?:ом)?|архитектор(?:ом)?)\b", "occupation", "занимается", "Род занятий"),
        (r"\bпо\s+профессии\s+([A-Za-zА-Яа-яЁё0-9 ._+#-]{2,100})", "occupation", "занимается", "Род занятий"),
        (r"\b(?:я\s+)?(?:работаю\s+в|место\s+работы\s*[:—-]?)\s+([A-Za-zА-Яа-яЁё0-9 ._+#\"'-]{2,100})", "organization", "работает в", "Место работы"),
        (r"\b(?:я\s+)?(?:учусь\s+в|место\s+уч[её]бы\s*[:—-]?)\s+([A-Za-zА-Яа-яЁё0-9 ._+#\"'-]{2,100})", "organization", "учится в", "Место учебы"),
        (r"\b(?:я\s+)?учусь\s+(на\s+[A-Za-zА-Яа-яЁё0-9 ._+#\"'-]{2,100})", "education", "учится", "Учеба"),
        (r"\b(?:я\s+)?(?:сейчас\s+)?(?:живу\s+в|нахожусь\s+в)\s+([A-Za-zА-Яа-яЁё .'-]{2,100})", "place", "живет в", "Место проживания"),
        (r"\bродом\s+из\s+([A-Za-zА-Яа-яЁё .'-]{2,100})", "place", "родом из", "Родной город"),
        (r"\bпереехал[а]?\s+в\s+([A-Za-zА-Яа-яЁё .'-]{2,100})", "place", "живет в", "Место проживания"),
        (r"\b(?:я\s+)?(?:сам(?:а)?\s+)?из\s+([A-Za-zА-Яа-яЁё .'-]{2,100})", "place", "из", "Место"),
        (r"\b(?:моя\s+цель|цель)\s*[:—-]?\s+([A-Za-zА-Яа-яЁё0-9 .,_+#\"'-]{3,160})", "goal", "цель", "Цель"),
        (r"\b(?:я\s+)?(?:хочу|мечтаю|планирую)\s+([A-Za-zА-Яа-яЁё0-9 .,_+#\"'-]{3,160})", "goal", "хочет", "Цель"),
        (r"\b(?:мой\s+проект|проект)\s*[:—-]?\s+([A-Za-zА-Яа-яЁё0-9 ._+#\"'-]{2,140})", "project", "делает", "Проект"),
        (r"\b(?:я\s+)?(?:работаю\s+над|делаю)\s+проект(?:ом)?\s+([A-Za-zА-Яа-яЁё0-9 ._+#\"'-]{2,140})", "project", "делает", "Проект"),
        (r"\b(?:я\s+)?работаю\s+над\s+(?!проект(?:ом)?\b)([A-Za-zА-Яа-яЁё0-9 ._+#\"'-]{2,140})", "project", "делает", "Проект"),
        (r"\b(?:я\s+)?делаю\s+(?:(?:свою|собственную)\s+)?(экосистем[ау][A-Za-zА-Яа-яЁё0-9 ._+#\"'-]{0,120})", "project", "делает", "Проект"),
    ]
    for pattern, node_type, relation, _ in patterns:
        for match in re.finditer(pattern, value, re.I):
            label = clean_fact_label("".join(part for part in match.groups() if part is not None))
            if node_type == "age":
                age = int(label)
                if age < 1 or age > 130:
                    continue
                label = f"{age} лет"
            if node_type in {"profile", "age", "place", "occupation"} and re.search(
                r"\b(?:теперь|уже|сейчас|переехал|переехала|исполнилось|будет)\b", value, re.I
            ):
                operation = "update"
            add_candidate_fact(nodes, edges, seen, node_type, label, relation, confidence=0.95 if node_type in {"profile", "age"} else 0.9)

    list_patterns = [
        (r"\b(?:интересуюсь|мне\s+интерес(?:ен|на|но|ны)|мои\s+интересы\s*[:—-]?)\s+([A-Za-zА-Яа-яЁё0-9 .,_+#\"'-]{2,180})", "interest", "интересуется", "create"),
        (r"\b(?:люблю|очень\s+люблю)\s+([A-Za-zА-Яа-яЁё0-9 .,_+#\"'-]{2,180})", "interest", "любит", "create"),
        (r"\b(?:увлекаюсь|увлекся|увлёкся|увлеклась|увлеклась)\s+([A-Za-zА-Яа-яЁё0-9 .,_+#\"'-]{2,180})", "interest", "увлекается", "create"),
        (r"\bбольше\s+не\s+(?:изучаю|учу|использую)\s+([A-Za-zА-Яа-яЁё0-9 .,_+#\"'-]{2,180})", "skill", "изучает", "deactivate"),
        (r"\b(?:умею|знаю|владею|мои\s+навыки\s*[:—-]?)\s+([A-Za-zА-Яа-яЁё0-9 .,_+#\"'-]{2,180})", "skill", "знает"),
        (r"\b(?:(?:сейчас|последнее\s+время)\s+)?(?:изучаю|начал\s+изучать|начала\s+изучать)\s+([A-Za-zА-Яа-яЁё0-9 .,_+#\"'-]{2,180})", "skill", "изучает"),
        # Natural Russian word order: "я ещё Python изучаю".
        (r"\b(?:я\s+)?(?:ещ[её]\s+)?([A-Za-zА-Яа-яЁё0-9+#._-]{2,80})\s+(?:изучаю|учу|осваиваю)\b", "skill", "изучает"),
    ]
    for item in list_patterns:
        pattern, node_type, relation = item[:3]
        pattern_operation = item[3] if len(item) > 3 else "create"
        for match in re.finditer(pattern, value, re.I):
            if pattern_operation == "deactivate":
                operation = "deactivate"
            elif re.search(r"\b(?:теперь|сейчас)\b", value, re.I):
                operation = "update"
            for label in split_fact_list(match.group(1)):
                if relation == "любит" and TRANSIENT_INTEREST_LABEL_PATTERNS.search(label):
                    continue
                add_candidate_fact(nodes, edges, seen, node_type, label, relation)

    if not nodes:
        return None
    human_types = {
        "profile": "профиль",
        "age": "возраст",
        "occupation": "занятие",
        "education": "образование",
        "person": "человек",
        "interest": "интерес",
        "skill": "навык",
        "goal": "цель",
        "project": "проект",
        "organization": "организация",
        "place": "место",
        "fact": "факт",
    }
    summary = " · ".join(f"{human_types.get(node['type'], 'факт').capitalize()}: {node['label']}" for node in nodes[:4])
    if len(nodes) > 4:
        summary += f" · еще {len(nodes) - 4}"
    return {
        "operation": operation,
        "fact_text": value,
        "proposed_nodes": nodes,
        "proposed_edges": [] if operation == "deactivate" else edges,
        "explanation": f"Вы сообщили: {value}",
        "title": "Добавить в Graph",
        "summary": summary,
    }

def candidate_change(message: str) -> dict | None:
    value = message.strip()
    lifetime = personal_fact_lifetime(value)
    if lifetime == "short_term":
        return None
    lowered = value.casefold()
    delete_place = re.match(
        r"^удали(?:\s+(?:то|факт|информацию|данные))?(?:\s*,?\s*что)?\s+я\s+"
        r"(?:из|живу\s+в|нахожусь\s+в|переехал[а]?\s+в)\s+(.+?)\s*[.!?]*$",
        value,
        re.I,
    )
    if delete_place:
        label = normalize_fact_label(delete_place.group(1), "place")
        if not label:
            return None
        return {
            "operation": "deactivate",
            "fact_text": value,
            "proposed_nodes": [{"key": f"place:{label}", "type": "place", "label": label, "confidence": 0.95}],
            "proposed_edges": [],
            "explanation": f"Вы попросили удалить место из Graph: {label}",
            "title": "Удалить из Graph",
            "summary": label,
        }
    onboarding_change = deterministic_onboarding_change(value)
    if onboarding_change:
        return onboarding_change
    operation = None
    clean = re.sub(r"^(запомни,?\s*что|запомни|добавь в мой graph|добавь в мой граф)\s+", "", value, flags=re.I).strip()
    if lowered.startswith(("удали факт", "удали этот факт", "я больше не")):
        operation = "deactivate"
    elif lowered.startswith(("исправь",)):
        operation = "update"
    elif lowered.startswith(("запомни", "добавь в мой graph", "добавь в мой граф", "я начал", "я изучаю", "я знаю")):
        operation = "create"
    else:
        study = re.search(r"\bя\s+(?:начал\s+)?изуча(?:ю|ть)\s+([A-Za-zА-Яа-я0-9+# ._-]{2,80})", value, re.I)
        know = re.search(r"\bя\s+знаю\s+([A-Za-zА-Яа-я0-9+# ._,и-]{2,120})", value, re.I)
        if study or know:
            operation = "create"
    if not operation:
        return None
    label_match = re.search(r"\b(?:изуча(?:ю|ть)|знаю)\s+([A-Za-zА-Яа-я0-9+# ._,и-]{2,120})", clean, re.I)
    label = (label_match.group(1) if label_match else clean).strip(" .,!?:;")
    label = re.split(r"\s+(?:и|and)\s+", label)[0].strip(" .,!?:;")
    node_type = "skill" if ("изуч" in lowered or "знаю" in lowered or re.search(r"\b(python|rust|typescript|kotlin|java|go|sql)\b", clean, re.I)) else "fact"
    proposed_nodes = [{"key": label, "type": node_type, "label": label, "confidence": 0.9}] if label else []
    proposed_edges = [{"source": "user", "target": label, "relation": "изучает" if "изуч" in lowered else "знает", "confidence": 0.9}] if proposed_nodes else []
    if operation == "deactivate":
        proposed_edges = []
    title = "Добавить новый навык" if operation == "create" and node_type == "skill" else "Изменить Lurg Graph"
    return {
        "operation": operation,
        "fact_text": value,
        "proposed_nodes": proposed_nodes,
        "proposed_edges": proposed_edges,
        "explanation": f"Вы сообщили: {value}",
        "title": title,
        "summary": f"Вы начали изучать {label}" if "изуч" in lowered and label else value,
    }

def candidate_event(candidate: dict) -> dict:
    nodes = candidate.get("proposed_nodes") or []
    edges = candidate.get("proposed_edges") or []
    changes = []
    for node in nodes:
        relation = next((edge.get("relation") for edge in edges if edge.get("target") in {node.get("key"), node.get("label")}), None)
        changes.append({"type": node.get("type"), "value": node.get("label") or node.get("value"), "relation": relation})
    return {
        "candidate_id": str(candidate["id"]),
        "operation": candidate.get("operation") or candidate.get("action") or "create",
        "title": candidate.get("title") or ("Добавить в Graph" if changes else "Изменить Graph"),
        "summary": candidate.get("summary") or candidate.get("explanation") or candidate.get("fact_text"),
        "changes": changes,
        "confidence": float(candidate.get("confidence") or 0.9),
        "source_message_id": str(candidate.get("message_id")) if candidate.get("message_id") else None,
    }

def graph_extraction_change(message: str, graph: dict) -> dict | None:
    disposition = str(graph.get("memory_disposition") or "").strip().lower()
    if disposition and disposition != "graph_candidate":
        return None
    raw_nodes = graph.get("nodes") if isinstance(graph, dict) else None
    raw_edges = graph.get("edges") if isinstance(graph, dict) else None
    if not isinstance(raw_nodes, list):
        return None
    allowed_types = {"profile", "age", "occupation", "education", "person", "interest", "skill", "goal", "project", "organization", "place", "fact"}
    operation = str(graph.get("operation") or graph.get("action") or "create").strip().lower()
    if operation not in {"create", "update", "deactivate"}:
        operation = "create"
    nodes = []
    key_map: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    for raw in raw_nodes[:12]:
        if not isinstance(raw, dict):
            continue
        node_type = str(raw.get("type") or "fact").strip().lower()
        key = str(raw.get("key") or raw.get("label") or "").strip()
        label = normalize_fact_label(str(raw.get("label") or raw.get("value") or ""), node_type)
        if key.casefold() == "user" or label.casefold() in {"user", "пользователь"}:
            key_map[key or "user"] = "user"
            continue
        if not label:
            continue
        if node_type not in allowed_types:
            node_type = "fact"
        node_key = key if key and key.casefold() != "user" else f"{node_type}:{label}"
        dedupe = (node_type, label.casefold())
        if dedupe in seen:
            continue
        seen.add(dedupe)
        key_map[key or label] = node_key
        nodes.append({"key": node_key, "type": node_type, "label": label[:300], "confidence": max(0.0, min(1.0, float(raw.get("confidence") or 0.75)))})
    if not nodes:
        return None
    edges = []
    if isinstance(raw_edges, list):
        for raw in raw_edges[:20]:
            if not isinstance(raw, dict):
                continue
            target_raw = str(raw.get("target") or "").strip()
            target = key_map.get(target_raw) or key_map.get(str(raw.get("value") or "").strip())
            if not target or target == "user":
                continue
            relation = str(raw.get("relation") or "связан с").strip()[:80]
            edges.append({"source": "user", "target": target, "relation": relation, "confidence": max(0.0, min(1.0, float(raw.get("confidence") or 0.75)))})
    if not edges:
        relation_by_type = {
            "profile": "имеет профиль",
            "age": "возраст",
            "occupation": "занимается",
            "education": "учится",
            "interest": "интересуется",
            "skill": "знает",
            "goal": "цель",
            "project": "делает",
            "organization": "связан с",
            "place": "связан с",
        }
        edges = [{"source": "user", "target": node["key"], "relation": relation_by_type.get(node["type"], "связан с"), "confidence": node.get("confidence", 0.75)} for node in nodes]
    summary = " · ".join(f"{node['type']}: {node['label']}" for node in nodes[:4])
    if len(nodes) > 4:
        summary += f" · еще {len(nodes) - 4}"
    return {
        "operation": operation,
        "fact_text": message.strip(),
        "proposed_nodes": nodes,
        "proposed_edges": [] if operation == "deactivate" else edges,
        "explanation": f"Вы сообщили: {message.strip()}",
        "title": "Добавить в Graph",
        "summary": summary,
    }

async def ai_candidate_change(message: str, ai: AIEngine) -> dict | None:
    try:
        graph = await ai.extract_graph(message)
    except Exception:
        logger.warning("AI structured extraction failed for candidate workflow")
        return None
    return graph_extraction_change(message, graph)


async def route_message(
    db: AsyncSession,
    user_id: UUID,
    message: str,
    ai: AIEngine,
    history: list[dict[str, str]] | None = None,
    *, use_graph: bool = True,
) -> MessageRoute:
    """Classify one user message in the only supported priority order."""
    local_answer = local_chat_answer(message)
    if local_answer:
        return MessageRoute("local", local_answer=local_answer)
    if not use_graph:
        return MessageRoute("general")
    # A recognized personal question can be answered from existing facts
    # without first asking the model to extract a new memory.
    personal_intent = deterministic_personal_intent(message)
    if personal_intent.kind == "personal_fact_query" and personal_intent.confidence >= HIGH_CONFIDENCE:
        direct_lookup = await direct_graph_lookup(db, user_id, message, ai, history)
        if direct_lookup:
            return MessageRoute("personal_query", direct_lookup=direct_lookup)
    # Memory classification is semantic and model-backed. Pattern helpers remain
    # validation/legacy utilities; they do not decide whether to write Graph.
    change = await ai_candidate_change(message, ai)
    if change:
        return MessageRoute("candidate", candidate_change=change)
    direct_lookup = await direct_graph_lookup(db, user_id, message, ai, history)
    if direct_lookup:
        return MessageRoute("personal_query", direct_lookup=direct_lookup)
    return MessageRoute("general")

async def create_graph_candidate(db: AsyncSession, user_id: UUID, thread_id: UUID, message_id: UUID, change: dict) -> dict:
    row = (await db.execute(text("""INSERT INTO graph_change_candidates(
      user_id,thread_id,message_id,action,operation,fact_text,proposed_nodes,proposed_edges,explanation,confidence,expires_at
    ) VALUES(:uid,:thread,:message,:action,:operation,:fact,CAST(:nodes AS jsonb),CAST(:edges AS jsonb),:explanation,:confidence,now()+interval '24 hours')
    RETURNING id,thread_id,message_id,action,operation,fact_text,proposed_nodes,proposed_edges,explanation,confidence,status,created_at,expires_at"""), {
        "uid": user_id,
        "thread": thread_id,
        "message": message_id,
        "action": change["operation"],
        "operation": change["operation"],
        "fact": change["fact_text"],
        "nodes": json.dumps(change["proposed_nodes"], ensure_ascii=False),
        "edges": json.dumps(change["proposed_edges"], ensure_ascii=False),
        "explanation": change["explanation"],
        "confidence": max((float(node.get("confidence") or 0.9) for node in change.get("proposed_nodes") or [{}]), default=0.9),
    })).mappings().one()
    return {**dict(row), "title": change["title"], "summary": change["summary"]}

def json_safe(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value

def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(json_safe(data), ensure_ascii=False)}\n\n"

async def ensure_thread(db: AsyncSession, user_id: UUID, thread_id: str | None, title: str | None = None) -> dict:
    if thread_id:
        try:
            requested_id = UUID(thread_id)
        except ValueError:
            raise HTTPException(400, "Некорректный chat thread id")
        row = (await db.execute(text("""SELECT id,title,pinned,created_at,updated_at,last_message_at
          FROM chat_threads WHERE id=:id AND user_id=:uid AND deleted_at IS NULL"""), {
            "id": requested_id,
            "uid": user_id,
        })).mappings().first()
        if not row:
            raise HTTPException(404, "Чат не найден")
        return dict(row)
    row = (await db.execute(text("""INSERT INTO chat_threads(user_id,title)
      VALUES(:uid,:title)
      RETURNING id,title,pinned,created_at,updated_at,last_message_at"""), {
        "uid": user_id,
        "title": (title or "Новый чат")[:160],
    })).mappings().one()
    return dict(row)

async def get_history(db: AsyncSession, user_id: UUID, thread_id: UUID, limit: int = 10) -> list[dict[str, str]]:
    thread = (await db.execute(text("SELECT conversation_summary,summarized_message_count FROM chat_threads WHERE id=:id AND user_id=:uid"), {"id":thread_id,"uid":user_id})).mappings().one()
    count = int(await db.scalar(text("SELECT count(*) FROM chat_messages WHERE thread_id=:id AND user_id=:uid"), {"id":thread_id,"uid":user_id}) or 0)
    summary = thread["conversation_summary"] or ""
    if count >= 30 and count - int(thread["summarized_message_count"] or 0) >= 20:
        older = (await db.execute(text("""SELECT role,content FROM chat_messages WHERE thread_id=:id AND user_id=:uid
          ORDER BY created_at DESC OFFSET :recent LIMIT 40"""), {"id":thread_id,"uid":user_id,"recent":limit})).mappings().all()
        summary = " | ".join(f"{row['role']}: {row['content'][:300]}" for row in reversed(older))[-6000:]
        await db.execute(text("UPDATE chat_threads SET conversation_summary=:summary,summarized_message_count=:count WHERE id=:id AND user_id=:uid"), {"summary":summary,"count":count-limit,"id":thread_id,"uid":user_id})
    rows = (await db.execute(text("""SELECT role,content FROM chat_messages
      WHERE user_id=:uid AND thread_id=:thread_id
      ORDER BY created_at DESC LIMIT :limit"""), {
        "uid": user_id,
        "thread_id": thread_id,
        "limit": limit,
    })).mappings().all()
    history = [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
    return ([{"role":"system","content":"Earlier conversation summary: " + summary}] if summary else []) + history

async def graph_context(db: AsyncSession, user_id: UUID, ai: AIEngine, query: str, limit: int = 8, profile: dict | None = None) -> list[dict]:
    started = time.monotonic()
    embed_started = time.monotonic()
    vector = await ai.embed(query, "RETRIEVAL_QUERY")
    if profile is not None:
        profile["embedding_generation_ms"] = elapsed_ms(embed_started)
    search_started = time.monotonic()
    rows = (await db.execute(text("""SELECT n.id,n.type,n.label,n.properties,n.source_text,n.provenance,
      n.confidence,n.version,n.lifecycle_status,n.created_at,n.updated_at,
      1-(e.embedding <=> CAST(:vec AS vector)) similarity
      FROM embeddings e JOIN nodes n ON n.id=e.node_id
      WHERE e.user_id=:uid AND n.lifecycle_status='active' AND n.confidence >= .40
      ORDER BY e.embedding::halfvec(3072) <=> CAST(:vec AS halfvec(3072)) LIMIT :limit"""), {
        "uid": user_id,
        "vec": str(vector),
        "limit": limit,
    })).mappings().all()
    if profile is not None:
        profile["graph_vector_search_ms"] = elapsed_ms(search_started)
        profile["graph_relations_fetch_ms"] = 0
    build_started = time.monotonic()
    result = [
        {
            **json_safe(dict(row)),
            "id": str(row["id"]),
            "confidence": float(row["confidence"]),
            "similarity": round(float(row["similarity"]), 3),
        }
        for row in rows
    ]
    relevant = [item for item in result if item["similarity"] >= GRAPH_SOURCE_THRESHOLD]
    strong = [item for item in relevant if item["similarity"] >= .72]
    selected = strong[:5] if len(strong) >= 3 else relevant[:5]
    if not selected:
        fallback_types = graph_query_types(query)
        if fallback_types:
            fallback_rows = (await db.execute(text("""SELECT id,type,label,properties,source_text,provenance,
              confidence,version,lifecycle_status,created_at,updated_at,
              1.0 similarity
              FROM nodes
              WHERE user_id=:uid AND lifecycle_status='active' AND confidence >= .40
                AND type = ANY(:types)
              ORDER BY confidence DESC, updated_at DESC
              LIMIT :limit"""), {
                "uid": user_id,
                "types": fallback_types,
                "limit": limit,
            })).mappings().all()
            selected = [
                {
                    **json_safe(dict(row)),
                    "id": str(row["id"]),
                    "confidence": float(row["confidence"]),
                    "similarity": round(float(row["similarity"]), 3),
                }
                for row in fallback_rows
            ]
    if profile is not None:
        profile["graph_context_build_ms"] = elapsed_ms(started)
        profile["graph_context_compact_ms"] = elapsed_ms(build_started)
    return selected

def graph_query_types(query: str) -> list[str]:
    intent = deterministic_personal_intent(query)
    category = intent.category if intent.kind == "personal_fact_query" and intent.confidence >= HIGH_CONFIDENCE else personal_query_category(query)
    if category == "name":
        return ["profile", "person", "fact"]
    if category == "age":
        return ["age", "fact"]
    if category in {"residence", "origin"}:
        return ["place", "location", "fact"]
    if category == "skills":
        return ["skill", "technology", "framework"]
    if category == "projects":
        return ["project"]
    if category == "interests":
        return ["interest", "idea"]
    if category == "occupation":
        return ["occupation", "organization"]
    if category == "education":
        return ["education", "organization"]
    if category == "goals":
        return ["goal"]
    lowered = query.casefold()
    if "где" in lowered and ("жив" in lowered or "нахож" in lowered):
        return ["place"]
    if "откуда" in lowered or "родом" in lowered:
        return ["place"]
    if "сколько" in lowered and ("лет" in lowered or "возраст" in lowered):
        return ["age"]
    if "как" in lowered and ("зовут" in lowered or "имя" in lowered):
        return ["profile", "person"]
    if "интерес" in lowered or "увлеч" in lowered:
        return ["interest"]
    if "проект" in lowered:
        return ["project"]
    if "язык" in lowered and ("программ" in lowered or "изуч" in lowered):
        return ["skill"]
    if "навык" in lowered or "уме" in lowered or "зна" in lowered:
        return ["skill"]
    if "что" in lowered and "обо мне" in lowered:
        return ["profile", "age", "occupation", "education", "place", "interest", "skill", "goal", "project", "organization"]
    return []

async def search_web(query: str, profile: dict | None = None) -> tuple[list[dict], str | None]:
    started = time.monotonic()
    try:
        results = [item for item in await get_search_engine().search(query, "ru", 8) if float(item.get("score") or 0) >= 0.2][:8]
        if profile is not None:
            profile["web_search_ms"] = elapsed_ms(started)
            profile["page_fetch_ms"] = 0
        return results, None
    except SearchUnavailable:
        if profile is not None:
            profile["web_search_ms"] = elapsed_ms(started)
            profile["page_fetch_ms"] = 0
        return [], "Интернет-поиск временно недоступен, но я могу ответить на основе Graph и общих знаний."

async def direct_graph_lookup(db: AsyncSession, user_id: UUID, query: str, ai: AIEngine | None = None, history: list[dict[str, str]] | None = None) -> dict | None:
    resolver = PersonalFactResolver(db, user_id)
    deterministic = deterministic_personal_intent(query)
    telemetry = {
        "deterministic_hit": deterministic.kind == "personal_fact_query" and deterministic.confidence >= HIGH_CONFIDENCE,
        "deterministic_kind": deterministic.kind,
        "deterministic_category": deterministic.category,
        "deterministic_confidence": deterministic.confidence,
        "ai_intent_fallback": False,
        "classified_kind": deterministic.kind,
        "classified_category": deterministic.category,
        "classification_confidence": deterministic.confidence,
        "graph_lookup_hit": False,
        "semantic_retrieval_hit": False,
    }
    category = None
    overview = deterministic.kind == "graph_overview" and deterministic.confidence >= HIGH_CONFIDENCE
    classification = None
    if telemetry["deterministic_hit"]:
        category = deterministic.category
    elif ai is not None and should_ai_intent_fallback(query):
        classification = await ai_personal_intent(ai, query, history)
        if classification:
            telemetry.update({
                "ai_intent_fallback": True,
                "classified_kind": classification["kind"],
                "classified_category": classification["category"],
                "classification_confidence": classification["confidence"],
                "classification_cache_hit": classification.get("cache_hit", False),
                "classification_usage": classification.get("usage") or {},
                "classification_timings": classification.get("timings") or {},
            })
            if (
                classification["kind"] == "personal_fact_query"
                and classification["category"] != "unknown"
                and classification["confidence"] >= AI_INTENT_CONFIDENCE_THRESHOLD
            ):
                category = classification["category"]
            elif classification["kind"] == "graph_overview" and classification["confidence"] >= AI_INTENT_CONFIDENCE_THRESHOLD:
                overview = True
    if not category and not overview:
        return None
    result = await resolver.answer_overview() if overview else await resolver.answer_category(category)
    if not result:
        telemetry["graph_lookup_hit"] = False
        return {
            "answer": "В вашем Graph пока нет подтвержденных данных." if overview else missing_personal_fact_answer(category),
            "sources": [],
            "category": "overview" if overview else category,
            "node_count": 0,
            "conflict": False,
            "telemetry": telemetry,
        }
    telemetry["graph_lookup_hit"] = True
    logger.info("personal_fact.direct_answer user_id=%s category=%s node_count=%s conflict=%s ai_fallback=%s",
        user_id, result.category, len(result.facts), result.conflict, telemetry["ai_intent_fallback"])
    return {
        "answer": result.answer,
        "sources": result.sources,
        "category": result.category,
        "node_count": len(result.facts),
        "conflict": result.conflict,
        "telemetry": telemetry,
    }

async def direct_graph_answer(db: AsyncSession, user_id: UUID, query: str) -> str | None:
    lookup = await direct_graph_lookup(db, user_id, query)
    if lookup:
        return lookup["answer"]
    lowered = query.casefold()
    if "где" in lowered and ("жив" in lowered or "нахож" in lowered):
        row = (await db.execute(text("""SELECT label FROM nodes
          WHERE user_id=:uid AND type='place' AND lifecycle_status='active'
          ORDER BY confidence DESC, updated_at DESC LIMIT 1"""), {"uid": user_id})).mappings().first()
        return f"Ваше место проживания: {row['label']}." if row else "В Lurg Graph пока нет подтвержденного места проживания."
    if "сколько" in lowered and ("лет" in lowered or "возраст" in lowered):
        row = (await db.execute(text("""SELECT label FROM nodes
          WHERE user_id=:uid AND type='age' AND lifecycle_status='active'
          ORDER BY confidence DESC, updated_at DESC LIMIT 1"""), {"uid": user_id})).mappings().first()
        return f"Вам {row['label']}." if row else "В Lurg Graph пока нет подтвержденного возраста."
    if "как" in lowered and ("зовут" in lowered or "имя" in lowered):
        row = (await db.execute(text("""SELECT label FROM nodes
          WHERE user_id=:uid AND type='profile' AND lifecycle_status='active'
          ORDER BY confidence DESC, updated_at DESC LIMIT 1"""), {"uid": user_id})).mappings().first()
        return f"Вас зовут {row['label']}." if row else "В Lurg Graph пока нет подтвержденного имени."
    if "интерес" in lowered or "увлеч" in lowered:
        labels = (await db.execute(text("""SELECT label FROM nodes
          WHERE user_id=:uid AND type='interest' AND lifecycle_status='active'
          ORDER BY confidence DESC, updated_at DESC LIMIT 20"""), {"uid": user_id})).scalars().all()
        return "Ваши интересы: " + ", ".join(labels) + "." if labels else "В Lurg Graph пока нет подтвержденных интересов."
    if "проект" in lowered and ("над каким" in lowered or "каким" in lowered or "работ" in lowered):
        labels = (await db.execute(text("""SELECT label FROM nodes
          WHERE user_id=:uid AND type='project' AND lifecycle_status='active'
          ORDER BY confidence DESC, updated_at DESC LIMIT 10"""), {"uid": user_id})).scalars().all()
        return "Вы работаете над проектами: " + ", ".join(labels) + "." if labels else "В Lurg Graph пока нет подтвержденных проектов."
    if "язык" in lowered and ("программ" in lowered or "изуч" in lowered):
        labels = (await db.execute(text("""SELECT label FROM nodes
          WHERE user_id=:uid AND type='skill' AND lifecycle_status='active'
          ORDER BY confidence DESC, updated_at DESC LIMIT 20"""), {"uid": user_id})).scalars().all()
        return "Языки и навыки из вашего Graph: " + ", ".join(labels) + "." if labels else "В Lurg Graph пока нет подтвержденных языков программирования."
    if "что" in lowered and "обо мне" in lowered:
        rows = (await db.execute(text("""SELECT type,label FROM nodes
          WHERE user_id=:uid AND lifecycle_status='active' AND type <> 'person'
          ORDER BY confidence DESC, updated_at DESC LIMIT 20"""), {"uid": user_id})).mappings().all()
        if not rows:
            return "В Lurg Graph пока нет подтвержденных фактов о вас."
        facts = "; ".join(f"{row['type']}: {row['label']}" for row in rows)
        return f"В вашем Graph есть такие факты: {facts}."
    if "сколько" in lowered and "проект" in lowered:
        count = int(await db.scalar(text("SELECT count(*) FROM nodes WHERE user_id=:uid AND type='project' AND lifecycle_status='active'"), {"uid":user_id}) or 0)
        return f"В вашем Lurg Graph проектов: {count}."
    if ("покажи" in lowered or "какие" in lowered) and "навык" in lowered:
        labels = (await db.execute(text("SELECT label FROM nodes WHERE user_id=:uid AND type='skill' AND lifecycle_status='active' ORDER BY confidence DESC,updated_at DESC LIMIT 20"), {"uid":user_id})).scalars().all()
        return "Ваши навыки: " + ", ".join(labels) + "." if labels else "В Lurg Graph пока нет подтвержденных навыков."
    if "какие" in lowered and "сервис" in lowered and ("подключ" in lowered or "доступ" in lowered):
        rows = (await db.execute(text("""SELECT provider,status,updated_at FROM external_connections
          WHERE user_id=:uid ORDER BY updated_at DESC LIMIT 20"""), {"uid": user_id})).mappings().all()
        if not rows:
            return "Подключенных внешних сервисов пока нет."
        return "Подключенные сервисы: " + ", ".join(f"{row['provider']} ({row['status']})" for row in rows) + "."
    if "когда" in lowered and ("добавлен" in lowered or "добавлена" in lowered) and "факт" in lowered:
        row = (await db.execute(text("""SELECT label,created_at FROM nodes
          WHERE user_id=:uid AND lifecycle_status='active'
          ORDER BY created_at DESC LIMIT 1"""), {"uid": user_id})).mappings().first()
        return f"Последний подтвержденный факт: «{row['label']}», добавлен {row['created_at'].isoformat()}." if row else "В Lurg Graph пока нет подтвержденных фактов."
    return None

async def chat_preferences(db: AsyncSession, user_id: UUID) -> tuple[bool, bool]:
    prefs = await db.scalar(text("SELECT COALESCE(preferences,'{}'::jsonb) FROM users WHERE id=:uid"), {"uid": user_id}) or {}
    return bool(prefs.get("auto_use_graph", True)), bool(prefs.get("allow_web_search", True))

async def latest_pending_candidate(db: AsyncSession, user_id: UUID, thread_id: UUID) -> dict | None:
    row = (await db.execute(text("""SELECT id,user_id,thread_id,message_id,action,operation,fact_text,
      proposed_nodes,proposed_edges,explanation,confidence,status,created_at,expires_at
      FROM graph_change_candidates
      WHERE user_id=:uid AND thread_id=:thread AND status='pending'
        AND (expires_at IS NULL OR expires_at > now())
      ORDER BY created_at DESC LIMIT 1"""), {"uid": user_id, "thread": thread_id})).mappings().first()
    return dict(row) if row else None

async def latest_pending_candidate_any_thread(db: AsyncSession, user_id: UUID) -> dict | None:
    row = (await db.execute(text("""SELECT id,user_id,thread_id,message_id,action,operation,fact_text,
      proposed_nodes,proposed_edges,explanation,confidence,status,created_at,expires_at
      FROM graph_change_candidates
      WHERE user_id=:uid AND status='pending'
        AND (expires_at IS NULL OR expires_at > now())
      ORDER BY created_at DESC LIMIT 1"""), {"uid": user_id})).mappings().first()
    return dict(row) if row else None

async def profile_state(db: AsyncSession, user_id: UUID) -> dict:
    row = (await db.execute(text("""SELECT u.id,u.created_at,u.name,
      COALESCE(u.onboarding_completed,false) onboarding_completed,
      (SELECT count(*) FROM nodes n WHERE n.user_id=u.id AND n.lifecycle_status='active') graph_node_count
      FROM users u WHERE u.id=:uid"""), {"uid": user_id})).mappings().one()
    graph_node_count = int(row["graph_node_count"] or 0)
    onboarding_completed = bool(row["onboarding_completed"])
    graph_initialized = graph_node_count > 0
    onboarding_required = (not onboarding_completed) and (not graph_initialized) and graph_node_count == 0
    return {
        "user_id": str(row["id"]),
        "account_created_at": row["created_at"].isoformat(),
        "display_name": row["name"],
        "onboarding_completed": onboarding_completed or graph_initialized,
        "graph_initialized": graph_initialized,
        "graph_node_count": graph_node_count,
        "onboarding_required": onboarding_required,
        "user_state": "new" if onboarding_required else "returning",
    }

async def idempotent_initial_message(db: AsyncSession, user_id: UUID, thread_id: UUID, content: str, state_version: str) -> dict:
    key = f"chat_bootstrap:{user_id}:{thread_id}:{state_version}"
    existing = (await db.execute(text("""SELECT id,role,content,sources,metadata,created_at
      FROM chat_messages
      WHERE user_id=:uid AND thread_id=:thread AND metadata->>'idempotency_key'=:key
      ORDER BY created_at ASC LIMIT 1"""), {"uid": user_id, "thread": thread_id, "key": key})).mappings().first()
    if existing:
        return dict(existing)
    return await save_message(db, user_id, thread_id, "assistant", content, ["graph" if state_version == "returning" else "general"], {"idempotency_key": key, "bootstrap": True, "state_version": state_version})

async def save_usage_event(db: AsyncSession, user_id: UUID, thread_id: UUID, message_id: UUID, telemetry: dict) -> None:
    usage = telemetry.get("usage") or {}
    await db.execute(text("""INSERT INTO ai_usage_events(
      user_id,thread_id,message_id,provider,model,mode,input_tokens,output_tokens,reasoning_tokens,
      latency_ms,first_token_latency_ms,cache_hit,cost,cost_estimated,metadata
    ) VALUES(:uid,:thread,:message,:provider,:model,:mode,:input_tokens,:output_tokens,:reasoning_tokens,
      :latency,:first_token_latency,:cache_hit,:cost,:cost_estimated,CAST(:metadata AS jsonb))"""), {
        "uid": user_id,
        "thread": thread_id,
        "message": message_id,
        "provider": telemetry.get("provider"),
        "model": telemetry.get("model"),
        "mode": telemetry.get("mode"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        # Gemini and providers without a final usage frame legitimately omit
        # this field. The persisted column is NOT NULL, so bind zero explicitly.
        "reasoning_tokens": usage.get("reasoning_tokens") or 0,
        "latency": telemetry.get("total_latency_ms"),
        "first_token_latency": telemetry.get("first_token_latency_ms"),
        "cache_hit": bool(telemetry.get("cache_hit")),
        "cost": usage.get("cost"),
        "cost_estimated": bool(usage.get("cost_estimated", True)),
        "metadata": json.dumps({
            "graph_nodes_used": telemetry.get("graph_nodes_used"),
            "internet_results_used": telemetry.get("internet_results_used"),
            "profile": telemetry.get("profile") or {},
        }, ensure_ascii=False),
    })

def compact_graph_context(nodes: list[dict]) -> list[dict]:
    return [{
        "id": item["id"], "type": item["type"], "value": item["label"],
        "date": item["created_at"], "confidence": item["confidence"],
        "source": (item.get("provenance") or {}).get("source_type") or (item.get("provenance") or {}).get("origin"),
    } for item in nodes]

async def save_message(
    db: AsyncSession,
    user_id: UUID,
    thread_id: UUID,
    role: str,
    content: str,
    sources: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    row = (await db.execute(text("""INSERT INTO chat_messages(user_id,thread_id,role,content,sources,metadata)
      VALUES(:uid,:thread_id,:role,:content,:sources,CAST(:metadata AS jsonb))
      RETURNING id,role,content,sources,metadata,created_at"""), {
        "uid": user_id,
        "thread_id": thread_id,
        "role": role,
        "content": content,
        "sources": sources or [],
        "metadata": json.dumps(metadata or {}, ensure_ascii=False),
    })).mappings().one()
    await db.execute(text("""UPDATE chat_threads
      SET last_message_at=now(), updated_at=now(),
        title=CASE WHEN title='Новый чат' AND :role='user' THEN left(:content, 80) ELSE title END
      WHERE id=:thread_id AND user_id=:uid"""), {
        "uid": user_id,
        "thread_id": thread_id,
        "role": role,
        "content": content,
    })
    return dict(row)

@router.post("/threads", status_code=201)
async def create_thread(body: ChatCreate, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    thread = await ensure_thread(db, user_id, None, body.title)
    await db.commit()
    return json_safe(thread)

@router.get("/threads")
async def list_threads(q: str | None = None, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(text("""SELECT t.id,t.title,t.pinned,t.created_at,t.updated_at,t.last_message_at,
      (SELECT content FROM chat_messages m WHERE m.thread_id=t.id ORDER BY created_at DESC LIMIT 1) last_message
      FROM chat_threads t
      WHERE t.user_id=:uid AND t.deleted_at IS NULL
        AND (CAST(:q AS text) IS NULL OR t.title ILIKE '%' || CAST(:q AS text) || '%'
          OR EXISTS(SELECT 1 FROM chat_messages m WHERE m.thread_id=t.id AND m.content ILIKE '%' || CAST(:q AS text) || '%'))
      ORDER BY t.pinned DESC, COALESCE(t.last_message_at,t.created_at) DESC
      LIMIT 80"""), {"uid": user_id, "q": q})).mappings().all()
    return {"threads": [json_safe(dict(row)) for row in rows]}

@router.get("/threads/{thread_id}")
async def get_thread(thread_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    thread = await ensure_thread(db, user_id, str(thread_id))
    rows = (await db.execute(text("""SELECT id,role,content,sources,metadata,created_at
      FROM chat_messages WHERE user_id=:uid AND thread_id=:thread_id ORDER BY created_at"""), {
        "uid": user_id,
        "thread_id": thread_id,
    })).mappings().all()
    return {"thread": json_safe(thread), "messages": [json_safe(dict(row)) for row in rows]}

@router.get("/bootstrap")
async def chat_bootstrap(thread_id: UUID | None = None, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    thread = await ensure_thread(db, user_id, str(thread_id) if thread_id else None, "Lurg")
    state = await profile_state(db, user_id)
    if state["onboarding_required"]:
        initial = "Привет. Я Lurg. Чтобы создать твой начальный Graph, расскажи немного о себе: чем занимаешься, что тебе интересно, какие у тебя цели?"
    else:
        initial = f"С возвращением, {state['display_name'] or 'рад вас видеть'}. Чем помочь?"
    saved = await idempotent_initial_message(db, user_id, thread["id"], initial, state["user_state"])
    pending = await latest_pending_candidate(db, user_id, thread["id"]) or await latest_pending_candidate_any_thread(db, user_id)
    await db.commit()
    return {
        "thread": json_safe(thread),
        "user_state": state["user_state"],
        "onboarding_required": state["onboarding_required"],
        "initial_message": json_safe(saved),
        "pending_graph_change": candidate_event(pending) if pending else None,
        "profile_status": state,
    }

@router.patch("/threads/{thread_id}")
async def update_thread(thread_id: UUID, body: ChatUpdate, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await ensure_thread(db, user_id, str(thread_id))
    row = (await db.execute(text("""UPDATE chat_threads
      SET title=COALESCE(:title,title), pinned=COALESCE(:pinned,pinned), updated_at=now()
      WHERE id=:thread_id AND user_id=:uid AND deleted_at IS NULL
      RETURNING id,title,pinned,created_at,updated_at,last_message_at"""), {
        "uid": user_id,
        "thread_id": thread_id,
        "title": body.title[:160] if body.title else None,
        "pinned": body.pinned,
    })).mappings().one()
    await db.commit()
    return json_safe(dict(row))

@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await ensure_thread(db, user_id, str(thread_id))
    await db.execute(text("UPDATE chat_threads SET deleted_at=now(), updated_at=now() WHERE id=:thread_id AND user_id=:uid"), {
        "uid": user_id,
        "thread_id": thread_id,
    })
    await db.commit()

@router.post("/message")
async def post_message(
    body: ChatMessageCreate,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    ai: AIEngine = Depends(get_ai_engine),
):
    thread = await ensure_thread(db, user_id, body.thread_id, body.message)
    auto_graph, allow_web = await chat_preferences(db, user_id)
    effective_use_graph = body.use_graph if body.use_graph is not None else auto_graph
    history = await get_history(db, user_id, thread["id"])
    user_message = await save_message(db, user_id, thread["id"], "user", body.message)
    pending = await latest_pending_candidate(db, user_id, thread["id"]) if effective_use_graph else None
    normalized_message = normalize_query(body.message)
    if pending and CONFIRM_PATTERNS.match(normalized_message):
        candidate = await load_pending_candidate(db, user_id, pending["id"])
        graph_change = await apply_graph_candidate(candidate, user_id, db, ai)
        assistant = await save_message(db, user_id, thread["id"], "assistant", "Информация сохранена.", ["graph"], {"mode": "graph", "graph_change": graph_change})
        await db.commit()
        return {"thread": json_safe(thread), "message": json_safe(assistant), "graph_change": graph_change, "sources": [], "history_used": len(history), "mode": "graph", "warning": None}
    if pending and REJECT_PATTERNS.match(normalized_message):
        await db.execute(text("UPDATE graph_change_candidates SET status='rejected' WHERE id=:id AND user_id=:uid"), {"id": pending["id"], "uid": user_id})
        assistant = await save_message(db, user_id, thread["id"], "assistant", "Изменение Graph отменено.", ["graph"], {"mode": "graph", "graph_change": {"status": "rejected", "candidate_id": str(pending["id"])}})
        await db.commit()
        return {"thread": json_safe(thread), "message": json_safe(assistant), "sources": [], "history_used": len(history), "mode": "graph", "warning": None}
    if effective_use_graph and not pending and MISSING_PENDING_CONFIRM_PATTERNS.match(normalized_message):
        assistant = await save_message(db, user_id, thread["id"], "assistant", "Нет ожидающего подтверждения изменения Graph.", ["graph"], {"mode": "graph", "graph_change": {"status": "missing_pending"}})
        await db.commit()
        return {"thread": json_safe(thread), "message": json_safe(assistant), "sources": [], "history_used": len(history), "mode": "graph", "warning": None}
    route = await route_message(db, user_id, body.message, ai, history, use_graph=effective_use_graph)
    if route.kind == "candidate":
        candidate = await create_graph_candidate(db, user_id, thread["id"], user_message["id"], route.candidate_change)
        await db.commit()
        return {"thread": json_safe(thread), "message": None, "graph_change_candidate": candidate_event(candidate), "sources": [], "history_used": len(history), "mode": "graph_candidate", "warning": None}
    if route.kind == "local":
        assistant = await save_message(db, user_id, thread["id"], "assistant", route.local_answer, ["general"], {"mode": "local", "ai_used": False})
        await db.commit()
        return {"thread": json_safe(thread), "message": json_safe(assistant), "sources": [], "history_used": len(history), "mode": "local", "warning": None}
    if route.kind == "personal_query":
        direct_lookup = route.direct_lookup
        assistant = await save_message(db, user_id, thread["id"], "assistant", direct_lookup["answer"], ["graph"], {"sources": direct_lookup["sources"], "mode": "graph", "ai_used": False, "direct_lookup": direct_lookup})
        if (direct_lookup.get("telemetry") or {}).get("ai_intent_fallback"):
            await save_usage_event(db, user_id, thread["id"], assistant["id"], ai_intent_usage_telemetry(ai, direct_lookup))
        await db.commit()
        return {"thread": json_safe(thread), "message": json_safe(assistant), "sources": direct_lookup["sources"], "history_used": len(history), "mode": "graph", "warning": None}
    mode = classify_intent(
        body.message,
        body.use_graph if body.use_graph is not None else auto_graph,
        body.use_internet if body.use_internet is not None else allow_web,
    )
    context = await graph_context(db, user_id, ai, body.message) if mode in {"graph", "hybrid"} else []
    web, warning = await search_web(body.message) if mode in {"web", "hybrid"} else ([], None)
    response_chunks = []
    async with aclosing(ai.stream_answer(body.message, {"graph": compact_graph_context(context), "web": web}, history, mode=mode, complex_query=is_complex(body.message))) as stream:
        async for chunk in stream:
            response_chunks.append(chunk)
    if not "".join(response_chunks).strip():
        raise HTTPException(502, "ИИ вернул пустой ответ. Попробуйте ещё раз.")
    source_kinds = (["graph"] if context else []) + (["internet"] if web else []) or ["general"]
    metadata_sources = graph_sources(context) + web_sources(web)
    assistant = await save_message(db, user_id, thread["id"], "assistant", "".join(response_chunks), source_kinds, {"sources": metadata_sources, "mode": mode, "warning": warning})
    await db.commit()
    return {"thread": json_safe(thread), "message": json_safe(assistant), "sources": metadata_sources, "history_used": len(history), "mode": mode, "warning": warning}

STREAM_HEARTBEAT_SECONDS = 5.0
STREAM_TIMEOUT_SECONDS = 150.0


def stream_error(exc: Exception) -> dict:
    # Keep provider bodies, URLs, SQL values and exception text out of the wire.
    if isinstance(exc, TimeoutError):
        message, status = "ИИ не успел ответить. Попробуйте ещё раз.", 504
    else:
        status = exc.status_code if isinstance(exc, HTTPException) else 502
        safe_details = {
            "ИИ временно недоступен. Проверьте конфигурацию AI-провайдера.",
            "На сервере закончился баланс AI-провайдера.",
            "Слишком много запросов к ИИ. Попробуйте немного позже.",
            "ИИ временно недоступен. Попробуйте ещё раз.",
            "Ответ ИИ прервался. Попробуйте ещё раз.",
            "ИИ вернул пустой ответ. Попробуйте ещё раз.",
            "ИИ не смог сформировать ответ. Попробуйте изменить запрос.",
        }
        detail = getattr(exc, "detail", None)
        message = detail if isinstance(detail, str) and detail in safe_details else "ИИ временно недоступен. Попробуйте ещё раз."
    return {"message": message, "status_code": status, "saved": False, "state": "failed"}


async def live_sse(source):
    """One bounded producer preserves provider ContextVars and cancellation.

    Waiting for a token never blocks heartbeat delivery. A queue of one frame
    applies backpressure, rather than collecting an entire answer in memory.
    """
    queue = asyncio.Queue(maxsize=1)

    async def produce():
        try:
            async with aclosing(source):
                async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
                    async for frame in source:
                        await queue.put(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("chat.stream failed error_class=%s", type(exc).__name__)
            await queue.put(sse("error", stream_error(exc)))
        await queue.put(None)

    task = asyncio.create_task(produce())
    try:
        while True:
            try:
                frame = await asyncio.wait_for(queue.get(), STREAM_HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if frame is None:
                return
            yield frame
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@router.post("/stream")
async def stream_message(
    body: ChatMessageCreate,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    ai: AIEngine = Depends(get_ai_engine),
):
    started = time.monotonic()
    started_at = datetime.now(timezone.utc)
    profile: dict[str, int] = {
        "intent_classification_ms": 0, "embedding_generation_ms": 0,
        "graph_vector_search_ms": 0, "graph_relations_fetch_ms": 0,
        "graph_context_build_ms": 0, "web_search_ms": 0, "page_fetch_ms": 0,
        "openrouter_connection_ms": 0, "openrouter_first_token_ms": 0, "total_ms": 0,
    }
    # Authentication, thread ownership and the user message are resolved before
    # returning HTTP 200. Provider work then runs inside the live SSE response.
    thread = await ensure_thread(db, user_id, body.thread_id, body.message)
    auto_graph, allow_web = await chat_preferences(db, user_id)
    effective_use_graph = body.use_graph if body.use_graph is not None else auto_graph
    effective_use_internet = body.use_internet if body.use_internet is not None else allow_web
    pending = await latest_pending_candidate(db, user_id, thread["id"]) if effective_use_graph else None
    history = await get_history(db, user_id, thread["id"])
    user_message = await save_message(db, user_id, thread["id"], "user", body.message)
    await db.commit()

    async def events():
        yield sse("thread", {"thread": thread})
        yield sse("assistant_start", {"sources": [], "source_kinds": [], "mode": "preparing", "state": "preparing"})
        normalized = normalize_query(body.message)
        async with Session() as session:
            await ensure_thread(session, user_id, str(thread["id"]))
            result = None
            direct_lookup = None
            if effective_use_graph and not pending and MISSING_PENDING_CONFIRM_PATTERNS.match(normalized):
                answer, mode, source_kinds = "Нет ожидающего подтверждения изменения Graph.", "graph", ["graph"]
                result = {"status": "missing_pending"}
                metadata = {"mode": mode, "graph_change": result}
                source_metadata = []
            elif pending and CONFIRM_PATTERNS.match(normalized):
                candidate = await load_pending_candidate(session, user_id, pending["id"])
                result = await apply_graph_candidate(candidate, user_id, session, ai)
                answer, mode, source_kinds = "Информация сохранена.", "graph", ["graph"]
                metadata = {"mode": mode, "graph_change": result}
                source_metadata = []
            elif pending and REJECT_PATTERNS.match(normalized):
                await load_pending_candidate(session, user_id, pending["id"])
                await session.execute(text("UPDATE graph_change_candidates SET status='rejected' WHERE id=:id AND user_id=:uid"), {"id": pending["id"], "uid": user_id})
                result = {"status": "rejected", "candidate_id": str(pending["id"])}
                answer, mode, source_kinds = "Изменение Graph отменено.", "graph", ["graph"]
                metadata = {"mode": mode, "graph_change": result}
                source_metadata = []
            elif pending and EDIT_PATTERNS.match(normalized):
                yield sse("error", {"message": "Изменение candidate пока доступно только через карточку подтверждения.", "saved": False, "state": "failed"})
                return
            else:
                route = await route_message(session, user_id, body.message, ai, history, use_graph=effective_use_graph)
                if route.kind == "candidate":
                    candidate_payload = await create_graph_candidate(session, user_id, thread["id"], user_message["id"], route.candidate_change)
                    await session.commit()
                    yield sse("graph_change_candidate", candidate_event(candidate_payload))
                    yield sse("done", {"sources": [], "source_kinds": ["graph"], "saved": False, "state": "completed"})
                    return
                if route.kind == "local":
                    answer, mode, source_kinds = route.local_answer, "local", ["general"]
                    metadata, source_metadata = {"mode": mode, "ai_used": False}, []
                elif route.kind == "personal_query":
                    direct_lookup = route.direct_lookup
                    answer, mode, source_kinds = direct_lookup["answer"], "graph", ["graph"]
                    source_metadata = direct_lookup["sources"]
                    metadata = {"mode": mode, "ai_used": False, "sources": source_metadata, "direct_lookup": direct_lookup}
                else:
                    answer = None
                    intent_started = time.monotonic()
                    mode = classify_intent(body.message, effective_use_graph, effective_use_internet)
                    profile["intent_classification_ms"] = elapsed_ms(intent_started)
                    graph_task = graph_context(session, user_id, ai, body.message, profile=profile) if mode in {"graph", "hybrid"} else asyncio.sleep(0, result=[])
                    web_task = search_web(body.message, profile) if mode in {"web", "hybrid"} else asyncio.sleep(0, result=([], None))
                    tasks = [asyncio.create_task(graph_task), asyncio.create_task(web_task)]
                    try:
                        context, (web, warning) = await asyncio.gather(*tasks)
                    except BaseException:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        raise
                    source_kinds = (["graph"] if context else []) + (["internet"] if web else []) or ["general"]
                    source_metadata = graph_sources(context) + web_sources(web)
            if answer is not None:
                saved = await save_message(session, user_id, thread["id"], "assistant", answer, source_kinds, metadata)
                if direct_lookup and (direct_lookup.get("telemetry") or {}).get("ai_intent_fallback"):
                    await save_usage_event(session, user_id, thread["id"], saved["id"], ai_intent_usage_telemetry(ai, direct_lookup))
                await session.commit()
            else:
                await session.commit()
        # Release the database connection before waiting for external model tokens.
        if answer is not None:
            profile["total_ms"] = elapsed_ms(started)
            yield sse("assistant_start", {"sources": source_metadata, "source_kinds": source_kinds, "mode": mode, "state": "waiting_for_first_token"})
            yield sse("token", {"text": answer})
            done = {"message_id": saved["id"], "sources": source_metadata, "source_kinds": source_kinds, "telemetry": {"mode": mode, "ai_used": False, "profile": profile}, "saved": True, "state": "completed"}
            if direct_lookup:
                done["telemetry"]["direct_lookup"] = direct_lookup
            if result is not None:
                done["graph_change"] = result
            yield sse("done", done)
            return
        yield sse("assistant_start", {"sources": source_metadata, "source_kinds": source_kinds, "mode": mode, "warning": warning, "state": "waiting_for_first_token"})
        chunks = []
        first_token_at = None
        async with aclosing(ai.stream_answer(body.message, {"graph": compact_graph_context(context), "web": web}, history, mode=mode, complex_query=is_complex(body.message))) as stream:
            async for chunk in stream:
                if first_token_at is None:
                    first_token_at = datetime.now(timezone.utc)
                chunks.append(chunk)
                yield sse("token", {"text": chunk})
        content = "".join(chunks)
        if not content.strip():
            raise HTTPException(502, "ИИ вернул пустой ответ. Попробуйте ещё раз.")
        async with Session() as session:
            await ensure_thread(session, user_id, str(thread["id"]))
            completed_at = datetime.now(timezone.utc)
            telemetry = {
                "provider": ai.provider_name, "model": ai.text_model, "mode": mode,
                "request_started_at": started_at.isoformat(), "first_token_at": first_token_at.isoformat() if first_token_at else None,
                "completed_at": completed_at.isoformat(),
                "first_token_latency_ms": round((first_token_at - started_at).total_seconds() * 1000) if first_token_at else None,
                "total_latency_ms": elapsed_ms(started),
                "graph_nodes_used": len(context), "internet_results_used": len(web),
                "cache_hit": any(item.get("cache_hit") for item in web),
                "usage": getattr(ai, "usage", lambda: {})(),
            }
            profile.update(getattr(ai, "timings", lambda: {})())
            profile["total_ms"] = telemetry["total_latency_ms"]
            telemetry["profile"] = profile
            saved = await save_message(session, user_id, thread["id"], "assistant", content, source_kinds, {"sources": source_metadata, "telemetry": telemetry, "warning": warning})
            await save_usage_event(session, user_id, thread["id"], saved["id"], telemetry)
            await session.commit()
        yield sse("done", {"message_id": saved["id"], "sources": source_metadata, "source_kinds": source_kinds, "telemetry": telemetry, "saved": True, "state": "completed"})

    return StreamingResponse(live_sse(events()), media_type="text/event-stream", headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"})

@router.post("/graph-changes/{candidate_id}")
async def decide_graph_change(candidate_id: UUID, body: GraphChangeDecision, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), ai: AIEngine = Depends(get_ai_engine)):
    candidate = await load_pending_candidate(db, user_id, candidate_id)
    if not body.confirmed:
        await db.execute(text("UPDATE graph_change_candidates SET status='rejected' WHERE id=:id AND user_id=:uid"), {"id": candidate_id, "uid": user_id})
        await db.commit(); return {"status": "rejected"}
    result = await apply_graph_candidate(candidate, user_id, db, ai)
    await db.commit()
    return result
