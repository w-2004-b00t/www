import { apiGet, apiPost } from './client'
import type {
  AuditRecord,
  GenerationTask,
  LearningPath,
  LearningProgress,
  LearningResource,
  MindMapPayload,
  NextLearningTopic,
  ProfileUpdateDraft,
  ResourceFeedback,
  ResourcePracticeResult,
  VideoRenderMode,
  VideoDemoJob,
  VideoDemoPayload,
} from '../types/common'

export function listResourcesApi(includeCompleted = false) {
  return apiGet<LearningResource[]>(`/resources${includeCompleted ? '?include_completed=true' : ''}`)
}

export function getResourceApi(resourceId: string) {
  return apiGet<LearningResource>(`/resources/${encodeURIComponent(resourceId)}`)
}

export function getLearningPathApi() {
  return apiGet<LearningPath>('/learning-paths/me')
}

export function generateLearningPathApi() {
  return apiPost<LearningPath>('/learning-paths/generate', {})
}

export function getLearningProgressApi() {
  return apiGet<LearningProgress>('/learning-paths/me/progress')
}

export function getNextLearningTopicApi() {
  return apiGet<NextLearningTopic>('/learning-paths/me/next-topic')
}

export function generateResourcesApi(payload: {
  course_id: string
  topic: string
  target: string
  resource_types: string[]
  profile_id?: string
  chapter_id?: string
  chapter_name?: string
}) {
  return apiPost<{ task_id: string; taskId: string; status: string }>('/resources/generate', payload)
}

export function getTaskApi(taskId: string) {
  return apiGet<GenerationTask>(`/tasks/${taskId}`)
}

export function retryAgentStepApi(taskId: string, agentName: string) {
  return apiPost<GenerationTask>(`/tasks/${taskId}/agents/${agentName}/retry`, {})
}

export function updateResourceAuditApi(
  resourceId: string,
  payload: { status: LearningResource['auditStatus']; reason: string; scope: 'student' | 'class' },
) {
  return apiPost<{ resource: LearningResource; history: AuditRecord[] }>(`/resources/${resourceId}/audit`, payload)
}

export function submitResourceFeedbackApi(
  resourceId: string,
  payload: { type: ResourceFeedback['type']; note?: string },
) {
  return apiPost<{ resource: LearningResource; feedback: ResourceFeedback; profileUpdateDraft?: ProfileUpdateDraft | null }>(`/resources/${resourceId}/feedback`, payload)
}

export function submitResourcePracticeApi(resourceId: string, payload: { answers: Record<string, string> }) {
  return apiPost<ResourcePracticeResult>(`/resources/${resourceId}/practice/submit`, payload)
}

export function getResourceMindmapApi(resourceId: string) {
  return apiGet<MindMapPayload>(`/resources/${resourceId}/mindmap`)
}

export function getResourceVideoDemoApi(resourceId: string) {
  return apiGet<VideoDemoPayload>(`/resources/${resourceId}/video-demo`)
}

export function generateResourceVideoDemoApi(resourceId: string, payload: { mode?: VideoRenderMode } = {}) {
  return apiPost<VideoDemoJob>(`/resources/${resourceId}/video-demo/generate`, payload)
}

export function getResourceVideoDemoStatusApi(resourceId: string, jobId: string) {
  return apiGet<VideoDemoJob>(`/resources/${resourceId}/video-demo/status?jobId=${encodeURIComponent(jobId)}`)
}

export function retryResourceVideoDemoApi(resourceId: string, jobId: string) {
  return apiPost<VideoDemoJob>(`/resources/${resourceId}/video-demo/retry?jobId=${encodeURIComponent(jobId)}`, {})
}

export function completePathStageApi(stageId: string) {
  return apiPost<{ learningPath: LearningPath; nextTopic: NextLearningTopic }>(`/learning-paths/me/stages/${stageId}/complete`, {})
}

export function markLearningMasteryApi(payload: { knowledge_points?: string[]; chapter_ids?: string[]; resource_ids?: string[]; evidence?: string[] }) {
  return apiPost<{ progress: LearningProgress; learningPath: LearningPath; nextTopic?: NextLearningTopic }>('/learning-paths/me/mastery', {
    knowledge_points: payload.knowledge_points || [],
    chapter_ids: payload.chapter_ids || [],
    resource_ids: payload.resource_ids || [],
    evidence: payload.evidence || [],
  })
}

export function completeLearningResourceApi(resourceId: string) {
  return apiPost<{ progress: LearningProgress; nextTopic: NextLearningTopic }>(`/learning-paths/me/resources/${resourceId}/complete`, {})
}

export function viewLearningResourceApi(resourceId: string) {
  return apiPost<{ progress: LearningProgress; nextTopic: NextLearningTopic }>(`/learning-paths/me/resources/${resourceId}/view`, {})
}

export function updateLearningIntensityApi(intensity: LearningPath['intensity']) {
  return apiPost<LearningPath>('/learning-paths/me/intensity', { intensity })
}

export function attachResourcesToPathApi(payload: { resource_ids: string[]; task_id?: string }) {
  return apiPost<LearningPath>('/learning-paths/me/resources/attach', payload)
}
