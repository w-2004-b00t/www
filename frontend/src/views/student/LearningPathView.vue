<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ClipboardCheck,
  Database,
  FileSearch,
  RefreshCw,
} from 'lucide-vue-next'
import LearningPathTimeline from '../../components/learning/LearningPathTimeline.vue'
import { useOnboardingStore } from '../../stores/onboarding'
import { useResourceStore } from '../../stores/resource'
import type { LearningPath } from '../../types/common'

const resource = useResourceStore()
const onboarding = useOnboardingStore()
const generating = ref(false)

const hasFormalPath = computed(() => resource.learningPath.status === 'ready' && resource.learningPath.stages.length > 0)
const activeStage = computed(() => resource.learningPath.stages.find(
  (stage) => stage.status === 'active' || stage.status === 'awaiting_assessment',
))
const canStartAssessment = computed(() => Boolean(activeStage.value && hasFormalPath.value))
const totalDays = computed(() => resource.learningPath.stages.reduce((sum, stage) => sum + Number(stage.days || 0), 0))
const nextTopic = computed(() => resource.nextTopic)
const nextTopicBlocked = computed(() => Boolean(nextTopic.value.blocked || nextTopic.value.status === 'blocked'))
const completedStageCount = computed(() => resource.learningPath.stages.filter((stage) => stage.status === 'completed' || stage.isCompleted || stage.isMastered).length)
const linkedResourceIds = computed(() => new Set(resource.learningPath.stages.flatMap((stage) => stage.resources || [])))
const resourceCoverage = computed(() => {
  const coverage = resource.learningPath.resourceCoverage
  const approvedIds = resource.allResources.filter((item) => item.auditStatus === 'passed').map((item) => item.id)
  const computedCoverage = {
    approvedTotal: approvedIds.length,
    linkedTotal: approvedIds.filter((id) => linkedResourceIds.value.has(id)).length,
    pendingTotal: resource.allResources.filter((item) => item.auditStatus !== 'passed').length,
    unlinkedResourceIds: approvedIds.filter((id) => !linkedResourceIds.value.has(id)),
  }
  if (!coverage) return computedCoverage
  if (approvedIds.length && coverage.approvedTotal === 0) return computedCoverage
  return coverage
})
const totalPathResources = computed(() => {
  const ids = new Set(resource.learningPath.stages.flatMap((stage) => stage.resources || []))
  return ids.size || resourceCoverage.value.linkedTotal
})
const completedPathResources = computed(() => {
  const pathIds = new Set(resource.learningPath.stages.flatMap((stage) => stage.resources || []))
  const completedIds = new Set([
    ...resource.learningProgress.completedResourceIds,
    ...resource.learningProgress.masteredResourceIds,
  ])
  if (!pathIds.size) return 0
  return [...pathIds].filter((id) => completedIds.has(id)).length
})
const currentStageResourceCount = computed(() => activeStage.value?.resources?.length || 0)
const pathResourceLabel = computed(() => {
  if (!hasFormalPath.value) return '生成后开始'
  if (!totalPathResources.value) return '等待资源'
  return `${completedPathResources.value}/${totalPathResources.value} 已学完`
})
const nextStepLabel = computed(() => {
  if (!hasFormalPath.value) return '生成学习资料'
  if (nextTopicBlocked.value) return '继续当前阶段'
  return nextTopic.value.topic || nextTopic.value.chapterName || activeStage.value?.name || '继续学习'
})

const statusInfo = computed(() => {
  if (generating.value) return { label: '正在生成', type: 'warning' as const }
  if (hasFormalPath.value) return { label: '学习中', type: 'success' as const }
  return { label: '待生成', type: 'warning' as const }
})

const intensity = computed({
  get: () => resource.learningPath.intensity || '60min',
  set: (value: LearningPath['intensity']) => {
    resource.updateIntensity(value)
      .then(() => ElMessage.success('学习强度已保存。'))
      .catch((error) => ElMessage.error(error instanceof Error ? error.message : '学习强度保存失败'))
  },
})

const intensityLabel = computed(() => {
  if (intensity.value === 'sprint') return '考前冲刺'
  if (intensity.value === '30min') return '每天 30 分钟'
  return '每天 60 分钟'
})

const currentActionTitle = computed(() => {
  if (!hasFormalPath.value) return '先生成学习资料'
  return activeStage.value?.name || '继续当前学习阶段'
})

const currentActionText = computed(() => {
  if (!hasFormalPath.value) {
    return resource.learningPath.blockingReason || '生成通过审核的学习资料后，系统会自动安排到学习路径中。'
  }
  return `本阶段包含 ${currentStageResourceCount.value} 份学习资料。按顺序学完后进入阶段测评。`
})

async function generatePath() {
  generating.value = true
  try {
    const path = await resource.generateLearningPath()
    if (path.status === 'blocked') {
      ElMessage.warning(path.blockingReason || '暂时无法生成正式路径。')
    } else {
      await resource.loadAll(true)
      ElMessage.success('学习路径已更新，已审核资料会自动进入路径。')
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '学习路径生成失败')
  } finally {
    generating.value = false
  }
}

async function completeStage(stageId: string) {
  try {
    await resource.completeStage(stageId)
    ElMessage.success('阶段完成状态已保存。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '阶段完成状态保存失败')
  }
}

async function markStageMastered(stageId: string) {
  try {
    await resource.markStageMastered(stageId)
    ElMessage.success('已记录掌握状态，后续推荐会跳过已掌握知识点。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '掌握状态保存失败')
  }
}

onMounted(() => {
  onboarding.loadCourses()
  resource.loadAll(true)
})
</script>

<template>
  <div class="learning-path-page">
    <section class="mission-command" :class="{ empty: !hasFormalPath }">
      <div class="mission-copy">
        <span class="mission-kicker">学习作战台</span>
        <h1>{{ currentActionTitle }}</h1>
        <p>{{ currentActionText }}</p>
        <div class="mission-flow" aria-label="学习路径生成流程">
          <span>画像</span>
          <span>资料</span>
          <span>资源</span>
          <span>路径</span>
          <span>测评</span>
          <span>补强</span>
        </div>
      </div>

      <div class="mission-control">
        <div class="status-tile">
          <span>路径状态</span>
          <strong>{{ statusInfo.label }}</strong>
          <small>{{ pathResourceLabel }}</small>
        </div>
        <div class="status-tile accent">
          <span>下一步</span>
          <strong>{{ nextStepLabel }}</strong>
          <small>{{ intensityLabel }}</small>
        </div>
        <div class="mission-actions">
          <el-button :loading="generating" @click="generatePath">
            <RefreshCw :size="16" />
            {{ hasFormalPath ? '重新生成' : '生成路径' }}
          </el-button>
          <router-link v-if="!hasFormalPath" to="/student/profile-chat">
            <el-button>确认画像</el-button>
          </router-link>
          <router-link v-if="!hasFormalPath" to="/student/resource-generate">
            <el-button type="primary">
              <FileSearch :size="16" />
              生成资源
            </el-button>
          </router-link>
          <router-link v-else-if="canStartAssessment" to="/student/assessment">
            <el-button type="primary">
              <ClipboardCheck :size="16" />
              阶段测评
            </el-button>
          </router-link>
        </div>
      </div>
    </section>

    <el-alert v-if="resource.lastError" class="top-alert" type="warning" show-icon :closable="false">
      {{ resource.lastError }}
    </el-alert>

    <section class="mission-metrics" aria-label="路径概览">
      <div>
        <span>当前课程</span>
        <strong>{{ onboarding.selectedCourse?.name || '数据结构课程' }}</strong>
      </div>
      <div>
        <span>学习目标</span>
        <strong>{{ onboarding.studyGoal || '暂无已确认学习目标' }}</strong>
      </div>
      <div>
        <span>预计完成</span>
        <strong>{{ hasFormalPath ? `${totalDays} 天` : '资源生成后计算' }}</strong>
      </div>
      <div>
        <span>学习资料</span>
        <strong>{{ pathResourceLabel }}</strong>
      </div>
      <div>
        <span>阶段进度</span>
        <strong>{{ completedStageCount }}/{{ resource.learningPath.stages.length }}</strong>
      </div>
    </section>

    <section v-if="!hasFormalPath" class="empty-path-callout">
      <Database :size="32" />
      <div>
        <h2>{{ resource.learningPath.title || '等待正式学习路径' }}</h2>
        <p>{{ resource.learningPath.blockingReason || resource.learningPath.summary || '生成学习资料后即可规划学习路径。' }}</p>
      </div>
      <div class="empty-actions">
        <el-button type="primary" :loading="generating" @click="generatePath">生成资源并规划路径</el-button>
        <router-link to="/student/resource-generate"><el-button>生成学习资料</el-button></router-link>
      </div>
    </section>

    <LearningPathTimeline
      v-if="hasFormalPath"
      :path="resource.learningPath"
      :resources="resource.allResources"
      @complete="completeStage"
      @mastery="markStageMastered"
    />
    <section v-else class="guide-panel">
      <Database :size="30" />
      <h2>还没有正式学习路径</h2>
      <p>生成通过审核的学习资料后，系统会自动安排学习顺序。资料不足时不会用假路径填充页面。</p>
    </section>
  </div>
</template>

<style scoped>
.learning-path-page {
  display: grid;
  gap: 18px;
  max-width: 1520px;
  margin: 0 auto;
  padding: 24px 28px 38px;
}

.mission-command {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 380px);
  gap: 22px;
  min-height: 260px;
  padding: 28px;
  color: #f8fafc;
  border: 1px solid rgba(148, 163, 184, 0.28);
  border-radius: 12px;
  background:
    linear-gradient(135deg, rgba(15, 23, 42, 0.98), rgba(30, 64, 175, 0.92) 58%, rgba(15, 118, 110, 0.9)),
    radial-gradient(circle at 16% 16%, rgba(56, 189, 248, 0.3), transparent 34%);
  box-shadow: 0 24px 52px rgba(15, 23, 42, 0.22);
}

.mission-command.empty {
  background:
    linear-gradient(135deg, rgba(24, 24, 27, 0.97), rgba(120, 53, 15, 0.9) 62%, rgba(15, 23, 42, 0.95)),
    radial-gradient(circle at 16% 16%, rgba(251, 191, 36, 0.28), transparent 34%);
}

.mission-copy {
  display: grid;
  align-content: center;
  gap: 14px;
  min-width: 0;
}

.mission-kicker {
  width: fit-content;
  padding: 5px 10px;
  color: #bfdbfe;
  font-size: 12px;
  font-weight: 800;
  border: 1px solid rgba(191, 219, 254, 0.28);
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.24);
}

.mission-copy h1 {
  max-width: 820px;
  margin: 0;
  color: #ffffff;
  font-size: 36px;
  line-height: 1.2;
  letter-spacing: 0;
  overflow-wrap: anywhere;
}

.mission-copy p {
  max-width: 820px;
  margin: 0;
  color: #dbeafe;
  font-size: 15px;
  line-height: 1.8;
  overflow-wrap: anywhere;
}

.mission-flow {
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
  margin-top: 6px;
}

.mission-flow span {
  display: inline-flex;
  align-items: center;
  min-height: 30px;
  padding: 5px 12px;
  color: #ecfeff;
  font-size: 12px;
  font-weight: 800;
  border: 1px solid rgba(125, 211, 252, 0.28);
  border-radius: 999px;
  background: rgba(8, 47, 73, 0.34);
}

.mission-control {
  display: grid;
  gap: 12px;
  align-content: center;
}

.status-tile {
  display: grid;
  gap: 6px;
  min-width: 0;
  padding: 16px;
  border: 1px solid rgba(191, 219, 254, 0.22);
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.28);
}

.status-tile.accent {
  background: rgba(13, 148, 136, 0.2);
}

.status-tile span,
.status-tile small {
  color: #bfdbfe;
  font-size: 12px;
}

.status-tile strong {
  color: #ffffff;
  font-size: 18px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.mission-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.mission-actions :deep(.el-button) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 36px;
}

.mission-metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  overflow: hidden;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  background: #ffffff;
  box-shadow: 0 12px 26px rgba(15, 23, 42, 0.07);
}

.mission-metrics div {
  display: grid;
  gap: 5px;
  min-height: 74px;
  padding: 14px 16px;
  border-right: 1px solid #e2e8f0;
}

.mission-metrics div:last-child {
  border-right: 0;
}

.mission-metrics span {
  color: #64748b;
  font-size: 12px;
}

.mission-metrics strong {
  overflow: hidden;
  color: #0f172a;
  font-size: 15px;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-path-callout,
.guide-panel {
  display: grid;
  justify-items: center;
  gap: 12px;
  min-height: 260px;
  padding: 28px;
  text-align: center;
  border: 1px dashed #94a3b8;
  border-radius: 12px;
  background:
    linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
}

.empty-path-callout {
  grid-template-columns: auto minmax(0, 1fr) auto;
  justify-items: start;
  align-items: center;
  min-height: 0;
  text-align: left;
}

.empty-path-callout h2,
.guide-panel h2 {
  margin: 0;
  color: #0f172a;
  font-size: 20px;
}

.empty-path-callout p,
.guide-panel p {
  max-width: 680px;
  margin: 6px 0 0;
  color: #64748b;
  line-height: 1.7;
}

.support-deck {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(280px, 0.9fr) minmax(260px, 0.8fr) minmax(260px, 0.8fr);
  gap: 14px;
}

.support-card {
  display: grid;
  align-content: start;
  gap: 14px;
  min-width: 0;
  padding: 16px;
  border: 1px solid #dbe3ef;
  border-radius: 10px;
  background: #ffffff;
  box-shadow: 0 10px 22px rgba(15, 23, 42, 0.06);
}

.evidence-card {
  background: linear-gradient(180deg, #ffffff 0%, #f0fdfa 100%);
}

.diagnostics-card {
  background: #f8fafc;
}

.support-title {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  min-width: 0;
}

.support-title svg {
  flex-shrink: 0;
  margin-top: 2px;
  color: #2563eb;
}

.support-title div {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.support-title strong {
  color: #0f172a;
  font-size: 16px;
}

.support-title span,
.support-card p,
.empty-log,
.log-list span,
.log-list small {
  color: #64748b;
  font-size: 13px;
  line-height: 1.65;
  overflow-wrap: anywhere;
}

.support-card :deep(.el-segmented) {
  max-width: 100%;
}

.basis-list,
.evidence-list,
.side-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.basis-list :deep(.el-tag),
.evidence-list :deep(.el-tag),
.side-tags :deep(.el-tag) {
  height: auto;
  min-height: 24px;
  white-space: normal;
}

.log-list {
  display: grid;
  gap: 10px;
}

.log-list div {
  display: grid;
  gap: 4px;
  padding-bottom: 10px;
  border-bottom: 1px solid #e2e8f0;
}

.log-list div:last-child {
  padding-bottom: 0;
  border-bottom: 0;
}

.log-list strong {
  color: #0f172a;
  font-size: 13px;
}

.top-alert {
  margin: 0;
}

.top-alert :deep(.el-alert__content) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}

.alert-action {
  flex-shrink: 0;
}

.page-header > div:first-child {
  display: grid;
  gap: 8px;
}

.header-actions,
.empty-actions,
.button-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
}

.header-actions :deep(.el-button),
.button-row :deep(.el-button) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.current-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 18px;
  margin-bottom: 16px;
  border-color: #cfe0ff;
  background: linear-gradient(135deg, #ffffff 0%, #f2f6ff 58%, #fff8ec 100%);
}

.current-panel.empty {
  border-color: #f4d18f;
  background: linear-gradient(135deg, #ffffff 0%, #fff7e8 100%);
}

.current-main {
  display: grid;
  align-content: start;
  gap: 10px;
}

.current-main h2 {
  margin: 0;
  font-size: 22px;
  line-height: 1.35;
}

.current-main p {
  max-width: 780px;
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.workflow-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
}

.workflow-line span {
  min-height: 28px;
  padding: 4px 10px;
  color: var(--color-primary-strong);
  font-size: 12px;
  font-weight: 700;
  border: 1px solid #cfe0ff;
  border-radius: 999px;
  background: #eef4ff;
}

.current-actions {
  display: grid;
  gap: 10px;
}

.action-stat {
  display: grid;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.action-stat span,
.action-stat small {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.action-stat strong {
  overflow-wrap: anywhere;
  font-size: 15px;
  line-height: 1.5;
}

.empty-panel {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.basis-panel,
.path-layout {
  margin-bottom: 16px;
}

.basis-panel,
.diagnostics-panel {
  padding: 0 16px;
}

.basis-panel :deep(.el-collapse),
.diagnostics-panel :deep(.el-collapse) {
  border: 0;
}

.basis-panel :deep(.el-collapse-item__header),
.basis-panel :deep(.el-collapse-item__wrap),
.diagnostics-panel :deep(.el-collapse-item__header),
.diagnostics-panel :deep(.el-collapse-item__wrap) {
  border-bottom: 0;
  background: transparent;
}

.collapse-title {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 8px;
}

.collapse-title span {
  color: var(--color-text);
  font-weight: 700;
}

.collapse-title small {
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.basis-body {
  display: grid;
  gap: 14px;
  padding-bottom: 16px;
}

.basis-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.basis-stats div {
  display: grid;
  gap: 5px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fbfcff;
}

.basis-stats span,
.path-facts span,
.log-list span,
.log-list small,
.diagnostics-body span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.basis-list,
.evidence-list,
.side-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.basis-group {
  display: grid;
  gap: 8px;
}

.basis-group > strong {
  color: #0f172a;
  font-size: 13px;
}

.basis-group p {
  margin: 0;
}

.evidence-list {
  align-items: center;
}

.evidence-list strong {
  margin-right: 4px;
}

.path-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  align-items: start;
  gap: 18px;
}

.path-main,
.side-stack {
  min-width: 0;
}

.side-stack {
  display: grid;
  align-content: start;
  gap: 14px;
}

.guide-panel {
  display: grid;
  place-items: center;
  min-height: 320px;
  text-align: center;
}

.guide-panel h2 {
  margin: 10px 0 0;
}

.guide-panel p {
  max-width: 540px;
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.path-facts {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

.path-facts div {
  display: grid;
  gap: 4px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--color-border);
}

.path-facts div:last-child {
  padding-bottom: 0;
  border-bottom: 0;
}

.path-facts strong,
.log-list span,
.log-list small {
  overflow-wrap: anywhere;
}

.side-button {
  width: 100%;
  margin-top: 14px;
}

.compact {
  padding: 14px;
}

.log-list {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

.log-list div {
  display: grid;
  gap: 4px;
}

.diagnostics-body {
  display: grid;
  gap: 10px;
  padding-bottom: 16px;
}

.diagnostics-body div:not(.chunk-list) {
  display: grid;
  gap: 4px;
}

.diagnostics-body p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.chunk-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chunk-list span {
  flex-basis: 100%;
}

.chunk-list code {
  max-width: 100%;
  padding: 3px 6px;
  color: var(--color-primary-strong);
  border: 1px solid #cfe0ff;
  border-radius: 6px;
  background: #eef4ff;
  font-size: 12px;
  overflow-wrap: anywhere;
}

@media (max-width: 1180px) {
  .mission-command,
  .support-deck {
    grid-template-columns: 1fr;
  }

  .mission-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .mission-metrics div {
    border-right: 1px solid #e2e8f0;
    border-bottom: 1px solid #e2e8f0;
  }

  .mission-metrics div:nth-child(2n) {
    border-right: 0;
  }

  .current-panel,
  .path-layout,
  .basis-stats {
    grid-template-columns: 1fr;
  }

  .header-actions,
  .empty-actions,
  .button-row {
    justify-content: flex-start;
  }

  .empty-panel {
    flex-direction: column;
  }
}

@media (max-width: 760px) {
  .learning-path-page {
    padding: 16px;
  }

  .mission-command {
    min-height: 0;
    padding: 20px;
  }

  .mission-copy h1 {
    font-size: 28px;
  }

  .mission-metrics,
  .empty-path-callout {
    grid-template-columns: 1fr;
  }

  .mission-metrics div,
  .mission-metrics div:nth-child(2n) {
    border-right: 0;
  }

  .empty-path-callout {
    justify-items: center;
    text-align: center;
  }

  .collapse-title {
    align-items: flex-start;
  }

  .collapse-title small {
    white-space: normal;
  }
}
</style>
