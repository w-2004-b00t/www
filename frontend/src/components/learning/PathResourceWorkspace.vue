<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  BookOpen,
  CheckCircle2,
  Clapperboard,
  ClipboardCheck,
  Code2,
  FileText,
  GitBranch,
  PlayCircle,
} from 'lucide-vue-next'
import type { Component } from 'vue'
import { submitResourcePracticeApi } from '../../api/resource'
import { RESOURCE_TYPE_LABELS } from '../../constants/resourceMeta'
import { useResourceStore } from '../../stores/resource'
import type { LearningPathStage, LearningResource, ResourcePracticeResult } from '../../types/common'
import { getResourceLearningState } from '../../utils/resourceLearningState'
import ExerciseViewer from '../resource/ExerciseViewer.vue'
import LabPracticeViewer from '../resource/LabPracticeViewer.vue'
import MarkdownViewer from '../resource/MarkdownViewer.vue'
import MindMapViewer from '../resource/MindMapViewer.vue'

const props = defineProps<{
  stage: LearningPathStage
  resources: LearningResource[]
}>()

const store = useResourceStore()
const activeResourceId = ref('')
const loadingResourceId = ref('')
const submittingResourceId = ref('')
const exerciseAnswers = reactive<Record<string, Record<string, string>>>({})
const practiceResults = reactive<Record<string, ResourcePracticeResult | null>>({})

const resourceIcons: Record<LearningResource['resourceType'], Component> = {
  explanation: FileText,
  mindmap: GitBranch,
  reading: BookOpen,
  video_script: Clapperboard,
  exercise: ClipboardCheck,
  lab: Code2,
}

const resourceOrder: Record<LearningResource['resourceType'], number> = {
  explanation: 1,
  mindmap: 2,
  reading: 3,
  video_script: 4,
  exercise: 5,
  lab: 6,
}

const orderedResources = computed(() =>
  props.resources
    .filter((resource) => resource.auditStatus === 'passed')
    .slice()
    .sort((left, right) => {
      const orderDelta = resourceOrder[left.resourceType] - resourceOrder[right.resourceType]
      return orderDelta || left.title.localeCompare(right.title, 'zh-Hans-CN')
    }),
)

const blockedResources = computed(() => props.resources.filter((resource) => resource.auditStatus !== 'passed'))
const activeResource = computed(() => orderedResources.value.find((resource) => resource.id === activeResourceId.value))

const completedCount = computed(() =>
  orderedResources.value.filter((resource) => resource.isCompleted || resource.isMastered).length,
)

const workflowText = computed(() => {
  const labels = orderedResources.value.map((resource) => RESOURCE_TYPE_LABELS[resource.resourceType])
  return labels.length ? labels.join(' -> ') + ' -> 阶段测评' : '等待已审核资源进入路径'
})

function resourceMinutes(resource: LearningResource) {
  const minutes = resource.metadata?.minutes
  if (typeof minutes === 'number' && Number.isFinite(minutes)) return `${minutes} 分钟`
  return ''
}

function difficultyLabel(resource: LearningResource) {
  const value = resource.metadata?.difficulty
  if (typeof value === 'string' && value.trim()) return value
  return ''
}

function qualityLabel(resource: LearningResource) {
  const score = resource.qualityScore
  if (typeof score === 'number' && Number.isFinite(score)) return `质量 ${score}`
  return ''
}

function recommendationReason(resource: LearningResource) {
  return resource.fitReason || props.stage.aiReason || '暂无推荐理由'
}

function progressLabel(resource: LearningResource) {
  return getResourceLearningState(resource).label
}

function progressType(resource: LearningResource) {
  const status = getResourceLearningState(resource).status
  if (status === 'mastered' || status === 'completed') return 'success'
  if (status === 'learning') return 'primary'
  return 'info'
}

function answeredCount(resourceId: string) {
  const answers = exerciseAnswers[resourceId] || {}
  return Object.keys(answers).filter((key) => answers[key]).length
}

function exerciseCount(resource: LearningResource) {
  if (resource.resourceType !== 'exercise') return 0
  try {
    const parsed = JSON.parse(resource.content)
    return Array.isArray(parsed) ? parsed.length : 0
  } catch {
    return 0
  }
}

async function openResource(resource: LearningResource) {
  activeResourceId.value = resource.id
  if (resource.isViewed || resource.isCompleted || resource.isMastered) return
  loadingResourceId.value = resource.id
  try {
    await store.viewResource(resource.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源学习状态记录失败')
  } finally {
    loadingResourceId.value = ''
  }
}

function toggleResource(resource: LearningResource) {
  activeResourceId.value = activeResourceId.value === resource.id ? '' : resource.id
}

async function completeResource(resource: LearningResource) {
  if (!resource.isViewed && !resource.isCompleted && !resource.isMastered) {
    ElMessage.warning('请先开始学习这份资源，再标记学完。')
    return
  }
  loadingResourceId.value = resource.id
  try {
    await store.completeResource(resource.id)
    ElMessage.success('已记录学完状态。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源完成状态保存失败')
  } finally {
    loadingResourceId.value = ''
  }
}

async function masterResource(resource: LearningResource) {
  if (!resource.isCompleted && !resource.isMastered) {
    ElMessage.warning('请先标记学完后再确认掌握。')
    return
  }
  loadingResourceId.value = resource.id
  try {
    await store.markResourceMastered(resource.id)
    ElMessage.success('已确认掌握，后续推荐会结合该状态更新。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源掌握状态保存失败')
  } finally {
    loadingResourceId.value = ''
  }
}

async function submitPractice(resource: LearningResource) {
  const total = exerciseCount(resource)
  const answered = answeredCount(resource.id)
  if (answered < total) {
    ElMessage.warning(`还有 ${total - answered} 道题未作答。`)
    return
  }
  submittingResourceId.value = resource.id
  try {
    practiceResults[resource.id] = await submitResourcePracticeApi(resource.id, {
      answers: exerciseAnswers[resource.id] || {},
    })
    await store.loadAll(true)
    ElMessage.success(`提交成功，得分 ${practiceResults[resource.id]?.score}，错题和路径提示已同步。`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '题库提交失败')
  } finally {
    submittingResourceId.value = ''
  }
}

</script>

<template>
  <section class="path-resource-workspace">
    <div class="workspace-head">
      <div>
        <strong>阶段资源学习</strong>
        <span>{{ workflowText }}</span>
      </div>
      <el-tag type="success" effect="plain">阶段总进度：{{ completedCount }}/{{ orderedResources.length }}</el-tag>
    </div>

    <el-alert
      v-if="blockedResources.length"
      class="workspace-alert"
      type="warning"
      show-icon
      :closable="false"
    >
      {{ blockedResources.length }} 份资源未通过审核或仍需复核，不能进入正式学习路径。
    </el-alert>

    <el-alert
      v-if="!orderedResources.length"
      type="warning"
      show-icon
      :closable="false"
    >
      当前阶段没有已审核的学习资料，不能展示学习内容。请先生成学习资料并通过审核。
    </el-alert>

    <div v-else class="resource-step-list" aria-label="路径资源学习顺序">
      <article
        v-for="(resource, index) in orderedResources"
        :key="resource.id"
        class="resource-step-card"
        :class="{ active: activeResource?.id === resource.id, completed: resource.isCompleted || resource.isMastered }"
      >
        <button
          :key="resource.id"
          type="button"
          class="resource-step"
          @click="toggleResource(resource)"
        >
          <span class="step-index">{{ index + 1 }}</span>
          <span class="step-icon">
            <component :is="resourceIcons[resource.resourceType]" :size="16" />
          </span>
          <span class="step-copy">
            <strong>{{ RESOURCE_TYPE_LABELS[resource.resourceType] }}</strong>
            <small>{{ resource.title }}</small>
          </span>
          <el-tag size="small" :type="progressType(resource)" effect="plain">{{ progressLabel(resource) }}</el-tag>
        </button>

        <div v-if="activeResource?.id === resource.id" class="resource-learning-panel">
          <div class="learning-head">
            <div>
              <span class="type-line">
                <component :is="resourceIcons[resource.resourceType]" :size="16" />
                {{ RESOURCE_TYPE_LABELS[resource.resourceType] }}
              </span>
              <h5>{{ resource.title }}</h5>
              <p>{{ resource.summary || '暂无资源摘要' }}</p>
            </div>
            <div class="learning-actions">
              <span class="current-resource-status">当前资源：{{ getResourceLearningState(resource).label }}</span>
              <el-tooltip
                :disabled="getResourceLearningState(resource).canStart"
                :content="getResourceLearningState(resource).startDisabledReason"
              >
                <span class="action-wrap">
                  <el-button
                    size="small"
                    type="primary"
                    :disabled="!getResourceLearningState(resource).canStart"
                    :loading="loadingResourceId === resource.id"
                    @click="openResource(resource)"
                  >
                    <PlayCircle :size="15" />
                    {{ getResourceLearningState(resource).startLabel }}
                  </el-button>
                </span>
              </el-tooltip>
              <el-tooltip
                :disabled="getResourceLearningState(resource).canComplete"
                :content="getResourceLearningState(resource).completeDisabledReason"
              >
                <span class="action-wrap">
                  <el-button
                    size="small"
                    :disabled="!getResourceLearningState(resource).canComplete"
                    :loading="loadingResourceId === resource.id"
                    @click="completeResource(resource)"
                  >
                    <CheckCircle2 :size="15" />
                    {{ getResourceLearningState(resource).completeLabel }}
                  </el-button>
                </span>
              </el-tooltip>
              <el-tooltip
                :disabled="getResourceLearningState(resource).canMaster"
                :content="getResourceLearningState(resource).masterDisabledReason"
              >
                <span class="action-wrap">
                  <el-button
                    size="small"
                    type="success"
                    plain
                    :disabled="!getResourceLearningState(resource).canMaster"
                    :loading="loadingResourceId === resource.id"
                    @click="masterResource(resource)"
                  >
                    <CheckCircle2 :size="15" />
                    {{ getResourceLearningState(resource).masterLabel }}
                  </el-button>
                </span>
              </el-tooltip>
            </div>
          </div>

          <div class="recommend-reason">
            <strong>为什么推送给你</strong>
            <span>{{ recommendationReason(resource) }}</span>
            <div v-if="resourceMinutes(resource) || difficultyLabel(resource) || qualityLabel(resource)" class="resource-meter">
              <span v-if="resourceMinutes(resource)">{{ resourceMinutes(resource) }}</span>
              <span v-if="difficultyLabel(resource)">{{ difficultyLabel(resource) }}</span>
              <span v-if="qualityLabel(resource)">{{ qualityLabel(resource) }}</span>
            </div>
          </div>

          <div class="resource-content-shell">
            <MindMapViewer
              v-if="resource.resourceType === 'mindmap'"
              :content="resource.content"
            />
            <ExerciseViewer
              v-else-if="resource.resourceType === 'exercise'"
              v-model="exerciseAnswers[resource.id]"
              :content="resource.content"
              :result-details="practiceResults[resource.id]?.details"
            />
            <LabPracticeViewer
              v-else-if="resource.resourceType === 'lab'"
              :resource="resource"
            />
            <section v-else-if="resource.resourceType === 'video_script'" class="video-path-panel">
              <div>
                <strong>视频演示学习</strong>
                <span>先阅读脚本和分镜，再进入本地视频页面生成或播放 MP4。</span>
              </div>
              <router-link to="/student/video-demo">
                <el-button type="primary">生成/播放本地 MP4</el-button>
              </router-link>
            </section>
            <MarkdownViewer
              v-if="resource.resourceType === 'explanation' || resource.resourceType === 'reading' || resource.resourceType === 'video_script'"
              :content="resource.content"
              :variant="resource.resourceType === 'explanation' ? 'tutorial' : 'default'"
            />
          </div>

          <section v-if="resource.resourceType === 'exercise'" class="practice-submit-panel">
            <div>
              <strong>题库练习</strong>
              <span>已作答 {{ answeredCount(resource.id) }} / {{ exerciseCount(resource) }}，提交后同步错题本、学习报告和路径提示。</span>
            </div>
            <el-button
              type="primary"
              :loading="submittingResourceId === resource.id"
              @click="submitPractice(resource)"
            >
              提交本资源练习
            </el-button>
          </section>

          <section v-if="practiceResults[resource.id]" class="practice-result-panel">
            <div><span>得分</span><strong>{{ practiceResults[resource.id]?.score }}</strong></div>
            <div><span>正确题数</span><strong>{{ practiceResults[resource.id]?.correctCount }} / {{ practiceResults[resource.id]?.total }}</strong></div>
            <p>{{ practiceResults[resource.id]?.suggestion }}{{ practiceResults[resource.id]?.pathImpact }}</p>
          </section>

          <div class="resource-footer">
            <span>{{ progressLabel(resource) }}</span>
            <router-link :to="`/student/resources/${resource.id}`">打开完整资源详情</router-link>
          </div>
        </div>
      </article>

      <router-link to="/student/assessment" class="assessment-step">
        <ClipboardCheck :size="16" />
        <span>阶段测评</span>
      </router-link>
    </div>
  </section>
</template>

<style scoped>
.path-resource-workspace {
  display: grid;
  gap: 12px;
  min-width: 0;
  padding-top: 4px;
}

.workspace-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.workspace-head div {
  display: grid;
  gap: 4px;
}

.workspace-head strong {
  color: var(--color-text);
  font-size: 16px;
}

.workspace-head span,
.resource-footer,
.practice-submit-panel span,
.video-path-panel span {
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.workspace-alert {
  margin: 0;
}

.resource-step-list {
  display: grid;
  gap: 10px;
  min-width: 0;
  padding: 14px;
  overflow: hidden;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
}

.resource-step-card {
  display: grid;
  min-width: 0;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
}

.resource-step-card.active {
  border-color: #38bdf8;
  background: rgba(14, 165, 233, 0.18);
}

.resource-step-card.completed {
  border-color: rgba(134, 239, 172, 0.5);
  background: rgba(34, 197, 94, 0.14);
}

.resource-step {
  display: grid;
  grid-template-columns: 26px 30px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  width: 100%;
  min-width: 0;
  padding: 12px;
  border: 0;
  background: transparent;
  color: #e2e8f0;
  text-align: left;
  cursor: pointer;
}

.step-index,
.step-icon {
  display: inline-grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border-radius: 999px;
  background: rgba(191, 219, 254, 0.16);
  color: #93c5fd;
  font-size: 12px;
  font-weight: 700;
}

.step-copy {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.step-copy strong,
.step-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.step-copy small {
  color: #94a3b8;
  font-size: 12px;
}

.resource-step :deep(.el-tag) {
  grid-column: 3;
  width: fit-content;
  margin-top: 2px;
  background: rgba(255, 255, 255, 0.92);
}

.assessment-step {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  border: 1px dashed rgba(147, 197, 253, 0.62);
  border-radius: 8px;
  color: #bfdbfe;
  text-decoration: none;
}

.resource-learning-panel {
  display: grid;
  gap: 14px;
  min-width: 0;
  padding: 18px;
  border-top: 1px solid rgba(148, 163, 184, 0.22);
  background: #fff;
}

.learning-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.type-line {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #2563eb;
  font-size: 13px;
  font-weight: 700;
}

h5 {
  margin: 6px 0;
  color: var(--color-text);
  font-size: 18px;
  line-height: 1.35;
}

.learning-head p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.65;
}

.learning-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.action-wrap {
  display: inline-flex;
}

.current-resource-status {
  align-self: center;
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 700;
}

.recommend-reason,
.video-path-panel,
.practice-submit-panel,
.practice-result-panel {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  padding: 12px;
  border: 1px solid #ccfbf1;
  border-radius: 8px;
  background: #f0fdfa;
}

.recommend-reason {
  align-items: flex-start;
}

.recommend-reason span {
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.resource-meter {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 6px;
  max-width: 260px;
}

.resource-meter span {
  padding: 3px 7px;
  color: #0f766e;
  border: 1px solid #99f6e4;
  border-radius: 999px;
  background: #ffffff;
  font-size: 12px;
  font-weight: 700;
}

.resource-content-shell {
  min-width: 0;
  padding-top: 2px;
}

.video-path-panel {
  margin-bottom: 14px;
}

.video-path-panel div,
.practice-submit-panel div {
  display: grid;
  gap: 4px;
}

.practice-result-panel {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 140px)) minmax(0, 1fr);
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
  font-size: 20px;
}

.practice-result-panel p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.resource-footer {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--color-border);
}

.resource-footer a {
  color: #2563eb;
  text-decoration: none;
}

@media (max-width: 980px) {
  .practice-result-panel,
  .recommend-reason {
    grid-template-columns: 1fr;
  }

  .learning-head,
  .video-path-panel,
  .practice-submit-panel {
    display: grid;
  }

  .learning-actions {
    justify-content: flex-start;
  }
}
</style>
