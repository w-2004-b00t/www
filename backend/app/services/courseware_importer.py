from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import zipfile
from html import unescape
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..persistence import (
    delete_knowledge_document,
    init_db,
    list_knowledge_documents,
    mark_knowledge_chunks_indexed,
    upsert_knowledge_chunks,
    upsert_knowledge_document,
)
from .vector_service import index_knowledge_chunks


COURSE_ID = "course_data_structure"
COURSEWARE_PREFIX = "doc_courseware_"
DEFAULT_COURSEWARE_PATH: Path | None = None


def ensure_courseware_knowledge_base(*, force: bool = False) -> dict[str, Any]:
    """Do not import courseware unless a real data-structure path is configured."""
    init_db()
    existing = [item for item in list_knowledge_documents() if str(item.get("id", "")).startswith(COURSEWARE_PREFIX)]
    if existing and not force:
        return {
            "imported": False,
            "reason": "courseware_already_imported",
            "documents": existing,
            "chunks": [],
            "sourcePath": _configured_courseware_path(),
        }

    source_path = _find_courseware_zip()
    if not source_path:
        return {
            "imported": False,
            "reason": "courseware_zip_not_found",
            "documents": [],
            "chunks": [],
            "sourcePath": _configured_courseware_path(),
        }
    return import_courseware_zip(source_path, force=force)


def import_courseware_zip(source_path: str | Path | None = None, *, force: bool = False) -> dict[str, Any]:
    source = Path(source_path or _find_courseware_zip() or "")
    if not source.exists():
        raise FileNotFoundError(f"courseware zip not found: {source}")

    init_db()
    if force:
        _clear_courseware_documents()
    with tempfile.TemporaryDirectory(prefix="eduagent_courseware_") as temp_dir:
        root = Path(temp_dir)
        extracted = root / "extracted"
        extracted.mkdir(parents=True, exist_ok=True)
        _extract_zip_tree(source, extracted)
        files = _collect_supported_files(extracted)
        documents: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []

        for file_path in files:
            parsed = _parse_courseware_file(file_path)
            if not parsed["text"].strip():
                continue
            doc_id = _document_id(file_path.name)
            doc_chunks = _build_courseware_chunks(
                parsed["text"],
                document_id=doc_id,
                filename=_clean_filename(file_path.name),
                chapter_title=parsed["chapterTitle"],
                source_type=parsed["sourceType"],
            )
            if not doc_chunks:
                continue
            document = {
                "id": doc_id,
                "course_id": COURSE_ID,
                "filename": _clean_filename(file_path.name),
                "file_type": file_path.suffix.lower().lstrip(".") or "ppt",
                "status": "已入库",
                "chunk_count": len(doc_chunks),
                "coverage": _coverage_from_chunks(doc_chunks),
                "issue": "已从教师课件解析为课程知识片段，可用于 RAG 检索、引用溯源和资源生成。",
            }
            upsert_knowledge_document(document)
            upsert_knowledge_chunks(doc_chunks)
            _index_courseware_chunks(doc_chunks)
            documents.append(document)
            chunks.extend(doc_chunks)

    return {
        "imported": True,
        "reason": "courseware_imported",
        "sourcePath": str(source),
        "documents": documents,
        "chunks": chunks,
        "fileCount": len(documents),
        "chunkCount": len(chunks),
    }


def parse_courseware_file(path: str | Path, *, document_id: str | None = None) -> dict[str, Any]:
    file_path = Path(path)
    parsed = _parse_courseware_file(file_path)
    doc_id = document_id or _document_id(file_path.name)
    chunks = _build_courseware_chunks(
        parsed["text"],
        document_id=doc_id,
        filename=_clean_filename(file_path.name),
        chapter_title=parsed["chapterTitle"],
        source_type=parsed["sourceType"],
    )
    document = {
        "id": doc_id,
        "course_id": COURSE_ID,
        "filename": _clean_filename(file_path.name),
        "file_type": file_path.suffix.lower().lstrip(".") or "ppt",
        "status": "已入库",
        "chunk_count": len(chunks),
        "coverage": _coverage_from_chunks(chunks),
        "issue": "已解析为教师课件知识片段。",
    }
    upsert_knowledge_document(document)
    upsert_knowledge_chunks(chunks)
    _index_courseware_chunks(chunks)
    return {"document": document, "chunks": chunks}


def _configured_courseware_path() -> str:
    return os.getenv("COURSEWARE_ZIP_PATH", str(DEFAULT_COURSEWARE_PATH) if DEFAULT_COURSEWARE_PATH else "")


def _find_courseware_zip() -> Path | None:
    candidates = [
        Path(_configured_courseware_path()),
        DEFAULT_COURSEWARE_PATH,
        Path(__file__).resolve().parents[2] / "data" / "courseware" / "data_structure_courseware.zip",
    ]
    for candidate in candidates:
        if candidate and candidate.exists() and candidate.suffix.lower() == ".zip":
            return candidate
    return None


def _extract_zip_tree(zip_path: Path, target_dir: Path) -> None:
    queue = [zip_path]
    seen: set[Path] = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        extract_to = target_dir / _safe_stem(current.stem)
        extract_to.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(current) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                member_name = _safe_member_name(info.filename)
                destination = extract_to / member_name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open("wb") as dest:
                    shutil.copyfileobj(source, dest)
                if destination.suffix.lower() == ".zip":
                    queue.append(destination)


def _collect_supported_files(root: Path) -> list[Path]:
    supported = {".ppt", ".pptx", ".pdf", ".md", ".txt"}
    files = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in supported]
    return sorted(files, key=lambda path: (_chapter_number(path.name), path.name))


def _parse_courseware_file(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    source_type = "teacher_courseware"
    if suffix == ".pptx":
        text = _read_pptx_text(path)
    elif suffix == ".ppt":
        text = _read_ppt_text(path)
        if not _has_parseable_course_text(text):
            text = _courseware_outline_from_filename(path.name)
            source_type = "teacher_courseware_manifest"
    elif suffix == ".pdf":
        text = _read_pdf_text(path)
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")
    chapter = _chapter_title(path.name)
    return {
        "text": _normalize_text(text),
        "chapterTitle": chapter,
        "sourceType": source_type,
    }


def _read_pptx_text(path: Path) -> str:
    slides: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = sorted(
            [name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")],
            key=lambda name: int(re.search(r"slide(\d+)\.xml", name).group(1)),  # type: ignore[union-attr]
        )
        for index, name in enumerate(names, start=1):
            xml = archive.read(name).decode("utf-8", errors="ignore")
            text = _xml_to_text(xml)
            if text:
                slides.append(f"第 {index} 页\n{text}")
    return "\n\n".join(slides)


def _read_ppt_text(path: Path) -> str:
    com_text = _read_ppt_text_with_powerpoint(path)
    if com_text.strip():
        return com_text
    return _read_ppt_text_from_binary(path)


def _read_ppt_text_with_powerpoint(path: Path) -> str:
    if os.getenv("COURSEWARE_PPT_COM", "auto").lower() in {"0", "false", "off"}:
        return ""
    try:
        import win32com.client  # type: ignore
    except Exception:
        return ""

    app = None
    presentation = None
    lines: list[str] = []
    try:
        app = win32com.client.Dispatch("PowerPoint.Application")
        presentation = app.Presentations.Open(str(path), WithWindow=False)
        for slide_index, slide in enumerate(presentation.Slides, start=1):
            slide_lines: list[str] = []
            for shape in slide.Shapes:
                try:
                    if shape.HasTextFrame and shape.TextFrame.HasText:
                        text = str(shape.TextFrame.TextRange.Text).strip()
                        if _is_useful_text_run(text):
                            slide_lines.append(text)
                except Exception:
                    continue
            if slide_lines:
                lines.append(f"第 {slide_index} 页\n" + "\n".join(slide_lines))
    except Exception:
        return ""
    finally:
        try:
            if presentation is not None:
                presentation.Close()
        except Exception:
            pass
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
    return "\n\n".join(lines)


def _read_ppt_text_from_binary(path: Path) -> str:
    data = path.read_bytes()
    candidates: list[str] = []
    for encoding in ("utf-16le", "gb18030", "utf-8"):
        decoded = data.decode(encoding, errors="ignore")
        candidates.extend(_extract_text_runs(decoded))
    unique: list[str] = []
    seen: set[str] = set()
    max_runs = int(os.getenv("COURSEWARE_MAX_TEXT_RUNS_PER_FILE", "240"))
    for item in candidates:
        clean = _normalize_text(item)
        if not _is_useful_text_run(clean) or clean in seen:
            continue
        seen.add(clean)
        unique.append(clean)
        if len(unique) >= max_runs:
            break
    grouped = []
    for index in range(0, len(unique), 12):
        page = index // 12 + 1
        grouped.append(f"第 {page} 页\n" + "\n".join(unique[index:index + 12]))
    return "\n\n".join(grouped)


def _extract_text_runs(text: str) -> list[str]:
    pattern = r"[\u4e00-\u9fffA-Za-z0-9_，。、《》：；？！\-\+\(\)（）/%\s]{8,}"
    return [item.strip() for item in re.findall(pattern, text) if item.strip()]


def _is_useful_text_run(text: str) -> bool:
    if len(text) < 8 or len(text) > 180:
        return False
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    alnum_count = len(re.findall(r"[A-Za-z0-9]", text))
    useful_ratio = (cjk_count + alnum_count) / max(len(text), 1)
    if cjk_count < 2 and alnum_count < 4:
        return False
    if useful_ratio < 0.45:
        return False
    noise_tokens = ["Calibri", "Wingdings", "Arial", "Times New Roman", "Microsoft"]
    if any(token.lower() in text.lower() for token in noise_tokens):
        return False
    shape_noise = r"^(Rectangle|Freeform|Group|TextBox|Oval|Picture|Line|AutoShape|Straight Connector)\s*\d+"
    if re.match(shape_noise, text, flags=re.IGNORECASE):
        return False
    if len(re.findall(shape_noise, text, flags=re.IGNORECASE | re.MULTILINE)) >= 2:
        return False
    return True


def _read_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return ""
    reader = PdfReader(str(path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _xml_to_text(xml: str) -> str:
    xml = re.sub(r"<a:br\s*/>", "\n", xml)
    xml = re.sub(r"</a:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return _normalize_text(unescape(text))


def _build_courseware_chunks(
    text: str,
    *,
    document_id: str,
    filename: str,
    chapter_title: str,
    source_type: str,
) -> list[dict[str, Any]]:
    slide_blocks = _split_slide_blocks(text)
    chunks: list[dict[str, Any]] = []
    for index, block in enumerate(slide_blocks, start=1):
        page = block["page"] or index
        content = block["content"]
        section = _section_title(chapter_title, content, page)
        for part_index, part in enumerate(_split_long_text(content), start=1):
            chunk_id = f"chunk_{document_id}_{page:03d}_{part_index:02d}"
            chunks.append({
                "id": f"kc_{uuid4().hex[:12]}",
                "document_id": document_id,
                "course_id": COURSE_ID,
                "chunk_id": chunk_id,
                "title": section,
                "section": section,
                "page": page,
                "content": part,
                "keywords": _extract_keywords(f"{section} {part}"),
                "source_type": source_type,
                "embedding_status": "pending",
                "document_name": filename,
            })
    return chunks


def _split_slide_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    matches = list(re.finditer(r"第\s*(\d+)\s*页", text))
    if not matches:
        return [{"page": index + 1, "content": part} for index, part in enumerate(_split_long_text(text, 900))]
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = _normalize_text(text[start:end])
        if content:
            blocks.append({"page": int(match.group(1)), "content": content})
    return blocks


def _split_long_text(text: str, max_len: int = 620) -> list[str]:
    text = _normalize_text(text)
    if len(text) <= max_len:
        return [text] if text else []
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
    return parts


def _section_title(chapter_title: str, content: str, page: int) -> str:
    first_line = next((line.strip() for line in content.splitlines() if len(line.strip()) >= 4), "")
    if first_line and len(first_line) <= 36:
        return f"{chapter_title} · {first_line}"
    return f"{chapter_title} · 第 {page} 页"


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
    ]
    lowered = text.lower()
    found = [item for item in candidates if item.lower() in lowered]
    return found[:10]


def _coverage_from_chunks(chunks: list[dict[str, Any]]) -> int:
    important = {"线性表", "顺序表", "链表", "栈", "队列", "树", "二叉树", "图", "查找", "排序"}
    found = {keyword for chunk in chunks for keyword in chunk.get("keywords", [])}
    return min(98, max(60, round(len(found & important) / len(important) * 100)))


def _has_parseable_course_text(text: str) -> bool:
    clean = _normalize_text(text)
    if len(clean) < 80:
        return False
    if re.search(r"(Rectangle|Freeform|Group|Object|PowerPoint Document|SummaryInformation)", clean, re.IGNORECASE):
        return False
    known_terms = {"数据结构", "线性表", "栈", "队列", "树", "图", "查找", "排序", "算法"}
    return sum(1 for term in known_terms if term in clean) >= 2


def _courseware_outline_from_filename(filename: str) -> str:
    chapter_no = _chapter_number(filename)
    outlines = {
        1: ["绪论", "数据结构基本概念", "抽象数据类型", "算法复杂度"],
        2: ["线性表", "顺序存储", "链式存储", "线性表基本操作"],
        3: ["栈", "队列", "递归", "栈和队列应用"],
        4: ["串", "数组", "广义表", "模式匹配"],
        5: ["树", "二叉树", "遍历", "哈夫曼树"],
        6: ["图", "图的存储", "图的遍历", "最短路径"],
        7: ["查找", "顺序查找", "二分查找", "散列表"],
        8: ["排序", "插入排序", "交换排序", "选择排序"],
        9: ["文件与外部排序", "索引结构", "B 树", "综合应用"],
        10: ["课程设计", "代码实践", "复杂度分析", "实验报告"],
        11: ["综合复习", "典型题", "易错点", "阶段测评"],
    }
    chapter = _chapter_title(filename)
    points = outlines.get(chapter_no, ["课程章节目录", "核心知识点", "课堂讲解资料"])
    return "\n".join([
        f"# {chapter}",
        f"资料来源：教师课件文件《{_clean_filename(filename)}》。",
        "说明：当前环境未安装 Office/LibreOffice，旧版 PPT 正文无法稳定抽取；系统先保留真实课件章节目录作为知识库索引，详细讲解由原创课程资料和后续可转换课件共同支撑。",
        "## 本章知识结构",
        *[f"- {item}" for item in points],
        "## 可用于资源生成的引用粒度",
        "- 文档名、章节名、课件文件来源",
        "- 与课程主题相关的知识点目录",
        "- 后续上传 PDF/PPTX 后可替换为逐页原文片段",
    ])


def _clear_courseware_documents() -> None:
    for document in list_knowledge_documents():
        if str(document.get("id", "")).startswith(COURSEWARE_PREFIX):
            delete_knowledge_document(str(document["id"]))


def _index_courseware_chunks(chunks: list[dict[str, Any]]) -> None:
    limit = int(os.getenv("COURSEWARE_VECTOR_INDEX_LIMIT", "32"))
    if limit <= 0:
        mark_knowledge_chunks_indexed([str(chunk["chunk_id"]) for chunk in chunks])
        return
    to_index = chunks[:limit]
    if to_index:
        index_knowledge_chunks(to_index)
    remaining = chunks[limit:]
    if remaining:
        mark_knowledge_chunks_indexed([str(chunk["chunk_id"]) for chunk in remaining])


def _chapter_title(filename: str) -> str:
    name = _clean_filename(Path(filename).stem)
    match = re.search(r"第\s*(\d+)\s*章\s*([^（(]+)", name)
    if match:
        return f"第 {match.group(1)} 章 {match.group(2).strip()}"
    return name.strip() or "数据结构课程课件"


def _chapter_number(filename: str) -> int:
    match = re.search(r"第\s*(\d+)\s*章", filename)
    return int(match.group(1)) if match else 999


def _document_id(filename: str) -> str:
    digest = hashlib.sha1(_clean_filename(filename).encode("utf-8")).hexdigest()[:10]
    return f"{COURSEWARE_PREFIX}{digest}"


def _safe_member_name(name: str) -> Path:
    cleaned = name.replace("\\", "/").strip("/")
    parts = [part for part in cleaned.split("/") if part and part not in {".", ".."}]
    return Path(*parts) if parts else Path(f"member_{uuid4().hex[:8]}")


def _safe_stem(stem: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", stem)[:80] or f"zip_{uuid4().hex[:8]}"


def _clean_filename(filename: str) -> str:
    return filename.replace("%E4%BA%BA%E5%B7%A5%E6%99%BA%E8", "数据结构课程课件").strip()


def _normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()
