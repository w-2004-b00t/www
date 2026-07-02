import { API_BASE_URL, apiGet, apiPost, authHeaders } from './client'
import type { ProfileUpdateDraft, SourceCitation, TutorNote, VideoDemoScene } from '../types/common'

export type TutorGenerationMode = 'rag_llm' | 'insufficient_evidence' | 'llm_unavailable' | string

export interface TutorSuggestedAction {
  type: string
  title: string
  reason: string
}

export interface TutorDiagram {
  title?: string
  markdown?: string
  mermaid?: string
}

export interface TutorVideoScript {
  title?: string
  script?: string
  scenes?: VideoDemoScene[]
}

export interface TutorChatResponse {
  answer: string
  diagram?: TutorDiagram
  videoScript?: TutorVideoScript
  suggestedActions?: TutorSuggestedAction[]
  citations: SourceCitation[]
  allCitations?: SourceCitation[]
  inferred: boolean
  inferredSections?: string[]
  confidence: number
  confidenceReason?: string
  coverage?: string
  generationMode?: TutorGenerationMode
  profileUpdateDraft?: ProfileUpdateDraft | null
  llm?: {
    enabled: boolean
    model: string
    used: boolean
    error?: string
  }
  createdAt?: string
  requestId?: string
}

export interface TutorStreamStatus {
  stage: 'retrieval' | 'connecting' | 'generating' | string
  message: string
  requestId?: string
}

export interface TutorStreamErrorPayload {
  code: string
  message: string
  retryable: boolean
  requestId?: string
}

export class TutorStreamError extends Error {
  code: string
  retryable: boolean
  requestId?: string

  constructor(payload: TutorStreamErrorPayload) {
    super(payload.message)
    this.name = 'TutorStreamError'
    this.code = payload.code
    this.retryable = payload.retryable
    this.requestId = payload.requestId
  }
}

interface TutorStreamHandlers {
  onStatus?: (status: TutorStreamStatus) => void
  onDelta?: (text: string) => void
}

export function tutorChatApi(message: string) {
  return apiPost<TutorChatResponse>('/tutor/chat', {
    course_id: 'course_data_structure',
    message,
  })
}

export async function tutorChatStreamApi(
  message: string,
  handlers: TutorStreamHandlers = {},
  signal?: AbortSignal,
): Promise<TutorChatResponse> {
  const response = await fetch(`${API_BASE_URL}/tutor/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify({ course_id: 'course_data_structure', message }),
    signal,
  })
  if (!response.ok || !response.body) {
    throw new Error(`智能辅导流式接口请求失败（${response.status}）`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finalPayload: TutorChatResponse | null = null

  function handleBlock(block: string) {
    const lines = block.split('\n')
    const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() || 'message'
    const dataText = lines
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trim())
      .join('\n')
    if (!dataText) return
    const data = JSON.parse(dataText) as Record<string, unknown>
    if (event === 'status') handlers.onStatus?.(data as unknown as TutorStreamStatus)
    if (event === 'delta') handlers.onDelta?.(String(data.text || ''))
    if (event === 'error') throw new TutorStreamError(data as unknown as TutorStreamErrorPayload)
    if (event === 'done') finalPayload = data as unknown as TutorChatResponse
  }

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() || ''
    for (const block of blocks) {
      if (block.trim()) handleBlock(block.trim())
    }
    if (done) break
  }
  if (buffer.trim()) handleBlock(buffer.trim())
  if (!finalPayload) throw new Error('智能辅导流式响应未返回完成事件')
  return finalPayload
}

export interface TutorExtraResponse {
  type: 'diagram' | 'video'
  diagram?: TutorDiagram
  videoScript?: TutorVideoScript
  citations?: SourceCitation[]
  generationMode: TutorGenerationMode
  requestId?: string
  createdAt?: string
}

export function generateTutorExtraApi(payload: {
  message: string
  answer: string
  type: 'diagram' | 'video'
  course_id?: string
}) {
  return apiPost<TutorExtraResponse>('/tutor/extras', payload)
}

export function saveTutorNoteApi(payload: { title: string; content: string }) {
  return apiPost<TutorNote>('/tutor/notes', payload)
}

export function saveTutorMistakeApi(payload: { knowledge: string; stem: string; wrongReason: string; fixTask: string }) {
  return apiPost<{ id: string; knowledge: string; stem: string; wrongReason: string; fixTask: string; status: string }>('/tutor/mistakes', payload)
}

export interface TutorExerciseItem {
  type: string
  stem: string
  options: string[]
  answer: string
  analysis: string
  citationChunkIds: string[]
}

export function generateTutorExerciseApi(payload: { message: string; mode?: string; answer?: string; course_id?: string }) {
  return apiPost<{ id: string; title: string; items: TutorExerciseItem[]; createdAt: string }>('/tutor/exercises', payload)
}

export function generateTutorDocumentApi(payload: { message: string; mode?: string; answer?: string; course_id?: string }) {
  return apiPost<{ id: string; title: string; content: string; createdAt: string }>('/tutor/documents', payload)
}

export function createTutorRemedialTaskApi(payload: { message: string; mode?: string; answer?: string }) {
  return apiPost<{ stage: { id: string; name: string }; learningPath: import('../types/common').LearningPath }>('/tutor/remedial-task', payload)
}

export function submitTutorFeedbackApi(payload: { type: string; message?: string; answer?: string }) {
  return apiPost<{ id: string; type: string; createdAt: string }>('/tutor/feedback', payload)
}

export interface TutorMistakeRecord {
  id: string
  knowledge: string
  stem: string
  wrongReason: string
  fixTask: string
  status: '待订正' | '订正中' | '待验证' | '已掌握'
  type: string
  options: string[]
  userAnswer: string
  answer: string
  analysis: string
  rubric: string[]
  citations: SourceCitation[]
  correctionAttempts: MistakeCorrectionAttempt[]
  verificationQuestions: MistakeVerificationQuestion[]
  verificationAttempts: MistakeVerificationAttempt[]
  masteryEvidence: string[]
  version: number
  updatedAt: string
  generationMode?: 'rag_llm' | 'rule_fallback'
  generationReason?: string
  latestCorrection?: MistakeCorrectionAttempt
  latestVerification?: MistakeVerificationAttempt
  createdAt?: string
}

export interface MistakeCorrectionAttempt {
  answer: string
  score: number
  correct: boolean
  hitKeywords: string[]
  missingKeywords: string[]
  errorReason: string
  createdAt: string
}

export interface MistakeVerificationQuestion {
  id: string
  type: string
  knowledgePoint: string
  stem: string
  options?: string[]
  answer: string
  analysis: string
  rubric: string[]
  citations?: SourceCitation[]
}

export interface MistakeVerificationResult {
  questionId: string
  answer: string
  score: number
  correct: boolean
  hitKeywords: string[]
  missingKeywords: string[]
  errorReason: string
}

export interface MistakeVerificationAttempt {
  results: MistakeVerificationResult[]
  passed: boolean
  createdAt: string
}

export function listTutorMistakesApi() {
  return apiGet<TutorMistakeRecord[]>('/tutor/mistakes')
}

export function submitMistakeCorrectionApi(mistakeId: string, answer: string, expectedVersion?: number) {
  return apiPost<{ mistake: TutorMistakeRecord; result: MistakeCorrectionAttempt }>(
    `/tutor/mistakes/${mistakeId}/correction`,
    { answer, expectedVersion },
  )
}

export function generateSimilarMistakeApi(mistakeId: string, expectedVersion?: number) {
  return apiPost<{
    mistake: TutorMistakeRecord
    questions: MistakeVerificationQuestion[]
    generationMode: 'rag_llm' | 'rule_fallback'
    generationReason: string
  }>(
    `/tutor/mistakes/${mistakeId}/similar`,
    { expectedVersion },
  )
}

export function submitMistakeVerificationApi(
  mistakeId: string,
  answers: Record<string, string>,
  expectedVersion?: number,
) {
  return apiPost<{
    mistake: TutorMistakeRecord
    passed: boolean
    results: MistakeVerificationResult[]
    suggestion: string
  }>(`/tutor/mistakes/${mistakeId}/verification`, { answers, expectedVersion })
}
