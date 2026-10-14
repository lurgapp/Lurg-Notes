from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


BlockType = Literal[
    "paragraph", "heading", "heading1", "heading2", "heading3", "bullet", "numbered",
    "checklist", "quote", "image", "file", "table", "divider", "code",
]
OperationType = Literal["update_note", "upsert_block", "delete_block"]
NotesAiAction = Literal[
    "continue",
    "rewrite",
    "shorten",
    "expand",
    "fix_grammar",
    "change_tone",
    "translate",
    "make_table",
    "make_checklist",
    "structure_markdown",
    "summarize",
]


class NoteCreate(BaseModel):
    title: str = Field(default="", max_length=300)
    client_note_id: UUID | None = None
    initial_block: NoteBlockIn | None = None


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    metadata: dict[str, Any] | None = None


class NoteBlockIn(BaseModel):
    id: UUID
    parent_id: UUID | None = None
    position: Decimal = Field(max_digits=20, decimal_places=10)
    type: BlockType = "paragraph"
    content: dict[str, Any] = Field(default_factory=dict)
    attrs: dict[str, Any] = Field(default_factory=dict)
    origin: str = Field(default="user", max_length=40)
    revision: int = Field(default=1, ge=1)


class NoteOperationIn(BaseModel):
    client_operation_id: str = Field(min_length=1, max_length=160)
    base_revision: int = Field(ge=0)
    operation_type: OperationType
    payload: dict[str, Any] = Field(default_factory=dict)


class NoteOpsRequest(BaseModel):
    operations: list[NoteOperationIn] = Field(min_length=1, max_length=100)


class NoteSummary(BaseModel):
    id: UUID
    title: str
    revision: int
    updated_at: datetime
    created_at: datetime


class NoteBlockOut(BaseModel):
    id: UUID
    parent_id: UUID | None
    position: Decimal
    type: str
    content: dict[str, Any]
    attrs: dict[str, Any]
    origin: str
    revision: int
    updated_at: datetime


class NoteDetail(BaseModel):
    note: NoteSummary
    blocks: list[NoteBlockOut]


class NoteListResponse(BaseModel):
    notes: list[NoteSummary]


class NoteOpsResponse(BaseModel):
    note: NoteSummary
    applied: list[str]
    skipped: list[str]
    conflicts: list[dict[str, Any]] = Field(default_factory=list)


class NotesAiPayload(BaseModel):
    selected_text: str | None = Field(default=None, max_length=20000)
    document_text: str | None = Field(default=None, max_length=60000)
    user_request: str | None = Field(default=None, max_length=2000)
    cursor: int | None = Field(default=None, ge=0)
    locale: str = Field(default="ru", max_length=12)
    tone: str | None = Field(default=None, max_length=80)
    target_language: str | None = Field(default=None, max_length=80)


class NotesAiActionRequest(BaseModel):
    service: Literal["lurg_notes"]
    action: NotesAiAction
    payload: NotesAiPayload = Field(default_factory=NotesAiPayload)
    document_revision: int | None = Field(default=None, ge=0)
    selected_block_ids: list[UUID] = Field(default_factory=list, max_length=40)


class NotesAiResult(BaseModel):
    format: Literal["markdown"] = "markdown"
    content: str
    blocks: list[dict[str, Any]] = Field(default_factory=list)


class NotesAiActionResponse(BaseModel):
    service: Literal["lurg_notes"] = "lurg_notes"
    action: NotesAiAction
    request_id: UUID
    result: NotesAiResult
    usage: dict[str, Any] = Field(default_factory=dict)


class NotesGraphExtractRequest(BaseModel):
    """Explicit, user-consented analysis only. This endpoint never commits Graph."""
    document_text: str = Field(min_length=2, max_length=60000)
    note_title: str = Field(default="", max_length=300)
    document_revision: int = Field(ge=1)
    content_hash: str | None = Field(default=None, max_length=128)
    ai_analysis_consent: bool


class NotesGraphCandidate(BaseModel):
    candidate_id: UUID
    operation: str
    fact_text: str
    title: str
    explanation: str
    confidence: float
    source_excerpt: str
    changes: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "pending"


class NotesGraphExtractResponse(BaseModel):
    note_id: UUID
    document_revision: int
    candidates: list[NotesGraphCandidate] = Field(default_factory=list)


class EncryptedNoteEnvelope(BaseModel):
    algorithm: Literal["AES-256-GCM"]
    key_version: int = Field(ge=1, le=1000)
    nonce: str = Field(min_length=16, max_length=128)
    ciphertext: str = Field(min_length=16, max_length=2_000_000)
    content_hash: str = Field(min_length=32, max_length=128)
    document_revision: int = Field(ge=1)


class EncryptedNoteDocument(EncryptedNoteEnvelope):
    """An encrypted document plus the non-sensitive metadata required to restore it."""
    note_id: UUID
    created_at: datetime
    updated_at: datetime


class EncryptedNoteDocumentListResponse(BaseModel):
    documents: list[EncryptedNoteDocument] = Field(default_factory=list)


class EncryptedNoteTombstone(BaseModel):
    note_id: UUID
    document_revision: int = Field(ge=1)
    deleted_at: datetime


class EncryptedNoteTombstoneListResponse(BaseModel):
    tombstones: list[EncryptedNoteTombstone] = Field(default_factory=list)
