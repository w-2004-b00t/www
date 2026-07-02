from __future__ import annotations

import json
import threading
import time
from copy import deepcopy
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from .. import state
from ..course_config import COURSE_ID, COURSE_NAME
from ..demo_data import AGENT_STEPS, now_text
from ..persistence import is_database_busy_error, list_successful_resource_task_outputs
from ..schemas import ResourceGenerateRequest
from .agent_service import build_agent_steps_for_topic
from .agent_orchestrator import build_execution_order, build_task_runtime_metadata
from .agent_protocol import AGENT_EVENT_PROTOCOL, agent_event_payload
from .agent_state import append_agent_event, list_agent_events, load_agent_task, save_agent_task
from .knowledge_service import search_chunks
from .knowledge_directory_importer import ensure_local_knowledge_base
from .path_planner_service import attach_passed_resources_to_path
from .resource_service import build_resources_for_topic
from .course_progress_service import chapter_for_topic
from .topic_sanitizer import clean_generation_target, clean_generation_topic


def _database_busy_http_exception(exc: BaseException) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="后端数据库繁忙，请稍后重试。若频繁出现，请等待当前生成任务完成后再提交。",
    )


def _persist_task_event(task: dict[str, Any], agent_name: str | None, event_type: str, step: dict[str, Any], payload: dict[str, Any]) -> None:
    save_agent_task(task)
    append_agent_event(
        task["id"],
        agent_name,
        event_type,
        agent_event_payload(
            task["id"],
            agent_name,
            event_type,
            task=task,
            step=step,
            payload=payload,
        ),
    )


def run_generation_task(task_id: str) -> None:
    start_time = time.time()
    try:
        milestones = [
            (8, 0, "画像构建 Agent 正在从自然语言中抽取学习目标、薄弱点和偏好"),
            (20, 1, f"知识检索 Agent 正在检索《{COURSE_NAME}》课程讲义和知识片段"),
            (32, 2, "文档生成 Agent 正在生成 Markdown 课程讲解文档"),
            (44, 3, "题库生成 Agent 正在生成选择、简答、计算和代码题"),
            (56, 4, "多模态生成 Agent 正在生成完整思维导图和视频演示分镜"),
            (66, 5, "代码实操 Agent 正在根据课程引用生成代码案例和运行说明"),
            (78, 6, "路径规划 Agent 正在规划资源学习顺序和阶段检查点"),
            (90, 7, "内容审核 Agent 正在校验引用、难度和答案正确性"),
            (100, 8, "学习评估 Agent 已准备测评闭环，可根据结果更新画像和路径"),
        ]
        execution_order: list[str] = []
        for progress, running_index, message in milestones:
            time.sleep(0.65)
            persistence: tuple[dict[str, Any], str | None, str, dict[str, Any], dict[str, Any]] | None = None
            stop_after_persist = False
            final_input: dict[str, Any] | None = None
            with state.lock:
                task = state.tasks[task_id]
                task["progress"] = progress
                task["message"] = message
                task["updatedAt"] = now_text()
                task["durationMs"] = int((time.time() - start_time) * 1000)
                task["estimatedRemainingMs"] = 0 if progress == 100 else max(100 - progress, 0) * 220
                base_steps = task.get("baseAgentSteps") or task.get("agentSteps") or AGENT_STEPS
                if not execution_order:
                    execution_order = build_execution_order(base_steps)
                    task["agentRuntime"] = build_task_runtime_metadata()
                    task["executionOrder"] = execution_order
                task["currentAgent"] = base_steps[running_index]["name"]
                steps = deepcopy(base_steps)
                knowledge_step = steps[1] if len(steps) > 1 else {}
                knowledge_coverage = (knowledge_step.get("structuredOutput") or {}).get("coverage")
                if running_index == 1 and knowledge_coverage in {"low", "none"} and task.get("strictKnowledgeCoverage"):
                    for index, step in enumerate(steps):
                        if index < running_index:
                            step["status"] = "success"
                        elif index == running_index:
                            step["status"] = "failed"
                            step["errorReason"] = "知识库覆盖不足，已阻止高可信资源生成。"
                        else:
                            step["status"] = "pending"
                    task["agentSteps"] = steps
                    task["status"] = "failed"
                    task["message"] = "知识库未命中足够课程资料，已停止高可信资源生成。可补充资料或重试知识检索智能体。"
                    task["progress"] = progress
                    task["outputPayload"] = {
                        "failed_agent": "knowledge_agent",
                        "failure_reason": "coverage_insufficient",
                        "next_actions": ["补充课程资料", "重新导入本地知识库", "重试知识检索智能体"],
                    }
                    failed_step = deepcopy(steps[running_index]) if running_index < len(steps) else {}
                    persistence = (
                        deepcopy(task),
                        "knowledge_agent",
                        "failed",
                        failed_step,
                        {
                            "coverage": knowledge_coverage,
                            "reason": task["message"],
                            "protocol": AGENT_EVENT_PROTOCOL,
                        },
                    )
                    stop_after_persist = True
                if stop_after_persist:
                    pass
                else:
                    for index, step in enumerate(steps):
                        if progress == 100 or index < running_index:
                            step["status"] = "success"
                        elif index == running_index:
                            step["status"] = "running"
                        else:
                            step["status"] = "pending"
                    task["agentSteps"] = steps
                    if progress == 100:
                        task["status"] = "running"
                        task["message"] = "学习资源已生成，正在写入资源中心并同步学习路径。"
                    current_step = deepcopy(steps[running_index]) if running_index < len(steps) else {}
                    persistence = (
                        deepcopy(task),
                        task.get("currentAgent"),
                        "progress",
                        current_step,
                        {
                            "agentStep": current_step,
                            "executionOrder": execution_order,
                        },
                    )
                    final_input = deepcopy(task) if progress == 100 else None
            if persistence:
                persisted_task, event_agent, event_type, event_step, event_payload = persistence
                _persist_task_event(persisted_task, event_agent, event_type, event_step, event_payload)
            if stop_after_persist:
                return
            if progress == 100 and final_input:
                user_id = str(final_input.get("userId") or "anonymous")
                generated_resources = build_resources_for_topic(
                    final_input.get("topic", "课程资料"),
                    final_input.get("target", "掌握核心知识点"),
                    final_input.get("resourceTypes") or [],
                    final_input.get("chapter") if isinstance(final_input.get("chapter"), dict) else None,
                )
                _save_resources_with_global_snapshot(user_id, generated_resources)
                learning_path = attach_passed_resources_to_path(user_id, generated_resources, task_id=task_id)
                passed_count = len([item for item in generated_resources if item.get("auditStatus") == "passed"])
                warning_count = len([item for item in generated_resources if item.get("auditStatus") == "warning"])
                resource_ids = [item["id"] for item in generated_resources]
                passed_resource_ids = [item["id"] for item in generated_resources if item.get("auditStatus") == "passed"]
                with state.lock:
                    task = state.tasks[task_id]
                    task["message"] = f"已生成 {len(generated_resources)} 份学习资料，并已将通过审核的资源自动同步到学习路径"
                    task["outputPayload"] = {
                        "resource_count": len(generated_resources),
                        "audit_passed": passed_count,
                        "audit_warning": warning_count,
                        "resourceIds": resource_ids,
                        "passedResourceIds": passed_resource_ids,
                        "inserted_path_tasks": len(passed_resource_ids),
                        "profile_update_drafts": 1,
                        "resources": deepcopy(generated_resources),
                        "learningPath": deepcopy(learning_path),
                        "next_actions": ["查看学习路径", "开始学习", "进入测评"],
                        "audit_status": f"{warning_count} 份资源需教师复核，其余资源自动通过",
                    }
                    task["status"] = "success"
                    task["updatedAt"] = now_text()
                    final_task = deepcopy(task)
                    final_step = deepcopy(current_step)
                _persist_task_event(
                    final_task,
                    final_task.get("currentAgent"),
                    "progress",
                    final_step,
                    {
                        "agentStep": final_step,
                        "executionOrder": execution_order,
                    },
                )
    except Exception as exc:
        _mark_generation_task_failed(task_id, exc, start_time)


def _save_resources_with_global_snapshot(user_id: str, generated_resources: list[dict[str, Any]]) -> None:
    merged_user_resources = _merge_resources(state.load_user_resources(user_id), generated_resources)
    state.save_user_resources(user_id, merged_user_resources)
    state.resources[:] = _merge_resources(state.resources, generated_resources)
    state.persist_state()


def _merge_resources(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in [*incoming, *existing]:
        resource_id = str(item.get("id") or "").strip()
        if not resource_id:
            continue
        if resource_id not in by_id:
            order.append(resource_id)
            by_id[resource_id] = deepcopy(item)

    def sort_key(resource_id: str) -> str:
        item = by_id[resource_id]
        return str(item.get("createdAt") or item.get("updatedAt") or "")

    return [by_id[resource_id] for resource_id in sorted(order, key=sort_key, reverse=True)]


def _agent_title_to_step_name(agent_name: str | None) -> str | None:
    mapping = {
        "画像构建 Agent": "profile_agent",
        "知识检索 Agent": "knowledge_agent",
        "资源生成 Agent": "document_agent",
        "文档生成 Agent": "document_agent",
        "题库生成 Agent": "exercise_agent",
        "拓展阅读 Agent": "document_agent",
        "多模态生成 Agent": "multimodal_agent",
        "代码实操 Agent": "code_agent",
        "路径规划 Agent": "path_agent",
        "内容审核 Agent": "audit_agent",
        "学习评估 Agent": "assessment_agent",
    }
    return mapping.get(str(agent_name or "").strip())


def _failure_step_from_exception(task: dict[str, Any], exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            step_name = _agent_title_to_step_name(str(detail.get("agentName") or ""))
            if step_name:
                return step_name
        detail_text = str(detail)
        if "知识库" in detail_text or "引用" in detail_text:
            return "knowledge_agent"
    current_agent = str(task.get("currentAgent") or "").strip()
    if current_agent:
        return current_agent
    return "resource_agent"


def _failure_message_from_exception(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            message = str(detail.get("message") or detail.get("detail") or "").strip()
            missing = detail.get("missingRequirements")
            if isinstance(missing, list) and missing:
                message = f"{message} 缺少条件：{'、'.join(str(item) for item in missing)}".strip()
            if message:
                return message
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return f"{exc.__class__.__name__}: {exc}"


def _failure_detail_from_exception(exc: Exception) -> Any:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            result = deepcopy(detail)
            nested = result.get("detail")
            if isinstance(nested, dict):
                result.update(nested)
                result["detail"] = nested.get("rawFailure") or nested.get("reasonCode") or str(nested)
            return result
        if isinstance(detail, str) and detail.strip():
            return {"detail": detail.strip()}
    return {"detail": f"{exc.__class__.__name__}: {exc}"}


def _mark_generation_task_failed(task_id: str, exc: Exception, start_time: float) -> None:
    persistence: tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]] | None = None
    with state.lock:
        task = state.tasks.get(task_id) or load_agent_task(task_id)
        if not task:
            return
        steps = deepcopy(task.get("agentSteps") or task.get("baseAgentSteps") or AGENT_STEPS)
        current_agent = _failure_step_from_exception(task, exc)
        failure_message = _failure_message_from_exception(exc)
        failure_detail = _failure_detail_from_exception(exc)
        failed_index = next((index for index, item in enumerate(steps) if item.get("name") == current_agent), -1)
        for index, step in enumerate(steps):
            if failed_index >= 0 and index < failed_index:
                step["status"] = "success"
                step["errorReason"] = None
            elif step.get("name") == current_agent or (failed_index < 0 and step.get("status") == "running"):
                step["status"] = "failed"
                step["errorReason"] = failure_message
            elif failed_index >= 0 and index > failed_index:
                step["status"] = "pending"
        task["agentSteps"] = steps
        task["status"] = "failed"
        task["currentAgent"] = current_agent
        task["message"] = f"{failure_message} 已停止生成，不会使用假数据兜底。"
        task["updatedAt"] = now_text()
        task["durationMs"] = int((time.time() - start_time) * 1000)
        task["estimatedRemainingMs"] = 0
        task["outputPayload"] = {
            **(task.get("outputPayload") if isinstance(task.get("outputPayload"), dict) else {}),
            "failed_agent": current_agent,
            "failure_reason": "generation_exception",
            "error": failure_message,
            "errorDetail": failure_detail,
            "next_actions": ["检查 DeepSeek 配置", "确认本地知识库资料", "重试失败 Agent"],
        }
        state.tasks[task_id] = task
        persistence = (
            deepcopy(task),
            current_agent,
            next((step for step in steps if step.get("name") == current_agent), {}),
            {"error": task["outputPayload"]["error"], "protocol": AGENT_EVENT_PROTOCOL},
        )
    if persistence:
        persisted_task, current_agent, failed_step, payload = persistence
        _persist_task_event(persisted_task, current_agent, "failed", failed_step, payload)


def create_resource_task(payload: ResourceGenerateRequest, user_id: str = "anonymous") -> dict[str, str]:
    if payload.course_id != COURSE_ID:
        raise HTTPException(status_code=404, detail=f"当前系统仅维护《{COURSE_NAME}》")
    safe_topic = clean_generation_topic(payload.topic or "数据结构核心知识点", "数据结构核心知识点")
    safe_target = clean_generation_target(payload.target or "掌握核心知识点", safe_topic, "掌握核心知识点")
    chapter = {
        **chapter_for_topic(safe_topic),
        **({"chapterId": payload.chapter_id} if payload.chapter_id else {}),
        **({"chapterName": payload.chapter_name} if payload.chapter_name else {}),
    }
    import_result = ensure_local_knowledge_base()
    retrieval = search_chunks(safe_topic, 20)
    task_id = f"task_{uuid4().hex[:10]}"
    requested_types = _normalize_resource_types(payload.resource_types)
    agent_steps = _build_resource_agent_steps(safe_topic, safe_target, requested_types, retrieval, import_result)
    if retrieval.get("coverage") in {"low", "none"}:
        status = "failed"
        message = "本地知识库导入或检索不足，已停止高可信资源生成。请检查 E:\\知识库 后重试。"
        for index, step in enumerate(agent_steps):
            if step.get("name") == "profile_agent":
                step["status"] = "success"
                step["errorReason"] = None
            elif step.get("name") == "knowledge_agent":
                step["status"] = "failed"
                step["errorReason"] = "本地知识库未命中足够相关片段，已阻止高可信资源生成。"
            elif index > 1:
                step["status"] = "pending"
                step["errorReason"] = None
    else:
        status = "running"
        message = "本地知识库已就绪，资源生成任务正在执行。"
    task = {
        "id": task_id,
        "taskType": "resource",
        "status": status,
        "progress": 0,
        "currentAgent": "knowledge_agent",
        "message": message,
        "agentSteps": agent_steps,
        "createdAt": now_text(),
        "updatedAt": now_text(),
        "durationMs": 0,
        "estimatedRemainingMs": 0,
        "courseName": COURSE_NAME,
        "userId": user_id,
        "profileVersion": payload.profile_id or "profile_v1_confirmed",
        "inputPayload": {
            "course_id": payload.course_id,
            "course_name": COURSE_NAME,
            "topic": safe_topic,
            "target": safe_target,
            "resource_types": requested_types,
            "profile_id": payload.profile_id,
            "profile_version": payload.profile_id or "profile_v1_confirmed",
            "chapter_id": chapter.get("chapterId"),
            "chapter_name": chapter.get("chapterName"),
        },
        "topic": safe_topic,
        "target": safe_target,
        "chapter": chapter,
        "resourceTypes": requested_types,
        "strictKnowledgeCoverage": True,
        "agentRuntime": build_task_runtime_metadata(),
        "messageProtocol": AGENT_EVENT_PROTOCOL,
        "outputPayload": {
            "importResult": import_result,
            "retrievalCoverage": retrieval.get("coverage"),
            "matchedChunks": len(retrieval.get("items", [])),
            "failed_agent": "knowledge_agent" if status == "failed" else None,
            "failure_reason": "coverage_insufficient" if status == "failed" else None,
            "next_actions": ["等待生成完成", "查看引用证据", "加入学习路径"] if status == "running" else ["检查 E:\\知识库", "重新导入本地知识库", "更换具体学习主题"],
        },
    }
    task["baseAgentSteps"] = deepcopy(task["agentSteps"])
    with state.lock:
        state.tasks[task_id] = task
    try:
        _persist_task_event(
            deepcopy(task),
            "knowledge_agent",
            "started" if status == "running" else "failed",
            task["agentSteps"][1] if len(task.get("agentSteps", [])) > 1 else {},
            {"inputPayload": task["inputPayload"]},
        )
    except Exception as exc:
        with state.lock:
            state.tasks.pop(task_id, None)
        if is_database_busy_error(exc):
            raise _database_busy_http_exception(exc) from exc
        raise
    if status == "running":
        threading.Thread(target=run_generation_task, args=(task_id,), daemon=True).start()
    return {"task_id": task_id, "taskId": task_id, "status": status}


def _build_resource_agent_steps(topic: str, target: str, resource_types: list[str], retrieval: dict[str, Any], import_result: dict[str, Any]) -> list[dict[str, Any]]:
    safe_topic = topic.strip() or "数据结构核心知识点"
    citations = [
        {
            "documentName": item["document_name"],
            "sourceLocation": item["source_location"],
            "chunkId": item["chunk_id"],
            "contentPreview": item["content"][:160],
            "page": item["page"],
            "similarity": item["score"],
        }
        for item in retrieval.get("items", [])
    ]

    def step(name: str, title: str, summary: str, structured: dict[str, Any], status: str = "pending") -> dict[str, Any]:
        return {
            "name": name,
            "title": title,
            "status": status,
            "summary": summary,
            "inputSummary": f"主题：{safe_topic}",
            "outputSummary": summary,
            "tools": ["本地知识库", "SQLite 知识片段", "向量/关键词混合检索"],
            "responsibility": summary,
            "confidence": 0.9 if retrieval.get("coverage") == "sufficient" else 0.45,
            "auditStatus": "引用充分" if retrieval.get("coverage") == "sufficient" else "命中不足",
            "durationMs": 0,
            "citations": citations[:3],
            "structuredOutput": structured,
            "errorReason": None if retrieval.get("coverage") == "sufficient" else "本地知识库未命中足够相关片段。",
            "downstreamImpact": ["资源生成", "学习路径", "测评闭环"],
            "evidence": [
                {"title": "导入来源", "value": str(import_result.get("sourcePath", r"E:\知识库")), "type": "tool"},
                {"title": "命中片段", "value": str(len(retrieval.get("items", []))), "type": "citation"},
            ],
        }

    labels = {
        "explanation": "讲解文档",
        "mindmap": "思维导图",
        "exercise": "练习题",
        "reading": "拓展阅读",
        "video_script": "视频脚本",
        "lab": "代码实验",
    }
    selected_labels = [labels.get(item, item) for item in resource_types]
    return [
        step("profile_agent", "画像构建 Agent", "匹配学习目标、薄弱点和资源偏好。", {"topic": safe_topic, "target": target, "preferences": ["图解", "例题", "代码实践"]}, "running"),
        step("knowledge_agent", "知识检索 Agent", "检索 E:\\知识库 中的数据结构课程资料。", {
            "query": safe_topic,
            "coverage": retrieval.get("coverage"),
            "matched_chunks": [
                {
                    "document": item["document_name"],
                    "page": item["page"],
                    "section": item["source_location"],
                    "chunk_id": item["chunk_id"],
                    "score": item["score"],
                    "preview": item["content"][:80],
                }
                for item in retrieval.get("items", [])
            ],
            "missing_knowledge": retrieval.get("missing_knowledge", []),
            "importResult": import_result,
        }),
        step("document_agent", "文档生成 Agent", "生成带引用的 Markdown 讲解文档。", {"resource": "explanation", "citations": len(citations)}),
        step("exercise_agent", "题库生成 Agent", "生成选择、简答、复杂度分析和代码实践题。", {"resource": "exercise", "types": ["single", "short", "calculation", "code"]}),
        step("multimodal_agent", "多模态生成 Agent", "生成思维导图和视频演示脚本。", {"resources": ["mindmap", "video_script"]}),
        step("code_agent", "代码实操 Agent", "优先使用源码 zip 片段生成实验任务。", {"resource": "lab", "preferSourceType": "code_repository"}),
        step("path_agent", "路径规划 Agent", "规划资源学习顺序和阶段检查点。", {"sequence": selected_labels}),
        step("audit_agent", "内容审核 Agent", "校验引用、难度和答案完整性。", {"citationCount": len(citations), "coverage": retrieval.get("coverage")}),
        step("assessment_agent", "学习评估 Agent", "准备测评闭环与错题反馈。", {"weakness": [safe_topic], "next": "阶段测评"}),
    ]


def _normalize_resource_types(resource_types: list[str]) -> list[str]:
    required = ["explanation", "mindmap", "exercise", "reading", "video_script", "lab"]
    result: list[str] = []
    for item in [*(resource_types or []), *required]:
        if item and item not in result:
            result.append(item)
    return result or required


def get_task_or_404(task_id: str, user_id: str | None = None) -> dict[str, Any]:
    task = state.tasks.get(task_id)
    if not task:
        task = load_agent_task(task_id)
        if task:
            state.tasks[task_id] = task
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _repair_task_user_scope(task, user_id)
    result = deepcopy(task)
    result["events"] = list_agent_events(task_id)
    return result


def _repair_task_user_scope(task: dict[str, Any], user_id: str | None) -> None:
    if not user_id or user_id == "anonymous":
        return
    owner = str(task.get("userId") or "").strip()
    if owner and owner != user_id:
        raise HTTPException(status_code=404, detail="任务不属于当前用户")

    changed = False
    if not owner:
        task["userId"] = user_id
        changed = True

    if task.get("status") == "success":
        output = task.get("outputPayload") if isinstance(task.get("outputPayload"), dict) else {}
        output_resources = output.get("resources") if isinstance(output, dict) else None
        output_resource_ids = [
            str(item)
            for item in (output.get("resourceIds") if isinstance(output.get("resourceIds"), list) else [])
            if str(item or "").strip()
        ]
        if not isinstance(output_resources, list) or not output_resources:
            for repair_output in list_successful_resource_task_outputs(user_id=user_id):
                repair_resources = repair_output.get("resources") if isinstance(repair_output, dict) else None
                if isinstance(repair_resources, list) and repair_resources:
                    output_resources = repair_resources
                    output_resource_ids = [
                        str(item.get("id"))
                        for item in repair_resources
                        if isinstance(item, dict) and str(item.get("id") or "").strip()
                    ]
                    output["resources"] = deepcopy(repair_resources)
                    output["resourceIds"] = output_resource_ids
                    output["resource_count"] = len(output_resource_ids)
                    task["outputPayload"] = output
                    changed = True
                    break

        current_resources = state.load_user_resources(user_id)
        current_ids = {str(item.get("id") or "") for item in current_resources if isinstance(item, dict)}
        missing_ids = [resource_id for resource_id in output_resource_ids if resource_id not in current_ids]
        if missing_ids and isinstance(output_resources, list) and output_resources:
            _save_resources_with_global_snapshot(user_id, deepcopy(output_resources))
            task["message"] = task.get("message") or f"已生成 {len(output_resources)} 份学习资料"
            changed = True

    if changed:
        state.tasks[task["id"]] = task
        save_agent_task(task)


def retry_agent_step(task_id: str, agent_name: str, user_id: str | None = None) -> dict[str, Any]:
    task = state.tasks.get(task_id) or load_agent_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    _repair_task_user_scope(task, user_id)
    step_index = next((index for index, item in enumerate(task.get("agentSteps", [])) if item["name"] == agent_name), -1)
    if step_index < 0:
        raise HTTPException(status_code=404, detail="智能体步骤不存在")
    step = task["agentSteps"][step_index]
    _persist_task_event(
        deepcopy(task),
        agent_name,
        "retry_started",
        deepcopy(step),
        {"previousStatus": step.get("status"), "errorReason": step.get("errorReason")},
    )
    step["status"] = "running"
    step["errorReason"] = None
    step["retryCount"] = int(step.get("retryCount", 0)) + 1
    task["status"] = "running"
    task["currentAgent"] = agent_name
    task["message"] = f"{step['title']}正在单步重试"
    task["updatedAt"] = now_text()
    time.sleep(0.25)
    if agent_name == "knowledge_agent":
        topic = str((task.get("inputPayload") or {}).get("topic") or task.get("topic") or "")
        retrieval = search_chunks(topic, 20)
        step["structuredOutput"] = {
            **(step.get("structuredOutput") or {}),
            "matched_chunks": [
                {
                    "document": item["document_name"],
                    "page": item["page"],
                    "section": item["source_location"],
                    "chunk_id": item["chunk_id"],
                    "score": item["score"],
                    "preview": item["content"][:42],
                }
                for item in retrieval["items"]
            ],
            "coverage": retrieval["coverage"],
            "missing_knowledge": retrieval["missing_knowledge"],
        }
        if retrieval["coverage"] in {"low", "none"}:
            step["status"] = "failed"
            step["errorReason"] = "重试后仍未命中足够课程资料。"
            task["status"] = "failed"
            task["message"] = "知识检索重试失败，建议补充课程资料。"
        else:
            step["status"] = "success"
            task["message"] = "知识检索智能体重试成功，可继续执行任务。"
    else:
        step["status"] = "success"
        task["message"] = f"{step['title']}单步重试成功，正在继续执行严格生成链路。"
    task["agentSteps"][step_index] = step
    state.tasks[task_id] = task
    _persist_task_event(
        deepcopy(task),
        agent_name,
        "retry_finished",
        deepcopy(step),
        {"status": step["status"], "message": task["message"]},
    )
    if task.get("status") == "running" and step.get("status") == "success":
        threading.Thread(target=run_generation_task, args=(task_id,), daemon=True).start()
    result = deepcopy(task)
    result["events"] = list_agent_events(task_id)
    return result


def encode_task_event(task: dict[str, Any]) -> str:
    return f"data: {json.dumps(task, ensure_ascii=False)}\n\n"
