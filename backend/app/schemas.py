from __future__ import annotations

from typing import Any

from pydantic import BaseModel

class LoginRequest(BaseModel):
    username: str
    password: str | None = None
    role: str | None = None


class RegisterRequest(BaseModel):
    username: str
    password: str | None = None
    name: str
    role: str
    major: str | None = None
    grade: str | None = None


class ProfileExtractRequest(BaseModel):
    course_id: str | None = None
    message: str


class ProfileDialogTurnRequest(BaseModel):
    conversation: list[dict[str, str]]
    answered_dimensions: list[str] = []
    required_dimensions: list[str] = []
    current_profile_context: dict[str, Any] | None = None


class ProfileDialogSessionRequest(BaseModel):
    messages: list[dict[str, str]] = []
    rawProfileInput: str = ""
    coveredDimensions: list[str] = []
    missingDimensions: list[str] = []
    nextQuestionTitle: str = ""
    canExtract: bool = False
    draftItems: list[dict[str, Any]] = []
    saveCompleted: bool = False


class ProfileConfirmRequest(BaseModel):
    dimensions: list[dict[str, Any]]


class ProfileUpdateConfirmRequest(BaseModel):
    draft_ids: list[str] | None = None


class ProfileUpdateRejectRequest(BaseModel):
    draft_id: str


class ProfileManualUpdateDraftItem(BaseModel):
    dimension: str
    value: str
    note: str | None = None


class ProfileManualUpdateDraftRequest(BaseModel):
    items: list[ProfileManualUpdateDraftItem]


class KnowledgeSearchRequest(BaseModel):
    course_id: str | None = None
    query: str
    top_k: int = 5


class ResourceGenerateRequest(BaseModel):
    course_id: str
    topic: str
    target: str
    resource_types: list[str]
    profile_id: str | None = None
    chapter_id: str | None = None
    chapter_name: str | None = None


class VideoDemoGenerateRequest(BaseModel):
    mode: str = "animated_lesson"


class LearningPathAttachResourcesRequest(BaseModel):
    resource_ids: list[str]
    task_id: str | None = None


class LearningMasteryRequest(BaseModel):
    knowledge_points: list[str] = []
    resource_ids: list[str] = []
    chapter_ids: list[str] = []
    evidence: list[str] = []


class TutorChatRequest(BaseModel):
    message: str
    course_id: str | None = None


class TutorExtraRequest(BaseModel):
    message: str
    answer: str
    type: str
    course_id: str | None = None


class AssessmentSubmitRequest(BaseModel):
    assessmentId: str | None = None
    answers: dict[str, Any] = {}


class ResourceFeedbackRequest(BaseModel):
    type: str
    note: str | None = None


class ResourceAuditRequest(BaseModel):
    status: str
    reason: str
    scope: str = "student"


class ResourcePracticeSubmitRequest(BaseModel):
    answers: dict[str, str] = {}


class LearningIntensityRequest(BaseModel):
    intensity: str


class TutorNoteRequest(BaseModel):
    title: str
    content: str


class TutorMistakeRequest(BaseModel):
    knowledge: str
    stem: str
    wrongReason: str
    fixTask: str | None = None
    type: str | None = None
    options: list[str] = []
    answer: str | None = None
    analysis: str | None = None
    rubric: list[str] = []
    citations: list[dict[str, Any]] = []


class TutorActionRequest(BaseModel):
    message: str
    mode: str | None = None
    answer: str | None = None
    course_id: str | None = None


class TutorFeedbackRequest(BaseModel):
    type: str
    message: str | None = None
    answer: str | None = None


class MistakeStatusRequest(BaseModel):
    status: str


class MistakeCorrectionRequest(BaseModel):
    answer: str
    expectedVersion: int | None = None


class MistakeVerificationRequest(BaseModel):
    answers: dict[str, str]
    expectedVersion: int | None = None


class MistakeSimilarRequest(BaseModel):
    expectedVersion: int | None = None


class CourseChapterCreateRequest(BaseModel):
    name: str
    points: list[str] = []
    prerequisites: list[str] = []


class CourseChapterUpdateRequest(BaseModel):
    name: str | None = None
    points: list[str] | None = None
    prerequisites: list[str] | None = None
    risk: str | None = None


class KnowledgeRelationUpsertRequest(BaseModel):
    id: str | None = None
    source: str
    target: str
    type: str
    weight: float = 1
    direction: str = "directed"
    verified: bool = True
