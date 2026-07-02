import { defineStore } from 'pinia'
import { generateResourcesApi, getTaskApi, retryAgentStepApi } from '../api/resource'
import { DEFAULT_RESOURCE_TYPES } from '../constants/resourceMeta'
import { useResourceStore } from './resource'
import type { GenerationTask, LearningPath, ResourceType } from '../types/common'
import { cleanGenerationTarget, cleanGenerationTopic } from '../utils/resourceTopic'
import { readUserText, removeKeys, removeUserKeys, writeUserText } from '../utils/storage'

interface TaskState {
  activeTask: GenerationTask | null
  timer: number | null
  lastError: string
  creating: boolean
  recovered: boolean
}

const LEGACY_ACTIVE_TASK_KEYS = ['eduagent_data_structure_active_task_id']
const ACTIVE_TASK_KEY = 'eduagent_data_structure_active_task_id_v2'

function taskTopic(task: GenerationTask) {
  return String(task.inputPayload?.topic || task.topic || '')
}

function hasPollutedTopic(task: GenerationTask) {
  const raw = taskTopic(task)
  return Boolean(raw.trim()) && cleanGenerationTopic(raw, '') !== raw.trim()
}

function syncGeneratedResourcesFromTask(task?: GenerationTask | null) {
  const resources = task?.outputPayload?.resources
  if (task?.status !== 'success') return
  const resourceStore = useResourceStore()
  const learningPath = task.outputPayload?.learningPath
  if (learningPath && typeof learningPath === 'object') {
    resourceStore.setLearningPath(learningPath as LearningPath)
  }
  if (!Array.isArray(resources) || !resources.length) return
  void resourceStore.loadAll(true).then(() => {
    const resourceIds = new Set(resourceStore.allResources.map((item) => item.id))
    const outputIds = resources.map((item) => String(item.id)).filter(Boolean)
    const synced = outputIds.length && outputIds.every((id) => resourceIds.has(id))
    if (!synced) {
      resourceStore.lastError = '生成任务已返回资料，但后端资源中心尚未保存成功，请重新同步资源。'
    }
  })
}

function clearStoredActiveTaskId() {
  removeUserKeys([ACTIVE_TASK_KEY])
  removeKeys([ACTIVE_TASK_KEY, ...LEGACY_ACTIVE_TASK_KEYS])
}

export const useTaskStore = defineStore('task', {
  state: (): TaskState => ({
    activeTask: null,
    timer: null,
    lastError: '',
    creating: false,
    recovered: false,
  }),
  actions: {
    stopPolling() {
      if (this.timer) window.clearInterval(this.timer)
      this.timer = null
    },
    clearActiveTask() {
      this.stopPolling()
      this.activeTask = null
      this.lastError = ''
      this.creating = false
      this.recovered = false
      clearStoredActiveTaskId()
    },
    async resumeActiveTask() {
      removeKeys(LEGACY_ACTIVE_TASK_KEYS)
      removeKeys([ACTIVE_TASK_KEY])
      const taskId = readUserText(ACTIVE_TASK_KEY)
      if (!taskId || this.activeTask) return
      try {
        const recoveredTask = await getTaskApi(taskId)
        const failureReason = String(recoveredTask.outputPayload?.failure_reason || '')
        const staleKnowledgeFailure = (
          recoveredTask.status === 'failed'
          && (
            failureReason === 'no_real_course_materials'
            || String(recoveredTask.message || '').includes('暂无真实数据结构课程资料')
          )
        )
        if (staleKnowledgeFailure) {
          clearStoredActiveTaskId()
          this.activeTask = null
          this.lastError = ''
          this.recovered = false
          return
        }
        if (hasPollutedTopic(recoveredTask)) {
          clearStoredActiveTaskId()
          this.activeTask = null
          this.lastError = ''
          this.recovered = false
          return
        }
        if (recoveredTask.status === 'success') {
          this.activeTask = recoveredTask
          syncGeneratedResourcesFromTask(recoveredTask)
          this.lastError = ''
          this.recovered = true
          return
        }
        this.activeTask = recoveredTask
        syncGeneratedResourcesFromTask(this.activeTask)
        this.recovered = true
        if (this.activeTask.status === 'running') this.pollTask(taskId)
      } catch {
        removeUserKeys([ACTIVE_TASK_KEY])
      }
    },
    pollTask(taskId: string) {
      this.stopPolling()
      this.timer = window.setInterval(async () => {
        try {
          this.activeTask = await getTaskApi(taskId)
          if (this.activeTask.status === 'success' || this.activeTask.status === 'failed') {
            this.stopPolling()
            syncGeneratedResourcesFromTask(this.activeTask)
            if (this.activeTask.status === 'failed') {
              this.lastError = this.activeTask.message || '知识库未命中足够课程资料，已停止高可信资源生成。'
            }
          }
        } catch (error) {
          this.lastError = error instanceof Error ? error.message : '任务状态同步失败，请检查后端服务。'
          this.stopPolling()
        }
      }, 1500)
    },
    async retryActiveTask() {
      const input = this.activeTask?.inputPayload
      const topic = cleanGenerationTopic(input?.topic || '线性表')
      const target = cleanGenerationTarget(input?.target || '掌握线性表的存储结构、基本操作和代码实践', topic)
      const resourceTypes = Array.isArray(input?.resource_types)
        ? input.resource_types.map(String)
        : [...DEFAULT_RESOURCE_TYPES]
      await this.startResourceTask(topic, target, resourceTypes, {
        chapterId: typeof input?.chapter_id === 'string' ? input.chapter_id : undefined,
        chapterName: typeof input?.chapter_name === 'string' ? input.chapter_name : undefined,
      })
    },
    async retryAgentStep(agentName: string) {
      if (!this.activeTask) return
      this.lastError = ''
      try {
        this.activeTask = await retryAgentStepApi(this.activeTask.id, agentName)
        syncGeneratedResourcesFromTask(this.activeTask)
        if (this.activeTask.status === 'running') this.pollTask(this.activeTask.id)
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '智能体单步重试失败。'
      }
    },
    async startResourceTask(
      topic = '线性表',
      target = '掌握线性表的存储结构、基本操作和代码实践',
      resourceTypes: string[] = [...DEFAULT_RESOURCE_TYPES],
      chapter?: { chapterId?: string; chapterName?: string },
    ) {
      if (this.creating || this.activeTask?.status === 'running') return
      this.stopPolling()
      this.activeTask = null
      this.lastError = ''
      this.creating = true
      this.recovered = false
      removeUserKeys([ACTIVE_TASK_KEY])
      removeKeys([ACTIVE_TASK_KEY, ...LEGACY_ACTIVE_TASK_KEYS])
      const safeTopic = cleanGenerationTopic(topic)
      const safeTarget = cleanGenerationTarget(target, safeTopic)
      try {
        const created = await generateResourcesApi({
          course_id: 'course_data_structure',
          topic: safeTopic,
          target: safeTarget,
          resource_types: resourceTypes as ResourceType[],
          profile_id: 'profile_001',
          chapter_id: chapter?.chapterId,
          chapter_name: chapter?.chapterName,
        })
        const taskId = created.taskId || created.task_id
        writeUserText(ACTIVE_TASK_KEY, taskId)
        this.activeTask = await getTaskApi(taskId)
        syncGeneratedResourcesFromTask(this.activeTask)
        if (this.activeTask.status === 'running') {
          this.pollTask(taskId)
        } else if (this.activeTask.status === 'failed') {
          this.lastError = this.activeTask.message || '知识库未命中足够课程资料，已停止高可信资源生成。'
        }
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : '资源生成任务创建失败，请稍后重试。'
        this.activeTask = null
        removeUserKeys([ACTIVE_TASK_KEY])
      } finally {
        this.creating = false
      }
    },
  },
})
