from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from ...core.contracts import AIEngine
from .prompts import NOTES_SYSTEM_INSTRUCTION, build_notes_ai_input, notes_action_instruction
from .schemas import NotesAiActionRequest


@dataclass(frozen=True)
class NotesAiRunResult:
    request_payload: str
    raw_result: dict[str, Any]


class NotesAiService:
    """Application logic boundary for Lurg Notes AI.

    Public clients never provide system prompts or model settings. The router
    supplies only typed request data and ownership metadata; this service maps
    action -> prompt/schema policy through the Notes Action Registry.
    """

    service_namespace = "lurg_notes"

    async def run_action(
        self,
        *,
        ai: AIEngine,
        user_id: UUID,
        note_id: UUID,
        request_id: UUID,
        body: NotesAiActionRequest,
    ) -> NotesAiRunResult:
        if body.service != self.service_namespace:
            raise HTTPException(400, "Некорректный service namespace для Notes AI.")

        spec = notes_action_instruction(body.action)
        payload = body.payload.model_dump()
        instruction = f"{NOTES_SYSTEM_INSTRUCTION}\n{spec.instruction}"
        content = build_notes_ai_input(
            service=self.service_namespace,
            user_id=str(user_id),
            note_id=str(note_id),
            action=body.action,
            request_id=str(request_id),
            payload=payload,
            document_revision=body.document_revision,
            selected_block_ids=[str(item) for item in body.selected_block_ids],
        )
        raw = await ai.structured_json(
            instruction,
            content,
            max_tokens=spec.max_tokens,
            temperature=spec.temperature,
        )
        return NotesAiRunResult(request_payload=content, raw_result=raw)


notes_ai_service = NotesAiService()
