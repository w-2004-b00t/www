<script setup lang="ts">
import { computed } from 'vue'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Database,
  FileJson2,
  Hammer,
  PlayCircle,
  ShieldCheck,
} from 'lucide-vue-next'
import type { AgentStep, GenerationTask } from '../../types/common'

const props = defineProps<{ task: GenerationTask | null }>()
const emit = defineEmits<{ retryStep: [agentName: string] }>()

const currentStep = computed(() => props.task?.agentSteps.find((step) => step.name === props.task?.currentAgent))
const completedCount = computed(() => props.task?.agentSteps.filter((step) => step.status === 'success').length || 0)
const totalCount = computed(() => props.task?.agentSteps.length || 0)
const runningSentence = computed(() => {
  if (!currentStep.value) return props.task?.message || '创建任务后展示输入、工具、结构化输出、失败处理和下游交接。'
  if (currentStep.value.name === 'document_agent' && currentStep.value.status === 'running') {
    const citationCount = currentStep.value.citations?.length || 3
    return `文档生成 Agent 正在基于 ${citationCount} 条课程引用生成讲解文档`
  }
  if (currentStep.value.name === 'exercise_agent' && currentStep.value.status === 'running') {
    return '题库生成 Agent 正在生成选择题、简答题、计算题和代码题'
  }
  if (currentStep.value.name === 'multimodal_agent' && currentStep.value.status === 'running') {
    return '多模态生成 Agent 正在生成完整思维导图、视频演示和动画脚本'
  }
  return props.task?.message || currentStep.value.summary
})

function agentStatusText(status: AgentStep['status']) {
  const map = {
    pending: '等待中',
    running: '运行中',
    success: '已完成',
    failed: '失败',
  }
  return map[status]
}

function taskStatusText(status?: GenerationTask['status']) {
  const map = {
    pending: '等待中',
    running: '运行中',
    success: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return status ? map[status] : '未开始'
}

function tagType(status: AgentStep['status'] | GenerationTask['status'] | undefined) {
  if (status === 'success') return 'success'
  if (status === 'running') return 'primary'
  if (status === 'failed' || status === 'cancelled') return 'danger'
  return 'info'
}

function statusIcon(status: AgentStep['status']) {
  if (status === 'success') return CheckCircle2
  if (status === 'running') return PlayCircle
  if (status === 'failed') return AlertTriangle
  return Clock3
}

function formatDuration(ms?: number) {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatRemaining(ms?: number) {
  if (!ms) return '0s'
  return `${Math.ceil(ms / 1000)}s`
}

function stringify(value?: Record<string, unknown>) {
  if (!value) return '{}'
  return JSON.stringify(value, null, 2)
}

function compactTitle(title: string) {
  return title.replace('智能体', '').replace(' Agent', ' Agent')
}

function confidenceText(step: AgentStep) {
  return step.confidence ? `${Math.round(step.confidence * 100)}%` : '待计算'
}

function toolsSummary(step: AgentStep) {
  return step.tools?.length ? step.tools : ['等待工具调用']
}

function citationSummary(step: AgentStep) {
  if (step.citations?.length) {
    const first = step.citations[0]
    const suffix = step.citations.length > 1 ? ` 等 ${step.citations.length} 条` : ''
    return `${first.documentName} · ${first.sourceLocation}${suffix}`
  }
  if (step.name === 'knowledge_agent') return '知识库未命中时阻止高可信生成'
  if (['document_agent', 'exercise_agent', 'multimodal_agent', 'code_agent', 'audit_agent'].includes(step.name)) return '沿用知识检索 Agent 的课程引用'
  return '无直接课程引用，使用上游结构化结果'
}

function auditStatusText(step: AgentStep) {
  return step.auditStatus || (step.status === 'success' ? '已通过' : step.status === 'failed' ? '失败待处理' : '待执行')
}

function failureSummary(step: AgentStep) {
  return step.errorReason || step.failureCases?.[0] || step.retryStrategy || '失败时保留输入与中间结果，支持重新执行当前 Agent'
}

function handoffSummary(step: AgentStep) {
  if (!step.handoff) return '等待生成下游交接字段'
  return `${step.handoff.from || '上游任务'} → ${step.handoff.to || '下游智能体'}`
}

function downstreamImpact(step: AgentStep) {
  return step.downstreamImpact?.length ? step.downstreamImpact : step.affects || []
}

function downstreamSummary(step: AgentStep) {
  const impact = downstreamImpact(step)
  return impact.length ? impact.join('、') : '等待下游消费结构化结果'
}

const runtimeStatus = computed(() => props.task?.agentRuntime?.runtimeStatus || {})
const frameworkLabel = computed(() => {
  const status = runtimeStatus.value
  if (Boolean(status.langGraphActive)) return 'LangGraph active'
  return 'custom_sequential_workflow'
})
const collaborationLabel = computed(() => {
  const status = runtimeStatus.value
  if (Boolean(status.autoGenActive)) return 'AutoGen active'
  return 'structured_messages'
})
const runtimeAvailability = computed(() => {
  const status = runtimeStatus.value
  const langGraph = Boolean(status.langGraphAvailable) ? 'LangGraph installed' : 'LangGraph unavailable'
  const autoGen = Boolean(status.autoGenAvailable) ? 'AutoGen installed' : 'AutoGen unavailable'
  return `${langGraph} / ${autoGen}`
})
const errorDetail = computed(() => props.task?.outputPayload?.errorDetail)
const retrievalNoiseSummary = computed(() => (
  Array.isArray(errorDetail.value?.retrievalNoiseSummary) ? errorDetail.value.retrievalNoiseSummary : []
))
const noisyChunkIds = computed(() => new Set(retrievalNoiseSummary.value.map((item) => item.chunkId).filter(Boolean)))

function noiseReasonText(reasons?: string[]) {
  const labels: Record<string, string> = {
    ppt_noise: 'PPT 噪声',
    path_noise: '路径污染',
    code_fragment: '源码片段',
    thin_excerpt: '片段过短',
  }
  return reasons?.map((item) => labels[item] || item).join('、') || '疑似噪声'
}
</script>

<template>
  <div class="agent-audit panel">
    <header class="audit-header">
      <div>
        <span class="soft-tag">评审模式 · Agent 工程审计</span>
        <h3>{{ task ? '任务审计面板' : '等待创建任务' }}</h3>
        <p class="muted">
          {{ runningSentence }}
        </p>
      </div>
      <div class="progress-block">
        <strong>{{ task?.progress || 0 }}%</strong>
        <el-progress :percentage="task?.progress || 0" :show-text="false" />
      </div>
    </header>

    <div v-if="!task" class="empty-line">
      <Clock3 :size="18" />
      <span>还没有运行任务。生成学习资源后，这里会显示可复核的 Agent 处理轨迹。</span>
    </div>

    <template v-else>
      <el-collapse class="compact-audit-details">
        <el-collapse-item title="展开审计详情、输入和运行时信息" name="audit-detail">
          <section class="task-meta" aria-label="任务概览">
            <div>
              <span>任务 ID</span>
              <strong>{{ task.id }}</strong>
            </div>
            <div>
              <span>状态</span>
              <el-tag size="small" :type="tagType(task.status)">{{ taskStatusText(task.status) }}</el-tag>
            </div>
            <div>
              <span>当前 Agent</span>
              <strong>{{ currentStep?.title || '-' }}</strong>
            </div>
            <div>
              <span>进度</span>
              <strong>{{ completedCount }}/{{ totalCount }}</strong>
            </div>
            <div>
              <span>课程</span>
              <strong>{{ task.courseName || task.inputPayload?.course_name || '数据结构课程' }}</strong>
            </div>
            <div>
              <span>画像版本</span>
              <strong>{{ task.profileVersion || task.inputPayload?.profile_version || 'profile_v1' }}</strong>
            </div>
            <div>
              <span>已耗时</span>
              <strong>{{ formatDuration(task.durationMs) }}</strong>
            </div>
            <div>
              <span>预计剩余</span>
              <strong>{{ task.status === 'success' ? '0s' : formatRemaining(task.estimatedRemainingMs) }}</strong>
            </div>
          </section>

          <section v-if="task.agentRuntime" class="runtime-strip" aria-label="Agent 运行时状态">
            <div>
              <span>编排框架</span>
              <strong>{{ frameworkLabel }}</strong>
            </div>
            <div>
              <span>协作模式</span>
              <strong>{{ collaborationLabel }}</strong>
            </div>
            <div>
              <span>运行可用性</span>
              <strong>{{ runtimeAvailability }}</strong>
            </div>
            <div>
              <span>协议 / 节点</span>
              <strong>{{ task.messageProtocol || task.agentRuntime.messageProtocol || 'eduagent.agent.event.v1' }} · {{ task.executionOrder?.length || task.agentSteps.length }} 个 Agent</strong>
            </div>
          </section>

          <section v-if="task.inputPayload" class="input-strip">
            <div>
              <span>输入主题</span>
              <strong>{{ task.topic || task.inputPayload.topic || '-' }}</strong>
            </div>
            <div>
              <span>学习目标</span>
              <strong>{{ task.inputPayload.target || '-' }}</strong>
            </div>
            <div>
              <span>资源类型</span>
              <strong>{{ (task.inputPayload.resource_types as string[] | undefined)?.join('、') || '-' }}</strong>
            </div>
          </section>

          <section v-if="errorDetail" class="failure-detail-strip">
            <div>
              <span>失败原因码</span>
              <strong>{{ errorDetail.reasonCode || errorDetail.code || '-' }}</strong>
            </div>
            <div>
              <span>模型</span>
              <strong>{{ errorDetail.model || '-' }}</strong>
            </div>
            <div>
              <span>原始失败</span>
              <strong>{{ errorDetail.rawFailure || errorDetail.detail || '-' }}</strong>
            </div>
            <div>
              <span>建议操作</span>
              <strong>{{ errorDetail.suggestedActions?.join('、') || '-' }}</strong>
            </div>
          </section>

          <section v-if="retrievalNoiseSummary.length" class="noise-strip">
            <div v-for="item in retrievalNoiseSummary" :key="item.chunkId || item.documentName">
              <span>{{ noiseReasonText(item.reasons) }}</span>
              <strong>{{ item.documentName }}</strong>
              <p>{{ item.preview }}</p>
            </div>
          </section>

          <section class="audit-proof" aria-label="Agent 审计证据">
            <div>
              <span>输入</span>
              <strong>{{ task.inputPayload?.target || task.inputPayload?.topic || '等待任务输入' }}</strong>
            </div>
            <div>
              <span>工具</span>
              <strong>{{ currentStep?.tools?.slice(0, 2).join(' / ') || '等待工具调用' }}</strong>
            </div>
            <div>
              <span>审核状态</span>
              <strong>{{ currentStep ? auditStatusText(currentStep) : '待执行' }}</strong>
            </div>
            <div>
              <span>输出</span>
              <strong>{{ currentStep?.outputSummary || currentStep?.summary || '等待结构化输出' }}</strong>
            </div>
            <div>
              <span>失败处理</span>
              <strong>{{ currentStep?.errorReason || currentStep?.retryStrategy || '支持单 Agent 重试' }}</strong>
            </div>
            <div>
              <span>下游交接</span>
              <strong>{{ currentStep?.handoff?.to || '结构化结果传给下一智能体' }}</strong>
            </div>
            <div>
              <span>下游影响</span>
              <strong>{{ currentStep ? downstreamSummary(currentStep) : '等待下游消费结构化结果' }}</strong>
            </div>
          </section>
        </el-collapse-item>
      </el-collapse>

      <section class="agent-audit-matrix" aria-label="Agent 工程审计字段">
        <article v-for="step in task.agentSteps" :key="step.name" class="agent-audit-card" :class="step.status">
          <header class="agent-card-head">
            <div class="agent-title">
              <component :is="statusIcon(step.status)" :size="18" />
              <div>
                <h4>{{ compactTitle(step.title) }}</h4>
                <p>{{ step.responsibility || step.summary }}</p>
              </div>
            </div>
            <div class="agent-state">
              <el-tag size="small" :type="tagType(step.status)">{{ agentStatusText(step.status) }}</el-tag>
              <span>{{ formatDuration(step.durationMs) }}</span>
            </div>
          </header>

          <div class="agent-field-grid">
            <div class="agent-field input">
              <span>输入</span>
              <strong>{{ step.inputSummary || '等待上游输出' }}</strong>
            </div>
            <div class="agent-field tools">
              <span>调用工具</span>
              <div class="tool-list">
                <el-tag v-for="tool in toolsSummary(step)" :key="`${step.name}-${tool}`" size="small" effect="plain">
                  {{ tool }}
                </el-tag>
              </div>
            </div>
            <div class="agent-field output">
              <span>输出</span>
              <strong>{{ step.status === 'pending' ? '等待执行' : step.outputSummary || step.summary }}</strong>
            </div>
            <div class="agent-field confidence">
              <span>置信度</span>
              <strong>{{ confidenceText(step) }}</strong>
              <el-progress v-if="step.confidence" :percentage="Math.round(step.confidence * 100)" :show-text="false" />
            </div>
            <div class="agent-field audit-status">
              <span>审核状态</span>
              <strong>{{ auditStatusText(step) }}</strong>
            </div>
            <div class="agent-field citations">
              <span>引用来源</span>
              <strong>{{ citationSummary(step) }}</strong>
            </div>
            <div class="agent-field failure">
              <span>失败处理</span>
              <strong>{{ failureSummary(step) }}</strong>
              <el-button
                v-if="step.status === 'failed'"
                size="small"
                text
                type="primary"
                @click="emit('retryStep', step.name)"
              >
                重试当前 Agent
              </el-button>
            </div>
            <div class="agent-field transfer">
              <span>下游交接</span>
              <strong>{{ handoffSummary(step) }}</strong>
              <div class="field-list">
                <el-tag v-for="field in step.handoff?.fields || []" :key="field" size="small" effect="plain">
                  {{ field }}
                </el-tag>
              </div>
            </div>
            <div class="agent-field downstream">
              <span>下游影响</span>
              <strong>{{ downstreamSummary(step) }}</strong>
              <div class="field-list">
                <el-tag v-for="item in downstreamImpact(step)" :key="`${step.name}-${item}`" size="small" effect="plain">
                  {{ item }}
                </el-tag>
              </div>
            </div>
          </div>

          <el-collapse class="detail-collapse">
            <el-collapse-item title="查看结构化输出、页面证据和课程引用" :name="step.name">
              <div class="agent-detail">
                <div class="detail-section">
                  <h4><Database :size="15" /> 交接规则</h4>
                  <div class="handoff">
                    <span>{{ step.handoff?.from || '上游任务' }}</span>
                    <ArrowRight :size="15" />
                    <span>{{ step.handoff?.to || '下游智能体' }}</span>
                  </div>
                  <p>{{ step.handoff?.rule || '结构化结果传递给下一智能体。' }}</p>
                </div>

                <div class="detail-section">
                  <h4><ShieldCheck :size="15" /> 完整失败场景</h4>
                  <ul>
                    <li v-for="item in step.failureCases || ['等待执行或尚未记录失败']" :key="item">{{ item }}</li>
                  </ul>
                  <p class="retry">{{ step.retryStrategy || '失败时保留输入和中间结果，支持重新执行当前步骤。' }}</p>
                </div>

                <div class="detail-section">
                  <h4><Hammer :size="15" /> 页面证据</h4>
                  <div class="evidence-list">
                    <div v-for="item in step.evidence || []" :key="`${step.name}-${item.title}`">
                      <span>{{ item.title }}</span>
                      <strong>{{ item.value }}</strong>
                    </div>
                    <div v-if="!step.evidence?.length">
                      <span>输出摘要</span>
                      <strong>{{ step.outputSummary || step.summary }}</strong>
                    </div>
                  </div>
                </div>

                <div class="detail-section">
                  <h4><ArrowRight :size="15" /> 下游影响</h4>
                  <div class="evidence-list">
                    <div v-for="item in downstreamImpact(step)" :key="`${step.name}-impact-${item}`">
                      <span>影响对象</span>
                      <strong>{{ item }}</strong>
                    </div>
                    <div v-if="!downstreamImpact(step).length">
                      <span>影响对象</span>
                      <strong>等待下游消费结构化结果</strong>
                    </div>
                  </div>
                </div>

                <div class="detail-section json-section">
                  <h4><FileJson2 :size="15" /> 结构化输出 JSON</h4>
                  <pre>{{ stringify(step.structuredOutput) }}</pre>
                </div>

                <div v-if="step.citations?.length" class="detail-section citation-section">
                  <h4>课程引用</h4>
                  <div v-for="citation in step.citations" :key="citation.chunkId" class="citation-item">
                    <strong>{{ citation.documentName }}</strong>
                    <span>{{ citation.sourceLocation }} · 第 {{ citation.page || '-' }} 页 · 相似度 {{ citation.similarity || '-' }}</span>
                    <el-tag v-if="noisyChunkIds.has(citation.chunkId)" size="small" type="warning" effect="plain">疑似噪声引用</el-tag>
                    <p>{{ citation.contentPreview }}</p>
                  </div>
                </div>
              </div>
            </el-collapse-item>
          </el-collapse>
        </article>
      </section>

      <section v-if="task.outputPayload" class="result-strip">
        <div>
          <span>生成资源</span>
          <strong>{{ task.outputPayload.resource_count || 0 }}</strong>
        </div>
        <div>
          <span>审核通过</span>
          <strong>{{ task.outputPayload.audit_passed || 0 }}</strong>
        </div>
        <div>
          <span>风险资源</span>
          <strong>{{ task.outputPayload.audit_warning || 0 }}</strong>
        </div>
        <div>
          <span>路径任务</span>
          <strong>{{ task.outputPayload.inserted_path_tasks || 0 }}</strong>
        </div>
        <div>
          <span>画像草稿</span>
          <strong>{{ task.outputPayload.profile_update_drafts || 0 }}</strong>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.agent-audit {
  display: grid;
  gap: 14px;
}

.audit-header {
  display: grid;
  grid-template-columns: 1fr 180px;
  align-items: start;
  gap: 18px;
}

.audit-header h3 {
  margin: 8px 0 4px;
  font-size: 18px;
}

.progress-block {
  display: grid;
  gap: 8px;
  padding-top: 4px;
}

.progress-block strong {
  justify-self: end;
  color: var(--color-primary);
  font-size: 18px;
}

.empty-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 76px;
  padding: 14px;
  color: var(--color-text-secondary);
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.task-meta,
.runtime-strip,
.input-strip,
.failure-detail-strip,
.noise-strip,
.result-strip {
  display: grid;
  gap: 0;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}

.compact-audit-details {
  border-top: 0;
  border-bottom: 0;
}

.compact-audit-details :deep(.el-collapse-item__header) {
  height: 42px;
  padding: 0 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.compact-audit-details :deep(.el-collapse-item__content) {
  display: grid;
  gap: 12px;
  padding: 12px 0 0;
}

.task-meta {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.runtime-strip {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  background: #f8fafc;
}

.input-strip {
  grid-template-columns: 1fr 1.5fr 1fr;
}

.failure-detail-strip {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  background: #fff7ed;
  border-color: #fed7aa;
}

.noise-strip {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  background: #fffbeb;
  border-color: #fde68a;
}

.audit-proof {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.audit-proof div {
  display: grid;
  gap: 5px;
  min-width: 0;
  padding: 10px 12px;
  border-right: 1px solid var(--color-border);
}

.audit-proof div:last-child {
  border-right: 0;
}

.result-strip {
  grid-template-columns: repeat(5, minmax(0, 1fr));
  background: #f8fafc;
}

.task-meta div,
.runtime-strip div,
.input-strip div,
.failure-detail-strip div,
.noise-strip div,
.result-strip div {
  display: grid;
  gap: 5px;
  min-width: 0;
  padding: 11px 12px;
  border-right: 1px solid var(--color-border);
}

.task-meta div:nth-child(4n),
.runtime-strip div:nth-child(4n),
.input-strip div:last-child,
.failure-detail-strip div:nth-child(4n),
.noise-strip div:nth-child(2n),
.result-strip div:last-child {
  border-right: 0;
}

.task-meta span,
.runtime-strip span,
.input-strip span,
.failure-detail-strip span,
.noise-strip span,
.audit-proof span,
.result-strip span,
.status-cell span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.task-meta strong,
.runtime-strip strong,
.input-strip strong,
.failure-detail-strip strong,
.noise-strip strong,
.audit-proof strong,
.result-strip strong {
  overflow: hidden;
  color: var(--color-text);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.noise-strip p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.agent-table {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}

.agent-audit-matrix {
  display: grid;
  gap: 12px;
}

.agent-audit-card {
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.agent-audit-card.running {
  border-color: #bfdbfe;
  box-shadow: 0 0 0 1px rgba(37, 99, 235, 0.16), 0 10px 24px rgba(37, 99, 235, 0.08);
}

.agent-audit-card.failed {
  border-color: #fecaca;
}

.agent-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 14px;
  border-bottom: 1px solid var(--color-border);
  background: #fbfdff;
}

.agent-title {
  display: flex;
  gap: 10px;
  min-width: 0;
}

.agent-title svg {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--color-primary);
}

.agent-title h4 {
  margin: 0;
  color: var(--color-text);
  font-size: 15px;
}

.agent-title p {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.55;
}

.agent-state {
  display: grid;
  justify-items: end;
  gap: 5px;
  flex: 0 0 auto;
}

.agent-state span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.agent-field-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr) minmax(0, 1.2fr) 130px;
  gap: 0;
}

.agent-field {
  display: grid;
  align-content: start;
  gap: 7px;
  min-width: 0;
  min-height: 92px;
  padding: 12px;
  border-right: 1px solid var(--color-border);
  border-bottom: 1px solid var(--color-border);
}

.agent-field:nth-child(4n) {
  border-right: 0;
}

.agent-field.citations,
.agent-field.failure,
.agent-field.transfer,
.agent-field.downstream {
  min-height: 104px;
}

.agent-field.transfer,
.agent-field.downstream {
  grid-column: span 2;
}

.agent-field.downstream {
  border-right: 0;
}

.agent-field span {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 700;
}

.agent-field strong {
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.55;
}

.agent-field .el-progress {
  width: 100%;
}

.tool-list,
.field-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.detail-collapse {
  border: 0;
}

.detail-collapse :deep(.el-collapse-item__header) {
  height: 40px;
  padding: 0 14px;
  color: var(--color-text-secondary);
  font-size: 13px;
  background: #fff;
}

.detail-collapse :deep(.el-collapse-item__content) {
  padding-bottom: 0;
}

.table-head,
.agent-row {
  display: grid;
  grid-template-columns: minmax(190px, 1.1fr) minmax(220px, 1.2fr) minmax(240px, 1.4fr) 120px;
  align-items: center;
  gap: 12px;
}

.table-head {
  padding: 10px 14px;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 700;
  background: #f8fafc;
  border-bottom: 1px solid var(--color-border);
}

.agent-collapse {
  border: 0;
}

:deep(.el-collapse-item__header) {
  height: auto;
  padding: 0 14px;
  border-bottom: 1px solid var(--color-border);
}

:deep(.el-collapse-item__content) {
  padding: 0;
}

.agent-row {
  width: 100%;
  min-height: 70px;
}

.agent-name {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.agent-name svg {
  flex: 0 0 auto;
  color: var(--color-primary);
}

.agent-name div,
.agent-row p,
.status-cell {
  min-width: 0;
}

.agent-name strong {
  display: block;
  color: var(--color-text);
  font-size: 14px;
}

.agent-name small,
.agent-row p {
  display: -webkit-box;
  margin: 2px 0 0;
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.tool-cell,
.field-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tool-cell span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.status-cell {
  display: grid;
  gap: 5px;
  justify-items: start;
}

.agent-detail {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding: 14px;
  background: #fbfdff;
  border-bottom: 1px solid var(--color-border);
}

.detail-section {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.detail-section h4 {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 10px;
  color: var(--color-text);
  font-size: 13px;
}

.detail-section p,
.detail-section li {
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.65;
}

.detail-section ul {
  margin: 0;
  padding-left: 16px;
}

.retry {
  margin: 8px 0 0;
}

.handoff {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 9px;
  color: var(--color-text);
  font-size: 12px;
  font-weight: 700;
}

.handoff span {
  min-width: 0;
}

.evidence-list {
  display: grid;
  gap: 8px;
}

.evidence-list div {
  display: grid;
  gap: 3px;
}

.evidence-list span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.evidence-list strong {
  color: var(--color-text);
  font-size: 12px;
  line-height: 1.55;
}

.json-section {
  grid-column: 1 / -1;
}

pre {
  max-height: 230px;
  margin: 0;
  padding: 12px;
  overflow: auto;
  color: #334155;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  border-radius: 7px;
  background: #f8fafc;
}

.citation-section {
  grid-column: 1 / -1;
}

.citation-item {
  display: grid;
  gap: 4px;
  padding: 9px 0;
  border-top: 1px solid var(--color-border);
}

.citation-item:first-of-type {
  border-top: 0;
}

.citation-item strong {
  color: var(--color-text);
  font-size: 13px;
}

.citation-item span,
.citation-item p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

@media (max-width: 1180px) {
  .audit-header,
  .task-meta,
  .runtime-strip,
  .input-strip,
  .audit-proof,
  .result-strip,
  .agent-detail {
    grid-template-columns: 1fr;
  }

  .task-meta div,
  .runtime-strip div,
  .input-strip div,
  .audit-proof div,
  .result-strip div {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }

  .task-meta div:last-child,
  .runtime-strip div:last-child,
  .input-strip div:last-child,
  .audit-proof div:last-child,
  .result-strip div:last-child {
    border-bottom: 0;
  }

  .table-head {
    display: none;
  }

  .agent-row {
    grid-template-columns: 1fr;
    gap: 8px;
    padding: 12px 0;
  }
}
</style>
