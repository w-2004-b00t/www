import { apiGet, apiPost } from './client'
import type { GenerationTask } from '../types/common'

export interface AdminDocument {
  id: string
  name: string
  fileType?: string
  status: string
  chunks: number
  coverage: number
  issue: string
  updatedAt?: string
}

export interface AdminKnowledgeChunk {
  id: string
  chunkId: string
  documentId: string
  documentName?: string
  courseId: string
  title: string
  section: string
  page: number
  content: string
  keywords: string[]
  sourceType: string
  embeddingStatus: string
}

export interface AdminAnalytics {
  metrics: { students: number; active: number; averageMastery: number; intervention: number }
  weakPoints: { name: string; value: number; action: string }[]
  students: { name: string; progress: string; mastery: number; risk: string }[]
  suggestion: string
}

export interface ModelConfig {
  version: string
  activePrompt: 'audit' | 'resource' | 'tutor'
  prompts: Record<string, string>
  thresholds: { citationCoverage: number; lowConfidence: number; autoPassScore: number }
  agents: { name: string; model: string; temp: number; status: string; guard: string }[]
  updatedAt?: string
}

export function getAdminDashboardApi() {
  return apiGet<{
    teacherMetrics: { label: string; value: string; trend: string }[]
    adminMetrics: { label: string; value: string; trend: string }[]
    status: { knowledgeCoverage: number; auditPassRate: number; agentSuccessRate: number; pathAdjustments: number }
  }>('/admin/dashboard')
}

export function listAdminDocumentsApi() {
  return apiGet<AdminDocument[]>('/admin/documents')
}

export function parseAdminDocumentApi(name?: string, content?: string) {
  return apiPost<{ document: AdminDocument; documents: AdminDocument[]; chunks: AdminKnowledgeChunk[] }>('/admin/documents/parse', { name, content })
}

export function confirmAdminDocumentApi(docId: string) {
  return apiPost<{ document: AdminDocument; documents: AdminDocument[] }>(`/admin/documents/${docId}/confirm`, {})
}

export function listAdminDocumentChunksApi(docId: string) {
  return apiGet<AdminKnowledgeChunk[]>(`/admin/documents/${docId}/chunks`)
}

export function getModelConfigApi() {
  return apiGet<ModelConfig>('/admin/model-config')
}

export function saveModelConfigApi(payload: Partial<ModelConfig>) {
  return apiPost<ModelConfig>('/admin/model-config', payload)
}

export function rollbackModelConfigApi() {
  return apiPost<ModelConfig>('/admin/model-config/rollback', {})
}

export function getAdminAnalyticsApi() {
  return apiGet<AdminAnalytics>('/admin/analytics')
}

export function createClassRemedialTaskApi() {
  return apiPost<{ id: string; title: string; status: string; createdAt: string; studentCount: number }>('/admin/analytics/remedial-task', {})
}

export function listTasksApi() {
  return apiGet<GenerationTask[]>('/tasks')
}
