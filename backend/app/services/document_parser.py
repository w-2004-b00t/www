from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..persistence import init_db, upsert_knowledge_chunks, upsert_knowledge_document
from .vector_service import index_knowledge_chunks
from .courseware_importer import ensure_courseware_knowledge_base, import_courseware_zip, parse_courseware_file
from .course_materials import (
    DEFAULT_AI_INTRO_MARKDOWN,
    DEFAULT_DOCUMENT_ID,
    DEFAULT_DOCUMENT_NAME,
)

COURSE_ID = "course_data_structure"


def ensure_default_knowledge_base() -> dict[str, Any]:
    init_db()
    document = {
        "id": DEFAULT_DOCUMENT_ID,
        "course_id": COURSE_ID,
        "filename": DEFAULT_DOCUMENT_NAME,
        "file_type": "md",
        "status": "待上传",
        "chunk_count": 0,
        "coverage": 0,
        "issue": "暂无真实数据结构课程资料。",
    }
    return {"document": document, "chunks": [], "courseware": {"imported": False, "reason": "暂无默认课件。"}}


def parse_document_text(filename: str, text: str, *, document_id: str | None = None) -> dict[str, Any]:
    init_db()
    if not text.strip():
        raise ValueError("课程资料内容不能为空。")
    doc_id = document_id or f"doc_{uuid4().hex[:8]}"
    chunks = build_chunks_from_markdown(text, document_id=doc_id, filename=filename, source_type="uploaded_document")
    document = {
        "id": doc_id,
        "course_id": COURSE_ID,
        "filename": filename,
        "file_type": _file_type(filename),
        "status": "已入库",
        "chunk_count": len(chunks),
        "coverage": _coverage_from_chunks(chunks),
        "issue": "资料已解析为知识片段，可被知识检索 Agent 调用。",
    }
    upsert_knowledge_document(document)
    upsert_knowledge_chunks(chunks)
    index_knowledge_chunks(chunks)
    return {"document": document, "chunks": chunks}


def parse_document_file(path: Path, *, document_id: str | None = None) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8", errors="ignore")
    elif suffix == ".pdf":
        text = _read_pdf_text(path)
    elif suffix in {".ppt", ".pptx"}:
        return parse_courseware_file(path, document_id=document_id)
    elif suffix == ".zip":
        return import_courseware_zip(path)
    else:
        raise ValueError("当前知识库支持 Markdown、TXT、PDF、PPT、PPTX 和课件 ZIP。")
    return parse_document_text(path.name, text, document_id=document_id)


def build_chunks_from_markdown(text: str, *, document_id: str, filename: str, source_type: str) -> list[dict[str, Any]]:
    sections = _split_sections(text)
    chunks: list[dict[str, Any]] = []
    page = 1
    for index, section in enumerate(sections, start=1):
        content = _clean_text(section["content"])
        if not content:
            continue
        page = max(page, _infer_page(index))
        for part_index, part in enumerate(_split_long_text(content), start=1):
            chunk_id = f"chunk_{document_id}_{index:02d}_{part_index:02d}".replace("-", "_")
            title = section["title"]
            chunks.append({
                "id": f"kc_{uuid4().hex[:12]}",
                "document_id": document_id,
                "course_id": COURSE_ID,
                "chunk_id": chunk_id,
                "title": title,
                "section": title,
                "page": page,
                "content": part,
                "keywords": _extract_keywords(f"{title} {part}"),
                "source_type": source_type,
                "embedding_status": "pending",
                "document_name": filename,
            })
        page += 1
    return chunks


def _split_sections(text: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    current_title = "课程导入"
    current_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = re.match(r"^(#{1,4})\s+(.+)$", line)
        if match:
            if current_lines:
                sections.append({"title": current_title, "content": "\n".join(current_lines)})
                current_lines = []
            current_title = match.group(2).strip()
        else:
            current_lines.append(raw_line)
    if current_lines:
        sections.append({"title": current_title, "content": "\n".join(current_lines)})
    return sections


def _split_long_text(text: str, max_len: int = 520) -> list[str]:
    sentences = re.split(r"(?<=[。！？；])", text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) > max_len and current:
            parts.append(current.strip())
            current = sentence
        else:
            current += sentence
    if current.strip():
        parts.append(current.strip())
    return parts or [text[:max_len]]


def _extract_keywords(text: str) -> list[str]:
    candidates = [
        "数据结构课程",
        "线性表",
        "顺序表",
        "链表",
        "栈",
        "队列",
        "串",
        "数组",
        "广义表",
        "树",
        "二叉树",
        "图",
        "查找",
        "排序",
        "递归",
        "遍历",
        "存储结构",
        "逻辑结构",
        "时间复杂度",
        "空间复杂度",
        "插入",
        "删除",
        "定位",
        "代码实践",
        "引用溯源",
        "学习闭环",
        "错因分析",
        "学习路径",
        "测评",
    ]
    return [item for item in candidates if item.lower() in text.lower()][:8]


def _clean_text(text: str) -> str:
    return re.sub(r"\n{2,}", "\n", text).strip()


def _infer_page(index: int) -> int:
    return 12 + index


def _coverage_from_chunks(chunks: list[dict[str, Any]]) -> int:
    important = {
        "数据结构课程概述",
        "搜索",
        "知识表示",
        "课程资料",
        "课程资料",
        "课程资料",
        "课程资料",
        "课程资料",
        "神经网络",
        "课程资料",
        "课程资料",
        "智能体",
        "RAG",
        "学习闭环",
    }
    found = {keyword for chunk in chunks for keyword in chunk.get("keywords", [])}
    return min(96, max(60, round(len(found & important) / len(important) * 100)))


def _file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    return suffix or "md"


def _read_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise ValueError("解析 PDF 需要安装 pypdf。") from exc
    reader = PdfReader(str(path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)
