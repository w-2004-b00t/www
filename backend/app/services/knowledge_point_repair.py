from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from ..persistence import (
    list_json_keys,
    list_records,
    load_json,
    save_json,
    save_record,
)
from .knowledge_point_service import (
    clean_rubric_keywords,
    context_from_citation,
    sanitize_knowledge_points,
)


def repair_knowledge_point_pollution() -> dict[str, int]:
    counters = {"json": 0, "records": 0}
    for key in _repairable_json_keys():
        value = load_json(key, [] if "results" in key or "mistakes" in key or "drafts" in key else {})
        repaired = _repair_payload(value)
        if repaired != value:
            save_json(key, repaired)
            counters["json"] += 1

    for table in ["assessment_papers", "assessment_results", "mistake_records"]:
        for record in list_records(table, 10000):
            repaired = _repair_payload(record)
            if repaired != record:
                save_record(table, repaired)
                counters["records"] += 1
    return counters


def _repairable_json_keys() -> list[str]:
    keys: list[str] = []
    for prefix in [
        "learning_path", "assessment_results", "tutor_mistakes", "profile_update_drafts",
    ]:
        keys.extend(list_json_keys(prefix))
    return list(dict.fromkeys(keys))


def _repair_payload(value: Any) -> Any:
    if isinstance(value, list):
        return [_repair_payload(item) for item in value]
    if not isinstance(value, dict):
        return value

    item = deepcopy(value)
    if isinstance(item.get("questions"), list):
        item["questions"] = [_repair_question(question) for question in item["questions"]]
    if isinstance(item.get("questionDetails"), list):
        item["questionDetails"] = [_repair_question_detail(detail) for detail in item["questionDetails"]]
    if isinstance(item.get("question_details"), list):
        item["question_details"] = [_repair_question_detail(detail) for detail in item["question_details"]]
    if isinstance(item.get("weakness"), list):
        item["weakness"] = sanitize_knowledge_points(item["weakness"])
    if isinstance(item.get("profileUpdateDrafts"), list):
        item["profileUpdateDrafts"] = [_repair_profile_draft(draft) for draft in item["profileUpdateDrafts"]]
    if isinstance(item.get("profile_update_drafts"), list):
        item["profile_update_drafts"] = [_repair_profile_draft(draft) for draft in item["profile_update_drafts"]]
    if item.get("dimension") in {"薄弱知识点", "易错点"}:
        item = _repair_profile_draft(item)
    if isinstance(item.get("stages"), list):
        item["stages"] = _repair_stages(item["stages"])
        _repair_path_history(item)
    if item.get("source") in {"assessment", "resource"} and "knowledge" in item:
        points = sanitize_knowledge_points([item.get("knowledge")], context=str(item.get("stem") or ""))
        if points:
            item["knowledge"] = points[0]
            if str(item.get("fixTask") or "").endswith("补强练习并复述错因。"):
                item["fixTask"] = f"完成「{points[0]}」补强练习并复述错因。"
    if isinstance(item.get("responsePayload"), dict):
        item["responsePayload"] = _repair_payload(item["responsePayload"])
    return item


def _repair_question(question: Any) -> Any:
    if not isinstance(question, dict):
        return question
    item = deepcopy(question)
    citations = item.get("citations") if isinstance(item.get("citations"), list) else []
    citation = next((value for value in citations if isinstance(value, dict)), {})
    context = f"{context_from_citation(citation)} {item.get('stem', '')} {item.get('analysis', '')}"
    points = sanitize_knowledge_points([item.get("knowledgePoint")], context=context)
    if points:
        item["knowledgePoint"] = points[0]
    else:
        item["knowledgePoint"] = "线性表" if "线性表" in context else "数据结构课程资料"
    rubric = item.get("rubric") if isinstance(item.get("rubric"), list) else []
    item["rubric"] = clean_rubric_keywords(rubric, context=context) or [item["knowledgePoint"]]
    return item


def _repair_question_detail(detail: Any) -> Any:
    if not isinstance(detail, dict):
        return detail
    item = deepcopy(detail)
    point_key = "knowledge_point" if "knowledge_point" in item else "knowledgePoint"
    points = sanitize_knowledge_points([item.get(point_key)])
    item[point_key] = points[0] if points else ""
    return item


def _repair_profile_draft(draft: Any) -> Any:
    if not isinstance(draft, dict):
        return draft
    item = deepcopy(draft)
    if item.get("dimension") == "薄弱知识点":
        raw = str(item.get("value") or item.get("newValue") or "")
        points = sanitize_knowledge_points(raw.replace("，", "、").split("、"))
        cleaned = "、".join(points)
        item["value"] = cleaned
        if "newValue" in item:
            item["newValue"] = cleaned
    return item


def _repair_stages(stages: list[Any]) -> list[Any]:
    repaired: list[Any] = []
    for stage in stages:
        if not isinstance(stage, dict):
            repaired.append(stage)
            continue
        item = deepcopy(stage)
        context = " ".join(str(item.get(key) or "") for key in ["chapterName", "name", "acceptance", "aiReason"])
        points = sanitize_knowledge_points(item.get("knowledgePoints", []), context=context)
        chapter_points = sanitize_knowledge_points([item.get("chapterName")])
        if item.get("source") != "assessment" and chapter_points:
            points = list(dict.fromkeys([*chapter_points, *points]))
        if item.get("source") == "assessment" and not points:
            continue
        if points:
            item["knowledgePoints"] = points
            if item.get("source") == "assessment" or str(item.get("name") or "").lower() == "ai补强任务":
                item["name"] = f"{points[0]}补强任务"
        repaired.append(item)
    return repaired


def _repair_path_history(path: dict[str, Any]) -> None:
    stages = path.get("stages", [])
    current_remedial_name = next(
        (stage.get("name") for stage in stages if isinstance(stage, dict) and stage.get("source") == "assessment"),
        "",
    )
    stage_points = [
        point
        for stage in stages
        if isinstance(stage, dict)
        for point in stage.get("knowledgePoints", [])
    ]
    for entry in path.get("adjustmentHistory", []) or []:
        if not isinstance(entry, dict):
            continue
        reason = str(entry.get("reason") or "")
        match = re.search(r"薄弱点：(.+?)(?:，已插入|。|$)", reason)
        raw_points = re.split(r"[、,，;\s]+", match.group(1)) if match else []
        points = sanitize_knowledge_points(raw_points)
        if not points:
            points = sanitize_knowledge_points(stage_points)
        remedial_name = current_remedial_name or (f"{points[0]}补强任务" if points else "")
        if not remedial_name:
            continue
        for key in ["before", "after", "reason"]:
            if isinstance(entry.get(key), str):
                entry[key] = entry[key].replace("ai补强任务", remedial_name)
        if match and points:
            entry["reason"] = (
                reason[:match.start(1)]
                + ", ".join(points)
                + reason[match.end(1):]
            ).replace("ai补强任务", remedial_name)
        for key in ["beforePath", "afterPath"]:
            if isinstance(entry.get(key), list):
                entry[key] = [remedial_name if value == "ai补强任务" else value for value in entry[key]]
