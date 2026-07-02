import { defineStore } from 'pinia'
import {
  confirmProfileApi,
  confirmProfileUpdateDraftsApi,
  createProfileUpdateDraftsApi,
  extractAndConfirmProfileApi,
  extractProfileStreamApi,
  getProfileApi,
  listProfileUpdateDraftsApi,
  profileDialogTurnApi,
  rejectProfileUpdateDraftApi,
} from '../api/profile'
import type { ProfileDialogMessage, ProfileDialogTurnPayload, ProfileManualUpdateDraftItem } from '../api/profile'
import type { ProfileUpdateDraft, StudentProfileItem } from '../types/common'
import { readUserJson, writeUserJson } from '../utils/storage'

interface ProfileState {
  profileItems: StudentProfileItem[]
  draftItems: StudentProfileItem[]
  isLoading: boolean
  isExtracting: boolean
  isSaving: boolean
  lastError: string
  usedFallback: boolean
  savedSummary: string
  streamStatus: string
  streamText: string
  updateDrafts: ProfileUpdateDraft[]
  isUpdatingDrafts: boolean
  isThinking: boolean
  dialogTurn: ProfileDialogTurnPayload | null
}

export const REQUIRED_PROFILE_DIMENSIONS = [
  '专业背景',
  '年级 / 学习阶段',
  '知识基础',
  '学习目标',
  '薄弱知识点',
  '认知风格',
  '资源偏好',
  '可用学习时间',
  '易错点',
  '实践能力水平',
]

export const MIN_PROFILE_DIMENSIONS_TO_EXTRACT = 5

const DIMENSION_ALIASES: Record<string, string> = {
  学习进度: '年级 / 学习阶段',
  可学习时间: '可用学习时间',
  易错知识点: '易错点',
  代码能力水平: '实践能力水平',
  测评表现: '易错点',
}

const IMPACT_BY_DIMENSION: Record<string, string> = {
  专业背景: '影响案例语境、术语解释深度和课程知识边界。',
  '年级 / 学习阶段': '影响学习路径起点、任务节奏和资源难度。',
  知识基础: '影响公式推导粒度、先修知识补充和练习难度。',
  学习目标: '影响学习路径阶段、今日任务和资源生成主题。',
  薄弱知识点: '影响补强任务、资源推荐排序和测评题目生成。',
  认知风格: '影响智能辅导回答结构和多模态资源优先级。',
  资源偏好: '影响讲解文档、导图、视频、练习和代码案例的推荐顺序。',
  可用学习时间: '影响今日任务长度、资源数量和路径强度。',
  易错点: '影响错题本标签、测评反馈和路径调整原因。',
  实践能力水平: '影响代码案例、实验步骤和代码类测评难度。',
}

const PLACEHOLDER_VALUE_PATTERNS = [
  '课程资料待上传',
  '待确认',
  '待补充',
  '未知',
  '默认值',
  '待学生确认',
  '待通过测评识别',
  '待通过练习题和阶段测评识别',
]

function normalizeDimension(dimension: string) {
  return DIMENSION_ALIASES[dimension] || dimension
}

export function getProfileImpact(dimension: string) {
  return IMPACT_BY_DIMENSION[normalizeDimension(dimension)] || '影响后续学习路径、资源推荐和智能辅导。'
}

function isPlaceholderProfileValue(value: unknown) {
  const text = String(value || '').replace(/\s+/g, '')
  if (!text) return true
  if (['45分钟', '45min', '45m'].includes(text)) return true
  return PLACEHOLDER_VALUE_PATTERNS.some((pattern) => text.includes(pattern))
}

export function normalizeProfileItem(item: StudentProfileItem): StudentProfileItem {
  const dimension = normalizeDimension(item.dimension)
  return {
    ...item,
    dimension,
    impact: item.impact || getProfileImpact(dimension),
    status: item.status || 'confirmed',
    source: item.source || 'dialog',
    version: item.version || 1,
  }
}

export function normalizeProfileItems(items: StudentProfileItem[]) {
  const byDimension = new Map<string, StudentProfileItem>()
  items
    .filter((item) => !isPlaceholderProfileValue(item.value))
    .map(normalizeProfileItem)
    .forEach((item) => {
    byDimension.set(item.dimension, item)
  })
  const ordered = REQUIRED_PROFILE_DIMENSIONS
    .map((dimension) => byDimension.get(dimension))
    .filter(Boolean) as StudentProfileItem[]
  const extras = Array.from(byDimension.values()).filter((item) => !REQUIRED_PROFILE_DIMENSIONS.includes(item.dimension))
  return [...ordered, ...extras]
}

export const useProfileStore = defineStore('profile', {
  state: (): ProfileState => ({
    profileItems: normalizeProfileItems(readUserJson<StudentProfileItem[]>('eduagent_profile_items', [])),
    draftItems: [],
    isLoading: false,
    isExtracting: false,
    isSaving: false,
    lastError: '',
    usedFallback: false,
    savedSummary: '',
    streamStatus: '',
    streamText: '',
    updateDrafts: [],
    isUpdatingDrafts: false,
    isThinking: false,
    dialogTurn: null,
  }),
  getters: {
    completeness: (state) => {
      const confirmed = new Set(
        normalizeProfileItems(state.profileItems)
          .filter((item) => item.status === 'confirmed')
          .map((item) => item.dimension),
      )
      const completeCount = REQUIRED_PROFILE_DIMENSIONS.filter((dimension) => confirmed.has(dimension)).length
      return Math.min(100, Math.round((completeCount / REQUIRED_PROFILE_DIMENSIONS.length) * 100))
    },
    lowConfidenceDrafts: (state) => state.draftItems.filter((item) => item.confidence < 0.85),
  },
  actions: {
    async loadProfile() {
      this.isLoading = true
      this.lastError = ''
      try {
        this.profileItems = normalizeProfileItems(await getProfileApi())
        writeUserJson('eduagent_profile_items', this.profileItems)
        this.loadUpdateDrafts().catch(() => {})
      } catch {
        this.lastError = '画像加载失败，未使用本地演示画像。请确认后端服务可用后重试。'
        this.usedFallback = false
        this.profileItems = []
      } finally {
        this.isLoading = false
      }
    },
    async extractFromMessage(message: string) {
      this.isExtracting = true
      this.lastError = ''
      this.usedFallback = false
      this.streamStatus = '正在建立 SSE 流式连接'
      this.streamText = ''
      try {
        const response = await extractProfileStreamApi(message, {
          onStatus: (status) => {
            this.streamStatus = status
          },
          onToken: (token) => {
            this.streamText += token
          },
          onError: (error) => {
            this.streamStatus = error
          },
        })
        this.draftItems = normalizeProfileItems(response.dimensions)
        if (response.updateDrafts?.length) this.updateDrafts = response.updateDrafts
        this.streamStatus = response.registrationProfileReused
          ? 'DeepSeek 已沿用注册专业和年级，等待你确认其余画像草稿'
          : 'DeepSeek 已完成流式画像抽取，等待你确认画像草稿'
        this.isExtracting = false
        return
      } catch (error) {
        this.draftItems = []
        this.lastError = error instanceof Error
          ? error.message
          : 'DeepSeek 画像抽取服务暂不可用，未生成画像草稿。'
        this.streamStatus = this.lastError
        this.usedFallback = false
        throw error
      } finally {
        this.isExtracting = false
      }
    },
    async extractAndConfirmFromMessage(message: string) {
      this.isExtracting = true
      this.isSaving = true
      this.lastError = ''
      this.usedFallback = false
      this.streamStatus = '正在调用 DeepSeek 生成并保存画像'
      this.streamText = ''
      try {
        const response = await extractAndConfirmProfileApi(message)
        this.draftItems = []
        this.profileItems = normalizeProfileItems(response.profileItems)
        if (response.updateDrafts?.length) this.updateDrafts = response.updateDrafts
        writeUserJson('eduagent_profile_items', this.profileItems)
        this.savedSummary = `已保存 ${response.confirmedDimensions?.length || response.dimensions.length} 个画像维度。`
        this.streamStatus = '画像已生成并保存'
        return response
      } catch (error) {
        this.draftItems = []
        this.lastError = error instanceof Error
          ? error.message
          : 'DeepSeek 画像生成或保存失败，未写入画像。'
        this.streamStatus = this.lastError
        this.usedFallback = false
        throw error
      } finally {
        this.isExtracting = false
        this.isSaving = false
      }
    },
    async runDialogTurn(
      conversation: ProfileDialogMessage[],
      answeredDimensions: string[],
      requiredDimensions: string[] = REQUIRED_PROFILE_DIMENSIONS,
    ) {
      this.isThinking = true
      this.lastError = ''
      this.usedFallback = false
      this.streamStatus = '画像 Agent 正在分析回答、核对缺失维度'
      try {
        const response = await profileDialogTurnApi(conversation, answeredDimensions, requiredDimensions)
        this.dialogTurn = response
        this.streamStatus = response.canExtract
          ? '画像 Agent 已确认核心信息已足够，可以生成草稿'
          : '画像 Agent 已生成下一句追问'
        return response
      } catch (error) {
        this.dialogTurn = null
        this.lastError = error instanceof Error
          ? error.message
          : 'DeepSeek 画像对话服务暂不可用，未推进对话。'
        this.usedFallback = false
        throw error
      } finally {
        this.isThinking = false
      }
    },
    async confirmDrafts() {
      this.isSaving = true
      this.lastError = ''
      this.usedFallback = false
      try {
        this.draftItems = normalizeProfileItems(this.draftItems)
        this.profileItems = normalizeProfileItems(await confirmProfileApi(this.draftItems))
        writeUserJson('eduagent_profile_items', this.profileItems)
        this.savedSummary = `已保存 ${this.draftItems.length} 个画像维度。`
        this.draftItems = []
        this.isSaving = false
        return
      } catch {
        this.lastError = '画像保存到服务端失败，画像草稿尚未写入。请重试。'
        this.usedFallback = false
        this.isSaving = false
        throw new Error(this.lastError)
      }
    },
    updateItem(item: StudentProfileItem) {
      const normalized = normalizeProfileItem(item)
      const index = this.profileItems.findIndex((profile) => profile.id === item.id || normalizeDimension(profile.dimension) === normalized.dimension)
      if (index >= 0) this.profileItems[index] = normalized
      this.profileItems = normalizeProfileItems(this.profileItems)
      writeUserJson('eduagent_profile_items', this.profileItems)
    },
    async loadUpdateDrafts() {
      this.isUpdatingDrafts = true
      try {
        this.updateDrafts = await listProfileUpdateDraftsApi()
      } finally {
        this.isUpdatingDrafts = false
      }
    },
    async createManualUpdateDrafts(items: ProfileManualUpdateDraftItem[]) {
      this.isUpdatingDrafts = true
      this.lastError = ''
      try {
        const result = await createProfileUpdateDraftsApi(items)
        this.updateDrafts = result.drafts
        return result
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '画像更新建议创建失败，请检查填写内容。'
        throw error
      } finally {
        this.isUpdatingDrafts = false
      }
    },
    async confirmUpdateDrafts(draftIds?: string[]) {
      this.isUpdatingDrafts = true
      try {
        const result = await confirmProfileUpdateDraftsApi(draftIds)
        this.profileItems = normalizeProfileItems(result.profileItems)
        this.updateDrafts = result.drafts
        writeUserJson('eduagent_profile_items', this.profileItems)
        return result
      } finally {
        this.isUpdatingDrafts = false
      }
    },
    async rejectUpdateDraft(draftId: string) {
      this.isUpdatingDrafts = true
      try {
        const result = await rejectProfileUpdateDraftApi(draftId)
        this.updateDrafts = result.drafts
        return result
      } finally {
        this.isUpdatingDrafts = false
      }
    },
  },
})
