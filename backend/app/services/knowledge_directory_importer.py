from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..course_config import COURSE_ID
from ..persistence import (
    delete_knowledge_document,
    init_db,
    list_knowledge_documents,
    load_json,
    save_json,
    upsert_knowledge_chunks,
    upsert_knowledge_document,
)
from .courseware_importer import parse_courseware_file
from .vector_service import index_knowledge_chunks


LOCAL_DOCUMENT_PREFIX = "doc_local_kb_"
IMPORT_STATUS_KEY = "knowledge_local_import_status"
DEFAULT_KNOWLEDGE_BASE_PATH = r"E:\知识库"

TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".c",
    ".cpp",
    ".cc",
    ".cxx",
    ".h",
    ".hpp",
    ".js",
    ".ts",
    ".json",
    ".py",
    ".java",
}
DOCUMENT_SUFFIXES = {".pdf", ".ppt", ".pptx"} | TEXT_SUFFIXES
ZIP_INNER_SUFFIXES = DOCUMENT_SUFFIXES | {".zip"}
MAX_TEXT_CHARS = int(os.getenv("LOCAL_KB_MAX_TEXT_CHARS", "120000"))
MAX_PDF_PAGES = int(os.getenv("LOCAL_KB_MAX_PDF_PAGES", "80"))
MAX_PARSE_PDF_BYTES = int(os.getenv("LOCAL_KB_MAX_PARSE_PDF_BYTES", "5000000"))
FULL_PARSE_FILE_LIMIT = int(os.getenv("LOCAL_KB_FULL_PARSE_FILE_LIMIT", "8"))
MAX_ZIP_PARSE_ENTRIES = int(os.getenv("LOCAL_KB_MAX_ZIP_PARSE_ENTRIES", "20"))


def configured_knowledge_base_path() -> Path:
    return Path(os.getenv("KNOWLEDGE_BASE_PATH", DEFAULT_KNOWLEDGE_BASE_PATH))


def import_local_knowledge_base(source_path: str | Path | None = None, *, force: bool = False) -> dict[str, Any]:
    init_db()
    source = Path(source_path or configured_knowledge_base_path())
    if not source.exists():
        status = _status_payload(source, force=force, imported=False, reason="knowledge_base_path_not_found")
        save_json(IMPORT_STATUS_KEY, status)
        return status

    if force:
        _clear_local_documents()

    documents: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    files = _collect_source_files(source)

    full_parsed_files = 0
    for file_path in files:
        try:
            manifest_only = full_parsed_files >= FULL_PARSE_FILE_LIMIT and _is_heavy_source(file_path)
            parsed = _store_manifest_for_source(file_path, source) if manifest_only else _parse_source_file(file_path, source)
            if not manifest_only:
                full_parsed_files += 1
        except Exception as exc:
            failures.append({"path": str(file_path), "reason": f"{exc.__class__.__name__}: {exc}"})
            continue
        documents.extend(parsed.get("documents", []))
        chunks.extend(parsed.get("chunks", []))
        failures.extend(parsed.get("failures", []))

    status = _status_payload(
        source,
        force=force,
        imported=bool(documents),
        reason="local_knowledge_imported" if documents else "no_supported_files_imported",
        file_count=len(files),
        document_count=len(documents),
        chunk_count=len(chunks),
        failures=failures,
    )
    save_json(IMPORT_STATUS_KEY, status)
    return status


def local_import_status() -> dict[str, Any]:
    fallback = _status_payload(configured_knowledge_base_path(), imported=False, reason="not_imported")
    status = load_json(IMPORT_STATUS_KEY, fallback)
    documents = [
        item for item in list_knowledge_documents()
        if str(item.get("id", "")).startswith(LOCAL_DOCUMENT_PREFIX)
    ]
    status["documentCount"] = len(documents)
    status["chunkCount"] = sum(int(item.get("chunks", 0)) for item in documents)
    status["documents"] = documents[:20]
    return status


def ensure_local_knowledge_base() -> dict[str, Any]:
    existing = [
        item for item in list_knowledge_documents()
        if str(item.get("id", "")).startswith(LOCAL_DOCUMENT_PREFIX)
    ]
    if existing:
        status = local_import_status()
        status["imported"] = False
        status["reason"] = "local_knowledge_already_imported"
        return status
    return import_local_knowledge_base(force=False)


def _collect_source_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in DOCUMENT_SUFFIXES or root.suffix.lower() == ".zip" else []
    files = [
        path for path in root.rglob("*")
        if path.is_file() and (path.suffix.lower() in DOCUMENT_SUFFIXES or path.suffix.lower() == ".zip")
    ]
    return sorted(files, key=lambda item: (_source_priority(item), _chapter_sort_number(str(item)), str(item).lower()))


def _source_priority(path: Path) -> int:
    name = str(path).lower()
    suffix = path.suffix.lower()
    if suffix == ".pdf" and "数据结构" in name:
        return 0
    if "数据结构课件" in name or "线性表" in name:
        return 1
    if "data-structure" in name and suffix == ".zip":
        return 2
    if suffix in {".pdf", ".ppt", ".pptx"} and ("数据结构" in name or "data-structure" in name):
        return 3
    if "data-structure" in name and path.suffix.lower() == ".zip":
        return 0
    if "数据结构课件" in name:
        return 1
    if path.suffix.lower() in {".pdf", ".ppt", ".pptx"} and ("数据结构" in str(path) or "data-structure" in name):
        return 2
    if "data-structure" in name:
        return 3
    if path.suffix.lower() == ".zip":
        return 4
    return 5


def _is_heavy_source(path: Path) -> bool:
    return path.suffix.lower() in {".pdf", ".ppt", ".pptx", ".zip"}


def _store_manifest_for_source(file_path: Path, root: Path) -> dict[str, Any]:
    lines = [
        f"# {file_path.name}",
        "该文件已作为本地知识库来源登记。",
        f"相对路径：{_relative_key(file_path, root)}",
        f"文件类型：{file_path.suffix.lower().lstrip('.') or 'file'}",
        f"文件大小：{file_path.stat().st_size} bytes",
    ]
    if file_path.suffix.lower() == ".zip":
        lines.extend(["", "## 压缩包条目摘要"])
        try:
            with zipfile.ZipFile(file_path) as archive:
                entries = [item.filename for item in archive.infolist() if not item.is_dir()]
            lines.extend(f"- {entry}" for entry in entries[:240])
            if len(entries) > 240:
                lines.append(f"- ... 另有 {len(entries) - 240} 个条目")
        except Exception as exc:
            lines.append(f"- 条目读取失败：{exc.__class__.__name__}")
    else:
        lines.append("说明：超过快速导入全文解析预算，已用摘要方式入库；核心课件和源码包会优先全文解析。")
    return _store_text_document(
        file_path.name,
        "\n".join(lines),
        source_type="local_manifest",
        source_key=_relative_key(file_path, root),
        file_type=file_path.suffix.lower().lstrip(".") or "file",
        chapter_hint=_chapter_from_path(file_path),
    )


def _parse_source_file(file_path: Path, root: Path) -> dict[str, Any]:
    suffix = file_path.suffix.lower()
    if suffix == ".zip":
        return _parse_zip_file(file_path, root)
    if suffix in {".ppt", ".pptx"}:
        return _parse_courseware_document(file_path, root)
    if suffix == ".pdf":
        text_pages = _read_pdf_pages_or_manifest(file_path)
        return _store_text_document(
            file_path.name,
            "\n\n".join(f"第 {page} 页\n{text}" for page, text in text_pages),
            source_type="course_pdf",
            source_key=_relative_key(file_path, root),
            file_type="pdf",
        )
    if suffix in TEXT_SUFFIXES:
        text = _read_text_or_manifest(file_path, display_name=file_path.name)
        return _store_text_document(
            file_path.name,
            text,
            source_type=_source_type_for_text(file_path),
            source_key=_relative_key(file_path, root),
            file_type=suffix.lstrip(".") or "txt",
            chapter_hint=_chapter_from_path(file_path),
        )
    return {"documents": [], "chunks": [], "failures": []}


def _parse_zip_file(file_path: Path, root: Path) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="eduagent_local_kb_zip_") as temp_dir:
        extract_root = Path(temp_dir) / _safe_name(file_path.stem)
        extract_root.mkdir(parents=True, exist_ok=True)
        _extract_zip_recursive(file_path, extract_root)
        inner_files = [
            item for item in extract_root.rglob("*")
            if item.is_file() and item.suffix.lower() in ZIP_INNER_SUFFIXES and item.suffix.lower() != ".zip"
        ]
        sorted_inner_files = sorted(inner_files, key=_zip_entry_priority)
        parse_files = sorted_inner_files[:MAX_ZIP_PARSE_ENTRIES]
        remaining_files = sorted_inner_files[MAX_ZIP_PARSE_ENTRIES:]
        for inner in parse_files:
            try:
                parsed = _parse_extracted_file(inner, source_zip=file_path, extract_root=extract_root, root=root)
            except Exception as exc:
                failures.append({"path": f"{file_path}!{inner.relative_to(extract_root)}", "reason": f"{exc.__class__.__name__}: {exc}"})
                continue
            documents.extend(parsed.get("documents", []))
            chunks.extend(parsed.get("chunks", []))
            failures.extend(parsed.get("failures", []))
        if remaining_files:
            manifest = _store_zip_remaining_manifest(file_path, root, remaining_files, extract_root)
            documents.extend(manifest.get("documents", []))
            chunks.extend(manifest.get("chunks", []))
    return {"documents": documents, "chunks": chunks, "failures": failures}


def _zip_entry_priority(path: Path) -> tuple[int, str]:
    name = str(path).lower()
    suffix = path.suffix.lower()
    if suffix in {".c", ".h", ".cpp", ".hpp"}:
        kind = 0
    elif suffix in {".md", ".txt"}:
        kind = 1
    elif suffix in {".js", ".ts", ".py", ".java"}:
        kind = 2
    else:
        kind = 3
    topic_rank = _topic_sort_number(str(path))
    return (topic_rank, kind, name)


def _chapter_sort_number(text: str) -> int:
    match = re.search(r"第\s*(\d+)\s*章", text)
    if match:
        return int(match.group(1))
    match = re.search(r"▲\s*(\d{2})", text)
    if match:
        return int(match.group(1))
    return 999


def _topic_sort_number(text: str) -> int:
    ordered = ["线性表", "栈", "队列", "串", "递归", "数组", "树", "二叉树", "图", "查找", "排序"]
    for index, term in enumerate(ordered):
        if term in text:
            return index
    return 999


def _store_zip_remaining_manifest(file_path: Path, root: Path, remaining_files: list[Path], extract_root: Path) -> dict[str, Any]:
    lines = [
        f"# {file_path.name} 剩余条目清单",
        f"压缩包《{file_path.name}》已解析前 {MAX_ZIP_PARSE_ENTRIES} 个高优先级源码/文本条目。",
        f"剩余 {len(remaining_files)} 个条目作为清单入库，后续可按需提高 LOCAL_KB_MAX_ZIP_PARSE_ENTRIES 做全文解析。",
        "",
        "## 剩余条目",
        *[f"- {item.relative_to(extract_root).as_posix()}" for item in remaining_files[:500]],
    ]
    if len(remaining_files) > 500:
        lines.append(f"- ... 另有 {len(remaining_files) - 500} 个条目")
    return _store_text_document(
        f"{file_path.name}!remaining-manifest",
        "\n".join(lines),
        source_type="local_manifest",
        source_key=f"{_relative_key(file_path, root)}!remaining-manifest",
        file_type="zip-manifest",
    )


def _parse_extracted_file(inner: Path, *, source_zip: Path, extract_root: Path, root: Path) -> dict[str, Any]:
    suffix = inner.suffix.lower()
    inner_key = f"{_relative_key(source_zip, root)}!{inner.relative_to(extract_root).as_posix()}"
    display_name = f"{source_zip.name}!{inner.relative_to(extract_root).as_posix()}"
    if suffix in {".ppt", ".pptx"}:
        return _parse_courseware_document(inner, root, source_key=inner_key, display_name=display_name)
    if suffix == ".pdf":
        text_pages = _read_pdf_pages_or_manifest(inner)
        return _store_text_document(
            display_name,
            "\n\n".join(f"第 {page} 页\n{text}" for page, text in text_pages),
            source_type="course_pdf",
            source_key=inner_key,
            file_type="pdf",
        )
    text = _read_text_or_manifest(inner, display_name=display_name)
    return _store_text_document(
        display_name,
        text,
        source_type=_source_type_for_text(inner),
        source_key=inner_key,
        file_type=suffix.lstrip(".") or "txt",
        chapter_hint=_chapter_from_path(inner),
    )


def _parse_courseware_document(path: Path, root: Path, *, source_key: str | None = None, display_name: str | None = None) -> dict[str, Any]:
    doc_id = _document_id(source_key or _relative_key(path, root))
    previous_store = os.environ.get("VECTOR_STORE")
    previous_courseware_limit = os.environ.get("COURSEWARE_VECTOR_INDEX_LIMIT")
    os.environ["VECTOR_STORE"] = os.getenv("LOCAL_KB_VECTOR_STORE", "sqlite")
    os.environ["COURSEWARE_VECTOR_INDEX_LIMIT"] = os.getenv("LOCAL_KB_VECTOR_INDEX_LIMIT", "0")
    try:
        parsed = parse_courseware_file(path, document_id=doc_id)
    finally:
        if previous_store is None:
            os.environ.pop("VECTOR_STORE", None)
        else:
            os.environ["VECTOR_STORE"] = previous_store
        if previous_courseware_limit is None:
            os.environ.pop("COURSEWARE_VECTOR_INDEX_LIMIT", None)
        else:
            os.environ["COURSEWARE_VECTOR_INDEX_LIMIT"] = previous_courseware_limit
    document = parsed["document"]
    document["filename"] = display_name or path.name
    upsert_knowledge_document(document)
    return {"documents": [document], "chunks": parsed.get("chunks", []), "failures": []}


def _store_text_document(
    filename: str,
    text: str,
    *,
    source_type: str,
    source_key: str,
    file_type: str,
    chapter_hint: str | None = None,
) -> dict[str, Any]:
    clean = _normalize_text(text)
    if not clean:
        return {"documents": [], "chunks": [], "failures": []}
    doc_id = _document_id(source_key)
    chunks = _build_chunks(clean, document_id=doc_id, filename=filename, source_type=source_type, chapter_hint=chapter_hint)
    if not chunks:
        return {"documents": [], "chunks": [], "failures": []}
    document = {
        "id": doc_id,
        "course_id": COURSE_ID,
        "filename": filename,
        "file_type": file_type,
        "status": "已入库",
        "chunk_count": len(chunks),
        "coverage": _coverage_from_chunks(chunks),
        "issue": "已从本地知识库导入，可用于资源生成 RAG 检索与引用溯源。",
    }
    upsert_knowledge_document(document)
    upsert_knowledge_chunks(chunks)
    _index_chunks_limited(chunks)
    return {"documents": [document], "chunks": chunks, "failures": []}


def _build_chunks(text: str, *, document_id: str, filename: str, source_type: str, chapter_hint: str | None) -> list[dict[str, Any]]:
    blocks = _split_page_blocks(text)
    chunks: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, start=1):
        page = int(block.get("page") or index)
        content = str(block.get("content") or "").strip()
        if not content:
            continue
        section = _section_title(chapter_hint, filename, content, page)
        for part_index, part in enumerate(_split_long_text(content), start=1):
            chunks.append({
                "id": f"kc_{uuid4().hex[:12]}",
                "document_id": document_id,
                "course_id": COURSE_ID,
                "chunk_id": f"chunk_{document_id}_{page:04d}_{part_index:02d}",
                "title": section,
                "section": section,
                "page": page,
                "content": part,
                "keywords": _extract_data_structure_keywords(f"{section} {part} {filename}"),
                "source_type": source_type,
                "embedding_status": "pending",
                "document_name": filename,
            })
    return chunks


def _read_pdf_pages(path: Path) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:
        raise ValueError("解析 PDF 需要安装 pypdf。") from exc
    reader = PdfReader(str(path))
    pages = []
    for index, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append((index, text))
    if len(reader.pages) > MAX_PDF_PAGES:
        pages.append((MAX_PDF_PAGES + 1, f"PDF 共 {len(reader.pages)} 页，已抽取前 {MAX_PDF_PAGES} 页用于快速入库；文件本身已登记为知识库来源。"))
    return pages


def _read_pdf_pages_or_manifest(path: Path) -> list[tuple[int, str]]:
    size = path.stat().st_size
    parse_large = os.getenv("LOCAL_KB_PARSE_LARGE_PDF", "false").lower() in {"1", "true", "yes", "on"}
    if size > MAX_PARSE_PDF_BYTES and not parse_large:
        return [(
            1,
            "\n".join([
                f"PDF 文件《{path.name}》已作为本地知识库来源登记。",
                f"文件大小：{size} bytes。",
                "说明：该 PDF 体量较大，默认以文件摘要入库以保证资源生成实时性；如需全文抽取，可设置 LOCAL_KB_PARSE_LARGE_PDF=true。",
            ]),
        )]
    return _read_pdf_pages(path)


def _read_text_or_manifest(path: Path, *, display_name: str) -> str:
    size = path.stat().st_size
    if _is_low_value_large_text(path, size):
        return "\n".join([
            f"# {display_name}",
            "该文件已作为本地知识库来源登记。",
            f"文件类型：{path.suffix.lower().lstrip('.') or 'text'}",
            f"文件大小：{size} bytes",
            "说明：锁文件、依赖清单或超大结构化文件不展开全文切片，以避免噪声干扰数据结构课程资源生成。",
        ])
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) > MAX_TEXT_CHARS:
        return "\n".join([
            text[:MAX_TEXT_CHARS],
            "",
            f"... 文件过长，已截断前 {MAX_TEXT_CHARS} 字符用于知识库检索。",
        ])
    return text


def _is_low_value_large_text(path: Path, size: int) -> bool:
    name = path.name.lower()
    if name in {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "package.json"}:
        return True
    return path.suffix.lower() == ".json" and size > 200_000


def _extract_zip_recursive(zip_path: Path, target: Path) -> None:
    queue = [(zip_path, target)]
    while queue:
        current, current_target = queue.pop(0)
        with zipfile.ZipFile(current) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                destination = current_target / _safe_member_name(info.filename)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, destination.open("wb") as dst:
                    dst.write(src.read())
                if destination.suffix.lower() == ".zip":
                    nested_target = destination.parent / _safe_name(destination.stem)
                    nested_target.mkdir(parents=True, exist_ok=True)
                    queue.append((destination, nested_target))


def _split_page_blocks(text: str) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"第\s*(\d+)\s*页", text))
    if not matches:
        return [{"page": index + 1, "content": part} for index, part in enumerate(_split_long_text(text, 1200))]
    blocks: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = _normalize_text(text[start:end])
        if content:
            blocks.append({"page": int(match.group(1)), "content": content})
    return blocks


def _split_long_text(text: str, max_len: int = 720) -> list[str]:
    text = _normalize_text(text)
    if len(text) <= max_len:
        return [text] if text else []
    sentences = re.split(r"(?<=[。！？；;{}])", text)
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
    return parts


def _extract_data_structure_keywords(text: str) -> list[str]:
    candidates = [
        "数据结构", "算法", "线性表", "顺序表", "链表", "单链表", "双链表", "循环链表",
        "栈", "队列", "循环队列", "串", "KMP", "数组", "稀疏矩阵", "广义表", "递归",
        "树", "二叉树", "遍历", "先序", "中序", "后序", "层次遍历", "线索二叉树",
        "哈夫曼树", "并查集", "图", "邻接矩阵", "邻接表", "深度优先", "广度优先",
        "最小生成树", "Prim", "Kruskal", "最短路径", "Dijkstra", "Floyd", "拓扑排序",
        "关键路径", "查找", "二叉排序树", "平衡二叉树", "B树", "B+树", "哈希",
        "排序", "插入排序", "希尔排序", "冒泡排序", "快速排序", "选择排序", "堆排序",
        "归并排序", "基数排序", "时间复杂度", "空间复杂度", "C语言", "代码实现",
    ]
    lowered = text.lower()
    found = [item for item in candidates if item.lower() in lowered]
    return found[:12]


def _coverage_from_chunks(chunks: list[dict[str, Any]]) -> int:
    important = {"线性表", "栈", "队列", "串", "数组", "树", "二叉树", "图", "查找", "排序"}
    found = {keyword for chunk in chunks for keyword in chunk.get("keywords", [])}
    return min(98, max(60, round(len(found & important) / len(important) * 100)))


def _section_title(chapter_hint: str | None, filename: str, content: str, page: int) -> str:
    first_line = next((line.strip() for line in content.splitlines() if 4 <= len(line.strip()) <= 42), "")
    prefix = chapter_hint or _chapter_from_name(filename) or Path(filename).stem[:32]
    return f"{prefix} · {first_line or f'第 {page} 页'}"


def _chapter_from_path(path: Path) -> str | None:
    text = " ".join(path.parts)
    return _chapter_from_name(text)


def _chapter_from_name(name: str) -> str | None:
    match = re.search(r"第\s*(\d+)\s*章\s*([^\\/!]+?)(?:第|\s|/|\\|!|$)", name)
    if match:
        return f"第 {match.group(1)} 章 {match.group(2).strip()}"
    numbered = re.search(r"[▲\s]*(\d{2})\s*([^\\/!]+)", name)
    if numbered:
        return f"第 {int(numbered.group(1))} 章 {numbered.group(2).strip()}"
    return None


def _source_type_for_text(path: Path) -> str:
    return "code_repository" if path.suffix.lower() in {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".js", ".ts", ".py", ".java"} else "local_text"


def _index_chunks_limited(chunks: list[dict[str, Any]]) -> None:
    limit = int(os.getenv("LOCAL_KB_VECTOR_INDEX_LIMIT", "0"))
    if limit <= 0:
        return
    previous_store = os.environ.get("VECTOR_STORE")
    os.environ["VECTOR_STORE"] = os.getenv("LOCAL_KB_VECTOR_STORE", "sqlite")
    try:
        index_knowledge_chunks(chunks[:limit])
    finally:
        if previous_store is None:
            os.environ.pop("VECTOR_STORE", None)
        else:
            os.environ["VECTOR_STORE"] = previous_store


def _clear_local_documents() -> None:
    for document in list_knowledge_documents():
        if str(document.get("id", "")).startswith(LOCAL_DOCUMENT_PREFIX):
            delete_knowledge_document(str(document["id"]))


def _document_id(source_key: str) -> str:
    digest = hashlib.sha1(source_key.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{LOCAL_DOCUMENT_PREFIX}{digest}"


def _relative_key(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _safe_member_name(name: str) -> Path:
    cleaned = name.replace("\\", "/").strip("/")
    parts = [part for part in cleaned.split("/") if part and part not in {".", ".."}]
    return Path(*parts) if parts else Path(f"member_{uuid4().hex[:8]}")


def _safe_name(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value)[:80] or f"item_{uuid4().hex[:8]}"


def _normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _status_payload(
    source: Path,
    *,
    imported: bool,
    reason: str,
    force: bool = False,
    file_count: int = 0,
    document_count: int = 0,
    chunk_count: int = 0,
    failures: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "imported": imported,
        "reason": reason,
        "sourcePath": str(source),
        "force": force,
        "fileCount": file_count,
        "documentCount": document_count,
        "chunkCount": chunk_count,
        "failureCount": len(failures or []),
        "failures": failures or [],
        "updatedAt": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
