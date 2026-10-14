import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ...ai import get_ai_engine
from ...auth import current_user
from ...core.contracts import AIEngine
from ...db import get_db
from ...schemas import AnalyzeRequest, GraphChangeEdit, SearchRequest
from .resolver import graph_canonical_key_for_type, resolve_entity

router = APIRouter(prefix="/graph", tags=["Lurg Graph"])
CANONICAL_MERGE_TYPES = {"interest", "skill", "technology", "framework", "place"}

def normalized_confidence(value: object, default: float) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = default
    return max(0.0, min(1.0, confidence))

def graph_canonical_key(label: str) -> str:
    normalized = " ".join(label.strip().casefold().split())
    return graph_canonical_key_for_type("interest", label) or normalized

def json_safe(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value

async def append_fact_version(
    db: AsyncSession,
    user_id: UUID,
    fact_kind: str,
    fact_id: UUID,
    version: int,
    confidence: object,
    provenance: dict,
    payload: dict,
    lifecycle_status: str = "active",
    change_reason: str = "ingest",
) -> None:
    await db.execute(text("""INSERT INTO graph_fact_versions(
      user_id,fact_kind,fact_id,version,confidence,lifecycle_status,provenance,payload,change_reason
    ) VALUES(:uid,:fact_kind,:fact_id,:version,:confidence,:lifecycle_status,CAST(:provenance AS jsonb),CAST(:payload AS jsonb),:change_reason)
    ON CONFLICT(fact_kind,fact_id,version) DO NOTHING"""), {
        "uid": user_id,
        "fact_kind": fact_kind,
        "fact_id": fact_id,
        "version": version,
        "confidence": normalized_confidence(confidence, 0.5),
        "lifecycle_status": lifecycle_status,
        "provenance": json.dumps(json_safe(provenance), ensure_ascii=False),
        "payload": json.dumps(json_safe(payload), ensure_ascii=False),
        "change_reason": change_reason,
    })

async def graph_snapshot(user_id: UUID, db: AsyncSession) -> dict:
    nodes = (await db.execute(
        text("""SELECT id,type,label,properties,source_text,provenance,confidence,version,lifecycle_status,created_at,updated_at
          FROM nodes WHERE user_id=:uid AND lifecycle_status='active' ORDER BY created_at"""),
        {"uid": user_id},
    )).mappings().all()
    edges = (await db.execute(
        text("""SELECT id,source_id,target_id,relation,properties,provenance,confidence,version,created_at
          FROM edges
          WHERE user_id=:uid
            AND EXISTS(SELECT 1 FROM nodes source WHERE source.id=edges.source_id AND source.lifecycle_status='active')
            AND EXISTS(SELECT 1 FROM nodes target WHERE target.id=edges.target_id AND target.lifecycle_status='active')
          ORDER BY created_at"""),
        {"uid": user_id},
    )).mappings().all()
    return {
        "nodes": [{**dict(n), "id": str(n["id"])} for n in nodes],
        "edges": [{**dict(e), "id": str(e["id"]), "source_id": str(e["source_id"]), "target_id": str(e["target_id"])} for e in edges],
    }

async def upsert_graph_node(
    db: AsyncSession,
    user_id: UUID,
    ai: AIEngine,
    node_type: str,
    label: str,
    source_text: str,
    provenance: dict,
    confidence: float,
) -> dict:
    node_type = node_type[:40]
    label = label.strip()[:300]
    resolved = resolve_entity(node_type, label) if node_type in CANONICAL_MERGE_TYPES else {"label": label, "canonical_key": None, "alias": label}
    label = str(resolved["label"]).strip()[:300]
    canonical_key = resolved["canonical_key"] if node_type in CANONICAL_MERGE_TYPES else None
    properties = {"canonical_key": canonical_key, "alias": resolved["alias"]} if canonical_key else {}
    if canonical_key:
        legacy_rows = (await db.execute(text("""SELECT id,label,properties
          FROM nodes
          WHERE user_id=:uid AND type=:type AND lifecycle_status='active'"""), {
            "uid": user_id,
            "type": node_type,
        })).mappings().all()
        matching_legacy = [
            row for row in legacy_rows
            if (row["properties"] or {}).get("canonical_key") == canonical_key
            or resolve_entity(node_type, row["label"])["canonical_key"] == canonical_key
        ]
        primary_id = matching_legacy[0]["id"] if matching_legacy else None
        if primary_id:
            display_label = label if node_type == "place" else matching_legacy[0]["label"]
            row = (await db.execute(text("""UPDATE nodes
              SET label=:label,
                properties=nodes.properties || CAST(:props AS jsonb),
                provenance=nodes.provenance || CAST(:provenance AS jsonb),
                confidence=GREATEST(nodes.confidence, CAST(:confidence AS double precision)),
                version=nodes.version + 1,
                lifecycle_status='active',
                updated_at=now()
              WHERE id=:id AND user_id=:uid
              RETURNING id,type,label,properties,source_text,provenance,confidence,version,lifecycle_status"""), {
                "id": primary_id,
                "uid": user_id,
                "label": display_label,
                "props": json.dumps(properties, ensure_ascii=False),
                "provenance": json.dumps(provenance, ensure_ascii=False),
                "confidence": normalized_confidence(confidence, 0.85),
            })).mappings().one()
            duplicate_ids = [row["id"] for row in matching_legacy[1:]]
            if duplicate_ids:
                duplicate_rows = (await db.execute(text("""UPDATE nodes
                  SET lifecycle_status='superseded',
                    provenance=provenance || CAST(:provenance AS jsonb),
                    version=version + 1,
                    updated_at=now()
                  WHERE user_id=:uid AND id = ANY(:ids)
                  RETURNING id,type,label,properties,source_text,provenance,confidence,version,lifecycle_status"""), {
                    "uid": user_id,
                    "ids": duplicate_ids,
                    "provenance": json.dumps({**provenance, "superseded_by": str(row["id"])}, ensure_ascii=False),
                })).mappings().all()
                for duplicate in duplicate_rows:
                    await append_fact_version(
                        db,
                        user_id,
                        "node",
                        duplicate["id"],
                        duplicate["version"],
                        duplicate["confidence"],
                        duplicate["provenance"],
                        {"type": duplicate["type"], "label": duplicate["label"], "properties": duplicate["properties"], "source_text": duplicate["source_text"]},
                        duplicate["lifecycle_status"],
                        "canonical_merge",
                    )
            row = dict(row)
            await append_fact_version(
                db,
                user_id,
                "node",
                row["id"],
                row["version"],
                row["confidence"],
                row["provenance"],
                {"type": row["type"], "label": row["label"], "properties": row["properties"], "source_text": row["source_text"]},
                row["lifecycle_status"],
                "chat_confirm",
            )
            vector = await ai.embed(f"{row['type']}: {row['label']}", "RETRIEVAL_DOCUMENT")
            await db.execute(text("""INSERT INTO embeddings(node_id,user_id,embedding,model,provider,purpose)
              VALUES(:nid,:uid,CAST(:vec AS vector),:model,:provider,'RETRIEVAL_DOCUMENT')
              ON CONFLICT(node_id)
              DO UPDATE SET embedding=EXCLUDED.embedding,model=EXCLUDED.model,provider=EXCLUDED.provider,purpose=EXCLUDED.purpose,updated_at=now()"""), {
                "nid": row["id"],
                "uid": user_id,
                "vec": str(vector),
                "model": ai.embedding_model,
                "provider": ai.provider_name,
            })
            return row
        row = (await db.execute(text("""UPDATE nodes
          SET properties=nodes.properties || CAST(:props AS jsonb),
            provenance=nodes.provenance || CAST(:provenance AS jsonb),
            confidence=GREATEST(nodes.confidence, CAST(:confidence AS double precision)),
            version=nodes.version + 1,
            lifecycle_status='active',
            updated_at=now()
          WHERE user_id=:uid AND type=:type AND lifecycle_status='active'
            AND properties->>'canonical_key'=:canonical_key
          RETURNING id,type,label,properties,source_text,provenance,confidence,version,lifecycle_status"""), {
            "uid": user_id,
            "type": node_type,
            "props": json.dumps(properties, ensure_ascii=False),
            "provenance": json.dumps(provenance, ensure_ascii=False),
            "confidence": normalized_confidence(confidence, 0.85),
            "canonical_key": canonical_key,
        })).mappings().first()
        if row:
            row = dict(row)
            await append_fact_version(
                db,
                user_id,
                "node",
                row["id"],
                row["version"],
                row["confidence"],
                row["provenance"],
                {"type": row["type"], "label": row["label"], "properties": row["properties"], "source_text": row["source_text"]},
                row["lifecycle_status"],
                "chat_confirm",
            )
            vector = await ai.embed(f"{row['type']}: {row['label']}", "RETRIEVAL_DOCUMENT")
            await db.execute(text("""INSERT INTO embeddings(node_id,user_id,embedding,model,provider,purpose)
              VALUES(:nid,:uid,CAST(:vec AS vector),:model,:provider,'RETRIEVAL_DOCUMENT')
              ON CONFLICT(node_id)
              DO UPDATE SET embedding=EXCLUDED.embedding,model=EXCLUDED.model,provider=EXCLUDED.provider,purpose=EXCLUDED.purpose,updated_at=now()"""), {
                "nid": row["id"],
                "uid": user_id,
                "vec": str(vector),
                "model": ai.embedding_model,
                "provider": ai.provider_name,
            })
            return row
    row = (await db.execute(text("""INSERT INTO nodes(user_id,type,label,properties,source_text,provenance,confidence)
      VALUES(:uid,:type,:label,CAST(:props AS jsonb),:source,CAST(:provenance AS jsonb),:confidence)
      ON CONFLICT(user_id,type,label)
      DO UPDATE SET properties=nodes.properties || EXCLUDED.properties,
        provenance=nodes.provenance || EXCLUDED.provenance,
        confidence=GREATEST(nodes.confidence, EXCLUDED.confidence),
        version=nodes.version + 1,
        lifecycle_status='active',
        updated_at=now()
      RETURNING id,type,label,properties,source_text,provenance,confidence,version,lifecycle_status"""), {
        "uid": user_id,
        "type": node_type,
        "label": label,
        "props": json.dumps(properties, ensure_ascii=False),
        "source": source_text,
        "provenance": json.dumps(provenance, ensure_ascii=False),
        "confidence": normalized_confidence(confidence, 0.85),
    })).mappings().one()
    await append_fact_version(
        db,
        user_id,
        "node",
        row["id"],
        row["version"],
        row["confidence"],
        row["provenance"],
        {"type": row["type"], "label": row["label"], "properties": row["properties"], "source_text": row["source_text"]},
        row["lifecycle_status"],
        "chat_confirm",
    )
    vector = await ai.embed(f"{row['type']}: {row['label']}", "RETRIEVAL_DOCUMENT")
    await db.execute(text("""INSERT INTO embeddings(node_id,user_id,embedding,model,provider,purpose)
      VALUES(:nid,:uid,CAST(:vec AS vector),:model,:provider,'RETRIEVAL_DOCUMENT')
      ON CONFLICT(node_id)
      DO UPDATE SET embedding=EXCLUDED.embedding,model=EXCLUDED.model,provider=EXCLUDED.provider,purpose=EXCLUDED.purpose,updated_at=now()"""), {
        "nid": row["id"],
        "uid": user_id,
        "vec": str(vector),
        "model": ai.embedding_model,
        "provider": ai.provider_name,
    })
    return dict(row)

async def apply_graph_candidate(candidate: dict, user_id: UUID, db: AsyncSession, ai: AIEngine) -> dict:
    now = datetime.now(timezone.utc)
    expires_at = candidate.get("expires_at")
    if expires_at and expires_at < now:
        await db.execute(text("UPDATE graph_change_candidates SET status='expired' WHERE id=:id AND user_id=:uid"), {"id": candidate["id"], "uid": user_id})
        raise HTTPException(409, "Изменение Graph устарело. Создайте новое подтверждение.")
    source_type = str(candidate.get("source_type") or "chat")[:40]
    source_id = str(candidate.get("source_id") or candidate.get("message_id") or "")[:200]
    provenance = {
        "source_type": source_type,
        "source_id": source_id,
        "candidate_id": str(candidate["id"]),
        "confirmed": True,
        **(candidate.get("source_metadata") or {}),
    }
    changed_nodes = []
    proposed_nodes = candidate.get("proposed_nodes") or []
    proposed_edges = candidate.get("proposed_edges") or []
    operation = candidate.get("operation") or candidate.get("action") or "create"
    if operation in {"delete", "deactivate"}:
        for node in proposed_nodes:
            label = str(node.get("label") or node.get("value") or "").strip()
            node_type = str(node.get("type") or "fact")[:40]
            if not label:
                continue
            if node_type in CANONICAL_MERGE_TYPES:
                canonical_key = resolve_entity(node_type, label)["canonical_key"]
                existing = (await db.execute(text("""SELECT id,label,properties FROM nodes
                  WHERE user_id=:uid AND lifecycle_status='active' AND type=:type"""), {
                    "uid": user_id,
                    "type": node_type,
                })).mappings().all()
                target_ids = [
                    row["id"] for row in existing
                    if (row["properties"] or {}).get("canonical_key") == canonical_key
                    or resolve_entity(node_type, row["label"])["canonical_key"] == canonical_key
                ]
                rows = (await db.execute(text("""UPDATE nodes SET lifecycle_status='deleted',version=version+1,updated_at=now(),
                  provenance=provenance || CAST(:provenance AS jsonb)
                  WHERE user_id=:uid AND id = ANY(:ids)
                  RETURNING id,type,label,properties,source_text,provenance,confidence,version,lifecycle_status"""), {
                    "uid": user_id,
                    "ids": target_ids,
                    "provenance": json.dumps(provenance, ensure_ascii=False),
                })).mappings().all() if target_ids else []
            else:
                rows = (await db.execute(text("""UPDATE nodes SET lifecycle_status='deleted',version=version+1,updated_at=now(),
                  provenance=provenance || CAST(:provenance AS jsonb)
                  WHERE user_id=:uid AND lifecycle_status='active' AND type=:type AND label ILIKE :label
                  RETURNING id,type,label,properties,source_text,provenance,confidence,version,lifecycle_status"""), {
                    "uid": user_id,
                    "type": node_type,
                    "label": label,
                    "provenance": json.dumps(provenance, ensure_ascii=False),
                })).mappings().all()
            for row in rows:
                await append_fact_version(db, user_id, "node", row["id"], row["version"], row["confidence"], row["provenance"], {
                    "type": row["type"], "label": row["label"], "properties": row["properties"], "source_text": row["source_text"],
                }, row["lifecycle_status"], "chat_confirm")
    else:
        node_by_key: dict[str, dict] = {}
        user_node = await upsert_graph_node(db, user_id, ai, "person", "Пользователь", candidate["fact_text"], provenance, 0.95)
        node_by_key["user"] = user_node
        changed_nodes.append(user_node)
        relation_by_target = {
            str(edge.get("target") or edge.get("value") or ""): str(edge.get("relation") or "")
            for edge in proposed_edges
        }
        for node in proposed_nodes:
            node_type = str(node.get("type") or "fact")[:40]
            label = str(node.get("label") or node.get("value") or "").strip()
            node_key = str(node.get("key") or label)
            relation = relation_by_target.get(node_key, "")
            replace = node_type in {"profile", "age"} or (node_type == "occupation" and operation == "update")
            if node_type == "place" and relation in {"живет в", "живёт в", "проживает", "переехал в", "из", "родом из"}:
                relation_group = ["из", "родом из"] if relation in {"из", "родом из"} else ["живет в", "живёт в", "проживает", "переехал в"]
                old_edges = (await db.execute(text("""SELECT e.id,e.source_id,e.target_id,e.relation,e.properties,
                    e.provenance,e.confidence,e.version
                  FROM edges e
                  JOIN nodes source ON source.id=e.source_id AND source.user_id=:uid AND source.lifecycle_status='active'
                  JOIN nodes target ON target.id=e.target_id AND target.user_id=:uid AND target.lifecycle_status='active'
                  WHERE e.user_id=:uid AND source.type='person' AND target.type='place'
                    AND target.label <> :label AND e.relation = ANY(:relations)
                  FOR UPDATE OF e"""), {
                    "uid": user_id, "label": label[:300], "relations": relation_group,
                })).mappings().all()
                orphan_candidates = []
                for old_edge in old_edges:
                    await append_fact_version(db, user_id, "edge", old_edge["id"], old_edge["version"] + 1,
                        old_edge["confidence"], {**(old_edge["provenance"] or {}), **provenance}, {
                            "source_id": old_edge["source_id"], "target_id": old_edge["target_id"],
                            "relation": old_edge["relation"], "properties": old_edge["properties"],
                        }, "superseded", "chat_confirm")
                    orphan_candidates.append(old_edge["target_id"])
                    await db.execute(text("DELETE FROM edges WHERE id=:id AND user_id=:uid"), {"id": old_edge["id"], "uid": user_id})
                if orphan_candidates:
                    rows = (await db.execute(text("""UPDATE nodes n
                      SET lifecycle_status='superseded',version=n.version+1,updated_at=now(),
                        provenance=n.provenance || CAST(:provenance AS jsonb)
                      WHERE n.user_id=:uid AND n.id = ANY(:ids) AND n.lifecycle_status='active'
                        AND NOT EXISTS (SELECT 1 FROM edges remaining WHERE remaining.user_id=:uid AND remaining.target_id=n.id)
                      RETURNING n.id,n.type,n.label,n.properties,n.source_text,n.provenance,n.confidence,n.version,n.lifecycle_status"""), {
                        "uid": user_id, "ids": list(set(orphan_candidates)),
                        "provenance": json.dumps(provenance, ensure_ascii=False),
                    })).mappings().all()
                else:
                    rows = []
            elif replace and label:
                rows = (await db.execute(text("""UPDATE nodes SET lifecycle_status='superseded',version=version+1,updated_at=now(),
                  provenance=provenance || CAST(:provenance AS jsonb)
                  WHERE user_id=:uid AND type=:type AND lifecycle_status='active' AND label <> :label
                  RETURNING id,type,label,properties,source_text,provenance,confidence,version,lifecycle_status"""), {
                    "uid": user_id, "type": node_type, "label": label[:300],
                    "provenance": json.dumps(provenance, ensure_ascii=False),
                })).mappings().all()
            else:
                rows = []
            for row in rows:
                await append_fact_version(db, user_id, "node", row["id"], row["version"], row["confidence"], row["provenance"], {
                    "type": row["type"], "label": row["label"], "properties": row["properties"], "source_text": row["source_text"],
                }, row["lifecycle_status"], "chat_confirm")
        for node in proposed_nodes:
            label = str(node.get("label") or node.get("value") or "").strip()
            if not label:
                continue
            row = await upsert_graph_node(db, user_id, ai, str(node.get("type") or "fact"), label, candidate["fact_text"], provenance, float(node.get("confidence") or candidate["confidence"] or 0.85))
            node_by_key[str(node.get("key") or label)] = row
            changed_nodes.append(row)
        for edge in proposed_edges:
            source_key = str(edge.get("source") or "user")
            target_key = str(edge.get("target") or edge.get("value") or "")
            source = node_by_key.get(source_key) or node_by_key.get("user")
            target = node_by_key.get(target_key)
            if not source or not target or source["id"] == target["id"]:
                continue
            edge_row = (await db.execute(text("""INSERT INTO edges(user_id,source_id,target_id,relation,provenance,confidence)
              VALUES(:uid,:source,:target,:relation,CAST(:provenance AS jsonb),:confidence)
              ON CONFLICT(user_id,source_id,target_id,relation)
              DO UPDATE SET provenance=edges.provenance || EXCLUDED.provenance,
                confidence=GREATEST(edges.confidence, EXCLUDED.confidence),
                version=edges.version + 1
              RETURNING id,source_id,target_id,relation,properties,provenance,confidence,version"""), {
                "uid": user_id,
                "source": source["id"],
                "target": target["id"],
                "relation": str(edge.get("relation") or "связан с")[:80],
                "provenance": json.dumps(provenance, ensure_ascii=False),
                "confidence": normalized_confidence(edge.get("confidence"), 0.85),
            })).mappings().one()
            await append_fact_version(db, user_id, "edge", edge_row["id"], edge_row["version"], edge_row["confidence"], edge_row["provenance"], {
                "source_id": edge_row["source_id"],
                "target_id": edge_row["target_id"],
                "relation": edge_row["relation"],
                "properties": edge_row["properties"],
            }, change_reason="chat_confirm")
    await db.execute(text("""UPDATE graph_change_candidates
      SET status='confirmed',confirmed_at=now()
      WHERE id=:id AND user_id=:uid"""), {"id": candidate["id"], "uid": user_id})
    if operation not in {"delete", "deactivate"} and not changed_nodes:
        raise HTTPException(500, "Graph transaction did not create or update any node.")
    await db.execute(text("""INSERT INTO outbox_events(aggregate_type,aggregate_id,event_type,payload)
      VALUES('user_graph',:uid,'graph.updated',CAST(:payload AS jsonb))"""), {
        "uid": user_id,
        "payload": json.dumps({"user_id": str(user_id), "candidate_id": str(candidate["id"])}, ensure_ascii=False),
    })
    confirmed_name = next((
        str(node.get("label") or node.get("value") or "").strip()
        for node in proposed_nodes if str(node.get("type") or "") == "profile"
    ), "")
    await db.execute(text("""UPDATE users
      SET onboarding_completed=true,
        name=CASE WHEN :name <> '' THEN left(:name,120) ELSE name END
      WHERE id=:uid"""), {"uid": user_id, "name": confirmed_name})
    return {
        "status": "confirmed",
        "candidate_id": str(candidate["id"]),
        "node_ids": [str(row["id"]) for row in changed_nodes],
        "fragment": {"nodes": [{**json_safe(row), "id": str(row["id"])} for row in changed_nodes]},
    }

def graph_candidate_response(candidate: dict | None) -> dict:
    if not candidate:
        return {"candidate": None}
    from ..chat.router import candidate_event
    return {"candidate": candidate_event(candidate)}


@router.get("")
async def get_my_graph(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await graph_snapshot(user_id, db)

@router.get("/changes/pending")
async def pending_graph_change(
    conversation_id: UUID | None = None,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    params = {"uid": user_id, "thread": conversation_id}
    row = (await db.execute(text("""SELECT id,user_id,thread_id,message_id,action,operation,fact_text,
      proposed_nodes,proposed_edges,explanation,confidence,status,created_at,expires_at,source_type,source_id,source_metadata
      FROM graph_change_candidates
      WHERE user_id=:uid AND status='pending'
        AND (CAST(:thread AS uuid) IS NULL OR thread_id=CAST(:thread AS uuid))
        AND (expires_at IS NULL OR expires_at > now())
      ORDER BY created_at DESC LIMIT 1"""), params)).mappings().first()
    return graph_candidate_response(dict(row) if row else None)

async def load_pending_candidate(db: AsyncSession, user_id: UUID, candidate_id: UUID) -> dict:
    candidate = (await db.execute(text("""SELECT id,user_id,thread_id,message_id,action,operation,fact_text,
      proposed_nodes,proposed_edges,explanation,confidence,status,created_at,expires_at,source_type,source_id,source_metadata
      FROM graph_change_candidates WHERE id=:id AND user_id=:uid FOR UPDATE"""), {"id": candidate_id, "uid": user_id})).mappings().first()
    if not candidate:
        raise HTTPException(404, "Изменение Graph не найдено.")
    if candidate["status"] != "pending":
        raise HTTPException(409, "Изменение Graph уже обработано.")
    return dict(candidate)

@router.post("/changes/{candidate_id}/confirm")
async def confirm_graph_change(candidate_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), ai: AIEngine = Depends(get_ai_engine)):
    candidate = await load_pending_candidate(db, user_id, candidate_id)
    result = await apply_graph_candidate(candidate, user_id, db, ai)
    await db.commit()
    return result

@router.post("/changes/{candidate_id}/reject")
async def reject_graph_change(candidate_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    candidate = await load_pending_candidate(db, user_id, candidate_id)
    await db.execute(text("UPDATE graph_change_candidates SET status='rejected' WHERE id=:id AND user_id=:uid"), {"id": candidate["id"], "uid": user_id})
    await db.commit()
    return {"status": "rejected", "candidate_id": str(candidate_id)}


@router.patch("/changes/{candidate_id}")
async def edit_graph_change(
    candidate_id: UUID,
    body: GraphChangeEdit,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    candidate = await load_pending_candidate(db, user_id, candidate_id)
    nodes = list(candidate.get("proposed_nodes") or [])
    if len(nodes) != 1:
        raise HTTPException(409, "Изменение нескольких фактов пока нужно подтвердить или отменить по отдельности.")
    node = dict(nodes[0])
    node_type = str(node.get("type") or "fact")[:40]
    label = body.label.strip()
    if node_type in CANONICAL_MERGE_TYPES:
        label = str(resolve_entity(node_type, label)["label"])
    old_keys = {str(node.get("key") or ""), str(node.get("label") or "")}
    node_key = f"{node_type}:{label}"
    node["key"] = node_key
    node["label"] = label
    edges = []
    for raw_edge in candidate.get("proposed_edges") or []:
        edge = dict(raw_edge)
        if str(edge.get("target") or "") in old_keys:
            edge["target"] = node_key
        edges.append(edge)
    row = (await db.execute(text("""UPDATE graph_change_candidates
      SET proposed_nodes=CAST(:nodes AS jsonb),proposed_edges=CAST(:edges AS jsonb),
          explanation=:explanation
      WHERE id=:id AND user_id=:uid AND status='pending'
      RETURNING id,user_id,thread_id,message_id,action,operation,fact_text,
        proposed_nodes,proposed_edges,explanation,confidence,status,created_at,expires_at,
        source_type,source_id,source_metadata"""), {
            "id": candidate_id,
            "uid": user_id,
            "nodes": json.dumps([node], ensure_ascii=False),
            "edges": json.dumps(edges, ensure_ascii=False),
            "explanation": f"Пользователь изменил предложенное значение на: {label}",
        })).mappings().one()
    await db.commit()
    return graph_candidate_response(dict(row))

@router.post("/analyze")
async def analyze_graph(
    body: AnalyzeRequest,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    ai: AIEngine = Depends(get_ai_engine),
):
    graph = await ai.extract_graph(body.text)
    provenance = {
        "origin": "direct_user_input",
        "source_type": "text",
        "processor": {"provider": ai.provider_name, "model": ai.text_model},
    }
    ids = {}
    for node in graph["nodes"][:30]:
        key, label = str(node.get("key", ""))[:80], str(node.get("label", "")).strip()[:300]
        if not key or not label:
            continue
        row = (await db.execute(text("""INSERT INTO nodes(user_id,type,label,properties,source_text,provenance,confidence)
          VALUES(:uid,:type,:label,CAST(:props AS jsonb),:source,CAST(:provenance AS jsonb),:confidence) ON CONFLICT(user_id,type,label)
          DO UPDATE SET properties=nodes.properties || EXCLUDED.properties,
            provenance=nodes.provenance || EXCLUDED.provenance,
            confidence=GREATEST(nodes.confidence, EXCLUDED.confidence),
            version=nodes.version + 1,
            updated_at=now()
          RETURNING id,type,label,properties,source_text,provenance,confidence,version,lifecycle_status"""), {
            "uid": user_id, "type": str(node.get("type", "fact"))[:40], "label": label,
            "props": json.dumps(node.get("properties") or {}, ensure_ascii=False),
            "source": body.text,
            "provenance": json.dumps(provenance, ensure_ascii=False),
            "confidence": normalized_confidence(node.get("confidence"), 0.65),
        })).mappings().one()
        await append_fact_version(
            db,
            user_id,
            "node",
            row["id"],
            row["version"],
            row["confidence"],
            row["provenance"],
            {
                "type": row["type"],
                "label": row["label"],
                "properties": row["properties"],
                "source_text": row["source_text"],
            },
            row["lifecycle_status"],
        )
        ids[key] = row["id"]
        vector = await ai.embed(f"{row['type']}: {row['label']}", "RETRIEVAL_DOCUMENT")
        await db.execute(text("""INSERT INTO embeddings(node_id,user_id,embedding,model,provider,purpose)
          VALUES(:nid,:uid,CAST(:vec AS vector),:model,:provider,'RETRIEVAL_DOCUMENT') ON CONFLICT(node_id)
          DO UPDATE SET embedding=EXCLUDED.embedding,model=EXCLUDED.model,provider=EXCLUDED.provider,purpose=EXCLUDED.purpose,updated_at=now()"""), {
            "nid": row["id"], "uid": user_id, "vec": str(vector), "model": ai.embedding_model, "provider": ai.provider_name,
        })
    for edge in graph["edges"][:50]:
        source, target = ids.get(str(edge.get("source"))), ids.get(str(edge.get("target")))
        if source and target and source != target:
            edge_row = (await db.execute(text("""INSERT INTO edges(user_id,source_id,target_id,relation,provenance,confidence)
              VALUES(:uid,:source,:target,:relation,CAST(:provenance AS jsonb),:confidence) ON CONFLICT(user_id,source_id,target_id,relation)
              DO UPDATE SET provenance=edges.provenance || EXCLUDED.provenance,
                confidence=GREATEST(edges.confidence, EXCLUDED.confidence),
                version=edges.version + 1
              RETURNING id,source_id,target_id,relation,properties,provenance,confidence,version"""), {
                "uid": user_id, "source": source, "target": target,
                "relation": str(edge.get("relation", "связан с"))[:80],
                "provenance": json.dumps(provenance, ensure_ascii=False),
                "confidence": normalized_confidence(edge.get("confidence"), 0.6),
            })).mappings().one()
            await append_fact_version(
                db,
                user_id,
                "edge",
                edge_row["id"],
                edge_row["version"],
                edge_row["confidence"],
                edge_row["provenance"],
                {
                    "source_id": edge_row["source_id"],
                    "target_id": edge_row["target_id"],
                    "relation": edge_row["relation"],
                    "properties": edge_row["properties"],
                },
            )
    await db.execute(text("""INSERT INTO outbox_events(aggregate_type,aggregate_id,event_type,payload)
      VALUES('user_graph',:uid,'graph.updated',CAST(:payload AS jsonb))"""), {
        "uid": user_id, "payload": json.dumps({"user_id": str(user_id)}, ensure_ascii=False),
    })
    await db.execute(text("""INSERT INTO audit_log(user_id,action,resource_type)
      VALUES(:uid,'graph.analyze','user_graph')"""), {"uid": user_id})
    await db.commit()
    return await graph_snapshot(user_id, db)

@router.get("/nodes/{node_id}/details")
async def node_details(node_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    node = (await db.execute(text("""SELECT id,type,label,properties,source_text,provenance,confidence,version,lifecycle_status,created_at,updated_at
      FROM nodes WHERE id=:node_id AND user_id=:uid"""), {
        "node_id": node_id,
        "uid": user_id,
    })).mappings().first()
    if not node:
        raise HTTPException(404, "Узел не найден")
    outgoing = (await db.execute(text("""SELECT e.id,e.relation,e.confidence,e.version,e.created_at,
        n.id target_id,n.type target_type,n.label target_label
      FROM edges e JOIN nodes n ON n.id=e.target_id
      WHERE e.user_id=:uid AND e.source_id=:node_id ORDER BY e.created_at DESC LIMIT 80"""), {
        "node_id": node_id,
        "uid": user_id,
    })).mappings().all()
    incoming = (await db.execute(text("""SELECT e.id,e.relation,e.confidence,e.version,e.created_at,
        n.id source_id,n.type source_type,n.label source_label
      FROM edges e JOIN nodes n ON n.id=e.source_id
      WHERE e.user_id=:uid AND e.target_id=:node_id ORDER BY e.created_at DESC LIMIT 80"""), {
        "node_id": node_id,
        "uid": user_id,
    })).mappings().all()
    history = (await db.execute(text("""SELECT id,version,confidence,lifecycle_status,provenance,payload,change_reason,created_at
      FROM graph_fact_versions
      WHERE user_id=:uid AND fact_kind='node' AND fact_id=:node_id
      ORDER BY version DESC LIMIT 40"""), {
        "node_id": node_id,
        "uid": user_id,
    })).mappings().all()
    return {
        "node": {**dict(node), "id": str(node["id"]), "confidence": float(node["confidence"])},
        "relations": {
            "outgoing": [{**dict(row), "id": str(row["id"]), "target_id": str(row["target_id"]), "confidence": float(row["confidence"])} for row in outgoing],
            "incoming": [{**dict(row), "id": str(row["id"]), "source_id": str(row["source_id"]), "confidence": float(row["confidence"])} for row in incoming],
        },
        "history": [{**dict(row), "id": str(row["id"]), "confidence": float(row["confidence"])} for row in history],
    }

@router.get("/export")
async def export_graph(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    snapshot = await graph_snapshot(user_id, db)
    versions = (await db.execute(text("""SELECT id,fact_kind,fact_id,version,confidence,lifecycle_status,provenance,payload,change_reason,created_at
      FROM graph_fact_versions WHERE user_id=:uid ORDER BY created_at"""), {"uid": user_id})).mappings().all()
    return {
        **snapshot,
        "versions": [{**dict(row), "id": str(row["id"]), "fact_id": str(row["fact_id"]), "confidence": float(row["confidence"])} for row in versions],
    }

@router.get("/timeline")
async def graph_timeline(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    graph_rows = (await db.execute(text("""SELECT id,fact_kind,version,lifecycle_status,payload,change_reason,created_at
      FROM graph_fact_versions
      WHERE user_id=:uid
      ORDER BY created_at DESC LIMIT 30"""), {"uid": user_id})).mappings().all()
    sync_rows = (await db.execute(text("""SELECT id,provider,status,finished_at,created_at
      FROM connector_sync_jobs
      WHERE user_id=:uid
      ORDER BY COALESCE(finished_at,created_at) DESC LIMIT 10"""), {"uid": user_id})).mappings().all()
    items = []
    for row in graph_rows:
        payload = row["payload"] or {}
        label = payload.get("label") or payload.get("relation") or row["fact_kind"]
        action = "Обновлен" if int(row["version"] or 1) > 1 else "Добавлен"
        if row["lifecycle_status"] != "active":
            action = "Деактивирован"
        items.append({
            "id": str(row["id"]),
            "kind": "graph",
            "title": f"{action} {payload.get('type', row['fact_kind'])} {label}",
            "created_at": row["created_at"].isoformat(),
        })
    for row in sync_rows:
        items.append({
            "id": str(row["id"]),
            "kind": "connector",
            "title": f"Синхронизирован {row['provider']}",
            "created_at": (row["finished_at"] or row["created_at"]).isoformat(),
        })
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return {"items": items[:30]}

@router.delete("", status_code=204)
async def delete_graph(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(text("DELETE FROM embeddings WHERE user_id=:uid"), {"uid": user_id})
    await db.execute(text("DELETE FROM edges WHERE user_id=:uid"), {"uid": user_id})
    await db.execute(text("DELETE FROM nodes WHERE user_id=:uid"), {"uid": user_id})
    await db.execute(text("DELETE FROM graph_fact_versions WHERE user_id=:uid"), {"uid": user_id})
    await db.execute(text("""INSERT INTO audit_log(user_id,action,resource_type)
      VALUES(:uid,'graph.delete','user_graph')"""), {"uid": user_id})
    await db.commit()

@router.post("/search")
async def search_graph(
    body: SearchRequest,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    ai: AIEngine = Depends(get_ai_engine),
):
    vector = await ai.embed(body.query, "RETRIEVAL_QUERY")
    rows = (await db.execute(text("""SELECT n.id,n.type,n.label,n.properties,n.source_text,
      n.created_at,n.updated_at,
      1-(e.embedding <=> CAST(:vec AS vector)) similarity
      FROM embeddings e JOIN nodes n ON n.id=e.node_id WHERE e.user_id=:uid
      AND n.lifecycle_status='active'
      AND (CAST(:date_from AS timestamptz) IS NULL OR n.created_at >= CAST(:date_from AS timestamptz))
      AND (CAST(:date_to AS timestamptz) IS NULL OR n.created_at <= CAST(:date_to AS timestamptz))
      ORDER BY e.embedding::halfvec(3072) <=> CAST(:vec AS halfvec(3072)) LIMIT :limit"""), {
        "vec": str(vector), "uid": user_id, "limit": body.limit,
        "date_from": body.date_from, "date_to": body.date_to,
    })).mappings().all()
    context = [{
        **dict(r), "id": str(r["id"]),
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
        "similarity": round(float(r["similarity"]), 3),
    } for r in rows]
    return {"answer": await ai.answer(body.query, context), "nodes": context}

@router.get("/{requested_user_id}")
async def get_graph(requested_user_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if requested_user_id != user_id:
        raise HTTPException(403, "Нет доступа к этому графу")
    return await graph_snapshot(user_id, db)
