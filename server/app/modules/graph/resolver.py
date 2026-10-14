import re

CONVERSATIONAL_FILLERS = {
    "кста",
    "кстати",
    "вообще",
    "типа",
    "короче",
    "ну",
    "блин",
    "ээ",
    "эм",
    "ахаха",
    "хаха",
    "лол",
}

CITY_ALIASES = {
    "spb": "Санкт-Петербург",
    "sankt-peterburg": "Санкт-Петербург",
    "saint petersburg": "Санкт-Петербург",
    "saint-petersburg": "Санкт-Петербург",
    "спб": "Санкт-Петербург",
    "с-пб": "Санкт-Петербург",
    "с. петербург": "Санкт-Петербург",
    "с петербург": "Санкт-Петербург",
    "питер": "Санкт-Петербург",
    "питере": "Санкт-Петербург",
    "питера": "Санкт-Петербург",
    "питеру": "Санкт-Петербург",
    "питером": "Санкт-Петербург",
    "петербург": "Санкт-Петербург",
    "петербурге": "Санкт-Петербург",
    "петербурга": "Санкт-Петербург",
    "петербургом": "Санкт-Петербург",
    "санкт-петербург": "Санкт-Петербург",
    "санкт петербург": "Санкт-Петербург",
    "санкт-петербурге": "Санкт-Петербург",
    "санкт петербурге": "Санкт-Петербург",
    "санкт-петербурга": "Санкт-Петербург",
    "санкт петербурга": "Санкт-Петербург",
    "санкт-петербургом": "Санкт-Петербург",
    "санкт петербургом": "Санкт-Петербург",
    "москва": "Москва",
    "москве": "Москва",
    "москвы": "Москва",
    "москву": "Москва",
    "казань": "Казань",
    "казани": "Казань",
}

ENTITY_ALIASES = {
    "place": CITY_ALIASES,
    "interest": {
        "ии": "искусственный интеллект",
        "ai": "искусственный интеллект",
        "искусственный интеллект": "искусственный интеллект",
        "искусственным интеллектом": "искусственный интеллект",
    },
    "skill": {},
    "technology": {},
    "framework": {},
}


def normalized_entity_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold().replace("ё", "е"))


def strip_conversational_noise(value: str) -> str:
    text = re.sub(r"[\U0001F300-\U0001FAFF]+", " ", value)
    text = re.sub(r"(?:(?:\)\)+)|(?:\(+)|(?::\))|(?:;-\))|(?::-\))|(?::D))", " ", text)
    for filler in CONVERSATIONAL_FILLERS:
        text = re.sub(rf"(?<![\w-]){re.escape(filler)}(?![\w-])", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,!?:;")


def resolve_entity(node_type: str, label: str) -> dict:
    cleaned = strip_conversational_noise(label)
    normalized = normalized_entity_text(cleaned)
    canonical = ENTITY_ALIASES.get(node_type, {}).get(normalized, cleaned)
    canonical_key = normalized_entity_text(canonical)
    display_label = canonical if node_type == "place" else cleaned
    return {
        "label": display_label,
        "canonical_key": canonical_key,
        "alias": cleaned,
    }


def graph_canonical_key_for_type(node_type: str, label: str) -> str | None:
    if node_type not in ENTITY_ALIASES:
        return None
    normalized = normalized_entity_text(strip_conversational_noise(label))
    if node_type != "place" and normalized not in ENTITY_ALIASES.get(node_type, {}):
        return None
    return resolve_entity(node_type, label)["canonical_key"]
