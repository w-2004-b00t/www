<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getAdminDashboardApi } from '../../api/admin'
import FlowGuide from '../../components/common/FlowGuide.vue'
import { useAuthStore } from '../../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const isAdmin = computed(() => auth.role === 'admin')

const teacherMetrics = ref([
  { label: '待审核资源', value: '3', trend: '1 个有风险' },
  { label: '知识库片段', value: '42', trend: '已完成向量化' },
  { label: '薄弱学生占比', value: '31%', trend: '集中在课程资料' },
])

const adminMetrics = ref([
  { label: '运行中智能体任务', value: '2', trend: '平均耗时 8.4s' },
  { label: '模型调用成功率', value: '98.6%', trend: '近 24 小时' },
  { label: '待处理风险', value: '4', trend: '2 个引用缺失' },
])
const systemStatus = ref({ knowledgeCoverage: 92, auditPassRate: 83, agentSuccessRate: 96, pathAdjustments: 18 })

const teacherActions = [
  { title: '审核课程资料练习题', note: '答案解析引用不足，建议查看原文片段后通过或驳回。', path: '/admin/audit', type: 'warning' },
  { title: '查看班级薄弱点', note: '12 名学生在课程资料和课程资料待上传上表现不稳定。', path: '/admin/analytics', type: 'primary' },
  { title: '补充课程资料', note: '课程资料待上传章节仍是草稿，建议上传讲义或课件。', path: '/admin/documents', type: 'info' },
]

const adminActions = [
  { title: '检查内容审核智能体', note: '审核 Agent 正在处理一条低引用覆盖任务。', path: '/admin/tasks', type: 'warning' },
  { title: '调整提示词版本', note: '资源生成智能体 v2.1 已准备灰度，可对比输出质量。', path: '/admin/model-config', type: 'primary' },
  { title: '查看知识库解析队列', note: '1 个文档等待页码引用元数据补全。', path: '/admin/documents', type: 'info' },
]

const metrics = computed(() => (isAdmin.value ? adminMetrics.value : teacherMetrics.value))
const actions = computed(() => (isAdmin.value ? adminActions : teacherActions))
const primaryAction = computed(() => actions.value[0])
const secondaryActions = computed(() => actions.value.slice(1))
const heroTitle = computed(() => (isAdmin.value ? '当前需要处理 4 个系统风险' : '当前需要先审核 1 份风险资源'))
const heroDesc = computed(() => (
  isAdmin.value
    ? '风险主要来自引用覆盖不足和资源生成链路超时。建议先检查内容审核智能体与知识库解析状态。'
    : '课程资料练习题缺少充分课程依据，建议查看引用证据后决定通过、驳回或要求补充引用。'
))
const heroMeta = computed(() => (
  isAdmin.value
    ? ['近 24h 成功率 98.6%', '平均任务耗时 8.4s', '4 个风险拦截']
    : ['数据结构课程', '3 份待审资源', '31% 学生薄弱']
))
const opsGuideSteps = computed(() => (
  isAdmin.value
    ? [
        { label: '系统风险', desc: '先看失败任务与风险拦截', path: '/admin/dashboard' },
        { label: '智能体任务', desc: '核查 Agent 输入输出与重试策略', path: '/admin/tasks' },
        { label: '模型策略', desc: '调整提示词版本和质量阈值', path: '/admin/model-config' },
        { label: '知识库状态', desc: '补齐课程资料和引用元数据', path: '/admin/documents' },
      ]
    : [
        { label: '待审资源', desc: '查看自动审核风险原因', path: '/admin/audit' },
        { label: '引用证据', desc: '核对讲义页码和原文片段', path: '/admin/audit' },
        { label: '同步学生端', desc: '通过、驳回或要求补充引用', path: '/admin/audit' },
        { label: '班级薄弱点', desc: '查看测评结果和共性问题', path: '/admin/analytics' },
      ]
))

onMounted(async () => {
  const result = await getAdminDashboardApi()
  teacherMetrics.value = result.teacherMetrics
  adminMetrics.value = result.adminMetrics
  systemStatus.value = result.status
})
</script>

<template>
  <div class="page">
    <section class="ops-hero">
      <div>
        <span class="eyebrow">{{ isAdmin ? '系统治理工作台' : '教师教学工作台' }}</span>
        <h1>{{ heroTitle }}</h1>
        <p>{{ heroDesc }}</p>
        <div class="hero-meta">
          <span v-for="item in heroMeta" :key="item">{{ item }}</span>
        </div>
      </div>
      <el-button type="primary" size="large" @click="router.push(isAdmin ? '/admin/model-config' : '/admin/audit')">
        {{ isAdmin ? '检查模型策略' : '处理待审资源' }}
      </el-button>
    </section>

    <FlowGuide
      :title="isAdmin ? '管理员处理流程' : '教师审核流程'"
      :description="isAdmin ? '先处理系统风险，再查看任务证据和模型配置。' : '先审核风险资源，再让审核结果同步影响学生端推荐。'"
      :steps="opsGuideSteps"
      :current="0"
    />

    <div class="dashboard-layout">
      <section class="panel">
        <div class="section-head">
          <div>
            <h2 class="section-title">优先处理</h2>
            <p class="section-desc">只前置最影响教学可信度和系统稳定性的事项。</p>
          </div>
        </div>
        <article class="primary-action">
          <el-tag type="warning" effect="plain">{{ isAdmin ? '系统风险' : '资源风险' }}</el-tag>
          <h3>{{ primaryAction.title }}</h3>
          <p>{{ primaryAction.note }}</p>
          <el-button type="primary" @click="router.push(primaryAction.path)">
            {{ isAdmin ? '查看任务链路' : '查看引用并审核' }}
          </el-button>
        </article>
        <div class="action-list">
          <article v-for="item in secondaryActions" :key="item.title" class="action-card">
            <div>
              <el-tag :type="item.type" effect="plain">{{ item.type === 'warning' ? '需处理' : item.type === 'primary' ? '推荐' : '提醒' }}</el-tag>
              <h3>{{ item.title }}</h3>
              <p>{{ item.note }}</p>
            </div>
            <el-button @click="router.push(item.path)">查看</el-button>
          </article>
        </div>
      </section>

      <aside class="panel">
        <h2 class="section-title">{{ isAdmin ? '系统健康证据' : '课程运行证据' }}</h2>
        <div class="compact-metrics">
          <div v-for="item in metrics" :key="item.label">
            <strong>{{ item.value }}</strong>
            <span>{{ item.label }}</span>
            <em>{{ item.trend }}</em>
          </div>
        </div>
        <div class="status-list">
          <div><span>知识库引用覆盖</span><strong>{{ systemStatus.knowledgeCoverage }}%</strong></div>
          <div><span>内容审核通过率</span><strong>{{ systemStatus.auditPassRate }}%</strong></div>
          <div><span>多智能体成功率</span><strong>{{ systemStatus.agentSuccessRate }}%</strong></div>
          <div><span>学生路径更新次数</span><strong>{{ systemStatus.pathAdjustments }}</strong></div>
        </div>
        <el-alert class="hint" type="info" show-icon :closable="false">
          {{ isAdmin ? '管理员重点关注模型稳定性、任务失败和风险策略。' : '教师重点关注资源是否可信、学生是否真正完成学习闭环。' }}
        </el-alert>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.ops-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 22px;
  margin-bottom: 18px;
  padding: 22px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
  box-shadow: var(--shadow-panel);
}

.eyebrow {
  color: var(--color-primary);
  font-size: 13px;
  font-weight: 700;
}

.ops-hero h1 {
  margin: 7px 0 8px;
  font-size: 26px;
  line-height: 1.25;
}

.ops-hero p,
.section-desc {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.hero-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.hero-meta span {
  padding: 4px 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: #f8fafc;
}

.dashboard-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  gap: 18px;
  align-items: start;
}

.section-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 14px;
}

.action-list,
.status-list {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

.primary-action {
  display: grid;
  gap: 10px;
  padding: 18px;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  background: #fffaf5;
}

.primary-action h3 {
  margin: 0;
  font-size: 20px;
}

.primary-action p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.primary-action .el-button {
  justify-self: start;
}

.action-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.action-card h3 {
  margin: 8px 0 5px;
  font-size: 16px;
}

.action-card p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.status-list div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--color-border);
}

.status-list span {
  color: var(--color-text-secondary);
}

.compact-metrics {
  display: grid;
  gap: 10px;
  margin: 14px 0;
}

.compact-metrics div {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 2px 10px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.compact-metrics strong {
  grid-row: span 2;
  font-size: 24px;
  line-height: 1.1;
}

.compact-metrics span {
  color: var(--color-text);
  font-weight: 600;
}

.compact-metrics em {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-style: normal;
}

.hint {
  margin-top: 16px;
}

@media (max-width: 1100px) {
  .ops-hero,
  .dashboard-layout,
  .action-card {
    grid-template-columns: 1fr;
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
