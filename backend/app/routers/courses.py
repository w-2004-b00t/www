from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from .. import state
from ..course_config import COURSE_ID, COURSE_NAME
from ..persistence import list_knowledge_chunks
from ..schemas import CourseChapterCreateRequest, CourseChapterUpdateRequest
from ..services.knowledge_graph_service import ensure_course_chapters
from ..utils import ok

router = APIRouter(prefix="/api/admin/courses", tags=["courses"])


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _chapters(course_id: str) -> list[dict]:
    _ensure_single_course(course_id)
    if not state.course_chapters:
        ensure_course_chapters()
    return [item for item in state.course_chapters if item.get("courseId") == course_id]


def _ensure_single_course(course_id: str) -> None:
    if course_id != COURSE_ID:
        raise HTTPException(status_code=404, detail=f"当前系统仅维护《{COURSE_NAME}》")


def _overview(course_id: str) -> dict:
    _ensure_single_course(course_id)
    chapters = _chapters(course_id)
    point_count = sum(len(item.get("points", [])) for item in chapters)
    coverage = round(sum(item.get("citationCoverage", 0) for item in chapters) / len(chapters)) if chapters else 0
    return {
        "courseId": course_id,
        "courseName": COURSE_NAME,
        "chapterCount": len(chapters),
        "knowledgePointCount": point_count,
        "chunkCount": len(list_knowledge_chunks(course_id=course_id)),
        "citationCoverage": coverage,
        "updatedAt": _now(),
    }


def _find_chapter(course_id: str, chapter_id: str) -> dict:
    _ensure_single_course(course_id)
    for item in state.course_chapters:
        if item.get("courseId") == course_id and item.get("id") == chapter_id:
            return item
    raise HTTPException(status_code=404, detail="章节不存在")


@router.get("/{course_id}/chapters")
def list_course_chapters(course_id: str) -> dict:
    return ok({
        "overview": _overview(course_id),
        "chapters": _chapters(course_id),
    })


@router.post("/{course_id}/chapters")
def create_course_chapter(course_id: str, payload: CourseChapterCreateRequest) -> dict:
    _ensure_single_course(course_id)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="章节名称不能为空")
    chapter = {
        "id": f"chapter_{uuid4().hex[:8]}",
        "courseId": course_id,
        "name": name,
        "status": "草稿",
        "progress": 25,
        "points": payload.points or [],
        "risk": "新章节待补充课程引用资料",
        "prerequisites": payload.prerequisites or [],
        "citationCoverage": 25,
        "updatedAt": _now(),
    }
    with state.lock:
        state.course_chapters.append(chapter)
        state.persist_state()
    return ok({"chapter": chapter, "overview": _overview(course_id)})


@router.post("/{course_id}/chapters/{chapter_id}")
def update_course_chapter(course_id: str, chapter_id: str, payload: CourseChapterUpdateRequest) -> dict:
    _ensure_single_course(course_id)
    with state.lock:
        chapter = _find_chapter(course_id, chapter_id)
        if payload.name is not None and payload.name.strip():
            chapter["name"] = payload.name.strip()
        if payload.points is not None:
            chapter["points"] = [item.strip() for item in payload.points if item.strip()]
            chapter["progress"] = max(chapter.get("progress", 25), 62)
            chapter["citationCoverage"] = max(chapter.get("citationCoverage", 25), 62)
        if payload.prerequisites is not None:
            chapter["prerequisites"] = [item.strip() for item in payload.prerequisites if item.strip()]
        if payload.risk is not None:
            chapter["risk"] = payload.risk
        chapter["updatedAt"] = _now()
        state.persist_state()
    return ok({"chapter": chapter, "overview": _overview(course_id)})


@router.post("/{course_id}/chapters/{chapter_id}/publish")
def publish_course_chapter(course_id: str, chapter_id: str) -> dict:
    _ensure_single_course(course_id)
    with state.lock:
        chapter = _find_chapter(course_id, chapter_id)
        if not chapter.get("points"):
            raise HTTPException(status_code=400, detail="请先补充知识点后再发布")
        chapter["status"] = "已发布"
        chapter["progress"] = max(chapter.get("progress", 0), 88)
        chapter["citationCoverage"] = max(chapter.get("citationCoverage", 0), 88)
        chapter["risk"] = "已发布，后续可继续补充例题和引用"
        chapter["updatedAt"] = _now()
        state.persist_state()
    return ok({"chapter": chapter, "overview": _overview(course_id)})
