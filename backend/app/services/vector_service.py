from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Any

from ..env import load_backend_env
from ..persistence import list_vectors, upsert_vector


load_backend_env()

PROFILE_NAMESPACE = "profile_features"
RESOURCE_NAMESPACE = "learning_resources"
KNOWLEDGE_NAMESPACE = "course_knowledge_chunks"
FALLBACK_DIMENSION = 384
_EMBEDDING_MODEL: Any | None = None
_EMBEDDING_PROVIDER: str | None = None


def configured_embedding_model() -> str:
    return os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")


def configured_vector_store() -> str:
    preferred = os.getenv("VECTOR_STORE", "chroma").lower()
    if preferred == "chroma" and _chroma_available():
        return "ChromaDB"
    if preferred == "milvus" and _milvus_available():
        return "Milvus"
    return "SQLiteVectorFallback"


def embedding_provider() -> str:
    if _EMBEDDING_PROVIDER:
        return _EMBEDDING_PROVIDER
    if os.getenv("VECTOR_EMBEDDING_MODE", "fallback").lower() != "real":
        return "local_hash_fallback"
    return f"{configured_embedding_model()} (lazy)"


def vector_status() -> dict[str, Any]:
    return {
        "embeddingProvider": embedding_provider(),
        "embeddingModel": configured_embedding_model(),
        "vectorStore": configured_vector_store(),
        "chromaPath": str(resolved_chroma_path()),
        "profileVectorCount": len(list_vectors(PROFILE_NAMESPACE)),
        "resourceVectorCount": len(list_vectors(RESOURCE_NAMESPACE)),
        "knowledgeVectorCount": len(list_vectors(KNOWLEDGE_NAMESPACE)),
        "note": (
            "当前使用本地哈希向量兜底；配置 VECTOR_EMBEDDING_MODE=real 后会优先加载 BGE-M3。"
            if embedding_provider() == "local_hash_fallback"
            else "当前使用真实向量模型生成画像、资源和课程知识片段向量。"
        ),
    }


def resolved_chroma_path() -> Path:
    configured = os.getenv("CHROMA_PATH")
    backend_root = Path(__file__).resolve().parents[2]
    project_root = backend_root.parent
    if not configured:
        return backend_root / "data" / "chroma"

    path = Path(configured)
    if path.is_absolute():
        return path

    parts = path.parts
    if parts and parts[0].lower() == backend_root.name.lower():
        return project_root / path
    return backend_root / path


def embed_text(text: str) -> list[float]:
    if os.getenv("VECTOR_EMBEDDING_MODE", "fallback").lower() != "real":
        return _hash_embedding(text)
    model = _get_sentence_transformer()
    if model is not None:
        vector = model.encode([text], normalize_embeddings=True)[0]
        return [float(value) for value in vector]
    return _hash_embedding(text)


def index_profile(profile_items: list[dict[str, Any]], *, user_id: str = "demo_student") -> dict[str, Any]:
    text = profile_to_text(profile_items)
    embedding = embed_text(text)
    metadata = {
        "userId": user_id,
        "dimensions": [item.get("dimension") for item in profile_items],
        "embeddingModel": configured_embedding_model(),
        "embeddingProvider": embedding_provider(),
    }
    upsert_vector(PROFILE_NAMESPACE, f"profile::{user_id}", text, metadata, embedding)
    _upsert_external(PROFILE_NAMESPACE, f"profile::{user_id}", text, metadata, embedding)
    return {"id": f"profile::{user_id}", "text": text, "metadata": metadata}


def index_resources(resources: list[dict[str, Any]]) -> None:
    for resource in resources:
        text = resource_to_text(resource)
        embedding = embed_text(text)
        metadata = {
            "resourceId": resource.get("id"),
            "resourceType": resource.get("resourceType"),
            "title": resource.get("title"),
            "auditStatus": resource.get("auditStatus"),
            "embeddingModel": configured_embedding_model(),
            "embeddingProvider": embedding_provider(),
        }
        vector_id = f"resource::{resource.get('id')}"
        upsert_vector(RESOURCE_NAMESPACE, vector_id, text, metadata, embedding)
        _upsert_external(RESOURCE_NAMESPACE, vector_id, text, metadata, embedding)


def index_knowledge_chunks(chunks: list[dict[str, Any]]) -> None:
    indexed_ids: list[str] = []
    for chunk in chunks:
        chunk_id = str(chunk.get("chunkId") or chunk.get("chunk_id") or "")
        if not chunk_id:
            continue
        text = knowledge_chunk_to_text(chunk)
        embedding = embed_text(text)
        metadata = {
            "chunkId": chunk_id,
            "documentId": chunk.get("documentId") or chunk.get("document_id"),
            "documentName": chunk.get("documentName") or chunk.get("document_name"),
            "courseId": chunk.get("courseId") or chunk.get("course_id"),
            "page": chunk.get("page"),
            "section": chunk.get("section"),
            "sourceType": chunk.get("sourceType", "course_material"),
            "keywords": chunk.get("keywords", []),
            "embeddingModel": configured_embedding_model(),
            "embeddingProvider": embedding_provider(),
        }
        vector_id = f"knowledge::{chunk_id}"
        upsert_vector(KNOWLEDGE_NAMESPACE, vector_id, text, metadata, embedding)
        _upsert_external(KNOWLEDGE_NAMESPACE, vector_id, text, metadata, embedding)
        indexed_ids.append(chunk_id)
    if indexed_ids:
        from ..persistence import mark_knowledge_chunks_indexed

        mark_knowledge_chunks_indexed(indexed_ids)


def query_knowledge_vectors(query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
    query_embedding = embed_text(query)
    external_rows = _query_external(KNOWLEDGE_NAMESPACE, query_embedding, top_k=top_k)
    if external_rows:
        return external_rows
    rows: list[dict[str, Any]] = []
    for row in list_vectors(KNOWLEDGE_NAMESPACE):
        rows.append({
            "id": row["id"],
            "score": round(_hybrid_similarity(query, row["text"], query_embedding, row["embedding"]), 4),
            "metadata": row["metadata"],
            "text": row["text"],
        })
    rows.sort(key=lambda item: item["score"], reverse=True)
    return rows[:top_k]


def recommend_resources_by_profile(profile_items: list[dict[str, Any]], resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profile_vector = index_profile(profile_items)
    index_resources(resources)
    query_embedding = embed_text(profile_vector["text"])
    score_by_resource: dict[str, float] = {}
    for row in list_vectors(RESOURCE_NAMESPACE):
        resource_id = str(row["metadata"].get("resourceId") or row["id"].replace("resource::", ""))
        score_by_resource[resource_id] = _hybrid_similarity(
            profile_vector["text"],
            row["text"],
            query_embedding,
            row["embedding"],
        )
    matched_dimensions = _matched_profile_dimensions(profile_items)
    ranked: list[dict[str, Any]] = []
    for resource in resources:
        score = score_by_resource.get(str(resource.get("id")), 0.0)
        ranked.append({
            **resource,
            "vectorScore": round(score, 4),
            "vectorReason": _vector_reason(resource, matched_dimensions),
            "embeddingProvider": embedding_provider(),
            "embeddingModel": configured_embedding_model(),
            "vectorStore": configured_vector_store(),
            "matchedProfileDimensions": matched_dimensions,
        })
    return sorted(ranked, key=lambda item: (item.get("auditStatus") == "passed", item.get("vectorScore", 0)), reverse=True)


def profile_to_text(profile_items: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{item.get('dimension', '')}: {item.get('value', '')}; 影响: {item.get('impact', '')}"
        for item in profile_items
        if item.get("status") != "rejected"
    )


def resource_to_text(resource: dict[str, Any]) -> str:
    citations = " ".join(item.get("contentPreview", "") for item in resource.get("citations", []))
    return "；".join([
        str(resource.get("title", "")),
        str(resource.get("resourceType", "")),
        str(resource.get("summary", "")),
        str(resource.get("fitReason", "")),
        citations,
    ])


def knowledge_chunk_to_text(chunk: dict[str, Any]) -> str:
    keywords = chunk.get("keywords") or []
    if isinstance(keywords, str):
        keyword_text = keywords
    else:
        keyword_text = " ".join(str(item) for item in keywords)
    return "；".join([
        str(chunk.get("documentName", "")),
        str(chunk.get("section", "")),
        keyword_text,
        str(chunk.get("content", "")),
    ])


def _get_sentence_transformer() -> Any | None:
    global _EMBEDDING_MODEL, _EMBEDDING_PROVIDER
    if _EMBEDDING_PROVIDER:
        return _EMBEDDING_MODEL
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        model_name = configured_embedding_model()
        _EMBEDDING_MODEL = SentenceTransformer(model_name)
        _EMBEDDING_PROVIDER = model_name
        return _EMBEDDING_MODEL
    except Exception:
        _EMBEDDING_MODEL = None
        _EMBEDDING_PROVIDER = "local_hash_fallback"
        return None


def _hash_embedding(text: str, dimension: int = FALLBACK_DIMENSION) -> list[float]:
    values = [0.0] * dimension
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        values[index] += sign
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def _tokens(text: str) -> list[str]:
    chars = [char for char in text.lower() if not char.isspace()]
    grams = chars[:]
    grams.extend("".join(chars[index:index + 2]) for index in range(max(len(chars) - 1, 0)))
    grams.extend("".join(chars[index:index + 3]) for index in range(max(len(chars) - 2, 0)))
    return grams or ["empty"]


def _cosine(left: list[float], right: list[float]) -> float:
    length = min(len(left), len(right))
    if not length:
        return 0.0
    numerator = sum(left[index] * right[index] for index in range(length))
    left_norm = math.sqrt(sum(value * value for value in left[:length])) or 1.0
    right_norm = math.sqrt(sum(value * value for value in right[:length])) or 1.0
    return max(0.0, min(1.0, numerator / (left_norm * right_norm)))


def _hybrid_similarity(query_text: str, target_text: str, query_embedding: list[float], target_embedding: list[float]) -> float:
    semantic = _cosine(query_embedding, target_embedding)
    query_tokens = set(_tokens(query_text))
    target_tokens = set(_tokens(target_text))
    overlap = len(query_tokens & target_tokens) / (len(query_tokens | target_tokens) or 1)
    return max(0.0, min(1.0, semantic * 0.7 + overlap * 0.3))


def _matched_profile_dimensions(profile_items: list[dict[str, Any]]) -> list[str]:
    important = ["薄弱知识点", "资源偏好", "认知风格", "学习目标", "实践能力水平", "可用学习时间"]
    existing = [item.get("dimension", "") for item in profile_items if item.get("status") != "rejected"]
    return [dimension for dimension in important if dimension in existing][:4]


def _vector_reason(resource: dict[str, Any], dimensions: list[str]) -> str:
    type_label = {
        "mindmap": "图解型认知风格",
        "video_script": "短视频与动画偏好",
        "exercise": "薄弱点测评补强",
        "lab": "代码实践能力",
        "reading": "拓展阅读复盘",
        "explanation": "概念讲解补基础",
    }.get(resource.get("resourceType"), "画像匹配")
    basis = "、".join(dimensions) if dimensions else "已确认画像"
    return f"基于{basis}的向量相似度匹配，优先推荐：{type_label}。"


def _chroma_available() -> bool:
    try:
        import chromadb  # noqa: F401

        return True
    except Exception:
        return False


def _milvus_available() -> bool:
    try:
        import pymilvus  # noqa: F401

        return True
    except Exception:
        return False


def _upsert_external(namespace: str, vector_id: str, text: str, metadata: dict[str, Any], embedding: list[float]) -> None:
    store = os.getenv("VECTOR_STORE", "chroma").lower()
    if store == "chroma":
        _upsert_chroma(namespace, vector_id, text, metadata, embedding)
    elif store == "milvus":
        _upsert_milvus(namespace, vector_id, text, metadata, embedding)


def _query_external(namespace: str, embedding: list[float], *, top_k: int) -> list[dict[str, Any]]:
    store = os.getenv("VECTOR_STORE", "chroma").lower()
    if store == "chroma":
        return _query_chroma(namespace, embedding, top_k=top_k)
    if store == "milvus":
        return _query_milvus(namespace, embedding, top_k=top_k)
    return []


def _chroma_collection(namespace: str, embedding_dimension: int | None = None) -> Any | None:
    try:
        import chromadb  # type: ignore

        path = resolved_chroma_path()
        path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(path))
        model_key = configured_embedding_model().replace("/", "_").replace("-", "_").replace(".", "_").lower()
        suffix = f"_{embedding_dimension}d" if embedding_dimension else ""
        return client.get_or_create_collection(name=f"{namespace}_{model_key}{suffix}")
    except Exception:
        return None


def _upsert_chroma(namespace: str, vector_id: str, text: str, metadata: dict[str, Any], embedding: list[float]) -> None:
    collection = _chroma_collection(namespace, len(embedding))
    if collection is None:
        return
    try:
        collection.upsert(ids=[vector_id], documents=[text], metadatas=[metadata], embeddings=[embedding])
    except Exception:
        return


def _query_chroma(namespace: str, embedding: list[float], *, top_k: int) -> list[dict[str, Any]]:
    collection = _chroma_collection(namespace, len(embedding))
    if collection is None:
        return []
    try:
        result = collection.query(query_embeddings=[embedding], n_results=top_k)
    except Exception:
        return []
    ids = result.get("ids", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    documents = result.get("documents", [[]])[0]
    distances = result.get("distances", [[]])[0]
    rows = []
    for index, vector_id in enumerate(ids):
        distance = float(distances[index]) if index < len(distances) else 1.0
        rows.append({
            "id": vector_id,
            "score": max(0.0, min(1.0, 1.0 - distance)),
            "metadata": metadatas[index] if index < len(metadatas) else {},
            "text": documents[index] if index < len(documents) else "",
        })
    return rows


def _upsert_milvus(namespace: str, vector_id: str, text: str, metadata: dict[str, Any], embedding: list[float]) -> None:
    if not os.getenv("MILVUS_URI"):
        return
    try:
        from pymilvus import MilvusClient  # type: ignore

        client = MilvusClient(uri=os.getenv("MILVUS_URI"), token=os.getenv("MILVUS_TOKEN") or "")
        if not client.has_collection(collection_name=namespace):
            client.create_collection(collection_name=namespace, dimension=len(embedding), metric_type="COSINE")
        client.upsert(collection_name=namespace, data=[{"id": vector_id, "vector": embedding, "text": text, **metadata}])
    except Exception:
        return


def _query_milvus(namespace: str, embedding: list[float], *, top_k: int) -> list[dict[str, Any]]:
    if not os.getenv("MILVUS_URI"):
        return []
    try:
        from pymilvus import MilvusClient  # type: ignore

        client = MilvusClient(uri=os.getenv("MILVUS_URI"), token=os.getenv("MILVUS_TOKEN") or "")
        result = client.search(collection_name=namespace, data=[embedding], limit=top_k, output_fields=["text"])
    except Exception:
        return []
    rows = []
    for hit in result[0] if result else []:
        entity = hit.get("entity", {})
        rows.append({
            "id": str(hit.get("id")),
            "score": float(hit.get("distance", 0.0)),
            "metadata": entity,
            "text": entity.get("text", ""),
        })
    return rows
