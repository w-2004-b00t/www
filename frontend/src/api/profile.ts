import { API_BASE_URL, apiGet, apiPost, authHeaders } from './client'
import type { ProfileUpdateDraft, StudentProfileItem } from '../types/common'

export interface ProfileExtractPayload {
  dimensions: StudentProfileItem[]
  need_confirm: boolean
  agentTrace?: string[]
  parsedFields?: Record<string, unknown>
  registrationProfileReused?: boolean
  updateDrafts?: ProfileUpdateDraft[]
  missingDimensions?: string[]
  followupQuestions?: string[]
  llmStatus?: {
    usedLLM: boolean
    model: string
    fallback: false
  }
}

export interface ProfileExtractConfirmPayload extends ProfileExtractPayload {
  profileItems: StudentProfileItem[]
  confirmedDimensions: StudentProfileItem[]
  saved: boolean
}

export interface ProfileDialogTurnPayload {
  assistantMessage: string
  coveredDimensions: string[]
  missingDimensions: string[]
  nextQuestionTitle: string
  canExtract: boolean
  agentTrace: string[]
  llmStatus: {
    usedLLM: boolean
    model: string
    fallback: false
  }
}

export interface ProfileDialogMessage {
  role: 'agent' | 'student'
  text: string
}

export interface ProfileDialogSession {
  messages: ProfileDialogMessage[]
  rawProfileInput: string
  coveredDimensions: string[]
  missingDimensions: string[]
  nextQuestionTitle: string
  canExtract: boolean
  draftItems: StudentProfileItem[]
  saveCompleted: boolean
  updatedAt?: string
}

export interface ProfileManualUpdateDraftItem {
  dimension: string
  value: string
  note?: string
}

interface ProfileStreamHandlers {
  onStatus?: (message: string) => void
  onToken?: (text: string) => void
  onError?: (message: string) => void
}

export function getProfileApi() {
  return apiGet<StudentProfileItem[]>('/profile/me')
}

export function getProfileContextApi() {
  return apiGet<Record<string, unknown>>('/profile/context')
}

export function listProfileUpdateDraftsApi() {
  return apiGet<ProfileUpdateDraft[]>('/profile/update-drafts')
}

export function confirmProfileUpdateDraftsApi(draft_ids?: string[]) {
  return apiPost<{
    applied: StudentProfileItem[]
    profileItems: StudentProfileItem[]
    drafts: ProfileUpdateDraft[]
  }>('/profile/update-drafts/confirm', { draft_ids })
}

export function rejectProfileUpdateDraftApi(draft_id: string) {
  return apiPost<{ drafts: ProfileUpdateDraft[] }>('/profile/update-drafts/reject', { draft_id })
}

export function createProfileUpdateDraftsApi(items: ProfileManualUpdateDraftItem[]) {
  return apiPost<{ drafts: ProfileUpdateDraft[] }>('/profile/update-drafts/manual', { items })
}

export function extractProfileApi(message: string) {
  return apiPost<ProfileExtractPayload>('/profile/extract', {
    course_id: 'course_data_structure',
    message,
  })
}

export function extractAndConfirmProfileApi(message: string) {
  return apiPost<ProfileExtractConfirmPayload>('/profile/extract-confirm', {
    course_id: 'course_data_structure',
    message,
  })
}

export function profileDialogTurnApi(
  conversation: ProfileDialogMessage[],
  answered_dimensions: string[],
  required_dimensions: string[],
  current_profile_context?: Record<string, unknown>,
) {
  return apiPost<ProfileDialogTurnPayload>('/profile/dialog/turn', {
    conversation,
    answered_dimensions,
    required_dimensions,
    current_profile_context,
  })
}

export function getProfileDialogSessionApi() {
  return apiGet<ProfileDialogSession | null>('/profile/dialog/session')
}

export function saveProfileDialogSessionApi(session: ProfileDialogSession) {
  return apiPost<ProfileDialogSession>('/profile/dialog/session', session)
}

export function resetProfileDialogSessionApi() {
  return apiPost<{ cleared: boolean }>('/profile/dialog/session/reset')
}

export async function extractProfileStreamApi(message: string, handlers: ProfileStreamHandlers = {}): Promise<ProfileExtractPayload> {
  const response = await fetch(`${API_BASE_URL}/profile/extract/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify({ course_id: 'course_data_structure', message }),
  })
  if (!response.ok || !response.body) {
    throw new Error(`SSE ${response.status}: ${response.statusText}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finalPayload: ProfileExtractPayload | null = null

  function handleBlock(block: string) {
    const event = block.split('\n').find((line) => line.startsWith('event:'))?.slice(6).trim() || 'message'
    const dataLine = block.split('\n').find((line) => line.startsWith('data:'))
    if (!dataLine) return
    const data = JSON.parse(dataLine.slice(5).trim())
    if (event === 'status') handlers.onStatus?.(data.message || '')
    if (event === 'token') handlers.onToken?.(data.text || '')
    if (event === 'error') {
      handlers.onError?.(data.message || '流式画像抽取失败')
      const missing = Array.isArray(data.missingDimensions) && data.missingDimensions.length
        ? `缺失：${data.missingDimensions.join('、')}`
        : ''
      throw new Error([data.message || '流式画像抽取失败', missing].filter(Boolean).join(' '))
    }
    if (event === 'done') finalPayload = data as ProfileExtractPayload
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
  if (!finalPayload) throw new Error('流式画像抽取未返回结果')
  return finalPayload
}

export function confirmProfileApi(dimensions: StudentProfileItem[]) {
  return apiPost<StudentProfileItem[]>('/profile/confirm', { dimensions })
}
