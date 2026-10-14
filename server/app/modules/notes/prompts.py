from __future__ import annotations

from dataclasses import dataclass
from typing import Any


NOTES_SYSTEM_INSTRUCTION = """You are Lurg Notes AI, an embedded editor tool inside Lurg Notes.
You are not a chat assistant. Do not greet, do not discuss yourself, and do not claim that you saved a note or changed Graph.
Edit or generate only the requested document fragment. Return strict JSON only.
Use Markdown semantically when needed: headings, bold, italic, checklists, tables, quotes, code, Mermaid, LaTeX.
Never output unsafe HTML, script, iframe, javascript: URLs, hidden content, or CSS.
Never mix context from another note, another user, Lurg Chat history, Graph chat summaries, or another service.
If the request is ambiguous, produce the smallest useful edit in the document's language and style.
"""


@dataclass(frozen=True)
class NotesActionSpec:
    action: str
    instruction: str
    max_tokens: int
    temperature: float
    web_allowed: bool = False
    graph_allowed: bool = False


ACTION_REGISTRY: dict[str, NotesActionSpec] = {
    "continue": NotesActionSpec(
        action="continue",
        instruction="Continue the document from the cursor. Preserve style. Return {format:'markdown',content:string,blocks:[]}.",
        max_tokens=650,
        temperature=0.35,
    ),
    "rewrite": NotesActionSpec(
        action="rewrite",
        instruction="Rewrite the selected text to be clearer and more polished. Preserve meaning. Return {format:'markdown',content:string,blocks:[]}.",
        max_tokens=700,
        temperature=0.25,
    ),
    "shorten": NotesActionSpec(
        action="shorten",
        instruction="Shorten the selected text while preserving key facts and structure. Return {format:'markdown',content:string,blocks:[]}.",
        max_tokens=450,
        temperature=0.15,
    ),
    "expand": NotesActionSpec(
        action="expand",
        instruction="Expand the selected text with useful detail, preserving the document style. Return {format:'markdown',content:string,blocks:[]}.",
        max_tokens=900,
        temperature=0.35,
    ),
    "fix_grammar": NotesActionSpec(
        action="fix_grammar",
        instruction="Correct grammar, spelling, punctuation, and wording only. Preserve structure and language. Return {format:'markdown',content:string,blocks:[]}.",
        max_tokens=650,
        temperature=0,
    ),
    "change_tone": NotesActionSpec(
        action="change_tone",
        instruction="Change the selected text tone according to payload.tone. Preserve facts. Return {format:'markdown',content:string,blocks:[]}.",
        max_tokens=700,
        temperature=0.25,
    ),
    "translate": NotesActionSpec(
        action="translate",
        instruction="Translate the selected text to payload.target_language. Preserve Markdown structure. Return {format:'markdown',content:string,blocks:[]}.",
        max_tokens=800,
        temperature=0.1,
    ),
    "make_table": NotesActionSpec(
        action="make_table",
        instruction="Convert the selected text into a real Markdown table. Use concise headers and rows. Return {format:'markdown',content:string,blocks:[]}.",
        max_tokens=700,
        temperature=0.1,
    ),
    "make_checklist": NotesActionSpec(
        action="make_checklist",
        instruction="Convert the selected text into a Markdown checklist using '- [ ]'. Return {format:'markdown',content:string,blocks:[]}.",
        max_tokens=550,
        temperature=0.1,
    ),
    "structure_markdown": NotesActionSpec(
        action="structure_markdown",
        instruction="Structure the selected text as clean visual Markdown with headings, lists, tables, quotes, or callouts when useful. Return {format:'markdown',content:string,blocks:[]}.",
        max_tokens=900,
        temperature=0.2,
    ),
    "summarize": NotesActionSpec(
        action="summarize",
        instruction="Summarize the provided note fragment into concise Markdown. Return {format:'markdown',content:string,blocks:[]}.",
        max_tokens=450,
        temperature=0.1,
    ),
}


def notes_action_instruction(action: str) -> NotesActionSpec:
    return ACTION_REGISTRY[action]


def build_notes_ai_input(*, service: str, user_id: str, note_id: str, action: str, request_id: str, payload: dict[str, Any], document_revision: int | None, selected_block_ids: list[str]) -> str:
    selected = (payload.get("selected_text") or "").strip()
    document = payload.get("document_text") or ""
    cursor = payload.get("cursor")
    if selected:
        context = {
            "selected_text": selected[:20000],
            "neighbor_context": compact_neighbor_context(document, selected, cursor),
        }
    else:
        context = {
            "cursor": cursor,
            "current_section": compact_cursor_context(document, cursor),
        }
    return (
        "Return strict JSON object only. Schema: {\"format\":\"markdown\",\"content\":\"...\",\"blocks\":[]}.\n"
        f"service={service}\n"
        f"user_id={user_id}\n"
        f"note_id={note_id}\n"
        f"action={action}\n"
        f"request_id={request_id}\n"
        f"document_revision={document_revision}\n"
        f"selected_block_ids={selected_block_ids}\n"
        f"payload_locale={payload.get('locale') or 'ru'}\n"
        f"user_request={(payload.get('user_request') or '').strip()[:2000]}\n"
        f"payload_tone={payload.get('tone') or ''}\n"
        f"payload_target_language={payload.get('target_language') or ''}\n"
        f"context={context}\n"
    )


def compact_cursor_context(document: str, cursor: int | None) -> str:
    if cursor is None:
        return document[-6000:]
    start = max(0, cursor - 3500)
    end = min(len(document), cursor + 1800)
    return document[start:end]


def compact_neighbor_context(document: str, selected: str, cursor: int | None) -> str:
    index = document.find(selected)
    if index < 0:
        return compact_cursor_context(document, cursor)
    start = max(0, index - 1600)
    end = min(len(document), index + len(selected) + 1600)
    return document[start:end]
