from app.modules.notes.router import note_analysis_lines
from app.modules.chat.router import candidate_change


def test_notes_analysis_removes_markup_code_quotes_and_tables():
    document = """# План
Я работаю над проектом Lurg Notes.
> Я живу в Москве.
| field | value |
| --- | --- |
| owner | someone |
```kotlin
val city = \"Москва\"
```
"""
    lines = note_analysis_lines(document)
    assert lines == ["План", "Я работаю над проектом Lurg Notes."]


def test_explicit_notes_fact_stays_atomic_candidate():
    change = candidate_change("Я изучаю Kotlin.")
    assert change is not None
    assert len(change["proposed_nodes"]) == 1
    assert change["proposed_nodes"][0]["type"] == "skill"
    assert change["proposed_nodes"][0]["label"] == "Kotlin"


def test_quote_like_or_temporary_text_does_not_become_notes_fact():
    assert candidate_change("Сегодня я смотрю фильм.") is None
