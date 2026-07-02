import { apiGet, apiPost } from './client'
import type { AssessmentPaper, LearningPath, ProfileUpdateDraft, StudentProfileItem } from '../types/common'

export function generateAssessmentApi() {
  return apiPost<AssessmentPaper>('/assessments/generate', {})
}

export function submitAssessmentApi(assessmentId: string, answers: Record<string, unknown>) {
  return apiPost<{
    assessmentId?: string
    score: number
    weakness: string[]
    suggestion: string
    adjustedPath?: LearningPath
    mistakes_added?: number
    path_adjustment?: {
      before?: string
      after?: string
      reason?: string
      beforePath?: string[]
      afterPath?: string[]
    }
    error_reasons?: string[]
    profile_updates?: StudentProfileItem[]
    profile_update_drafts?: ProfileUpdateDraft[]
    profileUpdateDrafts?: ProfileUpdateDraft[]
    profile_update_draft?: Partial<StudentProfileItem> & {
      status?: 'draft' | 'confirmed' | 'rejected'
      source?: 'dialog' | 'behavior' | 'assessment' | 'manual'
    }
    rubric_version?: string
    question_details?: {
      question_id: string
      knowledge_point: string
      score: number
      correct: boolean
      rubric: string
      error_reason?: string
      hit_keywords?: string[]
      missing_keywords?: string[]
    }[]
  }>('/assessments/submit', { assessmentId, answers })
}

export interface AssessmentReportData {
  mastery: number
  summary: string
  next_actions: string[]
  dataSources: {
    assessmentResults: number
    resourceFeedback: number
    resourcePracticeRecords: number
    pathAdjustments: number
    profileDimensions: number
    mistakeRecords: number
  }
  assessmentSummary: {
    latest?: {
      id: string
      score: number
      weakness: string[]
      errorReasons: string[]
      createdAt: string
    } | null
    averageScore: number
    weakPoints: string[]
    history: unknown[]
  }
  resourceEffect: {
    feedback: unknown[]
    practice: unknown[]
  }
  pathAdjustments: unknown[]
  profileChanges: unknown[]
  mistakeSummary: unknown[]
  mistakeAnalytics: {
    total: number
    pendingCorrection: number
    pendingVerification: number
    mastered: number
    masteryRate: number
    averageCorrectionAttempts: number
    latestCorrectionAverageScore: number
    verificationPassRate: number
    knowledgeBreakdown: {
      knowledge: string
      total: number
      pendingCorrection: number
      pendingVerification: number
      mastered: number
    }[]
  }
}

export function getAssessmentReportApi() {
  return apiGet<AssessmentReportData>('/assessments/report')
}
