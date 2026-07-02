from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from typing import Any

from .. import state
from ..course_config import COURSE_ID, COURSE_NAME
from ..persistence import (
    list_knowledge_chunks,
    list_knowledge_graph_edges,
    list_knowledge_graph_nodes,
    list_records,
    replace_auto_knowledge_graph,
)

OPERATION_TERMS = {
    "算法", "时间复杂度", "空间复杂度", "KMP", "递归", "树的遍历", "深度优先搜索", "广度优先搜索",
    "最小生成树", "最短路径", "拓扑排序", "关键路径", "查找", "哈希", "排序", "插入排序", "希尔排序",
    "冒泡排序", "快速排序", "选择排序", "堆排序", "归并排序", "基数排序",
}
APPLICATION_TERMS = {"表达式求值", "关键路径", "最短路径", "哈夫曼树", "稀疏矩阵"}
DIFFICULTY_ORDER = {"基础": 1, "进阶": 2, "综合": 3}
RELATION_LABELS = {
    "contains": "包含",
    "prerequisite": "先修",
    "related": "相关",
    "applies_to": "应用于",
    "supports": "支撑实现",
}


def build_graph(course_id: str, user_id: str) -> dict[str, Any]:
    if course_id != COURSE_ID:
        return _empty_graph(course_id)
    chapters = ensure_course_chapters()
    nodes, edges = _derive_structure(chapters)
    replace_auto_knowledge_graph(course_id, nodes, edges)
    stored_nodes = list_knowledge_graph_nodes(course_id)
    stored_edges = _remove_dangling_edges(stored_nodes, list_knowledge_graph_edges(course_id))
    resources = state.load_user_resources(user_id)
    resource_index = _resource_index(resources, stored_nodes)
    mastery_by_node = _calculate_mastery_from_learning_data(user_id, stored_nodes, resources)

    for node in stored_nodes:
        node_mastery = mastery_by_node.get(node["id"], _empty_mastery_breakdown())
        node["mastery"] = int(node_mastery["finalScore"])
        node["masteryStatus"] = mastery_status(node["mastery"])
        node["masteryEvidence"] = node_mastery["evidence"]
        node["masteryBreakdown"] = node_mastery
        linked_resources = resource_index.get(node["name"], [])
        node["resourceCount"] = len(linked_resources)
        node["resourceIds"] = [item["id"] for item in linked_resources[:8]]
        node["symbolSize"] = _symbol_size(node)
        node["source"] = _source_label(node.get("sourceRefs", []))

    _roll_up_mastery(stored_nodes)
    stats = _graph_stats(stored_nodes, stored_edges)
    return {
        "courseId": course_id,
        "courseName": f"{COURSE_NAME}知识图谱",
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "nodes": stored_nodes,
        "edges": [{**edge, "relation": RELATION_LABELS.get(edge["type"], edge["type"])} for edge in stored_edges],
        "stats": stats,
        "relationTypes": [{"value": key, "label": value} for key, value in RELATION_LABELS.items()],
    }


def filter_graph(
    graph: dict[str, Any],
    *,
    chapter_id: str | None = None,
    node_types: list[str] | None = None,
    difficulty: str | None = None,
    depth: int | None = None,
) -> dict[str, Any]:
    nodes = graph["nodes"]
    selected_ids = {
        node["id"]
        for node in nodes
        if (not chapter_id or node.get("chapterId") == chapter_id or node["id"] == chapter_id)
        and (not node_types or node["type"] in node_types)
        and (not difficulty or node.get("difficulty") == difficulty)
    }
    if depth is not None and depth >= 0 and selected_ids:
        selected_ids = _expand_ids(selected_ids, graph["edges"], depth)
    filtered_nodes = [node for node in nodes if node["id"] in selected_ids]
    filtered_edges = [
        edge for edge in graph["edges"]
        if edge["source"] in selected_ids and edge["target"] in selected_ids
    ]
    return {**graph, "nodes": filtered_nodes, "edges": filtered_edges, "stats": _graph_stats(filtered_nodes, filtered_edges)}


def node_detail(course_id: str, user_id: str, node_id: str) -> dict[str, Any] | None:
    graph = build_graph(course_id, user_id)
    node = next((item for item in graph["nodes"] if item["id"] == node_id), None)
    if not node:
        return None
    upstream = []
    downstream = []
    related = []
    by_id = {item["id"]: item for item in graph["nodes"]}
    for edge in graph["edges"]:
        if edge["target"] == node_id:
            upstream.append({"edge": edge, "node": by_id.get(edge["source"])})
        elif edge["source"] == node_id:
            downstream.append({"edge": edge, "node": by_id.get(edge["target"])})
        elif edge["source"] == node_id or edge["target"] == node_id:
            other_id = edge["target"] if edge["source"] == node_id else edge["source"]
            related.append({"edge": edge, "node": by_id.get(other_id)})
    return {
        **node,
        "upstream": [item for item in upstream if item["node"]],
        "downstream": [item for item in downstream if item["node"]],
        "related": [item for item in related if item["node"]],
        "remedialPath": remedial_path(graph, node_id),
    }


def neighbors(course_id: str, user_id: str, node_id: str, depth: int = 1) -> dict[str, Any]:
    graph = build_graph(course_id, user_id)
    ids = _expand_ids({node_id}, graph["edges"], max(1, min(depth, 3)))
    return {
        "nodes": [node for node in graph["nodes"] if node["id"] in ids],
        "edges": [edge for edge in graph["edges"] if edge["source"] in ids and edge["target"] in ids],
    }


def remedial_path(graph: dict[str, Any], target_node_id: str) -> list[dict[str, Any]]:
    nodes = {node["id"]: node for node in graph["nodes"]}
    prerequisites: dict[str, list[str]] = defaultdict(list)
    for edge in graph["edges"]:
        if edge["type"] == "prerequisite":
            prerequisites[edge["target"]].append(edge["source"])

    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting or node_id not in nodes:
            return
        visiting.add(node_id)
        for prerequisite_id in prerequisites.get(node_id, []):
            prerequisite = nodes.get(prerequisite_id)
            if prerequisite and prerequisite.get("masteryStatus") != "mastered":
                visit(prerequisite_id)
        if nodes[node_id].get("masteryStatus") != "mastered" and node_id not in ordered:
            ordered.append(node_id)

    visit(target_node_id)
    return [
        {
            "order": index,
            "nodeId": node_id,
            "name": nodes[node_id]["name"],
            "mastery": nodes[node_id].get("mastery", 0),
            "masteryStatus": nodes[node_id].get("masteryStatus", "unlearned"),
            "estimatedMinutes": nodes[node_id].get("estimatedMinutes", 30),
            "reason": "先补齐先修知识" if node_id != target_node_id else "完成目标知识点补强",
        }
        for index, node_id in enumerate(ordered, start=1)
    ]


def ensure_course_chapters() -> list[dict[str, Any]]:
    configured = [
        item for item in state.course_chapters
        if not item.get("autoGenerated")
    ]
    if configured:
        return deepcopy(configured)
    chunks = list_knowledge_chunks(course_id=COURSE_ID)
    if not chunks:
        return []
    grouped: dict[int, dict[str, Any]] = {}
    for chunk in chunks:
        descriptor = _chapter_descriptor_from_chunk(chunk)
        if not descriptor:
            continue
        chapter_order, chapter_name = descriptor
        group = grouped.setdefault(chapter_order, {"name": chapter_name, "points": [], "chunkCount": 0})
        group["chunkCount"] += 1
        for keyword in chunk.get("keywords", []):
            point = str(keyword or "").strip()
            if _is_usable_point(point) and point not in group["points"]:
                group["points"].append(point)
    chapters = []
    for chapter_order, group in sorted(grouped.items()):
        chapter_name = group["name"]
        points = group["points"][:15]
        if not points and _is_usable_point(chapter_name):
            points = [chapter_name]
        if not points:
            continue
        chapter_id = f"chapter_doc_{chapter_order}"
        chapters.append({
            "id": chapter_id,
            "courseId": COURSE_ID,
            "name": chapter_name,
            "status": "已发布",
            "progress": 100,
            "points": points,
            "risk": "由已上传课程资料的章节和关键词生成，教师可继续校正知识点和先修关系。",
            "prerequisites": [],
            "citationCoverage": 100,
            "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "order": chapter_order,
            "autoGenerated": True,
            "sourceType": "knowledge_document",
        })
    if chapters:
        with state.lock:
            state.course_chapters[:] = chapters
            state.persist_state()
    return deepcopy(chapters)


def mastery_status(value: int) -> str:
    if value >= 75:
        return "mastered"
    if value >= 60:
        return "learning"
    if value > 0:
        return "weak"
    return "unlearned"


def _derive_structure(chapters: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not chapters:
        return [], []
    chunks = list_knowledge_chunks(course_id=COURSE_ID)
    nodes: list[dict[str, Any]] = [{
        "id": COURSE_ID,
        "name": COURSE_NAME,
        "type": "course",
        "description": "由课程章节、知识点、真实资料引用和学习结果共同构成的课程知识中枢。",
        "difficulty": "基础",
        "importance": 5,
        "estimatedMinutes": 0,
        "tags": ["课程"],
        "sourceRefs": [],
    }]
    edges: list[dict[str, Any]] = []
    chapter_name_to_id = {chapter["name"]: chapter["id"] for chapter in chapters}
    point_name_to_id: dict[str, str] = {}

    for order, chapter in enumerate(chapters, start=1):
        chapter_sources = _sources_for_terms(chunks, [chapter["name"], *chapter.get("points", [])], limit=4)
        nodes.append({
            "id": chapter["id"],
            "chapterId": chapter["id"],
            "name": chapter["name"],
            "type": "chapter",
            "description": chapter.get("risk") or f"{COURSE_NAME}第 {order} 个知识模块。",
            "difficulty": "进阶" if order >= 5 else "基础",
            "importance": 5,
            "estimatedMinutes": max(60, len(chapter.get("points", [])) * 30),
            "tags": ["章节", chapter.get("status", "")],
            "sourceRefs": chapter_sources,
        })
        edges.append(_edge(COURSE_ID, chapter["id"], "contains", "course_structure"))
        for point_index, point in enumerate(chapter.get("points", []), start=1):
            node_id = _point_id(chapter["id"], point)
            point_name_to_id[point] = node_id
            sources = _sources_for_terms(chunks, [point], limit=3)
            node_type = _node_type(point)
            nodes.append({
                "id": node_id,
                "chapterId": chapter["id"],
                "name": point,
                "type": node_type,
                "description": _description_for_point(point, sources),
                "difficulty": _difficulty_for_point(point, order, point_index),
                "importance": 5 if point_index <= 2 else 4,
                "estimatedMinutes": 45 if node_type == "operation" else 30,
                "tags": [chapter["name"], node_type],
                "sourceRefs": sources,
            })
            edges.append(_edge(chapter["id"], node_id, "contains", "course_structure"))

    for chapter in chapters:
        for prerequisite_name in chapter.get("prerequisites", []):
            prerequisite_id = chapter_name_to_id.get(prerequisite_name) or point_name_to_id.get(prerequisite_name)
            if prerequisite_id:
                edges.append(_edge(prerequisite_id, chapter["id"], "prerequisite", "teacher"))

    return nodes, _dedupe_edges(edges)


def _calculate_mastery_from_learning_data(
    user_id: str,
    nodes: list[dict[str, Any]],
    resources: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    progress = state.load_user_learning_progress(user_id)
    path = state.load_user_learning_path(user_id)
    stages = path.get("stages", []) if isinstance(path, dict) else []
    resources_by_id = {str(item.get("id") or ""): item for item in resources}
    viewed = {str(item) for item in progress.get("viewedResourceIds", [])}
    completed_resources = {str(item) for item in progress.get("completedResourceIds", [])}
    completed_stages = {str(item) for item in progress.get("completedStageIds", [])}
    mastered_resources = {str(item) for item in progress.get("masteredResourceIds", [])}
    mastered_chapters = {str(item) for item in progress.get("masteredChapterIds", [])}
    mastered_points = {str(item) for item in progress.get("masteredKnowledgePoints", [])}
    assessments = [
        item for item in list_records("assessment_results", 200)
        if item.get("userId") == user_id
    ]
    result: dict[str, dict[str, Any]] = {}

    for node in nodes:
        if node.get("type") == "course":
            result[node["id"]] = _empty_mastery_breakdown()
            continue
        matching_stages = [stage for stage in stages if _stage_matches_node(stage, node, resources_by_id)]
        matching_resource_ids = {
            str(resource_id)
            for stage in matching_stages
            for resource_id in stage.get("resources", [])
            if str(resource_id or "").strip()
        }
        matching_resource_ids.update(
            resource_id for resource_id, resource in resources_by_id.items()
            if _resource_matches_node(resource, node)
        )
        path_score = 0
        evidence: list[str] = []
        matched_stage_ids: list[str] = []
        matched_stage_names: list[str] = []

        if node.get("chapterId") in mastered_chapters or any(_names_match(node["name"], point) for point in mastered_points):
            path_score = 100
            evidence.append("学习进度已确认该知识点或章节掌握")
        if matching_resource_ids & mastered_resources:
            path_score = 100
            evidence.append("关联资源已在学习路径中确认掌握")
        for stage in matching_stages:
            stage_id = str(stage.get("id") or "")
            matched_stage_ids.append(stage_id)
            matched_stage_names.append(str(stage.get("name") or stage_id))
            if stage_id in completed_stages or stage.get("status") == "completed":
                path_score = max(path_score, 75)
                evidence.append(f"学习路径阶段「{stage.get('name', stage_id)}」已完成")
        if matching_resource_ids & completed_resources:
            path_score = max(path_score, 70)
            evidence.append(f"已学完 {len(matching_resource_ids & completed_resources)} 份关联资源")
        if matching_resource_ids & viewed:
            path_score = max(path_score, 40)
            evidence.append(f"已浏览 {len(matching_resource_ids & viewed)} 份关联资源")

        assessment_scores: list[int] = []
        assessment_evidence: list[str] = []
        for assessment in assessments:
            for detail in assessment.get("questionDetails", []):
                if not _names_match(node["name"], detail.get("knowledge_point", "")):
                    continue
                score = max(0, min(100, int(detail.get("score", 100 if detail.get("correct") else 0))))
                assessment_scores.append(score)
                assessment_evidence.append(
                    f"{assessment.get('createdAt', '阶段测评')}：{score} 分"
                    f"（{detail.get('error_reason') or '作答达到评分要求'}）"
                )
        recent_scores = assessment_scores[-6:]
        assessment_score = round(sum(recent_scores) / len(recent_scores)) if recent_scores else None
        final_score = (
            round(path_score * 0.4 + assessment_score * 0.6)
            if assessment_score is not None
            else path_score
        )
        all_evidence = list(dict.fromkeys([*evidence, *assessment_evidence]))[-8:]
        result[node["id"]] = {
            "pathScore": path_score,
            "assessmentScore": assessment_score,
            "finalScore": final_score,
            "matchedStageIds": list(dict.fromkeys(matched_stage_ids)),
            "matchedStageNames": list(dict.fromkeys(matched_stage_names)),
            "matchedResourceIds": sorted(matching_resource_ids),
            "assessmentCount": len(recent_scores),
            "formula": "路径进度 40% + 测评 60%" if assessment_score is not None else "暂无测评，使用路径进度",
            "evidence": all_evidence,
        }
    return result


def _empty_mastery_breakdown() -> dict[str, Any]:
    return {
        "pathScore": 0,
        "assessmentScore": None,
        "finalScore": 0,
        "matchedStageIds": [],
        "matchedStageNames": [],
        "matchedResourceIds": [],
        "assessmentCount": 0,
        "formula": "暂无学习路径或测评证据",
        "evidence": [],
    }


def _stage_matches_node(
    stage: dict[str, Any],
    node: dict[str, Any],
    resources_by_id: dict[str, dict[str, Any]],
) -> bool:
    if node.get("type") == "chapter" and str(stage.get("chapterId") or "") == str(node.get("id") or ""):
        return True
    if str(stage.get("chapterId") or "") and str(stage.get("chapterId")) == str(node.get("chapterId") or ""):
        return True
    texts = [
        stage.get("name", ""),
        stage.get("chapterName", ""),
        *(stage.get("knowledgePoints", []) or []),
    ]
    if any(_names_match(node["name"], text) for text in texts):
        return True
    return any(
        _resource_matches_node(resources_by_id.get(str(resource_id), {}), node)
        for resource_id in stage.get("resources", [])
    )


def _resource_matches_node(resource: dict[str, Any], node: dict[str, Any]) -> bool:
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
    if str(metadata.get("chapterId") or "") and str(metadata.get("chapterId")) == str(node.get("chapterId") or node.get("id") or ""):
        return True
    return any(
        _names_match(node["name"], text)
        for text in [metadata.get("topic", ""), metadata.get("chapterName", ""), resource.get("title", "")]
    )


def _resource_index(
    resources: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for resource in resources:
        for node in nodes:
            if _resource_matches_node(resource, node):
                result[node["name"]].append(resource)
    return result


def _sources_for_terms(chunks: list[dict[str, Any]], terms: list[str], limit: int) -> list[dict[str, Any]]:
    scored = []
    clean_terms = [term for term in terms if term]
    for chunk in chunks:
        haystack = f"{chunk.get('section', '')} {' '.join(chunk.get('keywords', []))} {chunk.get('content', '')}"
        score = sum(3 if term in chunk.get("keywords", []) else 1 for term in clean_terms if term in haystack)
        if score <= 0:
            continue
        scored.append((score, chunk))
    scored.sort(key=lambda item: (item[0], len(str(item[1].get("content") or ""))), reverse=True)
    refs = []
    seen = set()
    for _, chunk in scored:
        key = (chunk.get("documentId"), chunk.get("page"))
        if key in seen:
            continue
        seen.add(key)
        refs.append({
            "documentId": chunk.get("documentId"),
            "documentName": chunk.get("documentName"),
            "sourceLocation": chunk.get("section"),
            "chunkId": chunk.get("chunkId"),
            "page": chunk.get("page"),
            "contentPreview": _compact(chunk.get("content", ""), 150),
        })
        if len(refs) >= limit:
            break
    return refs


def _description_for_point(point: str, sources: list[dict[str, Any]]) -> str:
    if sources and sources[0].get("contentPreview"):
        return _compact(str(sources[0]["contentPreview"]), 110)
    return f"{COURSE_NAME}中的“{point}”知识点。"


def _node_type(point: str) -> str:
    if point in APPLICATION_TERMS:
        return "application"
    if point in OPERATION_TERMS or point.endswith("搜索") or point.endswith("排序"):
        return "operation"
    return "concept"


def _difficulty_for_point(point: str, chapter_order: int, point_order: int) -> str:
    if point in APPLICATION_TERMS or chapter_order >= 6 and point_order >= 4:
        return "综合"
    if chapter_order >= 4 or point_order >= 3:
        return "进阶"
    return "基础"


def _point_id(chapter_id: str, name: str) -> str:
    digest = hashlib.sha1(f"{chapter_id}:{name}".encode("utf-8")).hexdigest()[:10]
    return f"kp_{digest}"


def _edge(source: str, target: str, relation_type: str, source_type: str) -> dict[str, Any]:
    digest = hashlib.sha1(f"{source}:{target}:{relation_type}".encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"edge_{digest}",
        "source": source,
        "target": target,
        "type": relation_type,
        "weight": 1,
        "direction": "directed",
        "sourceType": source_type,
        "verified": True,
    }


def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = {}
    for edge in edges:
        result[(edge["source"], edge["target"], edge["type"])] = edge
    return list(result.values())


def _remove_dangling_edges(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = {node["id"] for node in nodes}
    return [edge for edge in edges if edge["source"] in ids and edge["target"] in ids]


def _expand_ids(seed: set[str], edges: list[dict[str, Any]], depth: int) -> set[str]:
    result = set(seed)
    frontier = set(seed)
    for _ in range(depth):
        next_frontier = set()
        for edge in edges:
            if edge["source"] in frontier:
                next_frontier.add(edge["target"])
            if edge["target"] in frontier:
                next_frontier.add(edge["source"])
        next_frontier -= result
        result |= next_frontier
        frontier = next_frontier
        if not frontier:
            break
    return result


def _roll_up_mastery(nodes: list[dict[str, Any]]) -> None:
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        if node.get("chapterId") and node["id"] != node["chapterId"]:
            children[node["chapterId"]].append(node)
    for node in nodes:
        if node["type"] == "chapter" and children.get(node["id"]):
            values = [child.get("mastery", 0) for child in children[node["id"]]]
            node["mastery"] = round(sum(values) / len(values))
            node["masteryStatus"] = mastery_status(node["mastery"])
            node["masteryEvidence"] = list(dict.fromkeys(
                evidence
                for child in children[node["id"]]
                for evidence in child.get("masteryEvidence", [])
            ))[-8:]
            node["masteryBreakdown"] = {
                **_empty_mastery_breakdown(),
                "finalScore": node["mastery"],
                "formula": "由本章节真实知识点掌握度平均聚合",
                "evidence": node["masteryEvidence"],
            }
    chapters = [node for node in nodes if node["type"] == "chapter"]
    course = next((node for node in nodes if node["type"] == "course"), None)
    if course and chapters:
        course["mastery"] = round(sum(node.get("mastery", 0) for node in chapters) / len(chapters))
        course["masteryStatus"] = mastery_status(course["mastery"])
        course["masteryEvidence"] = [f"由 {len(chapters)} 个真实章节掌握度平均聚合"]
        course["masteryBreakdown"] = {
            **_empty_mastery_breakdown(),
            "finalScore": course["mastery"],
            "formula": "由真实章节掌握度平均聚合",
            "evidence": course["masteryEvidence"],
        }


def _graph_stats(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    counts = defaultdict(int)
    for node in nodes:
        counts[node.get("masteryStatus", "unlearned")] += 1
    source_count = sum(1 for node in nodes if node.get("sourceRefs"))
    return {
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "sourceCoverage": round(source_count / max(len(nodes) - 1, 1) * 100),
        "masteryDistribution": dict(counts),
        "orphanCount": len(_orphan_nodes(nodes, edges)),
        "unverifiedEdgeCount": sum(1 for edge in edges if not edge.get("verified")),
    }


def _orphan_nodes(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    connected = {edge["source"] for edge in edges} | {edge["target"] for edge in edges}
    return [node["id"] for node in nodes if node["type"] != "course" and node["id"] not in connected]


def _symbol_size(node: dict[str, Any]) -> int:
    base = {"course": 84, "chapter": 64, "concept": 44, "operation": 48, "application": 50}.get(node["type"], 42)
    return base + max(0, int(node.get("importance", 3)) - 3) * 3


def _source_label(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "暂无课程引用"
    first = refs[0]
    return f"{first.get('documentName', '课程资料')} · 第 {first.get('page', 1)} 页"


def _normalize(value: Any) -> str:
    return re.sub(r"[\s、，,。()（）\-_/]+", "", str(value or "")).lower()


def _names_match(left: Any, right: Any) -> bool:
    left_value = _normalize(left)
    right_value = _normalize(right)
    if not left_value or not right_value:
        return False
    return left_value == right_value or (
        min(len(left_value), len(right_value)) >= 2
        and (left_value in right_value or right_value in left_value)
    )


def _chapter_descriptor_from_chunk(chunk: dict[str, Any]) -> tuple[int, str] | None:
    for value in [chunk.get("documentName"), chunk.get("section"), chunk.get("title")]:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        match = re.search(
            r"第\s*(\d+)\s*章\s*([^·!！/\\：:（）()]{1,24}?)(?=第\s*\d+\s*讲|[·!！/\\：:（(]|$)",
            text,
        )
        if not match:
            continue
        chapter_order = int(match.group(1))
        chapter_name = re.sub(r"\s+", "", match.group(2)).strip("-_ ")
        if (
            chapter_name
            and 1 <= chapter_order <= 20
            and not re.match(r"^\d", chapter_name)
            and ".pdf" not in chapter_name.lower()
            and not re.search(r"\d+题", chapter_name)
        ):
            return chapter_order, chapter_name
    return None


def _is_usable_point(value: str) -> bool:
    normalized = value.strip()
    if len(normalized) < 2 or len(normalized) > 30:
        return False
    return normalized not in {
        "数据结构", "课程资料", "学习目标", "知识点", "本章小结", "小结", "重点", "难点",
    }


def _compact(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else f"{text[:limit]}…"


def _empty_graph(course_id: str) -> dict[str, Any]:
    return {
        "courseId": course_id,
        "courseName": COURSE_NAME,
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "nodes": [],
        "edges": [],
        "stats": _graph_stats([], []),
        "relationTypes": [{"value": key, "label": value} for key, value in RELATION_LABELS.items()],
    }
