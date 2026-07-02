from __future__ import annotations

from typing import Any

RESOURCE_TOPIC_SUFFIXES = (
    "完整思维导图",
    "完整导图",
    "思维导图",
    "分层练习题",
    "练习题",
    "视频演示方案",
    "三分钟视频脚本",
    "视频演示",
    "拓展阅读路径",
    "拓展阅读",
    "代码实践实验",
    "代码实验",
    "实操案例",
    "代码案例",
    "讲解文档",
    "知识结构",
)


def clean_generation_topic(value: Any, fallback: str = "线性表") -> str:
    text = str(value or "").strip()
    for _ in range(8):
        next_text = text
        for suffix in RESOURCE_TOPIC_SUFFIXES:
            next_text = next_text.replace(suffix, "")
        next_text = " ".join(next_text.split()).strip()
        if next_text == text:
            break
        text = next_text
    return text or fallback


def clean_generation_target(value: Any, topic: str, fallback: str = "45 分钟内理解概念、完成例题和代码实践") -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    cleaned = raw
    suffix_count = 0
    for suffix in RESOURCE_TOPIC_SUFFIXES:
        suffix_count += raw.count(suffix)
        cleaned = cleaned.replace(suffix, "")
    cleaned = " ".join(cleaned.split()).strip()
    if not cleaned or suffix_count >= 2 or raw.startswith("重新生成资料："):
        return f"围绕{topic}生成讲解、导图、练习、阅读、视频和代码实践资料"
    return cleaned
