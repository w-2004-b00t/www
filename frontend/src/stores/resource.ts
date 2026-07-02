import { defineStore } from 'pinia'
import {
  attachResourcesToPathApi,
  completeLearningResourceApi,
  completePathStageApi,
  generateLearningPathApi,
  getResourceApi,
  getLearningProgressApi,
  getNextLearningTopicApi,
  getLearningPathApi,
  listResourcesApi,
  markLearningMasteryApi,
  submitResourceFeedbackApi,
  updateLearningIntensityApi,
  updateResourceAuditApi,
  viewLearningResourceApi,
} from '../api/resource'
import type { LearningPath, LearningProgress, LearningResource, NextLearningTopic, ResourceFeedback } from '../types/common'
import { readUserJson, writeUserJson } from '../utils/storage'

interface ResourceState {
  resources: LearningResource[]
  allResources: LearningResource[]
  visibleResources: LearningResource[]
  resourcesLoaded: boolean
  learningPath: LearningPath
  learningProgress: LearningProgress
  nextTopic: NextLearningTopic
  lastError: string
}

const RESOURCE_KEY = 'eduagent_data_structure_resources'
const PATH_KEY = 'eduagent_data_structure_learning_path'
const PROGRESS_KEY = 'eduagent_data_structure_learning_progress'

const defaultLearningProgress = (): LearningProgress => ({
  viewedResourceIds: [],
  completedStageIds: [],
  completedResourceIds: [],
  masteredChapterIds: [],
  masteredKnowledgePoints: [],
  masteredResourceIds: [],
  records: [],
})

const defaultLearningPath = (): LearningPath => ({
  id: 'path_empty_real_data_required',
  title: '暂无法生成正式学习路径',
  summary: '系统未获得足够真实课程依据，因此不会使用静态样例或假数据生成路径。',
  status: 'blocked',
  generationMode: 'strict_real_data',
  llmStatus: 'skipped',
  sourceCitations: [],
  generatedAt: null,
  blockingReason: '请先上传真实课程资料或生成带引用的学习资源。',
  stages: [],
  profileBasis: [],
  adjustmentHistory: [],
  intensity: '60min',
  initialReason: '系统不会基于演示内容生成正式学习路径。',
  resourceCoverage: {
    approvedTotal: 0,
    linkedTotal: 0,
    pendingTotal: 0,
    unlinkedResourceIds: [],
  },
})

const defaultNextTopic = (): NextLearningTopic => ({
  chapterId: '',
  chapterName: '',
  topic: '',
  knowledgePoints: [],
  reason: '暂无可推荐的正式下一步。',
  status: 'blocked',
  blocked: true,
  blockingReason: '暂无可推荐的正式下一步。请先生成带引用的学习资料或正式学习路径。',
  source: 'empty_state',
  evidence: [],
})

function stripResourceLearningStatus(resource: LearningResource): LearningResource {
  const { isViewed, isCompleted, isMastered, masteryEvidence, ...rest } = resource
  void isViewed
  void isCompleted
  void isMastered
  void masteryEvidence
  return rest
}

function stripLearningPathStatus(path: LearningPath): LearningPath {
  return {
    ...path,
    stages: (path.stages || []).map((stage) => {
      const { isCompleted, isMastered, masteryEvidence, ...rest } = stage
      void isCompleted
      void isMastered
      void masteryEvidence
      return rest
    }),
  }
}

const loadLocalProgress = () => readUserJson<LearningProgress | null>(PROGRESS_KEY, null)

function normalizeLearningPath(path: LearningPath): LearningPath {
  const fallback = defaultLearningPath()
  return {
    ...fallback,
    ...path,
    profileBasis: path.profileBasis || [],
    initialReason: path.initialReason || fallback.initialReason,
    status: path.status || fallback.status,
    generationMode: path.generationMode || fallback.generationMode,
    llmStatus: path.llmStatus || fallback.llmStatus,
    sourceCitations: path.sourceCitations || [],
    generatedAt: path.generatedAt ?? fallback.generatedAt,
    blockingReason: path.blockingReason || fallback.blockingReason,
    intensity: path.intensity || fallback.intensity || '60min',
    adjustmentHistory: path.adjustmentHistory || [],
    resourceCoverage: {
      approvedTotal: path.resourceCoverage?.approvedTotal ?? fallback.resourceCoverage?.approvedTotal ?? 0,
      linkedTotal: path.resourceCoverage?.linkedTotal ?? fallback.resourceCoverage?.linkedTotal ?? 0,
      pendingTotal: path.resourceCoverage?.pendingTotal ?? fallback.resourceCoverage?.pendingTotal ?? 0,
      unlinkedResourceIds: path.resourceCoverage?.unlinkedResourceIds || [],
    },
    stages: (path.stages || []).map((stage) => {
      return {
        ...stage,
        resources: stage.resources || [],
        tasks: stage.tasks || [],
      }
    }),
  }
}

function normalizeLearningProgress(progress?: LearningProgress | null): LearningProgress {
  const fallback = defaultLearningProgress()
  return {
    ...fallback,
    ...(progress || {}),
    viewedResourceIds: progress?.viewedResourceIds || [],
    completedStageIds: progress?.completedStageIds || [],
    completedResourceIds: progress?.completedResourceIds || [],
    masteredChapterIds: progress?.masteredChapterIds || [],
    masteredKnowledgePoints: progress?.masteredKnowledgePoints || [],
    masteredResourceIds: progress?.masteredResourceIds || [],
    records: progress?.records || [],
  }
}

function normalizeNextTopic(topic?: NextLearningTopic | null): NextLearningTopic {
  const fallback = defaultNextTopic()
  return {
    ...fallback,
    ...(topic || {}),
    knowledgePoints: topic?.knowledgePoints || [],
    evidence: topic?.evidence || [],
    blocked: Boolean(topic?.blocked || topic?.status === 'blocked'),
    blockingReason: topic?.blockingReason || (topic?.blocked ? topic.reason : fallback.blockingReason),
  }
}

function messageFromError(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

function sameResourceId(left: LearningResource, right: LearningResource) {
  return left.id === right.id
}

function upsertResourceList(items: LearningResource[], updated: LearningResource) {
  const exists = items.some((item) => sameResourceId(item, updated))
  return exists
    ? items.map((item) => (sameResourceId(item, updated) ? updated : item))
    : [updated, ...items]
}

export const useResourceStore = defineStore('resource', {
  state: (): ResourceState => ({
    resources: [],
    allResources: [],
    visibleResources: [],
    resourcesLoaded: false,
    learningPath: normalizeLearningPath(defaultLearningPath()),
    learningProgress: normalizeLearningProgress(loadLocalProgress()),
    nextTopic: defaultNextTopic(),
    lastError: '',
  }),
  getters: {
    getResourceById: (state) => (id: string) => (
      state.allResources.find((resource) => resource.id === id)
      || state.visibleResources.find((resource) => resource.id === id)
      || state.resources.find((resource) => resource.id === id)
    ),
    auditHistory: (state) => state.allResources.flatMap((item) => item.auditHistory || []),
    feedbackRecords: (state) => state.allResources.flatMap((item) => item.feedback || []),
  },
  actions: {
    persist() {
      writeUserJson(RESOURCE_KEY, this.allResources.map(stripResourceLearningStatus))
      writeUserJson(PATH_KEY, stripLearningPathStatus(this.learningPath))
      writeUserJson(PROGRESS_KEY, this.learningProgress)
    },
    async loadAll(includeCompleted = false) {
      this.lastError = ''
      const [resourcesResult, learningPathResult, learningProgressResult, nextTopicResult] = await Promise.allSettled([
        listResourcesApi(includeCompleted),
        getLearningPathApi(),
        getLearningProgressApi(),
        getNextLearningTopicApi(),
      ])

      if (resourcesResult.status === 'fulfilled') {
        this.resourcesLoaded = true
        if (includeCompleted) {
          this.allResources = resourcesResult.value
          this.visibleResources = resourcesResult.value
          this.resources = resourcesResult.value
        } else {
          this.visibleResources = resourcesResult.value
          this.resources = resourcesResult.value
          if (resourcesResult.value.length) {
            this.allResources = resourcesResult.value
          }
        }
      } else {
        this.allResources = []
        this.visibleResources = []
        this.resources = []
        this.resourcesLoaded = false
        this.lastError = messageFromError(resourcesResult.reason, '资源列表同步失败，未使用本地缓存或静态兜底。')
      }

      if (learningPathResult.status === 'fulfilled') {
        const learningPath = learningPathResult.value
        this.learningPath = normalizeLearningPath({
          ...learningPath,
          intensity: learningPath.intensity || this.learningPath.intensity || '60min',
          adjustmentHistory: learningPath.adjustmentHistory || this.learningPath.adjustmentHistory || [],
        })
      } else {
        this.learningPath = normalizeLearningPath(defaultLearningPath())
        this.lastError = this.lastError || messageFromError(learningPathResult.reason, '学习路径同步失败，未使用本地缓存或静态路径兜底。')
      }

      if (learningProgressResult.status === 'fulfilled') {
        this.learningProgress = normalizeLearningProgress(learningProgressResult.value)
      } else {
        this.learningProgress = defaultLearningProgress()
        this.lastError = this.lastError || messageFromError(learningProgressResult.reason, '学习进度同步失败，请重新加载。')
      }

      if (nextTopicResult.status === 'fulfilled') {
        this.nextTopic = normalizeNextTopic(nextTopicResult.value)
      } else {
        this.nextTopic = defaultNextTopic()
        this.lastError = this.lastError || messageFromError(nextTopicResult.reason, '下一步推荐同步失败，请重新加载。')
      }

      this.persist()
    },
    async loadNextTopic() {
      try {
        this.nextTopic = normalizeNextTopic(await getNextLearningTopicApi())
        return this.nextTopic
      } catch (error) {
        this.nextTopic = defaultNextTopic()
        this.lastError = error instanceof Error ? error.message : '下一步推荐同步失败。'
        throw new Error(this.lastError)
      }
    },
    replaceResource(updated: LearningResource) {
      this.allResources = this.allResources.map((item) => (item.id === updated.id ? updated : item))
      this.visibleResources = this.visibleResources.map((item) => (item.id === updated.id ? updated : item))
      this.resources = this.resources.map((item) => (item.id === updated.id ? updated : item))
      this.persist()
    },
    upsertResource(updated: LearningResource) {
      this.allResources = upsertResourceList(this.allResources, updated)
      this.visibleResources = upsertResourceList(this.visibleResources, updated)
      this.resources = upsertResourceList(this.resources, updated)
      this.persist()
    },
    setLearningPath(path: LearningPath) {
      this.learningPath = normalizeLearningPath(path)
      this.persist()
    },
    async generateLearningPath() {
      try {
        this.learningPath = normalizeLearningPath(await generateLearningPathApi())
        await this.loadNextTopic().catch(() => undefined)
        this.persist()
        return this.learningPath
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '正式学习路径生成失败，请确认后端服务和课程资料状态。'
        throw new Error(this.lastError)
      }
    },
    async updateAudit(
      resourceId: string,
      status: LearningResource['auditStatus'],
      reason: string,
      scope: 'student' | 'class' = 'student',
    ) {
      try {
        const result = await updateResourceAuditApi(resourceId, { status, reason, scope })
        this.replaceResource(result.resource)
        return result.resource
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '教师审核保存失败，学生端资源状态没有同步。'
        throw new Error(this.lastError)
      }
    },
    async submitFeedback(resourceId: string, type: ResourceFeedback['type'], note?: string) {
      try {
        const result = await submitResourceFeedbackApi(resourceId, { type, note })
        this.replaceResource(result.resource)
        return result.feedback
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '资源反馈保存失败，学习报告不会记录这次反馈。'
        throw new Error(this.lastError)
      }
    },
    async completeStage(stageId: string) {
      try {
        const result = await completePathStageApi(stageId)
        this.learningPath = normalizeLearningPath(result.learningPath)
        this.nextTopic = normalizeNextTopic(result.nextTopic)
        this.learningProgress = normalizeLearningProgress(await getLearningProgressApi())
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '阶段完成状态保存失败，学习路径没有更新。'
        throw new Error(this.lastError)
      }
      this.persist()
    },
    async markStageMastered(stageId: string) {
      const stage = this.learningPath.stages.find((item) => item.id === stageId)
      if (!stage) return
      try {
        const chapterId = String((stage as LearningPath['stages'][number] & { chapterId?: string }).chapterId || '')
        const result = await markLearningMasteryApi({
          knowledge_points: stage.knowledgePoints,
          chapter_ids: chapterId ? [chapterId] : [],
          resource_ids: stage.resources,
          evidence: [`学生手动标记阶段「${stage.name}」已掌握`],
        })
        this.learningProgress = normalizeLearningProgress(result.progress)
        this.learningPath = normalizeLearningPath(result.learningPath)
        this.nextTopic = normalizeNextTopic(result.nextTopic || await getNextLearningTopicApi())
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '掌握状态保存失败，学习路径没有更新。'
        throw new Error(this.lastError)
      }
      this.persist()
    },
    async completeResource(resourceId: string) {
      try {
        const result = await completeLearningResourceApi(resourceId)
        this.learningProgress = normalizeLearningProgress(result.progress)
        this.nextTopic = normalizeNextTopic(result.nextTopic)
        const latest = await getResourceApi(resourceId)
        this.upsertResource(latest)
        await this.loadAll(true)
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '资源完成状态保存失败。'
        throw new Error(this.lastError)
      }
      this.persist()
    },
    async viewResource(resourceId: string) {
      try {
        const result = await viewLearningResourceApi(resourceId)
        this.learningProgress = normalizeLearningProgress(result.progress)
        this.nextTopic = normalizeNextTopic(result.nextTopic)
        const latest = await getResourceApi(resourceId)
        this.upsertResource(latest)
        return latest
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '资源浏览状态保存失败。'
        throw new Error(this.lastError)
      } finally {
        this.persist()
      }
    },
    async markResourceMastered(resourceId: string) {
      const resource = this.getResourceById(resourceId)
      try {
        const result = await markLearningMasteryApi({
          resource_ids: [resourceId],
          evidence: [`学生手动标记资源「${resource?.title || resourceId}」已掌握`],
        })
        this.learningProgress = normalizeLearningProgress(result.progress)
        this.learningPath = normalizeLearningPath(result.learningPath)
        this.nextTopic = normalizeNextTopic(result.nextTopic || await getNextLearningTopicApi())
        const latest = await getResourceApi(resourceId)
        this.upsertResource(latest)
        await this.loadAll(true)
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '资源掌握状态保存失败。'
        throw new Error(this.lastError)
      }
      this.persist()
    },
    async updateIntensity(intensity: LearningPath['intensity']) {
      try {
        this.learningPath = normalizeLearningPath(await updateLearningIntensityApi(intensity))
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '学习强度保存失败，路径调整没有写入后端。'
        throw new Error(this.lastError)
      }
      this.persist()
    },
    async attachResourcesToPath(resourceIds: string[], taskId?: string) {
      try {
        this.learningPath = normalizeLearningPath(await attachResourcesToPathApi({
          resource_ids: resourceIds,
          task_id: taskId,
        }))
        this.persist()
        return this.learningPath
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '资料加入学习路径失败，请稍后重试。'
        throw new Error(this.lastError)
      }
    },
  },
})
