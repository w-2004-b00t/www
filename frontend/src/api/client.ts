import type { ApiResponse } from '../types/common'
import { readText } from '../utils/storage'

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8001/api'
const REQUEST_TIMEOUT_MS = 30_000
const NETWORK_ERROR_MESSAGE = '无法连接后端，请确认 127.0.0.1:8001 已启动，并且后端 CORS 已允许当前前端端口。'

export async function apiGet<T>(path: string): Promise<T> {
  const response = await request(`${API_BASE_URL}${path}`, {
    headers: authHeaders(),
  })
  return parseResponse<T>(response)
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await request(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(body ?? {}),
  })
  return parseResponse<T>(response)
}

export function authHeaders(): HeadersInit {
  const token = readText('eduagent_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function request(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  const abortFromCaller = () => controller.abort()
  init?.signal?.addEventListener('abort', abortFromCaller, { once: true })
  try {
    return await fetch(input, { ...init, signal: controller.signal })
  } catch (error) {
    if (controller.signal.aborted && !init?.signal?.aborted) {
      throw new Error('请求超过 30 秒仍未完成，请稍后重试。你的作答内容已保留。')
    }
    if (error instanceof Error && error.name === 'AbortError') {
      throw new Error('请求已取消。')
    }
    throw new Error(NETWORK_ERROR_MESSAGE)
  } finally {
    window.clearTimeout(timeoutId)
    init?.signal?.removeEventListener('abort', abortFromCaller)
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  const payload = await readJsonPayload(response)
  if (!response.ok) {
    const message = readErrorMessage(payload)
    throw new Error(formatHttpError(response.status, response.statusText, message))
  }
  const apiPayload = payload as ApiResponse<T>
  if (apiPayload.code !== 0) {
    throw new Error(readErrorMessage(apiPayload) || apiPayload.message || '接口返回失败')
  }
  return apiPayload.data
}

async function readJsonPayload(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return {}
  }
}

function readErrorMessage(payload: unknown) {
  const data = payload as Partial<ApiResponse<unknown>> & { detail?: unknown; message?: unknown }
  const detail = data.detail ?? data.message
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    const item = detail as {
      code?: string
      message?: string
      detail?: string
      agentName?: string
      missingRequirements?: string[]
      missingDimensions?: string[]
      followupQuestions?: string[]
      reasonCode?: string
      rawFailure?: string
      suggestedActions?: string[]
    }
    const code = item.code ? `错误码：${item.code}` : ''
    const reason = item.reasonCode ? `原因：${item.reasonCode}` : ''
    const agent = item.agentName ? `Agent：${item.agentName}` : ''
    const rawFailure = item.rawFailure ? `技术细节：${item.rawFailure}` : ''
    const missingRequirements = item.missingRequirements?.length
      ? `缺少条件：${item.missingRequirements.join('、')}`
      : ''
    const missingDimensions = item.missingDimensions?.length
      ? `缺少画像维度：${item.missingDimensions.join('、')}`
      : ''
    const followups = item.followupQuestions?.length
      ? `建议补充：${item.followupQuestions.join('；')}`
      : ''
    const suggested = item.suggestedActions?.length
      ? `建议操作：${item.suggestedActions.join('；')}`
      : ''
    return [item.message, item.detail, agent, code, reason, rawFailure, missingRequirements, missingDimensions, followups, suggested]
      .filter(Boolean)
      .join(' ')
  }
  if (typeof data.message === 'string') return data.message
  return ''
}

function formatHttpError(status: number, statusText: string, message?: string) {
  const detail = message || `API ${status}: ${statusText}`
  if (status === 409) return `生成条件不足：${detail}`
  if (status === 503) return `后端暂时繁忙：${detail}`
  if (status >= 500) return `后端接口执行失败：${detail}`
  if (status === 401 || status === 403) return `登录状态或权限异常：${detail}`
  if (status === 404) return `请求的资源不存在：${detail}`
  return detail
}
