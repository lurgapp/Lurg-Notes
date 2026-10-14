from app.modules.chat.personal_facts import deterministic_personal_intent


def test_remember_context_is_graph_overview():
    result = deterministic_personal_intent("Вспомнить контекст")
    assert result.kind == "graph_overview"
    assert result.confidence >= 0.9
