<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Clock3, FileSearch, GitBranch, ShieldCheck, Workflow } from 'lucide-vue-next'
import AgentProgress from '../../components/agent/AgentProgress.vue'
import { listTasksApi } from '../../api/admin'
import type { AgentStep, GenerationTask } from '../../types/common'

const router = useRouter()
const activePanel = ref<string[]>([])
const tasks = ref<GenerationTask[]>([])
const loadError = ref('')
const selectedId = ref('')
const selectedTask = computed(() => tasks.value.find((task) => task.id === selectedId.value) || tasks.value[0] || null)
const currentStep = computed(() => selectedTask.value?.agentSteps.find((item) => item.name === selectedTask.value?.currentAgent))
const completedCount = computed(() => selectedTask.value?.agentSteps.filter((step) => step.status === 'success').length || 0)
const failedCount = computed(() => tasks.value.filter((task) => task.status === 'failed').length)
const runningCount = computed(() => tasks.value.filter((task) => task.status === 'running').length)
const successCount = computed(() => tasks.value.filter((task) => task.status === 'success').length)
const warningCount = computed(() => tasks.value.reduce((sum, task) => sum + Number(task.outputPayload?.audit_warning || 0), 0))
const selectedErrorDetail = computed(() => selectedTask.value?.outputPayload?.errorDetail)
const selectedSuggestedActions = computed(() => selectedErrorDetail.value?.suggestedActions || selectedTask.value?.outputPayload?.next_actions || [])
const currentEvidence = computed(() => {
  const step = currentStep.value
  return [
    { icon: FileSearch, label: '输入', value: selectedTask.value?.inputPayload?.topic || selectedTask.value?.topic || '等待输入' },
    { icon: Workflow, label: '当前 Agent', value: step?.title || '等待调度' },
    { icon: ShieldCheck, label: '审核风险', value: `${selectedTask.value?.outputPayload?.audit_warning || 0} 个风险资源` },
    { icon: GitBranch, label: '下游影响', value: selectedTask.value?.outputPayload?.next_action || '等待生成结果' },
  ]
})

async function loadTasks() {
  try {
    const result = await listTasksApi()
    tasks.value = result.map((item) => ({
      ...item,
      agentSteps: item.agentSteps?.length ? item.agentSteps : [],
    }))
    selectedId.value = tasks.value[0]?.id || ''
    loadError.value = ''
  } catch (error) {
    tasks.value = []
    selectedId.value = ''
    loadError.value = error instanceof Error ? error.message : '任务列表加载失败。'
  }
}

function selectTask(row: GenerationTask) {
  selectedId.value = row.id
  activePanel.value = []
}

function taskStatusText(status: GenerationTask['status']) {
  const map = {
    pending: '等待中',
    running: '运行中',
    success: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return map[status]
}

function statusType(status: GenerationTask['status']) {
  if (status === 'success') return 'success'
  if (status === 'running') return 'primary'
  if (status === 'failed') return 'danger'
  return 'info'
}

function stepStatusText(status?: AgentStep['status']) {
  if (status === 'success') return '已完成'
  if (status === 'running') return '运行中'
  if (status === 'failed') return '失败'
  return '等待中'
}

onMounted(loadTasks)
</script>

<template>
  <div class="page task-manage-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">智能体任务运维</h1>
        <p class="page-subtitle">按任务查看多智能体执行状态、失败原因、引用证据和下游影响；完整审计信息可展开。</p>
      </div>
      <div class="head-actions">
        <el-button @click="router.push('/student/resource-generate')">新建资源生成任务</el-button>
        <el-button type="primary" @click="activePanel = activePanel.length ? [] : ['audit']">展开完整审计</el-button>
      </div>
    </div>

    <section class="task-overview">
      <div class="overview-card">
        <span>运行中</span>
        <strong>{{ runningCount }}</strong>
        <small>正在执行 Agent 链路</small>
      </div>
      <div class="overview-card success">
        <span>已完成</span>
        <strong>{{ successCount }}</strong>
        <small>结果已落库</small>
      </div>
      <div class="overview-card warning">
        <span>风险资源</span>
        <strong>{{ warningCount }}</strong>
        <small>等待教师复核</small>
      </div>
      <div class="overview-card danger">
        <span>失败任务</span>
        <strong>{{ failedCount }}</strong>
        <small>支持单步重试</small>
      </div>
    </section>

    <div class="ops-layout">
      <section class="panel task-queue">
        <div class="panel-title-line">
          <h2 class="section-title">任务队列</h2>
          <el-tag effect="plain">{{ tasks.length }} 条</el-tag>
        </div>
        <el-alert v-if="loadError" class="task-alert" type="warning" show-icon :closable="false">
          {{ loadError }}
        </el-alert>
        <div v-if="!tasks.length" class="empty-task-state">
          <Clock3 :size="18" />
          <span>暂无真实任务记录。创建资源生成任务后，这里会显示后端返回的 Agent 执行轨迹。</span>
        </div>
        <div class="task-list">
          <button
            v-for="item in tasks"
            :key="item.id"
            class="task-list-item"
            :class="{ active: item.id === selectedId }"
            @click="selectTask(item)"
          >
            <div>
              <el-tag :type="statusType(item.status)" effect="plain">{{ taskStatusText(item.status) }}</el-tag>
              <strong>{{ item.topic || item.inputPayload?.topic }}</strong>
              <span>{{ item.id }} · {{ item.courseName || '数据结构课程' }}</span>
            </div>
            <small>{{ item.progress }}%</small>
          </button>
        </div>
      </section>

      <section v-if="selectedTask" class="panel current-task-panel">
        <div class="current-head">
          <div>
            <span class="status-pill">当前任务</span>
            <h2>{{ selectedTask.topic || selectedTask.inputPayload?.topic }}</h2>
            <p>{{ selectedTask.message }}</p>
          </div>
          <el-tag :type="statusType(selectedTask.status)" effect="plain">{{ taskStatusText(selectedTask.status) }}</el-tag>
        </div>
        <div class="progress-line">
          <el-progress :percentage="selectedTask.progress" />
          <span>{{ completedCount }} / {{ selectedTask.agentSteps.length }} 个 Agent 已完成</span>
        </div>
        <div class="evidence-grid">
          <div v-for="item in currentEvidence" :key="item.label">
            <component :is="item.icon" :size="18" />
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </div>
        <div class="step-strip">
          <div v-for="step in selectedTask.agentSteps" :key="step.name" :class="step.status">
            <span>{{ step.title.replace('智能体', '') }}</span>
            <strong>{{ stepStatusText(step.status) }}</strong>
          </div>
        </div>
      </section>

      <section v-else class="panel current-task-panel empty-current-task">
        <Clock3 :size="22" />
        <h2>等待真实任务</h2>
        <p>当前没有可审计的资源生成任务。系统不会在这里填充静态样例。</p>
      </section>

      <aside v-if="selectedTask" class="panel task-side-panel">
        <h2 class="section-title">处理建议</h2>
        <el-alert v-if="selectedErrorDetail" class="task-alert" type="warning" show-icon :closable="false">
          {{ selectedErrorDetail.reasonCode || selectedErrorDetail.code || 'generation_failed' }}：{{ selectedErrorDetail.rawFailure || selectedErrorDetail.detail || selectedTask.outputPayload?.error }}
        </el-alert>
        <div class="side-block">
          <Clock3 :size="18" />
          <div>
            <strong>{{ selectedTask.status === 'failed' ? '先处理失败 Agent' : '优先查看审核风险' }}</strong>
            <p>{{ selectedTask.status === 'failed' ? '查看失败原因码、课程引用噪声和模型返回详情后再重试。' : '有风险资源不会进入学生端正式推荐，需要教师复核。' }}</p>
          </div>
        </div>
        <ul v-if="selectedSuggestedActions.length" class="suggestion-list">
          <li v-for="item in selectedSuggestedActions" :key="String(item)">{{ item }}</li>
        </ul>
        <div class="side-actions">
          <el-button v-if="selectedTask.status === 'failed'" type="warning">重试失败 Agent</el-button>
          <el-button @click="router.push('/admin/audit')">进入资源审核</el-button>
          <el-button @click="router.push('/admin/documents')">查看知识库资料</el-button>
        </div>
      </aside>
    </div>

    <el-collapse v-if="selectedTask" v-model="activePanel" class="audit-collapse">
      <el-collapse-item name="audit">
        <template #title>
          <strong>完整 Agent 工程审计</strong>
          <span>输入、工具、输出、置信度、引用、失败处理、下游交接</span>
        </template>
        <AgentProgress :task="selectedTask" />
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<style scoped>
.task-manage-page {
  max-width: none;
}

.head-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.task-overview {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}

.overview-card {
  display: grid;
  gap: 4px;
  padding: 16px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.overview-card span,
.overview-card small,
.task-list-item span,
.progress-line span,
.side-block p {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.overview-card strong {
  font-size: 28px;
  line-height: 1.1;
}

.overview-card.success {
  background: #f7fef9;
}

.overview-card.warning {
  background: #fffaf5;
}

.overview-card.danger {
  background: #fef2f2;
}

.ops-layout {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr) 340px;
  gap: 16px;
  align-items: start;
}

.panel-title-line,
.current-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.task-list {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.task-alert {
  margin-top: 12px;
}

.empty-task-state,
.empty-current-task {
  display: grid;
  gap: 8px;
  place-items: center;
  min-height: 148px;
  padding: 18px;
  color: var(--color-text-secondary);
  text-align: center;
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.empty-current-task h2,
.empty-current-task p {
  margin: 0;
}

.task-list-item {
  display: flex;
  width: 100%;
  justify-content: space-between;
  gap: 12px;
  padding: 13px;
  text-align: left;
  cursor: pointer;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.task-list-item.active {
  border-color: #bfdbfe;
  background: #eff6ff;
}

.task-list-item div {
  display: grid;
  gap: 7px;
  min-width: 0;
}

.task-list-item strong {
  overflow: hidden;
  color: var(--color-text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-list-item small {
  color: var(--color-primary);
  font-weight: 700;
}

.current-task-panel {
  display: grid;
  gap: 14px;
}

.current-head h2 {
  margin: 10px 0 6px;
  font-size: 22px;
}

.current-head p {
  margin: 0;
  color: var(--color-text-secondary);
}

.progress-line {
  display: grid;
  gap: 7px;
}

.evidence-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.evidence-grid div {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 5px 8px;
  min-width: 0;
  padding: 12px;
  border-right: 1px solid var(--color-border);
}

.evidence-grid div:last-child {
  border-right: 0;
}

.evidence-grid span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.evidence-grid strong {
  grid-column: 1 / -1;
  overflow: hidden;
  color: var(--color-text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.step-strip {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 8px;
}

.step-strip div {
  display: grid;
  gap: 5px;
  min-height: 66px;
  padding: 10px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.step-strip div.success {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.step-strip div.running {
  border-color: #bfdbfe;
  background: #eff6ff;
}

.step-strip div.failed {
  border-color: #fecaca;
  background: #fef2f2;
}

.step-strip span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.step-strip strong {
  font-size: 13px;
}

.task-side-panel {
  display: grid;
  gap: 14px;
}

.side-block {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px;
  padding: 12px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
}

.side-block p {
  margin: 5px 0 0;
  line-height: 1.6;
}

.side-actions {
  display: grid;
  gap: 8px;
}

.audit-collapse {
  margin-top: 16px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}

.audit-collapse :deep(.el-collapse-item__header) {
  height: 52px;
  padding: 0 16px;
  gap: 12px;
}

.audit-collapse :deep(.el-collapse-item__header span) {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.audit-collapse :deep(.el-collapse-item__content) {
  padding: 16px;
  background: #f8fafc;
}

@media (max-width: 1280px) {
  .ops-layout,
  .task-overview,
  .evidence-grid,
  .step-strip {
    grid-template-columns: 1fr;
  }

  .evidence-grid div {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }

  .evidence-grid div:last-child {
    border-bottom: 0;
  }
}
</style>
