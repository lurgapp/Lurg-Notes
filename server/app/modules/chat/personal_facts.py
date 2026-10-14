import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...runtime import normalize_query
from ..graph.resolver import resolve_entity, strip_conversational_noise


def _json_timestamp(value: object) -> object:
    return value.isoformat() if hasattr(value, "isoformat") else value


@dataclass(frozen=True)
class PersonalFact:
    id: str
    type: str
    label: str
    confidence: float
    version: int
    lifecycle_status: str
    created_at: object
    updated_at: object
    properties: dict
    relation: str | None = None
    source_kind: str = "graph"


@dataclass(frozen=True)
class PersonalFactResult:
    category: str
    answer: str
    facts: list[PersonalFact]
    conflict: bool = False

    @property
    def sources(self) -> list[dict]:
        sources = []
        for fact in self.facts:
            source = {
                "type": "graph",
                "source_kind": fact.source_kind,
                "node_id": fact.id,
                "node_type": fact.type,
                "label": fact.label,
                "created_at": _json_timestamp(fact.created_at),
                "updated_at": _json_timestamp(fact.updated_at),
                "confidence": fact.confidence,
            }
            if fact.type in {"place", "location"}:
                resolved = resolve_entity("place", fact.label)
                source["canonical_key"] = resolved["canonical_key"]
                source["canonical_label"] = resolved["label"]
            sources.append(source)
        return sources


PERSONAL_FACT_CATEGORIES = {
    "name", "age", "residence", "origin", "occupation", "education",
    "skills", "interests", "goals", "projects", "organizations", "services", "timeline",
}
HIGH_CONFIDENCE = 0.80
QUERY_FILLERS = {
    "а", "ну", "вообще", "же", "то", "ка", "пожалуйста", "плиз", "типа",
    "сейчас", "судя", "по", "graph", "граф", "lurg",
}
WORD_FIXES = {
    "вазраст": "возраст",
    "возрост": "возраст",
    "сколька": "сколько",
    "сколко": "сколько",
}


@dataclass(frozen=True)
class PersonalQueryIntent:
    kind: str
    category: str = "unknown"
    confidence: float = 0.0
    source: str = "deterministic"


def _normalized_personal_query(query: str) -> str:
    value = strip_conversational_noise(normalize_query(query)).casefold().replace("ё", "е")
    value = re.sub(r"[^\w\s+-]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    for wrong, right in WORD_FIXES.items():
        value = re.sub(rf"\b{re.escape(wrong)}\b", right, value)
    return value


def _tokens(query: str) -> set[str]:
    return {token for token in _normalized_personal_query(query).split() if token and token not in QUERY_FILLERS}


def deterministic_personal_intent(query: str) -> PersonalQueryIntent:
    normalized = _normalized_personal_query(query)
    words = _tokens(query)
    personal = bool(words & {"я", "мне", "меня", "мой", "моя", "мое", "моё", "мои", "моих", "у"})

    if re.search(
        r"\b(что\s+ты(?:\s+вообще)?\s+знаешь\s+обо\s+мне|что\s+ты\s+обо\s+мне\s+знаешь|"
        r"расскажи\s+обо\s+мне|сохранено\s+про\s+меня|данные\s+обо\s+мне|"
        r"вспомни\s+(?:мой\s+)?контекст|вспомнить\s+(?:мой\s+)?контекст|"
        r"напомни\s+(?:мой\s+)?контекст|покажи\s+(?:мой\s+)?контекст|"
        r"что\s+есть\s+в\s+(?:моем\s+)?graph|есть\s+в\s+graph)\b",
        normalized,
    ):
        return PersonalQueryIntent("graph_overview", confidence=0.96)
    if re.search(r"\b(как\s+меня\s+зовут|кто\s+я|напомни\s+.*имя|имя\s+.*какое|знаешь\s+.*имя)\b", normalized):
        return PersonalQueryIntent("personal_fact_query", "name", 0.95)
    if ("имя" in words and personal) or ("зовут" in words and personal):
        return PersonalQueryIntent("personal_fact_query", "name", 0.88)

    if "возраст" in words or "возраста" in words:
        if personal or words & {"какой", "напомни", "знаешь"}:
            return PersonalQueryIntent("personal_fact_query", "age", 0.93)
    if "лет" in words and ("сколько" in words or "напомни" in words or "знаешь" in words):
        return PersonalQueryIntent("personal_fact_query", "age", 0.94)
    if "сколько" in words and "мне" in words:
        return PersonalQueryIntent("personal_fact_query", "age", 0.86)

    if "откуда" in words or re.search(r"\bиз\s+какого\s+.*города\b", normalized):
        return PersonalQueryIntent("personal_fact_query", "origin", 0.94)
    if "живу" in words or ("дом" in words and personal):
        return PersonalQueryIntent("personal_fact_query", "residence", 0.92)
    if "город" in words and (personal or "указан" in words or "какой" in words):
        return PersonalQueryIntent("personal_fact_query", "residence", 0.84)

    if words & {"навыки", "умею", "изучаю", "технологии", "разбираюсь"}:
        if personal or words & {"какие", "что", "чем", "в"}:
            return PersonalQueryIntent("personal_fact_query", "skills", 0.90)
    if words & {"проекты", "проект"} and (personal or words & {"какие", "есть"}):
        return PersonalQueryIntent("personal_fact_query", "projects", 0.90)
    if re.search(r"\b(над\s+чем\s+.*работаю|что\s+.*создаю|какие\s+.*продукты\s+.*делаю)\b", normalized):
        return PersonalQueryIntent("personal_fact_query", "projects", 0.90)
    if words & {"интересы", "интересуюсь", "нравится", "увлекаюсь"} and personal:
        return PersonalQueryIntent("personal_fact_query", "interests", 0.88)
    if words & {"цели", "цель", "мечтаю", "планы", "планах"} and personal:
        return PersonalQueryIntent("personal_fact_query", "goals", 0.86)
    if words & {"работаю", "занимаюсь", "работа"} and words & {"кем", "чем", "какая"}:
        return PersonalQueryIntent("personal_fact_query", "occupation", 0.86)
    if words & {"учусь", "учеба", "учёба"} and words & {"где", "какая", "кого"}:
        return PersonalQueryIntent("personal_fact_query", "education", 0.86)
    if re.search(r"\b(связано\s+с|относится\s+к|про\s+\w+|о\s+моих\s+планах|по\s+graph)\b", normalized) and personal:
        return PersonalQueryIntent("graph_search", confidence=0.82)

    if personal or "graph" in normalized or "граф" in normalized:
        return PersonalQueryIntent("unknown", "unknown", 0.35)
    return PersonalQueryIntent("general", "unknown", 0.0)


def personal_query_category(query: str) -> str | None:
    intent = deterministic_personal_intent(query)
    if intent.kind == "personal_fact_query" and intent.confidence >= HIGH_CONFIDENCE:
        return intent.category
    return None


def _fact_from_row(row) -> PersonalFact:
    data = dict(row)
    return PersonalFact(
        id=str(data["id"]),
        type=str(data["type"]),
        label=str(data["label"]),
        confidence=float(data["confidence"]),
        version=int(data["version"] or 1),
        lifecycle_status=str(data["lifecycle_status"]),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        properties=data.get("properties") or {},
        relation=data.get("relation"),
    )


def _dedupe_facts(facts: list[PersonalFact], category: str) -> list[PersonalFact]:
    seen: set[str] = set()
    result: list[PersonalFact] = []
    for fact in facts:
        key = fact.label.casefold().replace("ё", "е")
        if category in {"residence", "origin"}:
            key = resolve_entity("place", fact.label)["canonical_key"]
        elif category in {"skills", "interests"}:
            key = fact.properties.get("canonical_key") or key
        if key in seen:
            continue
        seen.add(key)
        result.append(fact)
    return result


def _has_conflict(facts: list[PersonalFact], category: str) -> bool:
    if category not in {"name", "age", "residence", "origin", "occupation", "education"}:
        return False
    keys = {
        resolve_entity("place", fact.label)["canonical_key"] if category in {"residence", "origin"} else fact.label.casefold().replace("ё", "е")
        for fact in facts
    }
    return len(keys) > 1


class PersonalFactResolver:
    def __init__(self, db: AsyncSession, user_id: UUID):
        self.db = db
        self.user_id = user_id

    async def answer(self, query: str) -> PersonalFactResult | None:
        intent = deterministic_personal_intent(query)
        if intent.kind != "personal_fact_query" or intent.confidence < HIGH_CONFIDENCE:
            return None
        return await self.answer_category(intent.category)

    async def answer_category(self, category: str) -> PersonalFactResult | None:
        if category not in PERSONAL_FACT_CATEGORIES:
            return None
        facts = await self.resolve(category)
        if not facts:
            return None
        facts = _dedupe_facts(facts, category)
        conflict = _has_conflict(facts, category)
        return PersonalFactResult(category, self.format_answer(category, facts, conflict), facts, conflict)

    async def answer_overview(self) -> PersonalFactResult | None:
        rows = (await self.db.execute(text("""
          SELECT n.id,n.type,n.label,n.properties,n.confidence,n.version,n.lifecycle_status,
            n.created_at,n.updated_at,NULL::varchar AS relation
          FROM nodes n
          WHERE n.user_id=:uid
            AND n.lifecycle_status='active'
            AND NOT (n.type='person' AND lower(n.label) IN ('пользователь','user'))
          ORDER BY n.updated_at DESC, n.confidence DESC
          LIMIT 40"""), {"uid": self.user_id})).mappings().all()
        facts = _dedupe_facts([_fact_from_row(row) for row in rows], "overview")
        if not facts:
            return None
        return PersonalFactResult("overview", self.format_answer("overview", facts, False), facts)

    async def resolve(self, category: str) -> list[PersonalFact]:
        if category == "name":
            facts = await self._nodes(["profile"], relations=["зовут", "имя"], legacy_label_pattern=None)
            return facts or await self._identity_name()
        if category == "age":
            return await self._age()
        if category == "residence":
            facts = await self._places(["живет в", "живёт в", "проживает", "переехал в"])
            return facts or await self._places([])
        if category == "origin":
            facts = await self._places(["из", "родом из"])
            return facts or await self._places([])
        if category == "skills":
            return await self._nodes(["skill", "technology", "framework"], relations=["знает", "изучает", "умеет", "has skill"])
        if category == "projects":
            return await self._nodes(["project"], relations=["делает", "строит", "создает", "создаёт", "owns"])
        if category == "interests":
            return await self._nodes(["interest", "idea"], relations=["интересуется", "любит", "увлекается"])
        if category == "occupation":
            return await self._nodes(["occupation", "organization"], relations=["занимается", "работает в"])
        if category == "education":
            return await self._nodes(["education", "organization"], relations=["учится", "учится в", "посещает"])
        if category == "goals":
            return await self._nodes(["goal"], relations=["цель", "хочет"])
        return []

    async def _nodes(self, node_types: list[str], relations: list[str], legacy_label_pattern: str | None = None) -> list[PersonalFact]:
        rows = (await self.db.execute(text("""
          SELECT DISTINCT ON (n.id)
            n.id,n.type,n.label,n.properties,n.confidence,n.version,n.lifecycle_status,n.created_at,n.updated_at,
            e.relation
          FROM nodes n
          LEFT JOIN edges e ON e.user_id=:uid AND e.target_id=n.id
          LEFT JOIN nodes s ON s.id=e.source_id AND s.user_id=:uid AND s.lifecycle_status='active'
          WHERE n.user_id=:uid
            AND n.lifecycle_status='active'
            AND (
              n.type = ANY(:types)
              OR (e.relation = ANY(:relations) AND s.type='person')
              OR (CAST(:legacy_pattern AS text) IS NOT NULL AND n.label ~* CAST(:legacy_pattern AS text))
            )
          ORDER BY n.id, n.confidence DESC, n.updated_at DESC
          LIMIT 30"""), {
            "uid": self.user_id,
            "types": node_types,
            "relations": relations,
            "legacy_pattern": legacy_label_pattern,
        })).mappings().all()
        return [_fact_from_row(row) for row in rows]

    async def _identity_name(self) -> list[PersonalFact]:
        row = (await self.db.execute(text("""
          SELECT id,name,created_at
          FROM users
          WHERE id=:uid AND NULLIF(trim(name), '') IS NOT NULL
          LIMIT 1"""), {"uid": self.user_id})).mappings().first()
        if not row:
            return []
        return [PersonalFact(
            id=str(row["id"]),
            type="identity",
            label=str(row["name"]),
            confidence=1.0,
            version=1,
            lifecycle_status="active",
            created_at=row["created_at"],
            updated_at=row["created_at"],
            properties={},
            relation="имя",
            source_kind="identity",
        )]

    async def _age(self) -> list[PersonalFact]:
        rows = (await self.db.execute(text("""
          SELECT DISTINCT ON (n.id)
            n.id,n.type,n.label,n.properties,n.confidence,n.version,n.lifecycle_status,n.created_at,n.updated_at,
            e.relation
          FROM nodes n
          LEFT JOIN edges e ON e.user_id=:uid AND e.target_id=n.id
          LEFT JOIN nodes s ON s.id=e.source_id AND s.user_id=:uid AND s.lifecycle_status='active'
          WHERE n.user_id=:uid
            AND n.lifecycle_status='active'
            AND (
              n.type='age'
              OR n.properties->>'fact_type'='age'
              OR n.label ~* '^\\s*\\d{1,3}\\s*(лет|год|года)?\\s*$'
              OR (e.relation = ANY(:relations) AND s.type='person' AND n.label ~* '\\d{1,3}')
            )
          ORDER BY n.id, n.confidence DESC, n.updated_at DESC
          LIMIT 10"""), {
            "uid": self.user_id,
            "relations": ["возраст", "имеет"],
        })).mappings().all()
        return [_fact_from_row(row) for row in rows]

    async def _places(self, relations: list[str]) -> list[PersonalFact]:
        rows = (await self.db.execute(text("""
          SELECT DISTINCT ON (n.id)
            n.id,n.type,n.label,n.properties,n.confidence,n.version,n.lifecycle_status,n.created_at,n.updated_at,
            e.relation
          FROM nodes n
          LEFT JOIN edges e ON e.user_id=:uid AND e.target_id=n.id
          LEFT JOIN nodes s ON s.id=e.source_id AND s.user_id=:uid AND s.lifecycle_status='active'
          WHERE n.user_id=:uid
            AND n.lifecycle_status='active'
            AND (
              (CAST(:relation_scoped AS boolean) AND e.relation = ANY(:relations) AND s.type='person')
              OR (
                NOT CAST(:relation_scoped AS boolean)
                AND (n.type = ANY(:types) OR n.properties->>'fact_type' = ANY(:fact_types))
              )
            )
          ORDER BY n.id, n.confidence DESC, n.updated_at DESC
          LIMIT 10"""), {
            "uid": self.user_id,
            "types": ["place", "location"],
            "fact_types": ["place", "location", "residence", "origin"],
            "relations": relations,
            "relation_scoped": bool(relations),
        })).mappings().all()
        return [_fact_from_row(row) for row in rows]

    def format_answer(self, category: str, facts: list[PersonalFact], conflict: bool) -> str:
        if conflict:
            values = ", ".join(fact.label for fact in facts[:5])
            return f"В вашем Graph есть несколько активных значений для этого факта: {values}. Лучше уточнить, какое из них актуально."
        if category == "name":
            if facts[0].source_kind == "identity":
                return f"В вашем профиле указано имя: {facts[0].label}."
            return f"В вашем Graph указано имя: {facts[0].label}."
        if category == "age":
            return f"В вашем Graph указан возраст: {facts[0].label}."
        if category == "residence":
            return f"В вашем Graph указано место: {resolve_entity('place', facts[0].label)['label']}."
        if category == "origin":
            origin = next((fact for fact in facts if fact.relation in {"из", "родом из"}), facts[0])
            origin_label = resolve_entity("place", origin.label)["label"]
            if origin.relation in {"из", "родом из"}:
                return f"В вашем Graph указано происхождение: {origin_label}."
            return f"В вашем Graph указано место: {origin_label}."
        if category == "skills":
            return "Ваши навыки и технологии из Graph: " + ", ".join(fact.label for fact in facts[:20]) + "."
        if category == "projects":
            return "Ваши проекты из Graph: " + ", ".join(fact.label for fact in facts[:12]) + "."
        if category == "interests":
            return "Ваши интересы из Graph: " + ", ".join(fact.label for fact in facts[:20]) + "."
        if category == "occupation":
            return "В вашем Graph указано о работе: " + ", ".join(fact.label for fact in facts[:8]) + "."
        if category == "education":
            return "В вашем Graph указано об учебе: " + ", ".join(fact.label for fact in facts[:8]) + "."
        if category == "goals":
            return "Ваши цели из Graph: " + ", ".join(fact.label for fact in facts[:12]) + "."
        if category == "overview":
            type_titles = {
                "profile": "Имя", "age": "Возраст", "occupation": "Занятие",
                "education": "Учеба", "place": "Места", "skill": "Навыки",
                "technology": "Технологии", "framework": "Фреймворки",
                "interest": "Интересы", "goal": "Цели", "project": "Проекты",
                "organization": "Организации", "fact": "Факты",
            }
            grouped: dict[str, list[str]] = {}
            for fact in facts:
                grouped.setdefault(type_titles.get(fact.type, fact.type.capitalize()), []).append(fact.label)
            sections = []
            for title, values in grouped.items():
                unique_values = list(dict.fromkeys(values))[:12]
                sections.append(f"{title}\n" + "\n".join(f"• {value}" for value in unique_values))
            return "Вот что Lurg подтверждённо знает о вас:\n\n" + "\n\n".join(sections)
        return "В вашем Graph есть подтвержденные данные: " + ", ".join(fact.label for fact in facts[:10]) + "."
