import json
import re
from hashlib import sha256
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...ai import get_ai_engine
from ...auth import current_user
from ...core.contracts import AIEngine
from ...db import get_db
from .ai_service import notes_ai_service
from .e2ee import lock_notes_owner
from .schemas import EncryptedNoteDocumentListResponse, EncryptedNoteEnvelope, EncryptedNoteTombstoneListResponse, NoteCreate, NoteDetail, NoteListResponse, NoteOpsRequest, NoteOpsResponse, NoteSummary, NoteUpdate, NotesAiActionRequest, NotesAiActionResponse, NotesGraphExtractRequest, NotesGraphExtractResponse, NoteBlockIn
from ..chat.router import candidate_change


router = APIRouter(prefix="/notes", tags=["Lurg Notes"])


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def note_summary(row: Any) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "revision": row["revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def sanitize_markdown(value: str) -> str:
    value = re.sub(r"<\s*/?\s*(script|iframe|object|embed|style|link|meta)[^>]*>", "", value, flags=re.I)
    value = re.sub(r"\s+on[a-zA-Z]+\s*=\s*(['\"]).*?\1", "", value)
    value = re.sub(r"javascript\s*:", "", value, flags=re.I)
    value = re.sub(r"data\s*:\s*(?!image/(png|jpeg|jpg|gif|webp);base64)", "", value, flags=re.I)
    return value.strip()


def normalize_ai_result(raw: dict[str, Any]) -> dict[str, Any]:
    content = raw.get("content") or raw.get("markdown") or raw.get("text") or ""
    if not isinstance(content, str):
        raise HTTPException(502, "ИИ вернул некорректный результат для редактора.")
    blocks = raw.get("blocks") if isinstance(raw.get("blocks"), list) else []
    return {"format": "markdown", "content": sanitize_markdown(content), "blocks": blocks}


async def save_notes_usage_event(db: AsyncSession, user_id: UUID, note_id: UUID, request_id: UUID, action: str, ai: AIEngine) -> dict[str, Any]:
    usage = getattr(ai, "usage", lambda: {})() or {}
    timings = getattr(ai, "timings", lambda: {})() or {}
    metadata = {
        "service": "lurg_notes",
        "action": action,
        "request_id": str(request_id),
        "note_id_hash": sha256(str(note_id).encode()).hexdigest()[:24],
        "web_used": False,
        "graph_used": False,
        "cache_hit": False,
    }
    await db.execute(text("""INSERT INTO ai_usage_events(
      user_id,thread_id,message_id,provider,model,mode,input_tokens,output_tokens,reasoning_tokens,
      latency_ms,first_token_latency_ms,cache_hit,cost,cost_estimated,metadata
    ) VALUES(:uid,NULL,NULL,:provider,:model,:mode,:input_tokens,:output_tokens,:reasoning_tokens,
      :latency,:first_token_latency,:cache_hit,:cost,:cost_estimated,CAST(:metadata AS jsonb))"""), {
        "uid": user_id,
        "provider": ai.provider_name,
        "model": ai.text_model,
        "mode": f"notes.{action}",
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens") or 0,
        "latency": timings.get("total_ms") or timings.get("connection_ms"),
        "first_token_latency": timings.get("first_token_ms"),
        "cache_hit": False,
        "cost": usage.get("cost"),
        "cost_estimated": bool(usage.get("cost_estimated", True)),
        "metadata": json.dumps(metadata, ensure_ascii=False),
    })
    return {**metadata, **usage, "model": ai.text_model, "provider": ai.provider_name, "total_ms": timings.get("total_ms")}


async def load_note_row(db: AsyncSession, user_id: UUID, note_id: UUID) -> Any:
    row = (await db.execute(
        text("""SELECT id,title,revision,created_at,updated_at
          FROM notes
          WHERE id=:note_id AND user_id=:uid AND deleted_at IS NULL"""),
        {"note_id": note_id, "uid": user_id},
    )).mappings().first()
    if not row:
        raise HTTPException(404, "Заметка не найдена")
    return row


async def guard_legacy_write(db: AsyncSession, user_id: UUID, note_id: UUID) -> None:
    await lock_notes_owner(db, user_id)
    protected = await db.scalar(text("SELECT 1 FROM note_e2ee_documents WHERE note_id=:id AND user_id=:uid AND format_version=2"), {"id": note_id, "uid": user_id})
    if protected:
        raise HTTPException(409, {"code": "E2EE_V2_REQUIRED", "message": "Используйте API доверенного устройства Notes."})


def note_analysis_lines(document: str) -> list[str]:
    """Remove presentation-only Markdown before deterministic personal-fact extraction."""
    lines: list[str] = []
    in_code = False
    for raw in document.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped or stripped.startswith(">") or stripped.startswith("|"):
            continue
        text_value = re.sub(r"^#{1,6}\s+|^\s*[-*+]\s+(?:\[[ xX]\]\s+)?|^\s*\d+\.\s+", "", stripped)
        text_value = re.sub(r"`([^`]+)`", r"\1", text_value)
        text_value = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", text_value)
        if len(text_value.strip()) >= 3:
            lines.append(text_value.strip())
    return lines


async def create_notes_graph_candidate(
    db: AsyncSession,
    user_id: UUID,
    note_id: UUID,
    note_title: str,
    revision: int,
    content_hash: str,
    excerpt: str,
    change: dict,
) -> dict:
    nodes = change.get("proposed_nodes") or []
    edges = change.get("proposed_edges") or []
    confidence = max((float(node.get("confidence") or 0.75) for node in nodes), default=0.75)
    metadata = {
        "product": "Lurg Notes",
        "note_id": str(note_id),
        "note_title": note_title[:300],
        "source_revision": revision,
        "content_hash": content_hash,
        "source_excerpt": excerpt[:700],
        "analysis_mode": "explicit_user_action",
    }
    row = (await db.execute(text("""INSERT INTO graph_change_candidates(
      user_id,thread_id,message_id,action,operation,fact_text,proposed_nodes,proposed_edges,explanation,confidence,expires_at,
      source_type,source_id,source_metadata
    ) VALUES(:uid,NULL,NULL,:action,:operation,:fact,CAST(:nodes AS jsonb),CAST(:edges AS jsonb),:explanation,:confidence,
      now()+interval '24 hours','lurg_notes',:source_id,CAST(:metadata AS jsonb))
    RETURNING id,operation,fact_text,proposed_nodes,proposed_edges,explanation,confidence,status"""), {
        "uid": user_id,
        "action": str(change.get("operation") or "create")[:20],
        "operation": str(change.get("operation") or "create")[:20],
        "fact": str(change.get("fact_text") or excerpt)[:2000],
        "nodes": json.dumps(nodes, ensure_ascii=False),
        "edges": json.dumps(edges, ensure_ascii=False),
        "explanation": str(change.get("explanation") or "Факт найден в заметке.")[:1000],
        "confidence": max(0.0, min(1.0, confidence)),
        "source_id": str(note_id),
        "metadata": json.dumps(metadata, ensure_ascii=False),
    })).mappings().one()
    return {**dict(row), "title": str(change.get("title") or "Добавить в Lurg Graph"), "source_excerpt": excerpt[:700]}


@router.get("", response_model=NoteListResponse)
async def list_notes(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        text("""SELECT id,title,revision,created_at,updated_at
          FROM notes
          WHERE user_id=:uid AND deleted_at IS NULL
          ORDER BY updated_at DESC, created_at DESC
          LIMIT 200"""),
        {"uid": user_id},
    )).mappings().all()
    return {"notes": [note_summary(row) for row in rows]}


@router.post("", status_code=201, response_model=NoteDetail)
async def create_note(body: NoteCreate, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if body.client_note_id:
        existing = (await db.execute(text("""SELECT id,title,revision,created_at,updated_at,deleted_at FROM notes
          WHERE id=:id AND user_id=:uid"""), {"id": body.client_note_id, "uid": user_id})).mappings().first()
        if existing:
            if existing["deleted_at"] is not None:
                raise HTTPException(410, "Заметка была удалена на другом устройстве.")
            blocks = (await db.execute(text("""SELECT id,parent_id,position,type,content,attrs,origin,revision,updated_at
              FROM note_blocks WHERE note_id=:note_id AND user_id=:uid AND deleted_at IS NULL ORDER BY position"""), {"note_id": body.client_note_id, "uid": user_id})).mappings().all()
            return {"note": note_summary(existing), "blocks": [dict(block) for block in blocks]}
    row = (await db.execute(
        text("""INSERT INTO notes(id,user_id,title)
          VALUES(COALESCE(:id, gen_random_uuid()),:uid,:title)
          RETURNING id,title,revision,created_at,updated_at"""),
        {"id": body.client_note_id, "uid": user_id, "title": body.title.strip()},
    )).mappings().one()
    initial = body.initial_block
    block = (await db.execute(
        text("""INSERT INTO note_blocks(id,user_id,note_id,parent_id,position,type,content,attrs,origin)
          VALUES(COALESCE(:id, gen_random_uuid()),:uid,:note_id,:parent_id,:position,:type,CAST(:content AS jsonb),CAST(:attrs AS jsonb),:origin)
          RETURNING id,parent_id,position,type,content,attrs,origin,revision,updated_at"""),
        {
            "id": initial.id if initial else None,
            "uid": user_id,
            "note_id": row["id"],
            "parent_id": initial.parent_id if initial else None,
            "position": initial.position if initial else Decimal("1000"),
            "type": initial.type if initial else "paragraph",
            "content": json_dumps(initial.content if initial else {"text": ""}),
            "attrs": json_dumps(initial.attrs if initial else {}),
            "origin": initial.origin if initial else "user",
        },
    )).mappings().one()
    await db.commit()
    return {"note": note_summary(row), "blocks": [dict(block)]}


@router.post("/sync", response_model=NoteListResponse)
async def sync_notes(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await list_notes(user_id, db)


@router.get("/encrypted-documents", response_model=EncryptedNoteDocumentListResponse)
async def list_encrypted_documents(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """List only the caller's encrypted envelopes for device-local recovery.

    The API never decrypts or inspects ciphertext; authentication and the note owner
    are both enforced in the query.
    """
    rows = (await db.execute(text("""SELECT d.note_id,n.created_at,d.updated_at,
      d.algorithm,d.key_version,d.nonce,d.ciphertext,d.content_hash,d.document_revision
      FROM note_encrypted_documents d
      JOIN notes n ON n.id=d.note_id AND n.user_id=d.user_id AND n.deleted_at IS NULL
      WHERE d.user_id=:uid
      ORDER BY d.updated_at DESC
      LIMIT 200"""), {"uid": user_id})).mappings().all()
    return {"documents": [dict(row) for row in rows]}


@router.get("/encrypted-tombstones", response_model=EncryptedNoteTombstoneListResponse)
async def list_encrypted_tombstones(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Deleted note IDs are sync metadata, never document plaintext."""
    rows = (await db.execute(text("""SELECT id AS note_id,revision AS document_revision,deleted_at
      FROM notes WHERE user_id=:uid AND deleted_at IS NOT NULL
      ORDER BY deleted_at DESC LIMIT 200"""), {"uid": user_id})).mappings().all()
    return {"tombstones": [dict(row) for row in rows]}


@router.put("/{note_id}/encrypted-document", response_model=EncryptedNoteEnvelope)
async def put_encrypted_document(
    note_id: UUID,
    envelope: EncryptedNoteEnvelope,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await guard_legacy_write(db, user_id, note_id)
    state = (await db.execute(text("""SELECT deleted_at FROM notes WHERE id=:note_id AND user_id=:uid"""),
      {"note_id": note_id, "uid": user_id})).mappings().first()
    if not state:
        raise HTTPException(404, "Заметка не найдена")
    if state["deleted_at"] is not None:
        raise HTTPException(410, "Заметка была удалена на другом устройстве.")
    row = (await db.execute(text("""INSERT INTO note_encrypted_documents(
      note_id,user_id,algorithm,key_version,nonce,ciphertext,content_hash,document_revision
    ) VALUES(:note_id,:uid,:algorithm,:key_version,:nonce,:ciphertext,:content_hash,:document_revision)
    ON CONFLICT(note_id) DO UPDATE SET algorithm=EXCLUDED.algorithm,key_version=EXCLUDED.key_version,
      nonce=EXCLUDED.nonce,ciphertext=EXCLUDED.ciphertext,content_hash=EXCLUDED.content_hash,
      document_revision=EXCLUDED.document_revision,updated_at=now()
    WHERE note_encrypted_documents.user_id=:uid
      AND (EXCLUDED.document_revision > note_encrypted_documents.document_revision
        OR (EXCLUDED.document_revision = note_encrypted_documents.document_revision
          AND EXCLUDED.content_hash = note_encrypted_documents.content_hash))
    RETURNING algorithm,key_version,nonce,ciphertext,content_hash,document_revision"""), {
        "note_id": note_id, "uid": user_id, **envelope.model_dump(),
    })).mappings().one_or_none()
    if not row:
        raise HTTPException(409, "Не удалось сохранить зашифрованную версию заметки.")
    await db.commit()
    return dict(row)


@router.get("/{note_id}/encrypted-document", response_model=EncryptedNoteEnvelope)
async def get_encrypted_document(note_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await load_note_row(db, user_id, note_id)
    row = (await db.execute(text("""SELECT algorithm,key_version,nonce,ciphertext,content_hash,document_revision
      FROM note_encrypted_documents WHERE note_id=:note_id AND user_id=:uid"""), {"note_id": note_id, "uid": user_id})).mappings().first()
    if not row:
        raise HTTPException(404, "Зашифрованная версия заметки пока не синхронизирована.")
    return dict(row)


@router.post("/{note_id}/graph-candidates", response_model=NotesGraphExtractResponse)
async def extract_notes_graph_candidates(
    note_id: UUID,
    body: NotesGraphExtractRequest,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create reviewable Graph candidates. This route never writes nodes or edges."""
    await load_note_row(db, user_id, note_id)
    if not body.ai_analysis_consent:
        raise HTTPException(403, "Требуется отдельное согласие на анализ заметки для Graph.")

    digest = body.content_hash or sha256(body.document_text.encode("utf-8")).hexdigest()
    seen: set[tuple[str, str]] = set()
    candidates: list[dict] = []
    for excerpt in note_analysis_lines(body.document_text):
        # The shared deterministic extractor is intentionally called only on meaningful text lines.
        # It rejects transient statements and does not interpret code, quotes, or table markup as facts.
        change = candidate_change(excerpt)
        if not change:
            continue
        nodes = change.get("proposed_nodes") or []
        edges = change.get("proposed_edges") or []
        for node in nodes:
            node_type = str(node.get("type") or "fact")[:40]
            label = str(node.get("label") or "").strip()[:300]
            key = (node_type.casefold(), label.casefold())
            if not label or key in seen:
                continue
            seen.add(key)
            node_key = str(node.get("key") or label)
            single_edges = [edge for edge in edges if str(edge.get("target") or edge.get("value") or "") == node_key]
            single_change = {
                **change,
                "fact_text": excerpt,
                "proposed_nodes": [node],
                "proposed_edges": single_edges,
                "summary": f"{node_type}: {label}",
                "explanation": f"Найдено в заметке: {excerpt[:240]}",
            }
            candidates.append(await create_notes_graph_candidate(
                db, user_id, note_id, body.note_title, body.document_revision, digest, excerpt, single_change
            ))
    await db.commit()
    return {
        "note_id": note_id,
        "document_revision": body.document_revision,
        "candidates": [
            {
                "candidate_id": item["id"],
                "operation": item["operation"],
                "fact_text": item["fact_text"],
                "title": item["title"],
                "explanation": item["explanation"],
                "confidence": float(item["confidence"]),
                "source_excerpt": item["source_excerpt"],
                "changes": [
                    {"type": node.get("type"), "value": node.get("label"), "relation": next((edge.get("relation") for edge in item["proposed_edges"] if edge.get("target") == node.get("key")), None)}
                    for node in item["proposed_nodes"]
                ],
                "status": item["status"],
            }
            for item in candidates
        ],
    }


@router.get("/{note_id}", response_model=NoteDetail)
async def get_note(note_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    note = await load_note_row(db, user_id, note_id)
    blocks = (await db.execute(
        text("""SELECT id,parent_id,position,type,content,attrs,origin,revision,updated_at
          FROM note_blocks
          WHERE user_id=:uid AND note_id=:note_id AND deleted_at IS NULL
          ORDER BY position ASC, created_at ASC"""),
        {"uid": user_id, "note_id": note_id},
    )).mappings().all()
    return {"note": note_summary(note), "blocks": [dict(block) for block in blocks]}


@router.patch("/{note_id}", response_model=NoteSummary)
async def update_note(note_id: UUID, body: NoteUpdate, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await guard_legacy_write(db, user_id, note_id)
    await load_note_row(db, user_id, note_id)
    row = (await db.execute(
        text("""UPDATE notes
          SET title=COALESCE(:title,title),
            metadata=CASE WHEN :metadata IS NULL THEN metadata ELSE CAST(:metadata AS jsonb) END,
            revision=revision + 1,
            updated_at=now()
          WHERE id=:note_id AND user_id=:uid AND deleted_at IS NULL
          RETURNING id,title,revision,created_at,updated_at"""),
        {
            "note_id": note_id,
            "uid": user_id,
            "title": body.title.strip() if body.title is not None else None,
            "metadata": json_dumps(body.metadata) if body.metadata is not None else None,
        },
    )).mappings().one()
    await db.commit()
    return note_summary(row)


@router.patch("/{note_id}/ops", response_model=NoteOpsResponse)
async def apply_operations(note_id: UUID, body: NoteOpsRequest, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await guard_legacy_write(db, user_id, note_id)
    await load_note_row(db, user_id, note_id)
    applied: list[str] = []
    skipped: list[str] = []
    conflicts: list[dict[str, Any]] = []
    for operation in body.operations:
        existing = await db.scalar(
            text("""SELECT resulting_revision FROM note_operations
              WHERE user_id=:uid AND note_id=:note_id AND client_operation_id=:op_id"""),
            {"uid": user_id, "note_id": note_id, "op_id": operation.client_operation_id},
        )
        if existing is not None:
            skipped.append(operation.client_operation_id)
            continue

        current_revision = int(await db.scalar(text("SELECT revision FROM notes WHERE id=:note_id AND user_id=:uid FOR UPDATE"), {"note_id": note_id, "uid": user_id}) or 1)
        if operation.base_revision != current_revision:
            conflicts.append({"operation_id": operation.client_operation_id, "reason": "revision_conflict", "document_revision": current_revision})
            continue
        next_revision = current_revision + 1
        payload = operation.payload
        if operation.operation_type == "update_note":
            title = payload.get("title")
            await db.execute(
                text("""UPDATE notes
                  SET title=COALESCE(:title,title), revision=:revision, updated_at=now()
                  WHERE id=:note_id AND user_id=:uid"""),
                {"note_id": note_id, "uid": user_id, "title": str(title).strip()[:300] if title is not None else None, "revision": next_revision},
            )
        elif operation.operation_type == "upsert_block":
            block = payload.get("block")
            if not isinstance(block, dict) or not block.get("id"):
                conflicts.append({"operation_id": operation.client_operation_id, "reason": "invalid_block"})
                continue
            try:
                block = NoteBlockIn.model_validate({**block, "position": block.get("position", "1000")}).model_dump()
            except ValueError:
                conflicts.append({"operation_id": operation.client_operation_id, "reason": "invalid_block"})
                continue
            content = block.get("content") if isinstance(block.get("content"), dict) else {}
            attrs = block.get("attrs") if isinstance(block.get("attrs"), dict) else {}
            await db.execute(
                text("""INSERT INTO note_blocks(id,user_id,note_id,parent_id,position,type,content,attrs,origin,revision)
                  VALUES(:id,:uid,:note_id,:parent_id,:position,:type,CAST(:content AS jsonb),CAST(:attrs AS jsonb),:origin,:revision)
                  ON CONFLICT(id) DO UPDATE SET
                    parent_id=EXCLUDED.parent_id,
                    position=EXCLUDED.position,
                    type=EXCLUDED.type,
                    content=EXCLUDED.content,
                    attrs=EXCLUDED.attrs,
                    origin=EXCLUDED.origin,
                    revision=EXCLUDED.revision,
                    deleted_at=NULL,
                    updated_at=now()
                  WHERE note_blocks.user_id=:uid AND note_blocks.note_id=:note_id"""),
                {
                    "id": block["id"],
                    "uid": user_id,
                    "note_id": note_id,
                    "parent_id": block.get("parent_id"),
                    "position": Decimal(str(block.get("position") or "1000")),
                    "type": str(block.get("type") or "paragraph")[:40],
                    "content": json_dumps(content),
                    "attrs": json_dumps(attrs),
                    "origin": str(block.get("origin") or "user")[:40],
                    "revision": next_revision,
                },
            )
            await db.execute(text("UPDATE notes SET revision=:revision, updated_at=now() WHERE id=:note_id AND user_id=:uid"), {"revision": next_revision, "note_id": note_id, "uid": user_id})
        elif operation.operation_type == "delete_block":
            block_id = payload.get("block_id")
            try:
                block_id = UUID(str(block_id))
            except ValueError:
                block_id = None
            if not block_id:
                conflicts.append({"operation_id": operation.client_operation_id, "reason": "missing_block_id"})
                continue
            await db.execute(
                text("""UPDATE note_blocks SET deleted_at=now(), revision=:revision, updated_at=now()
                  WHERE id=:block_id AND note_id=:note_id AND user_id=:uid"""),
                {"block_id": block_id, "note_id": note_id, "uid": user_id, "revision": next_revision},
            )
            await db.execute(text("UPDATE notes SET revision=:revision, updated_at=now() WHERE id=:note_id AND user_id=:uid"), {"revision": next_revision, "note_id": note_id, "uid": user_id})

        await db.execute(
            text("""INSERT INTO note_operations(user_id,note_id,client_operation_id,base_revision,resulting_revision,operation_type,payload,status)
              VALUES(:uid,:note_id,:op_id,:base_revision,:resulting_revision,:operation_type,CAST(:payload AS jsonb),'applied')"""),
            {
                "uid": user_id,
                "note_id": note_id,
                "op_id": operation.client_operation_id,
                "base_revision": operation.base_revision,
                "resulting_revision": next_revision,
                "operation_type": operation.operation_type,
                "payload": json_dumps(payload),
            },
        )
        applied.append(operation.client_operation_id)

    note = await load_note_row(db, user_id, note_id)
    await db.commit()
    return {"note": note_summary(note), "applied": applied, "skipped": skipped, "conflicts": conflicts}


@router.post("/{note_id}/ai/actions", response_model=NotesAiActionResponse)
async def run_notes_ai_action(
    note_id: UUID,
    body: NotesAiActionRequest,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    ai: AIEngine = Depends(get_ai_engine),
):
    note = await load_note_row(db, user_id, note_id)
    if body.document_revision is not None and body.document_revision > int(note["revision"]):
        raise HTTPException(409, "Версия заметки устарела. Синхронизируйте документ и повторите действие.")
    request_id = uuid4()
    ai_result = await notes_ai_service.run_action(
        ai=ai,
        user_id=user_id,
        note_id=note_id,
        request_id=request_id,
        body=body,
    )
    result = normalize_ai_result(ai_result.raw_result)
    usage = await save_notes_usage_event(db, user_id, note_id, request_id, body.action, ai)
    await db.commit()
    return {"service": "lurg_notes", "action": body.action, "request_id": request_id, "result": result, "usage": usage}


@router.delete("/{note_id}", status_code=204)
async def delete_note(note_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await guard_legacy_write(db, user_id, note_id)
    state = (await db.execute(text("SELECT deleted_at FROM notes WHERE id=:note_id AND user_id=:uid"),
      {"note_id": note_id, "uid": user_id})).mappings().first()
    if not state:
        raise HTTPException(404, "Заметка не найдена")
    if state["deleted_at"] is not None:
        return
    await db.execute(
        text("UPDATE notes SET deleted_at=now(), updated_at=now(), revision=revision+1,sync_sequence=nextval('notes_sync_sequence') WHERE id=:note_id AND user_id=:uid"),
        {"note_id": note_id, "uid": user_id},
    )
    await db.commit()
