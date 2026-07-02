from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any
from collections import Counter, defaultdict
from contextlib import closing

from ..demo_data import now_text
from ..persistence import connect, write_transaction


STATUS_ALIASES = {"需补强": "待订正", "进行中": "订正中"}
VALID_STATUSES = {"待订正", "订正中", "待验证", "已掌握"}


class MistakeVersionConflict(RuntimeError):
    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__("mistake version conflict")
        self.current = current


def normalize_mistake(record: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(record)
    item["status"] = STATUS_ALIASES.get(str(item.get("status") or ""), str(item.get("status") or "待订正"))
    if item["status"] not in VALID_STATUSES:
        item["status"] = "待订正"
    item.setdefault("type", "short")
    item.setdefault("options", [])
    item.setdefault("userAnswer", "")
    item["answer"] = str(item.get("answer") or "")
    item.setdefault("analysis", str(item.get("wrongReason") or ""))
    item.setdefault("rubric", [])
    item.setdefault("citations", [])
    item.setdefault("correctionAttempts", [])
    item.setdefault("verificationQuestions", [])
    item.setdefault("verificationAttempts", [])
    item.setdefault("masteryEvidence", [])
    item["version"] = max(1, int(item.get("version") or 1))
    item.setdefault("createdAt", now_text())
    item.setdefault("updatedAt", item["createdAt"])
    return item


def _timestamp(record: dict[str, Any]) -> datetime:
    raw = str(record.get("updatedAt") or record.get("createdAt") or "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return datetime.min


def get_mistake(mistake_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        if user_id is None:
            row = conn.execute("select value from mistake_records where id = ?", (mistake_id,)).fetchone()
        else:
            row = conn.execute(
                "select value from mistake_records where id = ? and user_id = ?",
                (mistake_id, user_id),
            ).fetchone()
    return normalize_mistake(json.loads(row["value"])) if row else None


def list_mistakes(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "select value from mistake_records where user_id = ? "
            "order by updated_at desc, created_at desc limit ?",
            (user_id, limit),
        ).fetchall()
    return [normalize_mistake(json.loads(row["value"])) for row in rows]


def mistake_analytics(records: list[dict[str, Any]]) -> dict[str, Any]:
    items = [normalize_mistake(item) for item in records]
    total = len(items)
    status_counts = Counter(item["status"] for item in items)
    correction_attempts = [attempt for item in items for attempt in item.get("correctionAttempts", [])]
    latest_scores = [
        int(item["latestCorrection"].get("score") or 0)
        for item in items
        if isinstance(item.get("latestCorrection"), dict)
    ]
    verification_attempts = [
        attempt for item in items for attempt in item.get("verificationAttempts", [])
        if isinstance(attempt, dict)
    ]
    knowledge_stats: dict[str, Counter] = defaultdict(Counter)
    for item in items:
        knowledge_stats[str(item.get("knowledge") or "未分类")][item["status"]] += 1
    mastered = status_counts["已掌握"]
    passed_verifications = sum(1 for attempt in verification_attempts if attempt.get("passed"))
    return {
        "total": total,
        "pendingCorrection": status_counts["待订正"] + status_counts["订正中"],
        "pendingVerification": status_counts["待验证"],
        "mastered": mastered,
        "masteryRate": round(mastered / total * 100) if total else 0,
        "averageCorrectionAttempts": round(len(correction_attempts) / total, 1) if total else 0,
        "latestCorrectionAverageScore": round(sum(latest_scores) / len(latest_scores)) if latest_scores else 0,
        "verificationPassRate": (
            round(passed_verifications / len(verification_attempts) * 100)
            if verification_attempts else 0
        ),
        "knowledgeBreakdown": [
            {
                "knowledge": knowledge,
                "total": sum(counts.values()),
                "pendingCorrection": counts["待订正"] + counts["订正中"],
                "pendingVerification": counts["待验证"],
                "mastered": counts["已掌握"],
            }
            for knowledge, counts in sorted(
                knowledge_stats.items(),
                key=lambda pair: sum(pair[1].values()),
                reverse=True,
            )[:8]
        ],
    }


def create_mistake(record: dict[str, Any]) -> dict[str, Any]:
    item = normalize_mistake(record)
    item["version"] = 1
    item["updatedAt"] = now_text()
    with write_transaction() as conn:
        conn.execute(
            """
            insert into mistake_records(id, value, created_at, user_id, status, version, updated_at)
            values(?, ?, current_timestamp, ?, ?, ?, current_timestamp)
            on conflict(id) do update set
                value = excluded.value,
                user_id = excluded.user_id,
                status = excluded.status,
                version = excluded.version,
                updated_at = current_timestamp
            """,
            (
                item["id"],
                json.dumps(item, ensure_ascii=False),
                str(item.get("userId") or ""),
                item["status"],
                item["version"],
            ),
        )
    return deepcopy(item)


def update_mistake(record: dict[str, Any], expected_version: int | None = None) -> dict[str, Any]:
    item = normalize_mistake(record)
    with write_transaction() as conn:
        row = conn.execute(
            "select value, version from mistake_records where id = ? and user_id = ?",
            (item["id"], str(item.get("userId") or "")),
        ).fetchone()
        if not row:
            item["version"] = 1
            item["updatedAt"] = now_text()
            conn.execute(
                """
                insert into mistake_records(id, value, created_at, user_id, status, version, updated_at)
                values(?, ?, current_timestamp, ?, ?, 1, current_timestamp)
                """,
                (
                    item["id"],
                    json.dumps(item, ensure_ascii=False),
                    str(item.get("userId") or ""),
                    item["status"],
                ),
            )
            return deepcopy(item)
        current = normalize_mistake(json.loads(row["value"]))
        current_version = int(row["version"] or current.get("version") or 1)
        if expected_version is not None and expected_version != current_version:
            raise MistakeVersionConflict(current)
        item["version"] = current_version + 1
        item["updatedAt"] = now_text()
        conn.execute(
            """
            update mistake_records
            set value = ?, status = ?, version = ?, updated_at = current_timestamp
            where id = ? and user_id = ? and version = ?
            """,
            (
                json.dumps(item, ensure_ascii=False),
                item["status"],
                item["version"],
                item["id"],
                str(item.get("userId") or ""),
                current_version,
            ),
        )
        if conn.total_changes != 1:
            latest = get_mistake(item["id"], str(item.get("userId") or "")) or current
            raise MistakeVersionConflict(latest)
    return deepcopy(item)


def migrate_legacy_mistakes(legacy_records: list[dict[str, Any]]) -> int:
    existing: dict[str, dict[str, Any]] = {}
    with closing(connect()) as conn:
        rows = conn.execute("select value from mistake_records").fetchall()
    for row in rows:
        record = normalize_mistake(json.loads(row["value"]))
        existing[str(record["id"])] = record
    merged = dict(existing)
    for raw in legacy_records:
        if not raw.get("id"):
            continue
        candidate = normalize_mistake(raw)
        current = merged.get(str(candidate["id"]))
        if current is None or _timestamp(candidate) > _timestamp(current):
            candidate["version"] = max(int(candidate.get("version") or 1), int((current or {}).get("version") or 1))
            merged[str(candidate["id"])] = candidate
    with write_transaction() as conn:
        for record in merged.values():
            conn.execute(
                """
                insert into mistake_records(id, value, created_at, user_id, status, version, updated_at)
                values(?, ?, current_timestamp, ?, ?, ?, current_timestamp)
                on conflict(id) do update set
                    value = excluded.value,
                    user_id = excluded.user_id,
                    status = excluded.status,
                    version = excluded.version,
                    updated_at = current_timestamp
                """,
                (
                    record["id"],
                    json.dumps(record, ensure_ascii=False),
                    str(record.get("userId") or ""),
                    record["status"],
                    int(record.get("version") or 1),
                ),
            )
    return len(merged)
