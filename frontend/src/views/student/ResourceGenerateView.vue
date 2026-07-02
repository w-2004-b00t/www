<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import AgentProgress from '../../components/agent/AgentProgress.vue'
import FlowGuide from '../../components/common/FlowGuide.vue'
import ResourceCard from '../../components/resource/ResourceCard.vue'
import ProcessChain from '../../components/workflow/ProcessChain.vue'
import type { ProcessChainStep } from '../../components/workflow/ProcessChain.vue'
import { getNextLearningTopicApi } from '../../api/resource'
import { DEFAULT_RESOURCE_TYPES, RESOURCE_TYPE_LABELS, RESOURCE_TYPE_OPTIONS } from '../../constants/resourceMeta'
import { useResourceSummary } from '../../composables/useResourceSummary'
import { useOnboardingStore } from '../../stores/onboarding'
import { useProfileStore } from '../../stores/profile'
import { useResourceStore } from '../../stores/resource'
import { useTaskStore } from '../../stores/task'
import { useUiStore } from '../../stores/ui'
import type { LearningResource, NextLearningTopic, ResourceFeedback, ResourceType } from '../../types/common'
import { cleanGenerationTarget, cleanGenerationTopic } from '../../utils/resourceTopic'

const router = useRouter()
const route = useRoute()
const task = useTaskStore()
const resource = useResourceStore()
const profile = useProfileStore()
const onboarding = useOnboardingStore()
const ui = useUiStore()

const topic = ref('线性表')
const target = ref('理解线性表的顺序存储、链式存储、基本操作和复杂度，并完成代码实践。')
const selected = ref<ResourceType[]>([...DEFAULT_RESOURCE_TYPES])
const nextTopic = ref<NextLearningTopic | null>(null)
const pathJoined = ref(false)
const isRefreshingResources = ref(false)
const isAttachingPath = ref(false)
const showFailureDetail = ref<string[]>([])
const failureDetailPanels = computed<string[]>({
  get: () => (Array.isArray(showFailureDetail.value) ? showFailureDetail.value : []),
  set: (value) => {
    if (Array.isArray(value)) {
      showFailureDetail.value = value.map(String)
    } else if (typeof value === 'string' && value) {
      showFailureDetail.value = [value]
    } else {
      showFailureDetail.value = []
    }
  },
})

const usedProfile = computed(() => profile.profileItems.filter((item) => item.status === 'confirmed').slice(0, 10))
const done = computed(() => task.activeTask?.status === 'success')
const failed = computed(() => task.activeTask?.status === 'failed')
const running = computed(() => task.activeTask?.status === 'running')
const taskOutput = computed(() => task.activeTask?.outputPayload || {})
const errorDetail = computed(() => taskOutput.value.errorDetail)
const failureReasonCode = computed(() => String(errorDetail.value?.reasonCode || ''))
const failureStageText = computed(() => {
  const agent = String(errorDetail.value?.agentName || task.activeTask?.currentAgent || taskOutput.value.failed_agent || '资源生成 Agent')
  return agent.includes('Agent') ? agent : `${agent} Agent`
})
const failureShortText = computed(() => {
  const code = failureReasonCode.value
  if (['json_malformed', 'json_truncated', 'json_extra_text', 'json_escape_error'].includes(code)) {
    return 'DeepSeek 返回的讲解文档 JSON 格式异常，系统已停止生成以避免假数据。'
  }
  if (code === 'insufficient_teaching_citations') {
    return '本地知识库命中了资料，但可用于讲解文档的干净教学片段不足。'
  }
  return task.activeTask?.message || task.lastError || '资源生成失败，系统已停止生成以避免假数据。'
})
const failureCauseText = computed(() => {
  const code = failureReasonCode.value
  const map: Record<string, string> = {
    json_malformed: '模型返回内容不是合法 JSON，常见原因是正文里混入未转义的代码块、引号或额外说明文字。',
    json_truncated: '模型输出可能过长被截断，导致 JSON 对象没有完整闭合。',
    json_extra_text: '模型在 JSON 前后输出了额外说明文字，后端无法确认其结构可靠。',
    json_escape_error: '模型输出的字符串里存在未正确转义的换行、引号或控制字符。',
    insufficient_teaching_citations: '检索结果里源码、PPT 结束页或路径噪声过多，讲解文档缺少可引用的概念/例题片段。',
  }
  return map[code] || String(errorDetail.value?.detail || taskOutput.value.error || '后端严格校验未通过。')
})
const failureActions = computed(() => {
  const actions = errorDetail.value?.suggestedActions
  if (Array.isArray(actions) && actions.length) return actions.map(String)
  if (['json_malformed', 'json_truncated', 'json_extra_text', 'json_escape_error'].includes(failureReasonCode.value)) {
    return ['点击重试生成', '若多次失败，请清理知识库中的 PPT 结束页、源码片段或路径噪声', '减少讲解文档中的代码块长度']
  }
  return ['点击重试生成', '查看课程引用', '必要时补充或重新导入课程资料']
})
const retrievalNoiseSummary = computed(() => (
  Array.isArray(errorDetail.value?.retrievalNoiseSummary) ? errorDetail.value.retrievalNoiseSummary : []
))
const autoSyncedPath = computed(() => {
  const learningPath = taskOutput.value.learningPath
  return Boolean(
    done.value
    && learningPath
    && typeof learningPath === 'object'
    && 'status' in learningPath
    && learningPath.status === 'ready',
  )
})

const taskResourceIds = computed(() => {
  const ids = taskOutput.value.resourceIds
  return Array.isArray(ids) ? ids.map(String) : []
})
const taskOutputResources = computed<LearningResource[]>(() => {
  const resources = taskOutput.value.resources
  if (!Array.isArray(resources)) return []
  return resources.filter((item): item is LearningResource => Boolean(item && typeof item === 'object' && 'id' in item))
})
const resourcesSyncedToCenter = computed(() => {
  if (!taskResourceIds.value.length) return false
  const syncedIds = new Set(resource.allResources.map((item) => item.id))
  return taskResourceIds.value.every((id) => syncedIds.has(id))
})
const usingTaskOutputFallback = computed(() => done.value && !resourcesSyncedToCenter.value && taskOutputResources.value.length > 0)
const generatedResources = computed<LearningResource[]>(() => {
  const resources = resource.allResources
  if (!taskResourceIds.value.length) return taskOutputResources.value
  if (!resources.length) return taskOutputResources.value
  const idSet = new Set(taskResourceIds.value)
  const matched = resources.filter((item) => idSet.has(item.id))
  return matched.length ? matched : taskOutputResources.value
})

const {
  auditSummary,
  blockedResources,
  passedResources,
  recommendedResource: recommendedResult,
} = useResourceSummary(generatedResources)
const secondaryResults = computed(() => generatedResources.value.filter((item) => item.id !== recommendedResult.value?.id))

const selectedLabels = computed(() => selected.value.map((item) => RESOURCE_TYPE_LABELS[item] || item))
const courseName = computed(() => onboarding.selectedCourse?.name || '数据结构课程')
const courseSection = computed(() => '第 3 章第 2 节')
const chapterReasonText = computed(() => nextTopic.value?.reason || '系统会根据学习进度选择当前章节。')
const weakPointText = computed(() => (
  profile.profileItems.find((item) => item.dimension.includes('薄弱') && item.status === 'confirmed')?.value
  || onboarding.weakPoint
  || '线性表、栈和队列、树和二叉树'
))
const preferenceText = computed(() => (
  profile.profileItems.find((item) => item.dimension.includes('资源') && item.status === 'confirmed')?.value
  || (onboarding.preference?.length ? onboarding.preference.join('、') : '图解、例题、代码实践')
))
const knowledgeHitText = computed(() => {
  if (failed.value) {
    if (failureReasonCode.value === 'insufficient_teaching_citations') return '命中资料含噪声，需清理或补充'
    if (failureReasonCode.value) return '已命中引用，但生成校验失败'
    return '本地知识库导入或检索不足'
  }
  if ((task.activeTask?.progress || 0) >= 22 || done.value) return `已命中《${courseName.value}》课程引用`
  return '等待检索课程资料'
})

const currentStatusLabel = computed(() => {
  if (failed.value) return '生成失败，可重试'
  if (done.value && (pathJoined.value || autoSyncedPath.value)) return '已自动同步学习路径'
  if (done.value) return blockedResources.value.length ? '已生成，部分待复核' : '已生成，可学习'
  if (running.value) {
    const progress = task.activeTask?.progress || 0
    if (progress >= 90) return '正在检查内容质量'
    if (progress >= 58) return '正在生成学习资料'
    return '正在查找课程资料'
  }
  return usedProfile.value.length ? '画像已匹配' : '可直接生成'
})

const primaryButtonText = computed(() => {
  if (task.creating) return '正在创建任务'
  if (running.value) return '正在生成学习资料'
  if (failed.value) return '重试生成'
  if (done.value && !(pathJoined.value || autoSyncedPath.value) && passedResources.value.length) return '同步并查看学习路径'
  if (done.value) return '查看学习路径'
  return '生成学习资料'
})
const primaryButtonType = computed(() => (failed.value ? 'warning' : 'primary'))

const resultSentence = computed(() => {
  const total = Number(taskOutput.value.resource_count || generatedResources.value.length || 0)
  const passed = Number(taskOutput.value.audit_passed || passedResources.value.length || 0)
  const warning = Number(taskOutput.value.audit_warning || blockedResources.value.length || 0)
  return `已生成 ${total} 份学习资料，其中 ${passed} 份可学习，${warning} 份等待教师复核。`
})
const syncFailureText = computed(() => {
  const taskMessage = String(task.activeTask?.message || '')
  const auditStatus = String(taskOutput.value.audit_status || '')
  const nextActions = Array.isArray(taskOutput.value.next_actions) ? taskOutput.value.next_actions.join('、') : ''
  return [taskMessage, auditStatus, nextActions].filter(Boolean).join('；') || '后端还没有返回资源列表，请重新同步或重试生成。'
})

const studentSteps = computed(() => {
  const progress = task.activeTask?.progress || 0
  const status = task.activeTask?.status
  const stepStatus = (threshold: number): ProcessChainStep['status'] => {
    if (!task.activeTask) return 'pending'
    if (status === 'failed') return progress >= threshold ? 'warning' : 'pending'
    if (progress >= threshold || done.value) return 'done'
    if (progress >= Math.max(0, threshold - 20)) return 'active'
    return 'pending'
  }

  return [
    {
      key: 'profile',
      title: '匹配学习画像',
      desc: usedProfile.value.length ? `已使用 ${usedProfile.value.length} 个画像维度` : '使用默认学习画像兜底',
      status: usedProfile.value.length ? 'done' : 'warning',
    },
    {
      key: 'knowledge',
      title: '查找课程资料',
      desc: knowledgeHitText.value,
      status: stepStatus(25),
    },
    {
      key: 'generate',
      title: '生成学习资料',
      desc: selectedLabels.value.join('、'),
      status: stepStatus(70),
    },
    {
      key: 'audit',
      title: '检查内容质量',
      desc: done.value ? auditSummary.value : '检查引用、答案和难度是否适合你',
      status: stepStatus(90),
    },
    {
      key: 'ready',
      title: '同步到学习路径',
      desc: (autoSyncedPath.value || pathJoined.value) ? '已保存资源并自动写入学习路径' : resourcesSyncedToCenter.value ? '已保存资源，正在等待路径绑定' : usingTaskOutputFallback.value ? '已从任务结果恢复，正在同步资源中心' : '生成完成后自动保存并规划路径',
      status: (autoSyncedPath.value || pathJoined.value) ? 'done' : resourcesSyncedToCenter.value ? 'active' : usingTaskOutputFallback.value ? 'warning' : 'pending',
    },
  ] satisfies ProcessChainStep[]
})

const reviewGuideSteps = [
  { label: '确认需求', desc: '主题、目标和资源类型', path: '/student/resource-generate' },
  { label: '匹配画像', desc: '使用已确认画像或默认画像', path: '/student/profile' },
  { label: '检索资料', desc: '命中课程讲义引用片段' },
  { label: 'Agent 生成', desc: '生成 6 类学习资料' },
  { label: '内容审核', desc: '检查引用、难度和答案' },
  { label: '同步路径', desc: '自动进入补强任务和测评', path: '/student/learning-path' },
]

function validate() {
  if (!topic.value.trim()) {
    ElMessage.warning('请填写本次学习主题。')
    return false
  }
  if (!selected.value.length) {
    ElMessage.warning('请至少选择一种学习资料。')
    return false
  }
  return true
}

async function refreshGeneratedResources() {
  isRefreshingResources.value = true
  try {
    await resource.loadAll(true)
    const outputIds = taskResourceIds.value
      const syncedIds = new Set(resource.allResources.map((item) => item.id))
      const missingIds = outputIds.filter((id) => !syncedIds.has(id))
    if (outputIds.length && missingIds.length && taskOutputResources.value.length) {
      ElMessage.warning('已从任务结果恢复显示，资源中心仍在等待同步。')
    } else if (outputIds.length && missingIds.length) {
      ElMessage.warning('资源未同步到后端，请重新同步或重试生成。')
    } else if (!generatedResources.value.length) {
      ElMessage.warning(syncFailureText.value)
    } else {
      ElMessage.success(`已同步 ${generatedResources.value.length} 份资料到资源中心。`)
    }
  } finally {
    isRefreshingResources.value = false
  }
}

async function generate() {
  if (!validate()) return
  pathJoined.value = false
  task.clearActiveTask()
  await task.startResourceTask(topic.value, target.value, selected.value, {
    chapterId: nextTopic.value?.chapterId,
    chapterName: nextTopic.value?.chapterName,
  })
}

async function attachPassedResources() {
  return attachPassedResourcesToPath({ showMessage: true })
}

async function attachPassedResourcesToPath(options: { showMessage?: boolean; refreshFirst?: boolean } = {}) {
  if (pathJoined.value || autoSyncedPath.value) return true
  if (options.refreshFirst) {
    await resource.loadAll(true)
  }
  const ids = passedResources.value.map((item) => item.id)
  if (!ids.length) {
    if (options.showMessage) ElMessage.warning('当前没有已通过审核的资料可加入路径。')
    return false
  }
  isAttachingPath.value = true
  try {
    await resource.attachResourcesToPath(ids, task.activeTask?.id)
    pathJoined.value = true
    if (options.showMessage) ElMessage.success(`已将 ${ids.length} 份资料加入学习路径。`)
    return true
  } catch (error) {
    if (options.showMessage) {
      ElMessage.error(error instanceof Error ? error.message : '资料加入学习路径失败')
    } else {
      resource.lastError = error instanceof Error ? error.message : '资料加入学习路径失败'
    }
    return false
  } finally {
    isAttachingPath.value = false
  }
}

async function handlePrimaryAction() {
  if (task.creating) return
  if (running.value) return
  if (failed.value) {
    await task.retryActiveTask()
    return
  }
  if (done.value) {
    if (!(pathJoined.value || autoSyncedPath.value)) {
      const attached = await attachPassedResourcesToPath({ showMessage: true, refreshFirst: true })
      if (!attached && passedResources.value.length) return
    }
    router.push('/student/learning-path')
    return
  }
  await generate()
}

function showCitationHint() {
  ElMessage.info(`本次资料基于《${courseName.value}》${courseSection.value}的课程片段生成；评审模式可查看 Agent 输出和引用证据。`)
}

async function handleFeedback(payload: { resourceId: string; type: ResourceFeedback['type'] }) {
  try {
    await resource.submitFeedback(payload.resourceId, payload.type)
    ElMessage.success('反馈已保存，会影响后续资源排序和学习画像更新。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源反馈保存失败')
  }
}

async function attachSingleResource(resourceId: string) {
  try {
    await resource.attachResourcesToPath([resourceId], task.activeTask?.id)
    ElMessage.success('已加入学习路径，学习完成和掌握状态不会自动改变。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资料加入学习路径失败')
  }
}

async function completeGeneratedResource(resourceId: string) {
  try {
    await resource.completeResource(resourceId)
    ElMessage.success('已记录为学完；掌握状态仍需你单独确认。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源完成状态保存失败')
  }
}

async function masterGeneratedResource(resourceId: string) {
  try {
    await resource.markResourceMastered(resourceId)
    ElMessage.success('已确认掌握，后续推荐会跳过这份资料。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源掌握状态保存失败')
  }
}

function resetSelectedResourceTypes() {
  selected.value = [...DEFAULT_RESOURCE_TYPES]
}

function readMetadataText(item: LearningResource, key: 'topic' | 'target') {
  const value = item.metadata?.[key]
  return typeof value === 'string' ? value.trim() : ''
}

function getRegenerationSeed(item: LearningResource) {
  const metadata = item.metadata || {}
  const seedTopic = cleanGenerationTopic(readMetadataText(item, 'topic') || item.title || topic.value)
  const seedTarget = cleanGenerationTarget(readMetadataText(item, 'target'), seedTopic)
  return {
    topic: seedTopic,
    target: seedTarget,
    chapterId: typeof metadata.chapterId === 'string' ? metadata.chapterId : nextTopic.value?.chapterId,
    chapterName: typeof metadata.chapterName === 'string' ? metadata.chapterName : nextTopic.value?.chapterName,
  }
}

async function regenerateFromResource(item: LearningResource) {
  const seed = getRegenerationSeed(item)
  topic.value = seed.topic
  target.value = seed.target
  resetSelectedResourceTypes()
  task.clearActiveTask()
  await task.startResourceTask(topic.value, target.value, selected.value, {
    chapterId: seed.chapterId,
    chapterName: seed.chapterName,
  })
}

async function handleRegenerate(resourceId: string) {
  const item = resource.getResourceById(resourceId)
  if (!item) return
  await regenerateFromResource(item)
}

async function regenerateRecommended() {
  if (!recommendedResult.value) {
    ElMessage.warning('暂无可重新生成的推荐资料。')
    return
  }
  await regenerateFromResource(recommendedResult.value)
}

async function loadNextTopic() {
  try {
    const current = await getNextLearningTopicApi()
    nextTopic.value = current
    topic.value = current.topic
    target.value = `掌握${current.chapterName}的核心概念、典型操作、复杂度和代码实践。`
  } catch (error) {
    ElMessage.warning(error instanceof Error ? error.message : '下一章节读取失败，已使用默认章节。')
  }
}

onMounted(async () => {
  onboarding.loadCourses()
  profile.loadProfile()
  await task.resumeActiveTask()
  await resource.loadAll(true)
  if (!task.activeTask) {
    const queryTopic = typeof route.query.topic === 'string' ? route.query.topic.trim() : ''
    if (queryTopic) {
      const queryChapterId = typeof route.query.chapterId === 'string' ? route.query.chapterId : ''
      const queryChapterName = typeof route.query.chapterName === 'string' ? route.query.chapterName : ''
      topic.value = queryTopic
      target.value = `掌握${queryTopic}的核心概念、典型操作、复杂度、易错点和代码实践。`
      nextTopic.value = {
        chapterId: queryChapterId,
        chapterName: queryChapterName || queryTopic,
        topic: queryTopic,
        knowledgePoints: [queryTopic],
        reason: '从知识图谱薄弱节点进入，优先生成针对性补强资料。',
        status: 'ready',
        blocked: false,
        source: 'knowledge_graph',
        evidence: [],
      }
    } else {
      await loadNextTopic()
    }
  }
})

onBeforeUnmount(() => {
  task.stopPolling()
})

watch(done, async (value, previous) => {
  if (value && value !== previous) {
    pathJoined.value = autoSyncedPath.value
    await refreshGeneratedResources()
    await attachPassedResourcesToPath()
  }
})
</script>

<template>
  <div class="page resource-generate-page">
    <div class="page-breadcrumb">
      <span>学生端</span>
      <span>生成学习资料</span>
    </div>

    <section class="panel hero-panel">
      <div>
        <span class="status-pill">本次学习任务</span>
        <h1>生成本次学习资料</h1>
        <p>
          系统会根据你的学习画像和《数据结构课程》课程资料，生成讲解文档、完整导图、练习题、拓展阅读、视频演示和代码案例。
        </p>
      </div>
      <div class="hero-status">
        <span>当前状态</span>
        <strong>{{ currentStatusLabel }}</strong>
      </div>
    </section>

    <el-alert v-if="task.recovered" class="top-alert" type="success" show-icon :closable="false">
      已恢复上次生成任务，刷新页面后仍可继续查看进度。
    </el-alert>
    <el-alert v-if="task.lastError" class="top-alert" type="warning" show-icon :closable="false">
      {{ failed ? failureShortText : task.lastError }} 可以点击“重试生成”继续。
    </el-alert>
    <el-alert v-if="resource.lastError" class="top-alert" type="warning" show-icon :closable="false">
      {{ resource.lastError }}
    </el-alert>

    <section class="panel generate-card">
      <div class="card-head">
        <div>
          <h2>这次要生成什么？</h2>
          <p>填写学习主题和目标，系统会自动匹配画像、课程资料和资源类型。</p>
        </div>
        <el-button
          :type="primaryButtonType"
          size="large"
          :loading="task.creating || running || isAttachingPath"
          @click="handlePrimaryAction"
        >
          {{ primaryButtonText }}
        </el-button>
      </div>

      <el-form class="simple-form" label-position="top">
        <div class="form-row">
          <el-form-item label="当前课程">
            <el-input :model-value="courseName" disabled />
          </el-form-item>
          <el-form-item label="学习主题">
            <el-input v-model="topic" placeholder="例如：线性表、栈和队列、树和二叉树、图、排序" />
          </el-form-item>
        </div>
        <el-form-item label="本次学习目标">
          <el-input
            v-model="target"
            type="textarea"
            :rows="3"
            placeholder="例如：理解线性表的存储结构、基本操作和复杂度，并完成代码实践。"
          />
        </el-form-item>
        <fieldset class="resource-choice-field">
          <legend id="resource-type-legend">希望生成的资料</legend>
          <el-checkbox-group v-model="selected" class="resource-choice" aria-labelledby="resource-type-legend">
            <el-checkbox v-for="item in RESOURCE_TYPE_OPTIONS" :key="item.value" :value="item.value">
              {{ item.label }}
            </el-checkbox>
          </el-checkbox-group>
        </fieldset>
      </el-form>

      <div class="context-strip">
        <div>
          <span>薄弱点</span>
          <strong>{{ weakPointText }}</strong>
        </div>
        <div>
          <span>资源偏好</span>
          <strong>{{ preferenceText }}</strong>
        </div>
        <div>
          <span>课程资料</span>
          <strong>{{ knowledgeHitText }}</strong>
        </div>
        <div>
          <span>章节推进</span>
          <strong>{{ chapterReasonText }}</strong>
        </div>
      </div>
    </section>

    <section v-if="task.activeTask" class="panel progress-card">
      <div class="card-head">
        <div>
          <h2>生成进度</h2>
          <p>你只需要等待资料生成完成；技术细节已收起到评审模式。</p>
        </div>
        <el-tag :type="done ? 'success' : failed ? 'warning' : 'primary'" effect="plain">
          {{ currentStatusLabel }}
        </el-tag>
      </div>
      <ProcessChain :steps="studentSteps" />
      <div class="progress-actions">
        <el-button @click="showCitationHint">查看课程引用</el-button>
        <el-button v-if="done" :loading="isRefreshingResources" @click="refreshGeneratedResources">重新同步资源</el-button>
        <el-button v-if="ui.reviewMode" @click="ui.setReviewMode(false)">收起评审证据</el-button>
      </div>
    </section>

    <el-collapse-transition>
      <section v-if="ui.reviewMode && task.activeTask" class="panel review-panel">
        <div class="card-head">
          <div>
            <h2>评审证据链</h2>
            <p>这里展示 Agent 输入、工具、输出、课程引用和下游影响。</p>
          </div>
        </div>
        <FlowGuide title="资源生成评审路线" description="评委可沿这条路线检查多智能体协作、RAG 引用和审核闭环。" :steps="reviewGuideSteps" :current="done ? 5 : running ? 3 : 0" />
        <AgentProgress :task="task.activeTask" @retry-step="task.retryAgentStep" />
      </section>
    </el-collapse-transition>

    <section v-if="failed" class="panel failure-panel">
      <h2>生成失败</h2>
      <p>{{ failureShortText }}</p>
      <div class="failure-grid">
        <div>
          <span>失败阶段</span>
          <strong>{{ failureStageText }}</strong>
        </div>
        <div>
          <span>可能原因</span>
          <strong>{{ failureCauseText }}</strong>
        </div>
        <div>
          <span>建议操作</span>
          <ul>
            <li v-for="item in failureActions" :key="item">{{ item }}</li>
          </ul>
        </div>
      </div>
      <el-collapse v-if="errorDetail" v-model="failureDetailPanels" class="failure-detail">
        <el-collapse-item title="展开技术详情" name="detail">
          <pre>{{ JSON.stringify(errorDetail, null, 2) }}</pre>
          <div v-if="retrievalNoiseSummary.length" class="noise-list">
            <strong>疑似噪声引用</strong>
            <p v-for="item in retrievalNoiseSummary" :key="item.chunkId || item.documentName">
              {{ item.documentName }}：{{ item.reasons?.join('、') }}；{{ item.preview }}
            </p>
          </div>
        </el-collapse-item>
      </el-collapse>
      <div class="inline-actions">
        <el-button type="primary" @click="task.retryActiveTask()">重试生成</el-button>
        <el-button @click="showCitationHint">查看课程引用</el-button>
        <el-button @click="failureDetailPanels = failureDetailPanels.length ? [] : ['detail']">展开技术详情</el-button>
      </div>
    </section>

    <section v-if="done" class="panel result-panel">
      <div class="card-head">
        <div>
          <h2>生成结果</h2>
          <p>{{ resultSentence }} {{ pathJoined || autoSyncedPath ? '已把通过审核的资料同步到学习路径。' : '正在等待同步到学习路径。' }}推荐先学习：{{ recommendedResult?.title || '等待资源同步' }}。</p>
        </div>
        <div class="inline-actions">
          <el-button type="primary" :loading="isAttachingPath" @click="handlePrimaryAction">查看学习路径</el-button>
          <el-button v-if="!(autoSyncedPath || pathJoined)" :loading="isAttachingPath" @click="attachPassedResources">手动同步路径</el-button>
          <el-button v-if="recommendedResult" @click="regenerateRecommended">重新生成学习资料</el-button>
          <router-link to="/student/resources"><el-button>查看资源中心</el-button></router-link>
          <router-link to="/student/assessment"><el-button>进入阶段测评</el-button></router-link>
        </div>
      </div>

      <el-skeleton v-if="isRefreshingResources" :rows="5" animated />

      <template v-else>
        <el-alert v-if="usingTaskOutputFallback" class="risk-status" type="warning" show-icon :closable="false">
          已从任务结果恢复 {{ taskOutputResources.length }} 份资料，资源中心列表仍在同步；可先查看本页结果。
        </el-alert>

        <div v-if="!generatedResources.length" class="empty-result">
          <h3>生成完成，但资源还没有同步到列表</h3>
          <p>{{ syncFailureText }}</p>
          <el-button type="primary" :loading="isRefreshingResources" @click="refreshGeneratedResources">重新同步资源</el-button>
        </div>

        <template v-else>
        <div v-if="recommendedResult" class="featured-result">
          <div class="featured-copy">
            <span class="status-pill">推荐先学</span>
            <h3>{{ recommendedResult.title }}</h3>
            <p>这份资料最贴合你的薄弱点和资源偏好，建议先完成它，再进入练习和测评。</p>
            <div class="reason-list">
              <span>薄弱点：{{ weakPointText }}</span>
              <span>偏好：{{ preferenceText }}</span>
              <span>审核：{{ auditSummary }}</span>
            </div>
          </div>
          <ResourceCard
            :resource="recommendedResult"
            variant="featured"
            @feedback="handleFeedback"
            @regenerate="handleRegenerate"
            @attach="attachSingleResource"
            @complete="completeGeneratedResource"
            @mastery="masterGeneratedResource"
          />
        </div>

        <el-alert v-if="blockedResources.length" class="risk-status" type="warning" show-icon :closable="false">
          有 {{ blockedResources.length }} 份资料等待教师复核，暂不作为正式推荐资料。
        </el-alert>

        <el-alert v-if="pathJoined || autoSyncedPath" class="risk-status" type="success" show-icon :closable="false">
          已将通过审核的资料自动同步到学习路径，可前往路径页继续学习。
        </el-alert>

        <div class="all-results-head">
          <h3>其他已生成资料</h3>
          <span>{{ secondaryResults.length }} 份</span>
        </div>
        <div class="resource-grid">
          <ResourceCard
            v-for="item in secondaryResults"
            :key="item.id"
            :resource="item"
            @feedback="handleFeedback"
            @regenerate="handleRegenerate"
            @attach="attachSingleResource"
            @complete="completeGeneratedResource"
            @mastery="masterGeneratedResource"
          />
        </div>
        </template>
      </template>
    </section>
  </div>
</template>

<style scoped>
.resource-generate-page {
  max-width: 1480px;
}

.top-alert {
  margin-bottom: 16px;
}

.hero-panel,
.generate-card,
.progress-card,
.review-panel,
.failure-panel,
.result-panel {
  margin-bottom: 16px;
}

.hero-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: 24px;
  align-items: center;
}

.hero-panel h1 {
  margin: 10px 0 8px;
  font-size: 26px;
  line-height: 1.25;
}

.hero-panel p,
.card-head p,
.failure-panel p,
.featured-copy p,
.empty-result p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.hero-status {
  display: grid;
  gap: 6px;
  padding: 18px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.hero-status span,
.context-strip span,
.reason-list span,
.all-results-head span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.hero-status strong {
  font-size: 20px;
}

.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.card-head h2 {
  margin: 0 0 6px;
  font-size: 20px;
}

.simple-form {
  max-width: 1080px;
}

.form-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}

.resource-choice {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
}

.resource-choice-field {
  min-width: 0;
  margin: 0 0 18px;
  padding: 0;
  border: 0;
}

.resource-choice-field legend {
  margin: 0 0 8px;
  color: var(--color-text);
  font-size: 14px;
  line-height: 1.5;
}

.context-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  overflow: hidden;
  margin-top: 6px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.context-strip div {
  display: grid;
  gap: 5px;
  min-width: 0;
  padding: 12px 14px;
  border-right: 1px solid var(--color-border);
}

.context-strip div:last-child {
  border-right: 0;
}

.context-strip strong {
  overflow: hidden;
  color: var(--color-text);
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.progress-actions,
.inline-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}

.review-panel {
  border-color: #bfdbfe;
  background: #f8fbff;
}

.failure-panel {
  border-color: #fed7aa;
  background: #fffbeb;
}

.failure-grid {
  display: grid;
  grid-template-columns: 0.8fr 1.4fr 1fr;
  gap: 0;
  overflow: hidden;
  margin-top: 14px;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  background: #fff;
}

.failure-grid > div {
  min-width: 0;
  padding: 12px;
  border-right: 1px solid #fed7aa;
}

.failure-grid > div:last-child {
  border-right: 0;
}

.failure-grid span {
  display: block;
  margin-bottom: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.failure-grid strong {
  color: var(--color-text);
  font-size: 14px;
  line-height: 1.6;
}

.failure-grid ul {
  margin: 0;
  padding-left: 18px;
  color: var(--color-text);
  line-height: 1.7;
}

.failure-detail {
  margin-top: 12px;
}

.failure-detail pre {
  overflow: auto;
  max-height: 260px;
  margin: 0;
  padding: 12px;
  border-radius: 8px;
  background: #111827;
  color: #e5e7eb;
  font-size: 12px;
}

.noise-list {
  display: grid;
  gap: 6px;
  margin-top: 12px;
}

.noise-list p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.empty-result {
  display: grid;
  gap: 10px;
  justify-items: start;
  padding: 22px;
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.empty-result h3 {
  margin: 0;
  font-size: 18px;
}

.featured-result {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
  gap: 16px;
  align-items: stretch;
  margin-top: 8px;
}

.featured-copy {
  display: grid;
  align-content: start;
  gap: 10px;
  padding: 18px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.featured-copy h3 {
  margin: 0;
  font-size: 20px;
}

.reason-list {
  display: grid;
  gap: 8px;
}

.reason-list span {
  padding: 8px 10px;
  border-radius: 6px;
  background: #fff;
}

.risk-status {
  margin: 16px 0;
}

.all-results-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 18px 0 10px;
}

.all-results-head h3 {
  margin: 0;
  font-size: 18px;
}

.resource-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

@media (max-width: 1200px) {
  .hero-panel,
  .form-row,
  .context-strip,
  .failure-grid,
  .featured-result,
  .resource-grid {
    grid-template-columns: 1fr;
  }

  .context-strip div {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }

  .failure-grid > div {
    border-right: 0;
    border-bottom: 1px solid #fed7aa;
  }

  .context-strip div:last-child {
    border-bottom: 0;
  }

  .failure-grid > div:last-child {
    border-bottom: 0;
  }

  .card-head {
    flex-direction: column;
  }
}
</style>
