from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException

from ..course_config import COURSE_NAME
from ..schemas import KnowledgeRelationUpsertRequest, KnowledgeSearchRequest
from ..persistence import (
    delete_knowledge_graph_edge,
    list_knowledge_chunks,
    list_knowledge_documents,
    list_knowledge_graph_nodes,
    upsert_knowledge_graph_edge,
)
from ..services.document_parser import parse_document_text
from ..services.courseware_importer import ensure_courseware_knowledge_base, import_courseware_zip
from ..services.knowledge_directory_importer import import_local_knowledge_base, local_import_status
from ..services.knowledge_service import search_chunks
from ..services.knowledge_graph_service import (
    RELATION_LABELS,
    build_graph,
    filter_graph,
    neighbors,
    node_detail,
    remedial_path,
)
from ..services.vector_service import vector_status
from ..utils import ok, user_id_from_authorization

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/graph")
def get_knowledge_graph(
    course_id: str = "course_data_structure",
    chapter_id: str | None = None,
    node_types: str | None = None,
    difficulty: str | None = None,
    depth: int | None = None,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    graph = build_graph(course_id, user_id)
    filtered = filter_graph(
        graph,
        chapter_id=chapter_id,
        node_types=_split_csv(node_types),
        difficulty=difficulty,
        depth=depth,
    )
    return ok(filtered)


@router.get("/nodes/{node_id}")
def get_knowledge_node(
    node_id: str,
    course_id: str = "course_data_structure",
    authorization: str | None = Header(default=None),
) -> dict:
    result = node_detail(course_id, user_id_from_authorization(authorization), node_id)
    if not result:
        raise HTTPException(status_code=404, detail="知识节点不存在")
    return ok(result)


@router.get("/nodes/{node_id}/neighbors")
def get_knowledge_neighbors(
    node_id: str,
    course_id: str = "course_data_structure",
    depth: int = 1,
    authorization: str | None = Header(default=None),
) -> dict:
    return ok(neighbors(course_id, user_id_from_authorization(authorization), node_id, depth))


@router.get("/nodes/{node_id}/remedial-path")
def get_remedial_path(
    node_id: str,
    course_id: str = "course_data_structure",
    authorization: str | None = Header(default=None),
) -> dict:
    graph = build_graph(course_id, user_id_from_authorization(authorization))
    if not any(node["id"] == node_id for node in graph["nodes"]):
        raise HTTPException(status_code=404, detail="知识节点不存在")
    return ok({"targetNodeId": node_id, "steps": remedial_path(graph, node_id)})


@router.post("/nodes/{node_id}/mastery")
def update_knowledge_mastery(
    node_id: str,
) -> dict:
    raise HTTPException(
        status_code=410,
        detail=(
            "知识图谱不再接受任意掌握百分比。请在学习路径中完成对应资源或阶段，"
            "再通过 /api/learning-paths/me/mastery 确认掌握。"
        ),
    )


@router.post("/relations")
def save_knowledge_relation(
    payload: KnowledgeRelationUpsertRequest,
    course_id: str = "course_data_structure",
) -> dict:
    if payload.type not in RELATION_LABELS:
        raise HTTPException(status_code=400, detail="不支持的知识关系类型")
    node_ids = {node["id"] for node in list_knowledge_graph_nodes(course_id)}
    if payload.source not in node_ids or payload.target not in node_ids:
        raise HTTPException(status_code=400, detail="关系两端必须是已存在的知识节点")
    if payload.source == payload.target:
        raise HTTPException(status_code=400, detail="知识节点不能与自身建立关系")
    edge = {
        "id": payload.id or f"edge_teacher_{uuid4().hex[:12]}",
        "source": payload.source,
        "target": payload.target,
        "type": payload.type,
        "weight": payload.weight,
        "direction": payload.direction,
        "sourceType": "teacher",
        "verified": payload.verified,
        "relation": RELATION_LABELS[payload.type],
    }
    return ok(upsert_knowledge_graph_edge(course_id, edge))


@router.post("/relations/{edge_id}/delete")
def remove_knowledge_relation(edge_id: str) -> dict:
    if not delete_knowledge_graph_edge(edge_id):
        raise HTTPException(status_code=404, detail="教师维护的关系不存在，自动关系不能直接删除")
    return ok({"deleted": True, "id": edge_id})


@router.post("/search")
def search_knowledge(payload: KnowledgeSearchRequest) -> dict:
    result = search_chunks(payload.query, payload.top_k)
    chunks = [
        {
            "chunk_id": item["chunk_id"],
            "content": item["content"],
            "score": item["score"],
            "rerank_score": item["rerank_score"],
            "page": item["page"],
            "source_location": item["source_location"],
            "document_name": item["document_name"],
            "coverage": result["coverage"],
        }
        for item in result["items"]
    ]
    return ok({
        "items": chunks,
        "coverage": result["coverage"],
        "missing_knowledge": result["missing_knowledge"],
        "query_tokens": result["query_tokens"],
        "retrieval_pipeline": result.get("retrieval_pipeline", []),
    })


@router.get("/documents")
def list_documents() -> dict:
    return ok(list_knowledge_documents())


@router.get("/status")
def get_knowledge_status() -> dict:
    documents = list_knowledge_documents()
    chunks = list_knowledge_chunks()
    vector = vector_status()
    return ok({
        "documentCount": len(documents),
        "chunkCount": len(chunks),
        "chromaPath": vector.get("chromaPath"),
        "vectorStore": vector.get("vectorStore"),
        "embeddingProvider": vector.get("embeddingProvider"),
        "embeddingModel": vector.get("embeddingModel"),
        "knowledgeVectorCount": vector.get("knowledgeVectorCount"),
        "vectorQueryEnabled": True,
        "courseKnowledgeShared": True,
        "accountScope": "课程知识库全局共享；画像、学习进度和生成资源按账号隔离。",
        "retrievalPipeline": [
            "SQLite 课程知识片段",
            "Chroma/BGE-M3 或本地向量兜底",
            "关键词召回",
            "主题相关性重排",
            "去重后引用溯源",
        ],
    })


@router.post("/import-local")
def import_local_knowledge(payload: dict | None = None) -> dict:
    payload = payload or {}
    result = import_local_knowledge_base(payload.get("path"), force=bool(payload.get("force", False)))
    return ok({
        "importResult": result,
        "documents": list_knowledge_documents(),
    })


@router.get("/import-status")
def get_local_import_status() -> dict:
    return ok(local_import_status())


@router.get("/documents/{document_id}/chunks")
def list_document_chunks(document_id: str) -> dict:
    return ok(list_knowledge_chunks(document_id=document_id))


@router.post("/documents/parse-text")
def parse_text_document(payload: dict) -> dict:
    result = parse_document_text(payload.get("filename") or f"{COURSE_NAME}补充资料.md", payload.get("content") or "")
    return ok(result)


@router.post("/documents/import-courseware")
def import_courseware_document(payload: dict | None = None) -> dict:
    payload = payload or {}
    force = bool(payload.get("force", False))
    source_path = payload.get("path")
    result = import_courseware_zip(source_path, force=force) if source_path else {
        "imported": False,
        "reason": "暂无默认数据结构课件路径，请上传真实课程资料后导入。",
        "sourcePath": None,
        "documents": [],
        "chunks": [],
    }
    return ok({
        "importResult": {
            "imported": result.get("imported"),
            "reason": result.get("reason"),
            "sourcePath": result.get("sourcePath"),
            "fileCount": result.get("fileCount", len(result.get("documents", []))),
            "chunkCount": result.get("chunkCount", len(result.get("chunks", []))),
        },
        "documents": list_knowledge_documents(),
        "chunks": result.get("chunks", [])[:12],
    })


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]
