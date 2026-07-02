from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .course_config import COURSE_ID


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "eduagent.sqlite"
_WRITE_LOCK = threading.RLock()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma busy_timeout = 30000")
    conn.execute("pragma journal_mode = WAL")
    conn.execute("pragma synchronous = NORMAL")
    conn.execute("pragma foreign_keys = ON")
    return conn


def is_database_busy_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and (
        "database is locked" in text or "database table is locked" in text or "database is busy" in text
    )


@contextmanager
def write_transaction():
    with _WRITE_LOCK:
        conn = connect()
        started = False
        try:
            conn.execute("begin immediate")
            started = True
            yield conn
            conn.execute("commit")
        except Exception:
            if started:
                conn.execute("rollback")
            raise
        finally:
            conn.close()


def init_db() -> None:
    with write_transaction() as conn:
        conn.execute(
            """
            create table if not exists kv_store (
                key text primary key,
                value text not null,
                updated_at text default current_timestamp
            )
            """
        )
        conn.execute(
            """
            create table if not exists task_store (
                id text primary key,
                value text not null,
                status text not null,
                updated_at text default current_timestamp
            )
            """
        )
        conn.execute(
            """
            create table if not exists task_events (
                id integer primary key autoincrement,
                task_id text not null,
                agent_name text,
                event_type text not null,
                payload text not null,
                created_at text default current_timestamp
            )
            """
        )
        conn.execute(
            """
            create table if not exists video_jobs (
                id text primary key,
                resource_id text not null,
                user_id text not null,
                status text not null,
                phase text not null,
                idempotency_key text,
                next_retry_at text,
                lease_owner text,
                lease_expires_at text,
                value text not null,
                created_at text not null,
                updated_at text not null
            )
            """
        )
        conn.execute(
            "create index if not exists idx_video_jobs_resource_user_updated "
            "on video_jobs(resource_id, user_id, updated_at desc)"
        )
        conn.execute(
            "create index if not exists idx_video_jobs_status_retry "
            "on video_jobs(status, next_retry_at)"
        )
        conn.execute(
            "create index if not exists idx_video_jobs_idempotency "
            "on video_jobs(idempotency_key, updated_at desc)"
        )
        for table in ["assessment_papers", "assessment_results", "mistake_records", "report_snapshots"]:
            conn.execute(
                f"""
                create table if not exists {table} (
                    id text primary key,
                    value text not null,
                    created_at text default current_timestamp
                )
                """
            )
        mistake_columns = {
            row["name"]
            for row in conn.execute("pragma table_info(mistake_records)").fetchall()
        }
        for column, definition in {
            "user_id": "text not null default ''",
            "status": "text not null default '待订正'",
            "version": "integer not null default 1",
            "updated_at": "text",
        }.items():
            if column not in mistake_columns:
                conn.execute(f"alter table mistake_records add column {column} {definition}")
        conn.execute(
            "update mistake_records set updated_at = coalesce(updated_at, created_at, current_timestamp)"
        )
        conn.execute(
            "create index if not exists idx_mistake_records_user_updated "
            "on mistake_records(user_id, updated_at desc)"
        )
        conn.execute(
            """
            create table if not exists vector_store (
                id text primary key,
                namespace text not null,
                text text not null,
                metadata text not null,
                embedding text not null,
                updated_at text default current_timestamp
            )
            """
        )
        conn.execute(
            """
            create table if not exists knowledge_documents (
                id text primary key,
                course_id text not null,
                filename text not null,
                file_type text not null,
                status text not null,
                chunk_count integer default 0,
                coverage integer default 0,
                issue text default '',
                created_at text default current_timestamp,
                updated_at text default current_timestamp
            )
            """
        )
        conn.execute(
            """
            create table if not exists knowledge_chunks (
                id text primary key,
                document_id text not null,
                course_id text not null,
                chunk_id text not null unique,
                title text default '',
                section text default '',
                page integer default 1,
                content text not null,
                keywords text not null,
                source_type text default 'uploaded_document',
                embedding_status text default 'pending',
                created_at text default current_timestamp
            )
            """
        )
        conn.execute(
            """
            create table if not exists knowledge_graph_nodes (
                id text primary key,
                course_id text not null,
                chapter_id text,
                name text not null,
                node_type text not null,
                description text default '',
                difficulty text default '基础',
                importance integer default 3,
                estimated_minutes integer default 30,
                tags text not null default '[]',
                source_refs text not null default '[]',
                auto_managed integer default 1,
                updated_at text default current_timestamp
            )
            """
        )
        conn.execute(
            """
            create table if not exists knowledge_graph_edges (
                id text primary key,
                course_id text not null,
                source_id text not null,
                target_id text not null,
                relation_type text not null,
                weight real default 1,
                direction text default 'directed',
                source text default 'course_structure',
                verified integer default 1,
                auto_managed integer default 1,
                updated_at text default current_timestamp
            )
            """
        )
        conn.execute(
            """
            create table if not exists knowledge_mastery (
                user_id text not null,
                node_id text not null,
                mastery integer not null default 0,
                status text not null default 'unlearned',
                evidence text not null default '[]',
                updated_at text default current_timestamp,
                primary key(user_id, node_id)
            )
            """
        )
        conn.execute("create index if not exists idx_kg_nodes_course on knowledge_graph_nodes(course_id)")
        conn.execute("create index if not exists idx_kg_edges_course on knowledge_graph_edges(course_id)")
        conn.execute("create index if not exists idx_kg_mastery_user on knowledge_mastery(user_id)")


def load_json(key: str, fallback: Any) -> Any:
    with connect() as conn:
        row = conn.execute("select value from kv_store where key = ?", (key,)).fetchone()
    if not row:
        save_json(key, fallback)
        return fallback
    return json.loads(row["value"])


def save_json(key: str, value: Any) -> None:
    with write_transaction() as conn:
        conn.execute(
            """
            insert into kv_store(key, value, updated_at)
            values(?, ?, current_timestamp)
            on conflict(key) do update set value = excluded.value, updated_at = current_timestamp
            """,
            (key, json.dumps(value, ensure_ascii=False)),
        )


def list_json_keys(prefix: str) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "select key from kv_store where key like ? order by key asc",
            (f"{prefix}%",),
        ).fetchall()
    return [str(row["key"]) for row in rows]


def save_task(task: dict[str, Any]) -> None:
    with write_transaction() as conn:
        conn.execute(
            """
            insert into task_store(id, value, status, updated_at)
            values(?, ?, ?, current_timestamp)
            on conflict(id) do update set
                value = excluded.value,
                status = excluded.status,
                updated_at = current_timestamp
            """,
            (task["id"], json.dumps(task, ensure_ascii=False), task.get("status", "pending")),
        )


def save_video_job(job: dict[str, Any]) -> None:
    job_id = str(job["jobId"])
    with write_transaction() as conn:
        conn.execute(
            """
            insert into video_jobs(
                id, resource_id, user_id, status, phase, idempotency_key,
                next_retry_at, lease_owner, lease_expires_at, value, created_at, updated_at
            )
            values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                resource_id = excluded.resource_id,
                user_id = excluded.user_id,
                status = excluded.status,
                phase = excluded.phase,
                idempotency_key = excluded.idempotency_key,
                next_retry_at = excluded.next_retry_at,
                lease_owner = coalesce(excluded.lease_owner, video_jobs.lease_owner),
                lease_expires_at = coalesce(excluded.lease_expires_at, video_jobs.lease_expires_at),
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (
                job_id,
                str(job.get("resourceId") or ""),
                str(job.get("userId") or "anonymous"),
                str(job.get("status") or "queued"),
                str(job.get("phase") or job.get("status") or "queued"),
                job.get("idempotencyKey"),
                job.get("nextRetryAt"),
                job.get("leaseOwner"),
                job.get("leaseExpiresAt"),
                json.dumps(job, ensure_ascii=False),
                str(job.get("createdAt") or ""),
                str(job.get("updatedAt") or job.get("createdAt") or ""),
            ),
        )


def load_video_job(job_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("select value from video_jobs where id = ?", (job_id,)).fetchone()
    return json.loads(row["value"]) if row else None


def latest_video_job(resource_id: str, user_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            select value from video_jobs
            where resource_id = ? and user_id = ?
            order by created_at desc, updated_at desc
            limit 1
            """,
            (resource_id, user_id),
        ).fetchone()
    return json.loads(row["value"]) if row else None


def find_video_job_by_idempotency(idempotency_key: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            select value from video_jobs
            where idempotency_key = ?
            order by updated_at desc
            limit 1
            """,
            (idempotency_key,),
        ).fetchone()
    return json.loads(row["value"]) if row else None


def list_runnable_video_jobs(limit: int = 10) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select value from video_jobs
            where status in ('queued', 'submitting', 'rendering', 'retry_wait', 'downloading', 'validating', 'composing')
              and (next_retry_at is null or next_retry_at = '' or next_retry_at <= datetime('now', 'localtime'))
              and (lease_expires_at is null or lease_expires_at = '' or lease_expires_at <= datetime('now', 'localtime'))
            order by updated_at asc
            limit ?
            """,
            (limit,),
        ).fetchall()
    return [json.loads(row["value"]) for row in rows]


def acquire_video_job_lease(job_id: str, owner: str, lease_seconds: int = 900) -> bool:
    with write_transaction() as conn:
        cursor = conn.execute(
            """
            update video_jobs
            set lease_owner = ?,
                lease_expires_at = datetime('now', 'localtime', ?)
            where id = ?
              and (lease_expires_at is null or lease_expires_at = '' or lease_expires_at <= datetime('now', 'localtime'))
            """,
            (owner, f"+{max(1, int(lease_seconds))} seconds", job_id),
        )
    return cursor.rowcount == 1


def release_video_job_lease(job_id: str, owner: str) -> None:
    with write_transaction() as conn:
        conn.execute(
            """
            update video_jobs
            set lease_owner = null, lease_expires_at = null
            where id = ? and lease_owner = ?
            """,
            (job_id, owner),
        )


def load_task(task_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("select value from task_store where id = ?", (task_id,)).fetchone()
    return json.loads(row["value"]) if row else None


def list_tasks(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select value
            from task_store
            order by updated_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    return [json.loads(row["value"]) for row in rows]


def append_task_event(task_id: str, agent_name: str | None, event_type: str, payload: dict[str, Any]) -> None:
    with write_transaction() as conn:
        conn.execute(
            "insert into task_events(task_id, agent_name, event_type, payload) values(?, ?, ?, ?)",
            (task_id, agent_name, event_type, json.dumps(payload, ensure_ascii=False)),
        )


def list_task_events(task_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "select agent_name, event_type, payload, created_at from task_events where task_id = ? order by id asc",
            (task_id,),
        ).fetchall()
    return [
        {
            "agentName": row["agent_name"],
            "eventType": row["event_type"],
            "payload": json.loads(row["payload"]),
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def list_successful_resource_task_outputs(user_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent successful resource-generation task outputs.

    This is used only as a repair source when a user's resource list was not
    materialized but the generation task was durably saved.
    """
    with connect() as conn:
        rows = conn.execute(
            """
            select value
            from task_store
            where status = 'success'
            order by updated_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()

    tasks = [json.loads(row["value"]) for row in rows]
    if user_id:
        tasks = [task for task in tasks if str(task.get("userId") or "") == user_id]
    return [
        task.get("outputPayload", {})
        for task in tasks
        if isinstance(task.get("outputPayload"), dict)
    ]


_RECORD_TABLES = {"assessment_papers", "assessment_results", "mistake_records", "report_snapshots"}


def save_record(table: str, record: dict[str, Any]) -> None:
    if table not in _RECORD_TABLES:
        raise ValueError(f"unsupported record table: {table}")
    record_id = str(record["id"])
    with write_transaction() as conn:
        conn.execute(
            f"""
            insert into {table}(id, value, created_at)
            values(?, ?, current_timestamp)
            on conflict(id) do update set value = excluded.value
            """,
            (record_id, json.dumps(record, ensure_ascii=False)),
        )


def list_records(table: str, limit: int = 100) -> list[dict[str, Any]]:
    if table not in _RECORD_TABLES:
        raise ValueError(f"unsupported record table: {table}")
    with connect() as conn:
        rows = conn.execute(
            f"select value from {table} order by created_at desc limit ?",
            (limit,),
        ).fetchall()
    return [json.loads(row["value"]) for row in rows]


def load_record(table: str, record_id: str) -> dict[str, Any] | None:
    if table not in _RECORD_TABLES:
        raise ValueError(f"unsupported record table: {table}")
    with connect() as conn:
        row = conn.execute(f"select value from {table} where id = ?", (record_id,)).fetchone()
    return json.loads(row["value"]) if row else None


def upsert_vector(namespace: str, vector_id: str, text: str, metadata: dict[str, Any], embedding: list[float]) -> None:
    with write_transaction() as conn:
        conn.execute(
            """
            insert into vector_store(id, namespace, text, metadata, embedding, updated_at)
            values(?, ?, ?, ?, ?, current_timestamp)
            on conflict(id) do update set
                namespace = excluded.namespace,
                text = excluded.text,
                metadata = excluded.metadata,
                embedding = excluded.embedding,
                updated_at = current_timestamp
            """,
            (
                vector_id,
                namespace,
                text,
                json.dumps(metadata, ensure_ascii=False),
                json.dumps(embedding),
            ),
        )


def list_vectors(namespace: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select id, namespace, text, metadata, embedding, updated_at
            from vector_store
            where namespace = ?
            order by updated_at desc
            """,
            (namespace,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "namespace": row["namespace"],
            "text": row["text"],
            "metadata": json.loads(row["metadata"]),
            "embedding": json.loads(row["embedding"]),
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def upsert_knowledge_document(document: dict[str, Any]) -> None:
    with write_transaction() as conn:
        conn.execute(
            """
            insert into knowledge_documents(
                id, course_id, filename, file_type, status, chunk_count, coverage, issue, updated_at
            )
            values(?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
            on conflict(id) do update set
                course_id = excluded.course_id,
                filename = excluded.filename,
                file_type = excluded.file_type,
                status = excluded.status,
                chunk_count = excluded.chunk_count,
                coverage = excluded.coverage,
                issue = excluded.issue,
                updated_at = current_timestamp
            """,
            (
                document["id"],
                document.get("course_id", COURSE_ID),
                document["filename"],
                document.get("file_type", "md"),
                document.get("status", "parsed"),
                int(document.get("chunk_count", 0)),
                int(document.get("coverage", 0)),
                document.get("issue", ""),
            ),
        )


def list_knowledge_documents() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select id, course_id, filename, file_type, status, chunk_count, coverage, issue, created_at, updated_at
            from knowledge_documents
            order by updated_at desc
            """
        ).fetchall()
    return [
        {
            "id": row["id"],
            "courseId": row["course_id"],
            "name": row["filename"],
            "fileType": row["file_type"],
            "status": row["status"],
            "chunks": row["chunk_count"],
            "coverage": row["coverage"],
            "issue": row["issue"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def delete_knowledge_document(document_id: str) -> None:
    with write_transaction() as conn:
        conn.execute("delete from knowledge_chunks where document_id = ?", (document_id,))
        conn.execute("delete from knowledge_documents where id = ?", (document_id,))


def upsert_knowledge_chunks(chunks: list[dict[str, Any]]) -> None:
    with write_transaction() as conn:
        for chunk in chunks:
            conn.execute(
                """
                insert into knowledge_chunks(
                    id, document_id, course_id, chunk_id, title, section, page, content,
                    keywords, source_type, embedding_status
                )
                values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(chunk_id) do update set
                    title = excluded.title,
                    section = excluded.section,
                    page = excluded.page,
                    content = excluded.content,
                    keywords = excluded.keywords,
                    source_type = excluded.source_type,
                    embedding_status = excluded.embedding_status
                """,
                (
                    chunk["id"],
                    chunk["document_id"],
                    chunk.get("course_id", COURSE_ID),
                    chunk["chunk_id"],
                    chunk.get("title", ""),
                    chunk.get("section", ""),
                    int(chunk.get("page", 1)),
                    chunk["content"],
                    json.dumps(chunk.get("keywords", []), ensure_ascii=False),
                    chunk.get("source_type", "uploaded_document"),
                    chunk.get("embedding_status", "pending"),
                ),
            )


def list_knowledge_chunks(course_id: str | None = None, document_id: str | None = None) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if course_id:
        where.append("kc.course_id = ?")
        params.append(course_id)
    if document_id:
        where.append("kc.document_id = ?")
        params.append(document_id)
    where_sql = f"where {' and '.join(where)}" if where else ""
    with connect() as conn:
        rows = conn.execute(
            f"""
            select kc.id, kc.document_id, kc.course_id, kc.chunk_id, kc.title, kc.section, kc.page, kc.content,
                   kc.keywords, kc.source_type, kc.embedding_status, kc.created_at,
                   coalesce(kd.filename, '') as document_name
            from knowledge_chunks kc
            left join knowledge_documents kd on kd.id = kc.document_id
            {where_sql}
            order by kc.page asc, kc.chunk_id asc
            """,
            tuple(params),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "documentId": row["document_id"],
            "courseId": row["course_id"],
            "chunk_id": row["chunk_id"],
            "chunkId": row["chunk_id"],
            "documentName": row["document_name"],
            "title": row["title"],
            "section": row["section"],
            "page": row["page"],
            "content": row["content"],
            "keywords": json.loads(row["keywords"]),
            "sourceType": row["source_type"],
            "embeddingStatus": row["embedding_status"],
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def mark_knowledge_chunks_indexed(chunk_ids: list[str]) -> None:
    with write_transaction() as conn:
        for chunk_id in chunk_ids:
            conn.execute(
                "update knowledge_chunks set embedding_status = 'indexed' where chunk_id = ?",
                (chunk_id,),
            )


def replace_auto_knowledge_graph(
    course_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    """Replace derived graph rows while preserving teacher-managed relations."""
    with write_transaction() as conn:
        conn.execute(
            "delete from knowledge_graph_edges where course_id = ? and auto_managed = 1",
            (course_id,),
        )
        conn.execute(
            "delete from knowledge_graph_nodes where course_id = ? and auto_managed = 1",
            (course_id,),
        )
        for node in nodes:
            conn.execute(
                """
                insert into knowledge_graph_nodes(
                    id, course_id, chapter_id, name, node_type, description, difficulty,
                    importance, estimated_minutes, tags, source_refs, auto_managed, updated_at
                ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, current_timestamp)
                on conflict(id) do update set
                    course_id = excluded.course_id,
                    chapter_id = excluded.chapter_id,
                    name = excluded.name,
                    node_type = excluded.node_type,
                    description = excluded.description,
                    difficulty = excluded.difficulty,
                    importance = excluded.importance,
                    estimated_minutes = excluded.estimated_minutes,
                    tags = excluded.tags,
                    source_refs = excluded.source_refs,
                    updated_at = current_timestamp
                """,
                (
                    node["id"],
                    course_id,
                    node.get("chapterId"),
                    node["name"],
                    node["type"],
                    node.get("description", ""),
                    node.get("difficulty", "基础"),
                    int(node.get("importance", 3)),
                    int(node.get("estimatedMinutes", 30)),
                    json.dumps(node.get("tags", []), ensure_ascii=False),
                    json.dumps(node.get("sourceRefs", []), ensure_ascii=False),
                ),
            )
        node_ids = {node["id"] for node in nodes}
        for edge in edges:
            if edge["source"] not in node_ids or edge["target"] not in node_ids:
                continue
            conn.execute(
                """
                insert into knowledge_graph_edges(
                    id, course_id, source_id, target_id, relation_type, weight,
                    direction, source, verified, auto_managed, updated_at
                ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, 1, current_timestamp)
                on conflict(id) do update set
                    source_id = excluded.source_id,
                    target_id = excluded.target_id,
                    relation_type = excluded.relation_type,
                    weight = excluded.weight,
                    direction = excluded.direction,
                    source = excluded.source,
                    verified = excluded.verified,
                    updated_at = current_timestamp
                """,
                (
                    edge["id"],
                    course_id,
                    edge["source"],
                    edge["target"],
                    edge.get("type", "related"),
                    float(edge.get("weight", 1)),
                    edge.get("direction", "directed"),
                    edge.get("sourceType", "course_structure"),
                    1 if edge.get("verified", True) else 0,
                ),
            )


def list_knowledge_graph_nodes(course_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select id, course_id, chapter_id, name, node_type, description, difficulty,
                   importance, estimated_minutes, tags, source_refs, auto_managed, updated_at
            from knowledge_graph_nodes where course_id = ? order by node_type, name
            """,
            (course_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "courseId": row["course_id"],
            "chapterId": row["chapter_id"],
            "name": row["name"],
            "type": row["node_type"],
            "description": row["description"],
            "difficulty": row["difficulty"],
            "importance": row["importance"],
            "estimatedMinutes": row["estimated_minutes"],
            "tags": json.loads(row["tags"]),
            "sourceRefs": json.loads(row["source_refs"]),
            "autoManaged": bool(row["auto_managed"]),
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def list_knowledge_graph_edges(course_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select id, source_id, target_id, relation_type, weight, direction, source,
                   verified, auto_managed, updated_at
            from knowledge_graph_edges where course_id = ? order by source_id, target_id
            """,
            (course_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "source": row["source_id"],
            "target": row["target_id"],
            "type": row["relation_type"],
            "weight": row["weight"],
            "direction": row["direction"],
            "sourceType": row["source"],
            "verified": bool(row["verified"]),
            "autoManaged": bool(row["auto_managed"]),
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def upsert_knowledge_graph_edge(course_id: str, edge: dict[str, Any]) -> dict[str, Any]:
    with write_transaction() as conn:
        conn.execute(
            """
            insert into knowledge_graph_edges(
                id, course_id, source_id, target_id, relation_type, weight,
                direction, source, verified, auto_managed, updated_at
            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, 0, current_timestamp)
            on conflict(id) do update set
                source_id = excluded.source_id,
                target_id = excluded.target_id,
                relation_type = excluded.relation_type,
                weight = excluded.weight,
                direction = excluded.direction,
                source = excluded.source,
                verified = excluded.verified,
                auto_managed = 0,
                updated_at = current_timestamp
            """,
            (
                edge["id"],
                course_id,
                edge["source"],
                edge["target"],
                edge["type"],
                float(edge.get("weight", 1)),
                edge.get("direction", "directed"),
                edge.get("sourceType", "teacher"),
                1 if edge.get("verified", True) else 0,
            ),
        )
    return edge


def delete_knowledge_graph_edge(edge_id: str) -> bool:
    with write_transaction() as conn:
        cursor = conn.execute(
            "delete from knowledge_graph_edges where id = ? and auto_managed = 0",
            (edge_id,),
        )
    return cursor.rowcount > 0


def list_user_knowledge_mastery(user_id: str) -> dict[str, dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            select node_id, mastery, status, evidence, updated_at
            from knowledge_mastery where user_id = ?
            """,
            (user_id,),
        ).fetchall()
    return {
        str(row["node_id"]): {
            "mastery": int(row["mastery"]),
            "status": row["status"],
            "evidence": json.loads(row["evidence"]),
            "updatedAt": row["updated_at"],
        }
        for row in rows
    }


def upsert_user_knowledge_mastery(
    user_id: str,
    node_id: str,
    mastery: int,
    status: str,
    evidence: list[str],
) -> None:
    with write_transaction() as conn:
        conn.execute(
            """
            insert into knowledge_mastery(user_id, node_id, mastery, status, evidence, updated_at)
            values(?, ?, ?, ?, ?, current_timestamp)
            on conflict(user_id, node_id) do update set
                mastery = excluded.mastery,
                status = excluded.status,
                evidence = excluded.evidence,
                updated_at = current_timestamp
            """,
            (user_id, node_id, max(0, min(100, int(mastery))), status, json.dumps(evidence, ensure_ascii=False)),
        )
