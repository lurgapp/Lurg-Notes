from app.main import app
from pathlib import Path


def test_notes_api_contract_is_registered():
    paths = app.openapi()["paths"]
    assert "get" in paths["/notes"]
    assert "post" in paths["/notes"]
    assert "get" in paths["/notes/{note_id}"]
    assert "patch" in paths["/notes/{note_id}/ops"]
    assert "post" in paths["/notes/{note_id}/ai/actions"]
    assert "delete" in paths["/notes/{note_id}"]
    assert "get" in paths["/notes/encrypted-documents"]
    assert "get" in paths["/notes/encrypted-tombstones"]


def test_encrypted_recovery_is_owner_scoped_and_never_decrypts_server_side():
    source = Path("app/modules/notes/router.py").read_text()
    assert 'WHERE d.user_id=:uid' in source
    assert 'JOIN notes n ON n.id=d.note_id AND n.user_id=d.user_id' in source
    assert "decrypt(" not in source


def test_encrypted_document_rejects_a_stale_conflicting_revision():
    source = Path("app/modules/notes/router.py").read_text()
    assert "EXCLUDED.document_revision > note_encrypted_documents.document_revision" in source
    assert "EXCLUDED.content_hash = note_encrypted_documents.content_hash" in source


def test_deleted_note_is_a_tombstone_not_a_recreatable_missing_row():
    source = Path("app/modules/notes/router.py").read_text()
    assert '"/encrypted-tombstones"' in source
    assert 'raise HTTPException(410, "Заметка была удалена на другом устройстве.")' in source
    assert "revision=revision+1" in source


def test_notes_module_uses_identity_auth_not_custom_users():
    source = Path("app/modules/notes/router.py").read_text()
    assert "current_user" in source
    assert "auth/login" not in source
    assert "INSERT INTO users" not in source
    assert "password" not in source


def test_notes_phase1_does_not_import_chat_or_openrouter():
    source = Path("app/modules/notes/router.py").read_text()
    assert "modules.chat" not in source
    assert "openrouter" not in source.lower()


def test_notes_ai_has_separate_prompt_and_registry():
    prompt_source = Path("app/modules/notes/prompts.py").read_text()
    service_source = Path("app/modules/notes/ai_service.py").read_text()
    chat_prompt = Path("app/prompts.py").read_text()
    assert "Lurg Notes AI" in prompt_source
    assert "ACTION_REGISTRY" in prompt_source
    assert "service_namespace = \"lurg_notes\"" in service_source
    assert "LURG_SYSTEM_PROMPT" not in prompt_source
    assert "Lurg Notes AI" not in chat_prompt
    assert "service=service" not in prompt_source


def test_notes_ai_public_schema_does_not_accept_system_prompt():
    schema_source = Path("app/modules/notes/schemas.py").read_text()
    assert "NotesAiActionRequest" in schema_source
    assert "system_prompt" not in schema_source
    assert "instruction" not in schema_source
    assert "temperature" not in schema_source
    assert "max_tokens" not in schema_source
    assert "model" not in schema_source


def test_notes_router_does_not_build_prompts_inline():
    router_source = Path("app/modules/notes/router.py").read_text()
    assert "NOTES_SYSTEM_INSTRUCTION" not in router_source
    assert "notes_action_instruction" not in router_source
    assert "build_notes_ai_input" not in router_source
    assert "notes_ai_service.run_action" in router_source


def test_notes_ai_registry_is_action_specific():
    prompt_source = Path("app/modules/notes/prompts.py").read_text()
    for action in ["continue", "fix_grammar", "make_table", "make_checklist", "structure_markdown"]:
        assert f'action="{action}"' in prompt_source
    assert "web_allowed" in prompt_source
    assert "graph_allowed" in prompt_source
