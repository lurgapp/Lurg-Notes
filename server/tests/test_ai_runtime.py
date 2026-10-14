import asyncio
import pytest
from fastapi import HTTPException

from app.openrouter import OpenRouterAIEngine, parse_sse_line, public_provider_error
from app.prompts import LURG_SYSTEM_PROMPT
from app.runtime import classify_intent, graph_sources, normalize_query, web_sources
from app.search import validate_public_url
from app.modules.chat.router import ai_candidate_change, candidate_change, candidate_event, canonical_entity_key, direct_graph_answer, direct_graph_lookup, graph_context, graph_extraction_change, local_chat_answer, personal_fact_lifetime, route_message
from app.modules.chat.personal_facts import deterministic_personal_intent, personal_query_category
from app.modules.graph.resolver import resolve_entity, strip_conversational_noise

def test_openrouter_streaming_parser_and_done():
    kind, payload = parse_sse_line('data: {"choices":[{"delta":{"content":"Hi"}}]}')
    assert kind == "data"
    assert payload["choices"][0]["delta"]["content"] == "Hi"
    assert parse_sse_line("data: [DONE]") == ("done", None)
    assert parse_sse_line("event: ping") == (None, None)
    assert parse_sse_line("data: broken") == (None, None)

@pytest.mark.parametrize("upstream, expected", [(401, 503), (402, 503), (429, 429), (500, 502)])
def test_openrouter_public_errors_do_not_leak_body(upstream, expected):
    error = public_provider_error(upstream)
    assert error.status_code == expected
    assert "trace" not in error.detail.lower()
    assert "openrouter" not in error.detail.lower()

def test_openrouter_structured_json_rejects_non_object(monkeypatch):
    class Response:
        is_error = False
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": '"free text"'}}]}

    class Client:
        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("app.openrouter.client", lambda: Client())
    with pytest.raises(HTTPException) as error:
        asyncio.run(OpenRouterAIEngine()._json("Return JSON", "text"))
    assert error.value.status_code == 502

def test_intent_modes():
    assert classify_intent("Расскажи короткую шутку") == "general"
    assert classify_intent("Какие у меня цели?") == "graph"
    assert classify_intent("Какие последние новости?") == "web"
    assert classify_intent("На основе моих навыков найди актуальные проекты") == "hybrid"

def test_personal_greeting_routes_to_graph_only():
    query = "Привет, что ты знаешь обо мне?"
    assert normalize_query(query) == "что ты знаешь обо мне?"
    assert classify_intent(query) == "graph"

def test_current_python_routes_to_web():
    assert classify_intent("Какая сейчас версия Python?") == "web"

def test_personal_current_recommendation_routes_to_hybrid():
    assert classify_intent("На основе моих навыков найди актуальные технологии") == "hybrid"

def test_explicit_overrides_are_request_scoped():
    assert classify_intent("Какая сейчас версия Python? Не используй интернет") == "general"
    assert classify_intent("Не используй мои данные, что сейчас с Python?") == "web"

def test_plain_memory_statement_creates_structured_candidate():
    change = candidate_change("Я изучаю Rust")
    assert change is not None
    assert change["operation"] == "create"
    assert change["proposed_nodes"][0]["type"] == "skill"
    assert change["proposed_nodes"][0]["label"] == "Rust"
    event = candidate_event({"id": "00000000-0000-0000-0000-000000000001", "message_id": "00000000-0000-0000-0000-000000000002", "confidence": 0.9, **change})
    assert event["candidate_id"] == "00000000-0000-0000-0000-000000000001"
    assert event["changes"][0]["value"] == "Rust"


def test_skill_statement_with_natural_reverse_word_order_creates_candidate():
    change = candidate_change("Я ещё Python изучаю, кстати")
    assert change is not None
    assert change["operation"] == "create"
    assert any(node["type"] == "skill" and node["label"] == "Python" for node in change["proposed_nodes"])
    assert any(edge["relation"] == "изучает" for edge in change["proposed_edges"])

def test_name_intro_creates_structured_candidate_before_llm():
    change = candidate_change("Меня зовут Миша")
    assert change is not None
    assert change["operation"] == "create"
    assert change["proposed_nodes"] == [{"key": "profile:Миша", "type": "profile", "label": "Миша", "confidence": 0.95}]
    assert change["proposed_edges"] == [{"source": "user", "target": "profile:Миша", "relation": "зовут", "confidence": 0.95}]
    event = candidate_event({"id": "00000000-0000-0000-0000-000000000001", "message_id": "00000000-0000-0000-0000-000000000002", "confidence": 0.95, **change})
    assert event["summary"] == "Профиль: Миша"
    assert event["changes"][0] == {"type": "profile", "value": "Миша", "relation": "зовут"}

@pytest.mark.parametrize("message,node_type,label,relation", [
    ("Моё имя Михаил", "profile", "Михаил", "зовут"),
    ("Мне 29 лет", "age", "29 лет", "возраст"),
    ("Я работаю backend engineer", "occupation", "backend engineer", "занимается"),
    ("Работаю в Яндексе", "organization", "Яндексе", "работает в"),
    ("Учусь в МГУ", "organization", "МГУ", "учится в"),
    ("Живу в Москве", "place", "Москва", "живет в"),
    ("Я из России", "place", "России", "из"),
    ("Интересуюсь AI и дизайном", "interest", "AI", "интересуется"),
    ("Умею Python, SQL и Rust", "skill", "Python", "знает"),
    ("Изучаю Kotlin", "skill", "Kotlin", "изучает"),
    ("Моя цель запустить продукт", "goal", "запустить продукт", "цель"),
    ("Планирую выучить английский", "goal", "выучить английский", "хочет"),
    ("Мой проект Lurg", "project", "Lurg", "делает"),
    ("Работаю над проектом Atlas", "project", "Atlas", "делает"),
])
def test_onboarding_phrases_create_structured_candidates(message, node_type, label, relation):
    change = candidate_change(message)
    assert change is not None
    assert change["operation"] == "create"
    assert any(node["type"] == node_type and node["label"] == label for node in change["proposed_nodes"])
    assert any(edge["relation"] == relation for edge in change["proposed_edges"])

@pytest.mark.parametrize("message,node_type,label,relation,operation", [
    ("Я Миша.", "profile", "Миша", "зовут", "create"),
    ("Все зовут меня Мишей.", "profile", "Миша", "зовут", "create"),
    ("Можно просто Миша.", "profile", "Миша", "зовут", "create"),
    ("Мне недавно исполнилось 29.", "age", "29 лет", "возраст", "update"),
    ("Через месяц будет 30.", "age", "30 лет", "возраст", "update"),
    ("Живу в Питере.", "place", "Санкт-Петербург", "живет в", "create"),
    ("Родом из Москвы.", "place", "Москва", "родом из", "create"),
    ("Сейчас живу в Санкт-Петербурге.", "place", "Санкт-Петербург", "живет в", "update"),
    ("Работаю backend разработчиком.", "occupation", "backend разработчиком", "занимается", "create"),
    ("Учусь на первом курсе.", "education", "на первом курсе", "учится", "create"),
    ("Сейчас изучаю Rust.", "skill", "Rust", "изучает", "update"),
    ("Очень люблю искусственный интеллект.", "interest", "искусственный интеллект", "любит", "create"),
    ("Последнее время увлекся авиацией.", "interest", "авиацией", "увлекается", "create"),
    ("Последнее время увлёкся авиацией.", "interest", "авиацией", "увлекается", "create"),
    ("Мне интересен AI.", "interest", "AI", "интересуется", "create"),
    ("Мечтаю создать большую IT-компанию.", "goal", "создать большую IT-компанию", "хочет", "create"),
    ("Делаю собственную экосистему.", "project", "экосистему", "делает", "create"),
    ("Работаю над проектом Lurg.", "project", "Lurg", "делает", "create"),
    ("Переехал в Казань.", "place", "Казань", "живет в", "update"),
])
def test_natural_onboarding_phrases_create_structured_candidates(message, node_type, label, relation, operation):
    change = candidate_change(message)
    assert change is not None
    assert change["operation"] == operation
    assert any(node["type"] == node_type and node["label"] == label for node in change["proposed_nodes"])
    assert any(edge["relation"] == relation for edge in change["proposed_edges"])

@pytest.mark.parametrize("message", [
    "Я из Питера кста",
    "Привет, я из Питера кста",
    "Живу в СПб",
    "Родом из Санкт-Петербурга",
    "Я вообще из Питера",
])
def test_natural_place_phrases_resolve_to_single_city_canonical_key(message):
    change = candidate_change(message)
    assert change is not None
    place = next(node for node in change["proposed_nodes"] if node["type"] == "place")
    resolved = resolve_entity("place", place["label"])
    assert place["label"] == "Санкт-Петербург"
    assert resolved["canonical_key"] == "санкт-петербург"

def test_conversational_noise_is_removed_before_fact_extraction():
    assert strip_conversational_noise("Питера кста )))") == "Питера"

def test_onboarding_message_can_create_multiple_candidate_facts():
    change = candidate_change("Меня зовут Миша, мне 29 лет, живу в Москве, интересуюсь AI и Rust")
    assert change is not None
    labels = {node["label"] for node in change["proposed_nodes"]}
    assert {"Миша", "29 лет", "Москва", "AI", "Rust"} <= labels

def test_natural_message_can_create_single_candidate_with_many_facts():
    change = candidate_change("Меня зовут Миша, мне 29 лет, живу в Санкт-Петербурге, изучаю Rust и Go, люблю авиацию, хочу создать компанию.")
    assert change is not None
    labels = {node["label"] for node in change["proposed_nodes"]}
    assert {"Миша", "29 лет", "Санкт-Петербург", "Rust", "Go", "авиацию", "создать компанию"} <= labels
    assert len(change["proposed_nodes"]) >= 7

@pytest.mark.parametrize("message,node_type,label", [
    ("Я Максим", "profile", "Максим"),
    ("Меня Максим зовут", "profile", "Максим"),
    ("Мне уже 25", "age", "25 лет"),
    ("Мне 25 годиков", "age", "25 лет"),
    ("Я пилот", "occupation", "пилот"),
    ("По профессии пилот", "occupation", "пилот"),
    ("Вообще я пилот", "occupation", "пилот"),
    ("Я backend разработчик", "occupation", "backend разработчик"),
    ("Люблю Rust", "interest", "Rust"),
    ("Я сейчас изучаю авиацию", "skill", "авиацию"),
    ("Работаю над Lurg", "project", "Lurg"),
    ("Делаю свою экосистему", "project", "экосистему"),
])
def test_requested_natural_fact_phrases_create_candidates(message, node_type, label):
    change = candidate_change(message)
    assert change is not None
    assert any(node["type"] == node_type and node["label"] == label for node in change["proposed_nodes"])

def test_grouped_onboarding_confirmation_contains_all_explicit_facts():
    change = candidate_change("Меня зовут Максим, я пилот, мне 25")
    assert change is not None
    assert {(node["type"], node["label"]) for node in change["proposed_nodes"]} >= {
        ("profile", "Максим"), ("occupation", "пилот"), ("age", "25 лет"),
    }

def test_message_route_uses_semantic_candidate_before_personal_resolver():
    class SemanticCandidateAi:
        provider_name = text_model = embedding_model = "fake"
        async def extract_graph(self, content):
            return {"memory_disposition": "graph_candidate", "nodes": [{"key": "occupation:pilot", "type": "occupation", "label": "пилот", "confidence": 0.9}], "edges": []}
    class NoDb:
        async def execute(self, *args, **kwargs):
            raise AssertionError("personal resolver must not run for a candidate")
    route = asyncio.run(route_message(NoDb(), "u1", "Я пилот", SemanticCandidateAi(), []))
    assert route.kind == "candidate"
    assert route.candidate_change["proposed_nodes"][0]["type"] == "occupation"

def test_message_route_runs_ai_extraction_before_personal_resolver():
    class CandidateAi:
        provider_name = text_model = embedding_model = "fake"
        async def extract_graph(self, content):
            return {"memory_disposition": "graph_candidate", "nodes": [{"key": "fact:family", "type": "fact", "label": "двое детей", "confidence": 0.9}], "edges": []}
    class NoDb:
        async def execute(self, *args, **kwargs):
            raise AssertionError("personal resolver must not run after AI found a candidate")
    route = asyncio.run(route_message(NoDb(), "u1", "У меня двое детей", CandidateAi(), []))
    assert route.kind == "candidate"
    assert route.candidate_change["proposed_nodes"][0]["label"] == "двое детей"


def test_semantic_temporary_disposition_never_creates_graph_candidate():
    graph = {"memory_disposition": "temporary", "operation": "create", "nodes": [{"key": "place:road", "type": "place", "label": "Софийская", "confidence": 0.9}], "edges": []}
    assert graph_extraction_change("Завтра еду через Софийскую", graph) is None

def test_onboarding_list_extraction_stops_before_next_fact_clause():
    change = candidate_change("Меня зовут Миша, мне 29 лет, живу в Москве, интересуюсь AI и Rust, моя цель запустить продукт")
    assert change is not None
    assert any(node["type"] == "goal" and node["label"] == "запустить продукт" for node in change["proposed_nodes"])
    assert not any(node["type"] == "interest" and "цель" in node["label"].casefold() for node in change["proposed_nodes"])

def test_workplace_phrase_does_not_create_fake_occupation():
    change = candidate_change("Работаю в Яндексе")
    assert change is not None
    assert any(node["type"] == "organization" and node["label"] == "Яндексе" for node in change["proposed_nodes"])
    assert not any(node["type"] == "occupation" and node["label"].casefold().startswith("в ") for node in change["proposed_nodes"])

def test_project_work_phrase_does_not_create_fake_occupation():
    change = candidate_change("Работаю над проектом Lurg")
    assert change is not None
    assert any(node["type"] == "project" and node["label"] == "Lurg" for node in change["proposed_nodes"])
    assert not any(node["type"] == "occupation" for node in change["proposed_nodes"])

@pytest.mark.parametrize("message,answer", [
    ("Привет", "Привет."),
    ("Спасибо", "Пожалуйста."),
    ("Пока", "До связи."),
    ("Ок", "Хорошо."),
])
def test_short_local_replies_do_not_need_ai(message, answer):
    assert local_chat_answer(message) == answer

def test_negative_skill_phrase_creates_deactivate_candidate():
    change = candidate_change("Я больше не изучаю Python.")
    assert change is not None
    assert change["operation"] == "deactivate"
    assert any(node["type"] == "skill" and node["label"] == "Python" for node in change["proposed_nodes"])
    assert change["proposed_edges"] == []


def test_delete_place_phrase_is_a_canonical_deactivation_candidate():
    change = candidate_change("Удали то, что я из Питера кста.")
    assert change is not None
    assert change["operation"] == "deactivate"
    assert change["proposed_nodes"] == [{
        "key": "place:Санкт-Петербург",
        "type": "place",
        "label": "Санкт-Петербург",
        "confidence": 0.95,
    }]
    assert change["proposed_edges"] == []

@pytest.mark.parametrize("message", [
    "Я устал.",
    "Я ем.",
    "Я посмотрел фильм.",
    "Сейчас смотрю видео.",
    "Я люблю пиццу.",
])
def test_short_term_or_low_value_preferences_do_not_create_candidates(message):
    assert personal_fact_lifetime(message) == "short_term"
    assert candidate_change(message) is None

def test_semantic_memory_classifier_handles_temporary_and_durable_facts():
    class FakeAi:
        provider_name = "fake"
        text_model = "fake"
        calls = 0
        async def extract_graph(self, content):
            self.calls += 1
            if "устал" in content.casefold():
                return {"memory_disposition": "conversation_only", "operation": "create", "nodes": [], "edges": []}
            return {
                "memory_disposition": "graph_candidate",
                "nodes": [{"key": "interest:neural", "type": "interest", "label": "нейросети", "confidence": 0.8}],
                "edges": [{"source": "user", "target": "interest:neural", "relation": "интересуется", "confidence": 0.8}],
            }

    short_ai = FakeAi()
    assert asyncio.run(ai_candidate_change("Я устал.", short_ai)) is None
    assert short_ai.calls == 1

    long_ai = FakeAi()
    change = asyncio.run(ai_candidate_change("В последнее время всё серьёзнее занимаюсь созданием собственного технологического проекта.", long_ai))
    assert change is not None
    assert long_ai.calls == 1

def test_entity_canonical_alias_foundation_without_model_change():
    assert canonical_entity_key("ИИ") == canonical_entity_key("AI")
    assert canonical_entity_key("AI") == canonical_entity_key("искусственный интеллект")
    ai_change = candidate_change("Интересуюсь AI")
    ru_change = candidate_change("Интересуюсь ИИ")
    assert ai_change["proposed_nodes"][0]["key"] == ru_change["proposed_nodes"][0]["key"]

def test_ai_structured_extraction_can_express_update_and_education():
    change = graph_extraction_change("Теперь учусь на втором курсе", {
        "operation": "update",
        "nodes": [
            {"key": "user", "type": "person", "label": "Пользователь"},
            {"key": "education:second", "type": "education", "label": "на втором курсе", "confidence": 0.82},
        ],
        "edges": [{"source": "user", "target": "education:second", "relation": "учится", "confidence": 0.82}],
    })
    assert change is not None
    assert change["operation"] == "update"
    assert change["proposed_nodes"][0]["type"] == "education"

def test_ai_structured_extraction_is_converted_to_candidate_workflow():
    change = graph_extraction_change("Люблю шахматы", {
        "nodes": [
            {"key": "user", "type": "person", "label": "Пользователь"},
            {"key": "interest:шахматы", "type": "interest", "label": "шахматы", "confidence": 0.74},
        ],
        "edges": [{"source": "user", "target": "interest:шахматы", "relation": "интересуется", "confidence": 0.74}],
    })
    assert change is not None
    assert change["proposed_nodes"][0]["label"] == "шахматы"
    assert change["proposed_edges"][0]["relation"] == "интересуется"
    event = candidate_event({"id": "00000000-0000-0000-0000-000000000001", "message_id": "00000000-0000-0000-0000-000000000002", "confidence": 0.74, **change})
    assert event["candidate_id"] == "00000000-0000-0000-0000-000000000001"

class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _Rows(self._rows)

    def scalars(self):
        values = [next(iter(row.values())) for row in self._rows]
        return _Rows(values)

class _GraphAnswerDb:
    def __init__(self, rows_by_type=None):
        uid = "00000000-0000-0000-0000-000000000001"
        self.rows_by_type = rows_by_type or {
            "place": [_node(user_id=uid, node_type="place", label="Санкт-Петербург")],
            "age": [_node(user_id=uid, node_type="age", label="29 лет")],
        }
        self.queries = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append(sql)
        if "n.type = ANY" in sql:
            types = set((params or {}).get("types") or [])
            rows = []
            for node_type in types:
                rows.extend(self.rows_by_type.get(node_type, []))
            return _Result(rows)
        if "type='place'" in sql:
            return _Result(self.rows_by_type.get("place", []))
        if "type='age'" in sql:
            return _Result(self.rows_by_type.get("age", []))
        return _Result([])

class _GraphContextDb:
    def __init__(self):
        self.calls = 0

    async def execute(self, statement, params=None):
        self.calls += 1
        sql = str(statement)
        if "FROM embeddings" in sql:
            return _Result([])
        if "FROM nodes" in sql and "type = ANY" in sql:
            return _Result([{
                "id": "00000000-0000-0000-0000-000000000010",
                "type": "place",
                "label": "Санкт-Петербург",
                "properties": {"canonical_key": "санкт-петербург"},
                "source_text": "Я из Питера",
                "provenance": {"source_type": "chat"},
                "confidence": 0.9,
                "version": 1,
                "lifecycle_status": "active",
                "created_at": "2026-07-13T00:00:00Z",
                "updated_at": "2026-07-13T00:00:00Z",
                "similarity": 1.0,
            }])
        return _Result([])

class _RetrievalAi:
    provider_name = "fake"
    text_model = "fake"
    embedding_model = "fake"

    async def embed(self, content, purpose):
        return [0.0, 0.0, 0.0]

class _IntentAi:
    provider_name = "fake"
    text_model = "fake"
    embedding_model = "fake"

    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.payloads = []

    async def classify_personal_query(self, content):
        self.calls += 1
        self.payloads.append(content)
        return self.response

    def usage(self):
        return {"input_tokens": 42, "output_tokens": 12, "reasoning_tokens": 0}

    def timings(self):
        return {"total_ms": 123}

class _PersonalFactsDb:
    def __init__(self, rows, user_rows=None):
        self.rows = rows
        self.user_rows = user_rows or []
        self.queries = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append((sql, params or {}))
        uid = (params or {}).get("uid")
        if "FROM users" in sql:
            return _Result([row for row in self.user_rows if row.get("id") == uid])
        rows = [row for row in self.rows if row.get("user_id") == uid and row.get("lifecycle_status") == "active"]
        if "n.type='age'" in sql:
            rows = [
                row for row in rows
                if row["type"] == "age"
                or row.get("properties", {}).get("fact_type") == "age"
                or re_match_age(row["label"])
            ]
        elif "n.type = ANY" in sql:
            types = set((params or {}).get("types") or [])
            relations = set((params or {}).get("relations") or [])
            if (params or {}).get("relation_scoped"):
                rows = [row for row in rows if row.get("relation") in relations and row.get("source_type") == "person"]
            else:
                rows = [
                    row for row in rows
                    if row["type"] in types or (row.get("relation") in relations and row.get("source_type") == "person")
                ]
        return _Result(rows)

def re_match_age(label):
    import re
    return bool(re.match(r"^\s*\d{1,3}\s*(лет|год|года)?\s*$", label, re.I))

def _node(user_id="u1", node_type="fact", label="14 лет", status="active", relation=None, confidence=0.9, node_id="n1"):
    return {
        "id": node_id,
        "user_id": user_id,
        "type": node_type,
        "label": label,
        "properties": {},
        "confidence": confidence,
        "version": 1,
        "lifecycle_status": status,
        "created_at": "2026-07-13T00:00:00Z",
        "updated_at": "2026-07-13T00:00:00Z",
        "relation": relation,
        "source_type": "person" if relation else None,
    }

def test_direct_graph_answer_uses_confirmed_place_without_llm():
    db = _GraphAnswerDb()
    answer = asyncio.run(direct_graph_answer(db, "00000000-0000-0000-0000-000000000001", "Где я живу?"))
    assert answer == "В вашем Graph указано место: Санкт-Петербург."
    assert any("lifecycle_status='active'" in query for query in db.queries)

def test_direct_graph_answer_uses_confirmed_age_without_llm():
    db = _GraphAnswerDb()
    answer = asyncio.run(direct_graph_answer(db, "00000000-0000-0000-0000-000000000001", "Сколько мне лет?"))
    assert answer == "В вашем Graph указан возраст: 29 лет."
    assert any("type='age'" in query and "lifecycle_status='active'" in query for query in db.queries)

def test_personal_query_mapping_is_stable_for_direct_questions():
    assert personal_query_category("Как меня зовут?") == "name"
    assert personal_query_category("Кто я?") == "name"
    assert personal_query_category("Сколько мне лет?") == "age"
    assert personal_query_category("Какой у меня возраст?") == "age"
    assert personal_query_category("Где я живу?") == "residence"
    assert personal_query_category("Откуда я?") == "origin"
    assert personal_query_category("Какие у меня навыки?") == "skills"
    assert personal_query_category("Какие проекты у меня есть?") == "projects"

@pytest.mark.parametrize("query,category", [
    ("Сколько мне лет?", "age"),
    ("Сколько лет мне?", "age"),
    ("Лет сколько мне?", "age"),
    ("А возраст у меня какой?", "age"),
    ("Возраст мой какой?", "age"),
    ("Напомни мой возраст.", "age"),
    ("Ты знаешь, сколько мне лет?", "age"),
    ("Мне сейчас сколько?", "age"),
    ("Я какого возраста?", "age"),
    ("Как меня зовут?", "name"),
    ("Имя моё какое?", "name"),
    ("Напомни моё имя.", "name"),
    ("Ты знаешь моё имя?", "name"),
    ("Кто я вообще?", "name"),
    ("Где я живу?", "residence"),
    ("Я где живу?", "residence"),
    ("Какой город у меня указан?", "residence"),
    ("Где мой дом, судя по Graph?", "residence"),
    ("Откуда я?", "origin"),
    ("Я откуда?", "origin"),
    ("Из какого я города?", "origin"),
    ("Какие у меня навыки?", "skills"),
    ("Что я умею?", "skills"),
    ("Что я изучаю?", "skills"),
    ("Какие технологии я знаю?", "skills"),
    ("В чём я разбираюсь?", "skills"),
    ("Какие у меня проекты?", "projects"),
    ("Над чем я работаю?", "projects"),
    ("Что я создаю?", "projects"),
    ("Какие продукты я делаю?", "projects"),
    ("Какие у меня интересы?", "interests"),
    ("Чем я интересуюсь?", "interests"),
    ("Какие у меня цели?", "goals"),
    ("О чём я мечтаю?", "goals"),
    ("Кем я работаю?", "occupation"),
    ("Чем я занимаюсь?", "occupation"),
    ("Где я учусь?", "education"),
    ("Какая у меня учёба?", "education"),
])
def test_deterministic_personal_intent_handles_natural_word_order(query, category):
    intent = deterministic_personal_intent(query)
    assert intent.kind == "personal_fact_query"
    assert intent.category == category
    assert intent.confidence >= 0.80

@pytest.mark.parametrize("query", [
    "Что ты знаешь обо мне?",
    "Расскажи обо мне.",
    "Что у тебя сохранено про меня?",
    "Какие данные обо мне есть в Graph?",
    "Что ты обо мне знаешь?",
    "Что есть в моём Graph?",
])
def test_deterministic_personal_intent_handles_overview(query):
    intent = deterministic_personal_intent(query)
    assert intent.kind == "graph_overview"
    assert intent.confidence >= 0.80

def test_personal_resolver_finds_age_stored_as_age_node():
    db = _PersonalFactsDb([_node(node_type="age", label="29 лет")])
    lookup = asyncio.run(direct_graph_lookup(db, "u1", "Сколько мне лет?"))
    assert lookup["answer"] == "В вашем Graph указан возраст: 29 лет."
    assert lookup["sources"][0]["node_id"] == "n1"

def test_graph_overview_is_built_by_backend_from_active_graph_nodes():
    db = _PersonalFactsDb([
        _node(node_type="age", label="25 лет", node_id="age"),
        _node(node_type="skill", label="Rust", node_id="rust"),
    ])
    lookup = asyncio.run(direct_graph_lookup(db, "u1", "Что есть в моём Graph?"))
    assert lookup["category"] == "overview"
    assert "Возраст\n• 25 лет" in lookup["answer"]
    assert "Навыки\n• Rust" in lookup["answer"]

def test_personal_resolver_finds_legacy_age_stored_as_fact_node():
    db = _PersonalFactsDb([_node(node_type="fact", label="14 лет")])
    lookup = asyncio.run(direct_graph_lookup(db, "u1", "Какой у меня возраст?"))
    assert lookup["answer"] == "В вашем Graph указан возраст: 14 лет."
    assert lookup["sources"] == [{
        "type": "graph",
        "source_kind": "graph",
        "node_id": "n1",
        "node_type": "fact",
        "label": "14 лет",
        "created_at": "2026-07-13T00:00:00Z",
        "updated_at": "2026-07-13T00:00:00Z",
        "confidence": 0.9,
    }]

def test_personal_resolver_finds_place_for_residence_and_origin_with_cautious_fallback():
    db = _PersonalFactsDb([_node(node_type="place", label="Питера кста", relation="из")])
    residence = asyncio.run(direct_graph_lookup(db, "u1", "Где я живу?"))
    origin = asyncio.run(direct_graph_lookup(db, "u1", "Откуда я?"))
    assert residence["answer"] == "В вашем Graph указано место: Санкт-Петербург."
    assert residence["sources"][0]["canonical_key"] == "санкт-петербург"
    assert origin["answer"] == "В вашем Graph указано происхождение: Санкт-Петербург."

def test_personal_resolver_separates_current_residence_from_origin():
    db = _PersonalFactsDb([
        _node(node_type="place", label="Питер", relation="из", node_id="origin"),
        _node(node_type="place", label="Москва", relation="живет в", node_id="residence"),
    ])
    residence = asyncio.run(direct_graph_lookup(db, "u1", "Где я живу?"))
    origin = asyncio.run(direct_graph_lookup(db, "u1", "Откуда я?"))
    assert residence["answer"] == "В вашем Graph указано место: Москва."
    assert origin["answer"] == "В вашем Graph указано происхождение: Санкт-Петербург."

def test_personal_resolver_finds_profile_name():
    db = _PersonalFactsDb([_node(node_type="profile", label="Миша")])
    lookup = asyncio.run(direct_graph_lookup(db, "u1", "Как меня зовут?"))
    assert lookup["answer"] == "В вашем Graph указано имя: Миша."

def test_personal_resolver_finds_identity_name_when_profile_node_is_missing():
    db = _PersonalFactsDb([], user_rows=[{"id": "u1", "name": "Михаил", "created_at": "2026-07-13T00:00:00Z"}])
    lookup = asyncio.run(direct_graph_lookup(db, "u1", "Как меня зовут?"))
    assert lookup["answer"] == "В вашем профиле указано имя: Михаил."
    assert lookup["sources"][0]["source_kind"] == "identity"

def test_personal_resolver_uses_only_active_values():
    db = _PersonalFactsDb([
        _node(node_type="age", label="13 лет", status="superseded", node_id="old"),
        _node(node_type="age", label="14 лет", node_id="new"),
    ])
    lookup = asyncio.run(direct_graph_lookup(db, "u1", "Сколько мне лет?"))
    assert lookup["answer"] == "В вашем Graph указан возраст: 14 лет."
    assert lookup["sources"][0]["node_id"] == "new"

def test_personal_resolver_reports_conflicting_active_singletons():
    db = _PersonalFactsDb([
        _node(node_type="age", label="14 лет", node_id="age1"),
        _node(node_type="age", label="29 лет", node_id="age2"),
    ])
    lookup = asyncio.run(direct_graph_lookup(db, "u1", "Сколько мне лет?"))
    assert lookup["conflict"] is True
    assert "несколько активных значений" in lookup["answer"]

def test_personal_resolver_preserves_owner_isolation():
    db = _PersonalFactsDb([
        _node(user_id="other", node_type="age", label="99 лет", node_id="other"),
        _node(user_id="u1", node_type="age", label="14 лет", node_id="own"),
    ])
    lookup = asyncio.run(direct_graph_lookup(db, "u1", "Сколько мне лет?"))
    assert lookup["sources"][0]["node_id"] == "own"

def test_deterministic_hit_does_not_call_ai_classifier():
    db = _PersonalFactsDb([_node(node_type="fact", label="14 лет")])
    ai = _IntentAi({"kind": "personal_fact_query", "category": "age", "confidence": 0.99})
    lookup = asyncio.run(direct_graph_lookup(db, "u1", "Сколько лет мне?", ai, []))
    assert lookup["answer"] == "В вашем Graph указан возраст: 14 лет."
    assert lookup["telemetry"]["deterministic_hit"] is True
    assert lookup["telemetry"]["ai_intent_fallback"] is False
    assert ai.calls == 0

def test_ai_intent_fallback_gets_no_graph_values_and_answer_comes_from_backend():
    db = _PersonalFactsDb([_node(node_type="fact", label="14 лет")])
    ai = _IntentAi({"kind": "personal_fact_query", "category": "age", "requested_scope": "single", "confidence": 0.96})
    lookup = asyncio.run(direct_graph_lookup(db, "u1", "А годиков у меня сколько?", ai, [{"role": "user", "content": "Привет"}]))
    assert lookup["answer"] == "В вашем Graph указан возраст: 14 лет."
    assert lookup["telemetry"]["ai_intent_fallback"] is True
    assert lookup["telemetry"]["classification_usage"]["input_tokens"] == 42
    assert ai.calls == 1
    assert "14 лет" not in ai.payloads[0]
    assert "nodes" not in ai.payloads[0]

@pytest.mark.parametrize("query,response", [
    ("Расскажи мои годики", ["not", "object"]),
    ("А сколько моих годиков?", {"kind": "personal_fact_query", "category": "age", "confidence": 0.2}),
    ("Подскажи мои годики", {"kind": "personal_fact_query", "category": "not_allowed", "confidence": 0.9}),
])
def test_ai_intent_fallback_invalid_or_low_confidence_does_not_create_direct_answer(query, response):
    db = _PersonalFactsDb([_node(node_type="fact", label="14 лет")])
    ai = _IntentAi(response)
    lookup = asyncio.run(direct_graph_lookup(db, "u1", query, ai, []))
    assert lookup is None

def test_missing_fact_answer_requires_recognized_intent():
    db = _PersonalFactsDb([])
    lookup = asyncio.run(direct_graph_lookup(db, "u1", "Сколько лет мне?", None, []))
    assert lookup["answer"] == "В вашем Graph пока не указан возраст."
    assert lookup["telemetry"]["graph_lookup_hit"] is False

def test_direct_graph_answer_does_not_read_superseded_place():
    db = _GraphAnswerDb({"place": [], "age": []})
    answer = asyncio.run(direct_graph_answer(db, "00000000-0000-0000-0000-000000000001", "Где я живу?"))
    assert answer == "В вашем Graph пока не указано место проживания."

def test_graph_context_falls_back_to_place_type_when_vector_search_is_empty():
    context = asyncio.run(graph_context(_GraphContextDb(), "00000000-0000-0000-0000-000000000001", _RetrievalAi(), "Где я живу?"))
    assert context
    assert context[0]["type"] == "place"
    assert context[0]["label"] == "Санкт-Петербург"

def test_llm_prompt_forbids_unbacked_graph_confirmation_claims():
    lowered = LURG_SYSTEM_PROMPT.lower()
    assert "never say that a graph change was prepared" in lowered
    assert "unless the backend supplied an explicit graph change result" in lowered

def test_citations_come_only_from_backend_results():
    web = web_sources([{"title": "Official", "url": "https://example.com/a", "snippet": "x"}])
    graph = graph_sources([{"id": "n1", "label": "Rust", "created_at": "2026-01-01", "confidence": 0.9}])
    assert web == [{"type": "web", "title": "Official", "url": "https://example.com/a", "domain": "example.com", "snippet": "x"}]
    assert graph[0]["node_id"] == "n1"

def test_ssrf_rejects_localhost():
    with pytest.raises(HTTPException) as error:
        asyncio.run(validate_public_url("http://127.0.0.1/admin"))
    assert error.value.status_code == 400
