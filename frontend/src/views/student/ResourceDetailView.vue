<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import PageActionGuide from '../../components/common/PageActionGuide.vue'
import ExerciseViewer from '../../components/resource/ExerciseViewer.vue'
import LabPracticeViewer from '../../components/resource/LabPracticeViewer.vue'
import MarkdownViewer from '../../components/resource/MarkdownViewer.vue'
import MindMapViewer from '../../components/resource/MindMapViewer.vue'
import SourceCitation from '../../components/resource/SourceCitation.vue'
import { getResourceApi, submitResourcePracticeApi } from '../../api/resource'
import { useResourceStore } from '../../stores/resource'
import { useUiStore } from '../../stores/ui'
import type { LearningResource, ResourceFeedback, ResourcePracticeResult } from '../../types/common'
import { getResourceLearningState } from '../../utils/resourceLearningState'

const route = useRoute()
const store = useResourceStore()
const ui = useUiStore()
const currentResource = ref<LearningResource | null>(null)
const exerciseAnswers = ref<Record<string, string>>({})
const practiceResult = ref<ResourcePracticeResult | null>(null)
const submittingPractice = ref(false)
const loadingResource = ref(false)
const resourceLoadError = ref('')
const resource = computed(() => {
  const id = route.params.id as string
  return currentResource.value || store.getResourceById(id)
})

const exerciseCount = computed(() => {
  if (!resource.value || resource.value.resourceType !== 'exercise') return 0
  try {
    return JSON.parse(resource.value.content).length || 0
  } catch {
    return 0
  }
})
const answeredCount = computed(() => Object.keys(exerciseAnswers.value).filter((key) => exerciseAnswers.value[key]).length)
const auditLabel = computed(() => {
  const labels = { passed: '已通过', warning: '有风险', pending: '待审核', rejected: '需修正' }
  return resource.value ? labels[resource.value.auditStatus] : ''
})
const learningState = computed(() => getResourceLearningState(resource.value))

const generationMeta = computed(() => {
  const metadata = (resource.value?.metadata || {}) as Record<string, unknown>
  const sourceChunkIds = Array.isArray(metadata.sourceChunkIds) ? metadata.sourceChunkIds.map((item) => String(item)) : []
  return {
    generationTemplate: String(metadata.generationTemplate || 'structured_template_v1'),
    promptSchema: String(metadata.promptSchema || 'title, summary, sections, citations'),
    retrievalCoverage: String(metadata.retrievalCoverage || 'sufficient'),
    generationMode: String(metadata.generationMode || 'structured_prompt'),
    llmModel: String(metadata.llmModel || 'DeepSeek 未返回模型信息'),
    sourceChunkIds,
    citationCount: Number(metadata.citationCount || resource.value?.citations?.length || 0),
  }
})

const citationSummary = computed(() => {
  if (!resource.value?.citations?.length) return '暂无课程引用'
  return `${resource.value.citations.length} 条课程片段`
})

const actionGuide = computed(() => {
  if (resource.value?.resourceType === 'exercise') {
    return {
      title: '先完成资源内练习',
      description: '提交后会生成得分、错因、错题本和路径提示。',
      currentAction: '当前要做：先完成题目，再查看判分和错因。',
    }
  }
  if (resource.value?.resourceType === 'lab') {
    return {
      title: '先完成代码实践任务',
      description: '这份资源会拆出操作目标、输入输出、手工跟踪、代码骨架和验收清单。',
      currentAction: '当前要做：先完成操作跟踪，再写代码骨架并对照验收清单。',
    }
  }
  return {
    title: '先阅读这份学习资料',
    description: '这里展示资源内容、引用来源和画像适配说明。',
    currentAction: '当前要做：先阅读内容，再查看引用和为什么推荐给你。',
  }
})

const labStatus = computed(() => {
  if (resource.value?.resourceType !== 'lab') return ''
  const dataStatus = String(resource.value.metadata?.dataStatus || '')
  return dataStatus === 'live'
    ? '已命中真实源码，可对照源码完成代码实践。'
    : '未命中真实源码，请先完成代码设计与手工跟踪；上传源码后可重新生成对照版实验。'
})

async function feedback(type: ResourceFeedback['type']) {
  if (!resource.value) return
  try {
    await store.submitFeedback(resource.value.id, type)
    ElMessage.success('反馈已保存，会进入资源效果分析。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源反馈保存失败')
  }
}

async function completeCurrentResource() {
  if (!resource.value) return
  if (!learningState.value.canComplete) {
    ElMessage.warning(resource.value.isCompleted ? '这份资源已经标记学完。' : '请先打开并浏览资源后再标记学完。')
    return
  }
  try {
    await store.completeResource(resource.value.id)
    currentResource.value = store.getResourceById(resource.value.id) || currentResource.value
    ElMessage.success('已记录学完状态；是否掌握仍由你单独确认。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源完成状态保存失败')
  }
}

async function masterCurrentResource() {
  if (!resource.value) return
  if (!learningState.value.canMaster) {
    ElMessage.warning(resource.value.isMastered ? '这份资源已经确认掌握。' : '请先标记学完后再确认掌握。')
    return
  }
  try {
    await store.markResourceMastered(resource.value.id)
    currentResource.value = store.getResourceById(resource.value.id) || currentResource.value
    ElMessage.success('已记录掌握状态，后续推荐会跳过这份资料。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源掌握状态保存失败')
  }
}

async function startCurrentResource() {
  if (!resource.value || !learningState.value.canStart) return
  try {
    currentResource.value = await store.viewResource(resource.value.id)
    ElMessage.success('已开始学习当前资源。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源学习状态记录失败')
  }
}

async function submitPractice() {
  if (!resource.value) return
  if (answeredCount.value < exerciseCount.value) {
    ElMessage.warning(`还有 ${exerciseCount.value - answeredCount.value} 道题未作答。`)
    return
  }
  submittingPractice.value = true
  try {
    practiceResult.value = await submitResourcePracticeApi(resource.value.id, { answers: exerciseAnswers.value })
    await store.loadAll()
    ElMessage.success(`提交成功，得分 ${practiceResult.value.score}，错题已同步学习记录。`)
  } finally {
    submittingPractice.value = false
  }
}

async function loadCurrentResource() {
  const id = String(route.params.id || '')
  currentResource.value = store.getResourceById(id) || null
  resourceLoadError.value = ''
  if (!id) return
  loadingResource.value = true
  try {
    const latest = await getResourceApi(id)
    currentResource.value = latest
    store.upsertResource(latest)
    if (latest.auditStatus === 'passed' && !latest.isViewed) {
      currentResource.value = await store.viewResource(id)
    }
  } catch (error) {
    currentResource.value = store.getResourceById(id) || null
    resourceLoadError.value = error instanceof Error ? error.message : '资源同步失败，请重新同步后再试。'
  } finally {
    loadingResource.value = false
  }
}

async function retryLoadCurrentResource() {
  await store.loadAll(true)
  await loadCurrentResource()
  if (resource.value) {
    ElMessage.success('资源已同步。')
  } else if (resourceLoadError.value) {
    ElMessage.warning(resourceLoadError.value)
  }
}

onMounted(async () => {
  await store.loadAll(true)
  await loadCurrentResource()
})

watch(
  () => route.params.id,
  async () => {
    exerciseAnswers.value = {}
    practiceResult.value = null
    await loadCurrentResource()
  },
)
</script>

<template>
  <div v-if="loadingResource && !resource" class="page">
    <section class="panel state-empty">
      <el-skeleton :rows="4" animated />
    </section>
  </div>
  <div class="page" v-else-if="resource">
    <div class="page-header">
      <div>
        <h1 class="page-title">{{ resource.title }}</h1>
        <p class="page-subtitle">{{ resource.summary }}</p>
      </div>
      <div class="head-tags">
        <el-tag :type="resource.auditStatus === 'passed' ? 'success' : resource.auditStatus === 'rejected' ? 'danger' : 'warning'">
          {{ auditLabel }}
        </el-tag>
        <el-tag
          :type="learningState.status === 'pending' ? 'info' : learningState.status === 'learning' ? 'primary' : 'success'"
          :effect="learningState.status === 'mastered' ? 'dark' : 'plain'"
        >
          {{ learningState.label }}
        </el-tag>
        <el-tag>质量评分 {{ resource.qualityScore }}</el-tag>
        <el-tag effect="plain">v{{ resource.version || 1 }}</el-tag>
      </div>
    </div>

    <PageActionGuide
      :title="actionGuide.title"
      :description="actionGuide.description"
      :current-action="actionGuide.currentAction"
      primary-label="查看学习路径"
      primary-to="/student/learning-path"
      secondary-label="返回资源中心"
      secondary-to="/student/resources"
    />

    <div class="resource-detail-layout">
      <section class="panel content-panel">
        <el-alert v-if="resource.auditStatus !== 'passed'" class="resource-alert" type="warning" show-icon :closable="false">
          该资源尚未完全通过教师审核，高风险内容不会作为正式推荐资料。
        </el-alert>
        <MindMapViewer v-if="resource.resourceType === 'mindmap'" :content="resource.content" />
        <section v-if="resource.resourceType === 'mindmap'" class="mindmap-entry-panel">
          <div>
            <strong>完整思维导图工作台</strong>
            <span>进入独立页面后可展开/收起节点、查看来源标签、跳转相关资源，并导出 PNG 或 Markdown。</span>
          </div>
          <router-link to="/student/mindmap">
            <el-button type="primary">查看完整导图</el-button>
          </router-link>
        </section>
        <ExerciseViewer
          v-else-if="resource.resourceType === 'exercise'"
          v-model="exerciseAnswers"
          :content="resource.content"
          :result-details="practiceResult?.details"
        />
        <LabPracticeViewer
          v-else-if="resource.resourceType === 'lab'"
          :resource="resource"
        />
        <section v-else-if="resource.resourceType === 'video_script'" class="video-real-entry">
          <div>
            <strong>本地开源教学视频</strong>
            <span>视频资源现在由本地缓存/SVD/CogVideo 适配器生成，并通过 FFmpeg 统一输出标准 MP4。进入独立页面后可发起生成、轮询状态、播放和下载成片。</span>
          </div>
          <router-link to="/student/video-demo">
            <el-button type="primary">生成/播放本地开源 MP4</el-button>
          </router-link>
        </section>
        <section v-if="resource.resourceType === 'exercise'" class="practice-submit-panel">
          <div>
            <strong>资源内练习作答</strong>
            <span>已作答 {{ answeredCount }} / {{ exerciseCount }}，提交后会写入学习报告和错题本。</span>
          </div>
          <el-button type="primary" :loading="submittingPractice" @click="submitPractice">提交本资源练习</el-button>
        </section>
        <section v-if="practiceResult" class="practice-result-panel">
          <div>
            <span>本次得分</span>
            <strong>{{ practiceResult.score }}</strong>
          </div>
          <div>
            <span>正确题数</span>
            <strong>{{ practiceResult.correctCount }} / {{ practiceResult.total }}</strong>
          </div>
          <div>
            <span>错题沉淀</span>
            <strong>{{ practiceResult.mistakesAdded }} 道</strong>
          </div>
          <p>{{ practiceResult.suggestion }}{{ practiceResult.studentImpact }}</p>
        </section>
        <section v-if="practiceResult" class="practice-impact-panel">
          <div>
            <span>学习路径提示</span>
            <strong>{{ practiceResult.pathImpact || '练习结果已保存，可继续当前路径。' }}</strong>
          </div>
          <div>
            <span>学习报告影响</span>
            <strong>{{ practiceResult.reportImpact || '本次练习会作为资源学习效果记录。' }}</strong>
          </div>
          <div class="practice-impact-actions">
            <router-link to="/student/mistakes"><el-button>查看错题本</el-button></router-link>
            <router-link to="/student/learning-path"><el-button>查看学习路径</el-button></router-link>
            <router-link to="/student/report"><el-button type="primary">查看学习报告</el-button></router-link>
          </div>
        </section>
        <MarkdownViewer
          v-if="resource.resourceType !== 'exercise' && resource.resourceType !== 'mindmap' && resource.resourceType !== 'video_script' && resource.resourceType !== 'lab'"
          :content="resource.content"
          :variant="resource.resourceType === 'explanation' ? 'tutorial' : 'default'"
        />
      </section>
      <aside class="panel evidence-panel">
        <h2 class="section-title">画像适配说明</h2>
        <p class="section-desc">{{ resource.fitReason || '根据你的薄弱点、资源偏好和当前路径阶段推荐。' }}</p>
        <el-divider />
        <h2 class="section-title">学习状态</h2>
        <p class="current-resource-line">当前资源：{{ learningState.label }}</p>
        <div class="feedback-row">
          <el-tooltip :disabled="learningState.canStart" :content="learningState.startDisabledReason">
            <span class="action-wrap">
              <el-button
                size="small"
                type="primary"
                :disabled="!learningState.canStart"
                @click="startCurrentResource"
              >
                {{ learningState.startLabel }}
              </el-button>
            </span>
          </el-tooltip>
          <el-tooltip :disabled="learningState.canComplete" :content="learningState.completeDisabledReason">
            <span class="action-wrap">
              <el-button
                size="small"
                :disabled="!learningState.canComplete"
                @click="completeCurrentResource"
              >
                {{ learningState.completeLabel }}
              </el-button>
            </span>
          </el-tooltip>
          <el-tooltip :disabled="learningState.canMaster" :content="learningState.masterDisabledReason">
            <span class="action-wrap">
              <el-button
                size="small"
                type="success"
                plain
                :disabled="!learningState.canMaster"
                @click="masterCurrentResource"
              >
                {{ learningState.masterLabel }}
              </el-button>
            </span>
          </el-tooltip>
        </div>
        <p class="section-desc">打开详情页后才会记录为已浏览；已浏览后可标记学完，学完后才能确认掌握。</p>
        <el-divider />
        <h2 class="section-title">资源反馈</h2>
        <div class="feedback-row">
          <el-button size="small" @click="feedback('helpful')">有帮助</el-button>
          <el-button size="small" @click="feedback('too_hard')">太难</el-button>
          <el-button size="small" @click="feedback('incorrect')">不准确</el-button>
          <el-button size="small" @click="feedback('need_example')">需要例子</el-button>
        </div>
        <p class="section-desc">已反馈 {{ resource.feedback?.length || 0 }} 次，学习报告会统计资源效果。</p>
        <el-divider />
        <el-collapse class="source-collapse">
          <el-collapse-item :title="`查看可信来源（${resource.citations.length} 条）`" name="sources">
            <el-alert
              v-if="resource.metadata?.dataStatus === 'cached'"
              class="data-status-alert"
              type="warning"
              show-icon
              :closable="false"
              title="当前展示的是上次同步缓存，建议重新同步后继续学习。"
            />
            <SourceCitation :citations="resource.citations" />
          </el-collapse-item>
          <el-collapse-item v-if="resource.resourceType === 'lab'" title="查看代码实践状态" name="lab">
            <el-alert
              :type="resource.metadata?.dataStatus === 'live' ? 'success' : 'warning'"
              show-icon
              :closable="false"
              :title="labStatus"
            />
          </el-collapse-item>
        </el-collapse>
        <el-divider v-if="ui.reviewMode" />
        <el-collapse v-if="ui.reviewMode" class="debug-collapse">
          <el-collapse-item title="生成与溯源信息" name="generation">
            <div class="generation-meta">
              <div>
                <strong>引用概况</strong>
                <p>{{ citationSummary }}</p>
              </div>
              <div>
                <strong>生成方式</strong>
                <p>{{ generationMeta.generationMode }}</p>
              </div>
              <div>
                <strong>引用覆盖</strong>
                <p>{{ generationMeta.retrievalCoverage }}</p>
              </div>
              <div>
                <strong>引用数量</strong>
                <p>{{ generationMeta.citationCount }} 条</p>
              </div>
              <div>
                <strong>模型</strong>
                <p>{{ generationMeta.llmModel }}</p>
              </div>
              <div v-if="generationMeta.sourceChunkIds.length">
                <strong>来源片段</strong>
                <p>{{ generationMeta.sourceChunkIds.length }} 个片段，详情见引用弹窗</p>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </aside>
    </div>
  </div>
  <div v-else class="page">
    <section class="panel state-empty">
      <h2>没有找到该学习资源</h2>
      <p>{{ resourceLoadError || '该资源没有从后端返回真实数据。请返回资源中心重新进入，或基于已上传课程资料重新生成。' }}</p>
      <div class="missing-actions">
        <el-button :loading="loadingResource" @click="retryLoadCurrentResource">重新同步资源</el-button>
        <router-link to="/student/resources"><el-button type="primary">返回资源中心</el-button></router-link>
        <router-link to="/student/resource-generate"><el-button>重新生成资源</el-button></router-link>
      </div>
    </section>
  </div>
</template>

<style scoped>
.head-tags,
.feedback-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.action-wrap {
  display: inline-flex;
}

.current-resource-line {
  margin: 0 0 10px;
  color: var(--color-text);
  font-size: 13px;
  font-weight: 700;
}

.state-empty {
  display: grid;
  gap: 12px;
  max-width: 760px;
  margin: 40px auto;
  text-align: center;
}

.state-empty p {
  margin: 0;
  color: var(--color-text-secondary);
}

.missing-actions {
  display: flex;
  justify-content: center;
  gap: 10px;
}

.resource-alert {
  margin-bottom: 14px;
}

.data-status-alert {
  margin-bottom: 10px;
}

.resource-detail-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 360px);
  gap: 16px;
  align-items: start;
}

.content-panel {
  min-width: 0;
}

.content-panel :deep(.markdown-body) {
  max-width: 880px;
}

.content-panel :deep(.markdown-doc--tutorial .markdown-body) {
  max-width: 940px;
}

.evidence-panel {
  position: sticky;
  top: calc(var(--header-height) + 16px);
  max-height: calc(100vh - var(--header-height) - 32px);
  overflow: auto;
}

.generation-meta {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}

.generation-meta > div {
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.generation-meta strong {
  display: block;
  margin-bottom: 4px;
}

.generation-meta p {
  margin: 0;
  color: var(--color-text-secondary);
  word-break: break-word;
}

.debug-collapse {
  border-top: 0;
  border-bottom: 0;
}

.source-collapse,
.debug-collapse {
  border-top: 0;
  border-bottom: 0;
}

.source-collapse :deep(.el-collapse-item__header),
.debug-collapse :deep(.el-collapse-item__header) {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.mindmap-entry-panel,
.video-real-entry {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 14px;
  padding: 14px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
}

.mindmap-entry-panel div,
.video-real-entry div {
  display: grid;
  gap: 4px;
}

.mindmap-entry-panel span,
.video-real-entry span {
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.practice-submit-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 14px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.practice-submit-panel div {
  display: grid;
  gap: 4px;
}

.practice-submit-panel span {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.practice-result-panel {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
  padding: 14px;
  border: 1px solid #bbf7d0;
  border-radius: 8px;
  background: #f0fdf4;
}

.practice-result-panel div {
  display: grid;
  gap: 4px;
}

.practice-result-panel span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.practice-result-panel strong {
  color: var(--color-text);
  font-size: 20px;
}

.practice-result-panel p {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.practice-impact-panel {
  display: grid;
  gap: 10px;
  margin-top: 12px;
  padding: 14px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
}

.practice-impact-panel div:not(.practice-impact-actions) {
  display: grid;
  gap: 4px;
}

.practice-impact-panel span {
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 700;
}

.practice-impact-panel strong {
  color: var(--color-text);
  line-height: 1.6;
}

.practice-impact-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-top: 4px;
}

@media (max-width: 900px) {
  .resource-detail-layout {
    grid-template-columns: 1fr;
  }

  .evidence-panel {
    position: static;
    max-height: none;
  }

  .practice-submit-panel {
    align-items: stretch;
    flex-direction: column;
  }

  .mindmap-entry-panel,
  .video-real-entry {
    align-items: stretch;
    flex-direction: column;
  }

  .practice-result-panel {
    grid-template-columns: 1fr;
  }
}
</style>
