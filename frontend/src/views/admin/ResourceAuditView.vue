<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { AlertTriangle, CheckCircle2, Eye, FileSearch, ShieldCheck, Workflow, XCircle } from 'lucide-vue-next'
import SourceCitation from '../../components/resource/SourceCitation.vue'
import { AUDIT_STATUS_META, RESOURCE_TYPE_LABELS, getAuditSyncText } from '../../constants/resourceMeta'
import { useResourceStore } from '../../stores/resource'
import type { LearningResource } from '../../types/common'

const resource = useResourceStore()
const selectedId = ref('')
const scope = ref<'student' | 'class'>('student')
const selected = computed(() => resource.resources.find((item) => item.id === selectedId.value) || resource.resources[0])
const pendingCount = computed(() => resource.resources.filter((item) => item.auditStatus !== 'passed').length)
const auditRecords = computed(() => selected.value?.auditHistory || [])
const studentVisibleCount = computed(() => resource.resources.filter((item) => item.auditStatus === 'passed').length)
const lastAuditRecord = computed(() => resource.auditHistory[0])
const selectedCitationScore = computed(() => {
  const citations = selected.value?.citations || []
  if (!citations.length) return 0
  const total = citations.reduce((sum, item) => sum + (item.similarity || 0.82), 0)
  return Math.round((total / citations.length) * 100)
})
const selectedRiskReason = computed(() => {
  if (!selected.value) return '等待选择资源'
  if (selected.value.auditStatus === 'passed') return '引用覆盖、难度匹配和答案一致性已通过门禁。'
  if (selected.value.auditStatus === 'rejected') return '教师已驳回，学生端不展示，需要重新生成或人工修正。'
  if (!selected.value.citations.length) return '缺少课程引用，不能作为高可信学习资料推荐。'
  return '自动审核提示引用不足或答案解析依据不完整，需要教师确认。'
})
const selectedStudentImpact = computed(() => {
  if (!selected.value) return '等待审核'
  if (selected.value.auditStatus === 'passed') return '已进入学生端推荐，可加入学习路径。'
  if (selected.value.auditStatus === 'warning') return '学生端显示待教师复核，暂不进入推荐排序。'
  if (selected.value.auditStatus === 'rejected') return '学生端不可见，需重新生成后再审核。'
  return '学生端显示待审核，暂不作为推荐资源。'
})
const auditFlow = computed(() => [
  {
    title: '自动审核',
    desc: selected.value?.auditStatus === 'passed' ? '引用、难度和答案一致性已通过' : '发现引用或难度风险',
    status: selected.value?.auditStatus === 'passed' ? 'done' : 'warning',
  },
  {
    title: '查看引用',
    desc: `${selected.value?.citations.length || 0} 条课程原文可追溯`,
    status: selected.value?.citations.length ? 'done' : 'warning',
  },
  {
    title: '教师决策',
    desc: auditRecords.value.length ? '已有人工审核记录' : '等待通过、驳回或要求补充引用',
    status: auditRecords.value.length ? 'done' : 'active',
  },
  {
    title: '同步学生端',
    desc: selectedStudentImpact.value,
    status: selected.value?.auditStatus === 'passed' ? 'done' : 'pending',
  },
])
const autoAuditChecks = computed(() => {
  const item = selected.value
  if (!item) return []
  return [
    {
      label: '引用覆盖',
      value: item.citations.length ? `${item.citations.length} 条引用，平均相似度 ${selectedCitationScore.value}%` : '未命中引用',
      passed: item.citations.length > 0 && selectedCitationScore.value >= 80,
    },
    {
      label: '答案一致性',
      value: item.auditStatus === 'warning' ? '答案解析缺少直接依据' : '未发现答案冲突',
      passed: item.auditStatus !== 'warning' && item.auditStatus !== 'rejected',
    },
    {
      label: '难度匹配',
      value: item.resourceType === 'exercise' && item.auditStatus !== 'passed' ? '需教师确认是否过难' : '符合当前学生画像',
      passed: item.auditStatus === 'passed' || item.resourceType !== 'exercise',
    },
    {
      label: '幻觉拦截',
      value: item.auditStatus === 'passed' ? '无未溯源关键结论' : '未溯源内容已拦截推荐',
      passed: item.auditStatus === 'passed',
    },
  ]
})

function statusLabel(status: LearningResource['auditStatus']) {
  return AUDIT_STATUS_META[status].shortLabel
}

function statusType(status: LearningResource['auditStatus']) {
  return AUDIT_STATUS_META[status].type
}

async function setStatus(item: LearningResource, status: LearningResource['auditStatus']) {
  let reason = status === 'passed' ? '教师确认引用充分、难度匹配，可推荐给学生。' : ''
  if (status !== 'passed') {
    try {
      const { value } = await ElMessageBox.prompt(
        status === 'warning' ? '请填写需要补充引用或修正的原因' : '请填写驳回原因',
        '审核原因',
        {
          confirmButtonText: '保存审核结果',
          cancelButtonText: '取消',
          inputPattern: /\S{4,}/,
          inputErrorMessage: '原因至少 4 个字符',
        },
      )
      reason = value
    } catch {
      return
    }
  }
  try {
    await resource.updateAudit(item.id, status, reason, scope.value)
    if (status === 'passed') {
      ElMessage.success(scope.value === 'class' ? '已通过并加入班级资源库，学生端可见。' : '已通过并同步到学生端。')
    } else if (status === 'warning') {
      ElMessage.warning('已要求补充引用，学生端暂不推荐该资源。')
    } else {
      ElMessage.error('已驳回资源，学生端不可见，需重新生成。')
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '审核结果保存失败')
  }
}

onMounted(async () => {
  await resource.loadAll()
  selectedId.value = resource.resources[0]?.id || ''
})
</script>

<template>
  <div class="page">
    <div class="page-breadcrumb">
      <span>教师端</span>
      <span>资源审核</span>
      <strong>引用详情与审核记录</strong>
    </div>

    <div class="page-header">
      <div>
        <h1 class="page-title">教师资源审核</h1>
        <p class="page-subtitle">围绕课程引用、答案一致性和学生端推荐状态做质量门禁，避免未经溯源的 AI 内容直接进入学习路径。</p>
      </div>
      <div class="head-actions">
        <el-segmented
          v-model="scope"
          :options="[
            { label: '仅当前学生', value: 'student' },
            { label: '加入班级资源库', value: 'class' },
          ]"
        />
        <el-tag type="warning" effect="plain">{{ pendingCount }} 个资源待处理</el-tag>
      </div>
    </div>

    <section class="panel audit-overview">
      <div>
        <span class="status-pill">今日审核重点</span>
        <h2>{{ pendingCount }} 个资源需要教师确认</h2>
        <p>优先处理“引用不足、答案不稳定、难度不匹配”的资源。通过后会影响学生端推荐，驳回后学生端不可见。</p>
      </div>
      <div class="overview-stats">
        <div><span>已同步学生端</span><strong>{{ studentVisibleCount }}</strong></div>
        <div><span>当前引用证据</span><strong>{{ selected?.citations.length || 0 }} 条</strong></div>
        <div><span>平均相似度</span><strong>{{ selectedCitationScore }}%</strong></div>
        <div><span>同步范围</span><strong>{{ scope === 'class' ? '班级资源库' : '当前学生' }}</strong></div>
      </div>
    </section>

    <div class="audit-layout">
      <section class="panel">
        <h2 class="section-title">审核队列</h2>
        <p class="section-desc">按风险状态处理，教师确认前不会进入学生端正式推荐。</p>
        <div class="audit-list">
          <article
            v-for="item in resource.resources"
            :key="item.id"
            class="audit-item"
            :class="{ active: selected?.id === item.id }"
            @click="selectedId = item.id"
          >
            <div>
              <el-tag :type="statusType(item.auditStatus)" effect="plain">
                {{ statusLabel(item.auditStatus) }}
              </el-tag>
              <el-tag class="resource-type" type="info" effect="plain">
                {{ RESOURCE_TYPE_LABELS[item.resourceType] }}
              </el-tag>
              <h3>{{ item.title }}</h3>
              <p>{{ item.summary }}</p>
              <small>{{ getAuditSyncText(item) }}</small>
            </div>
            <strong>{{ item.qualityScore }}</strong>
          </article>
        </div>
      </section>

      <section v-if="selected" class="panel detail-panel">
        <div class="detail-head">
          <div>
            <h2 class="section-title">{{ selected.title }}</h2>
            <p class="section-desc">{{ selected.summary }}</p>
          </div>
          <div class="score-box">
            <span>质量分</span>
            <strong>{{ selected.qualityScore }}</strong>
          </div>
        </div>

        <section class="audit-flow" aria-label="教师审核流程">
          <div v-for="(step, index) in auditFlow" :key="step.title" class="flow-step" :class="step.status">
            <span>{{ index + 1 }}</span>
            <div>
              <strong>{{ step.title }}</strong>
              <p>{{ step.desc }}</p>
            </div>
          </div>
        </section>

        <section class="auto-audit-panel">
          <div class="panel-head-line">
            <div>
              <h2 class="section-title">自动审核结果</h2>
              <p class="section-desc">内容审核智能体先做引用、答案、难度和幻觉风险检查，教师在此基础上最终确认。</p>
            </div>
            <el-tag :type="selected.auditStatus === 'passed' ? 'success' : selected.auditStatus === 'rejected' ? 'danger' : 'warning'" effect="plain">
              {{ selectedRiskReason }}
            </el-tag>
          </div>
          <div class="auto-check-grid">
            <div v-for="check in autoAuditChecks" :key="check.label" class="auto-check" :class="{ passed: check.passed }">
              <component :is="check.passed ? CheckCircle2 : AlertTriangle" :size="18" />
              <span>{{ check.label }}</span>
              <strong>{{ check.value }}</strong>
            </div>
          </div>
        </section>

        <div class="audit-checks">
          <div><FileSearch :size="18" /><span>引用覆盖</span><strong>{{ selected.citations.length }} 条课程引用</strong></div>
          <div><ShieldCheck :size="18" /><span>防幻觉门禁</span><strong>{{ selected.auditStatus === 'passed' ? '关键结论已溯源' : '未溯源内容已拦截' }}</strong></div>
          <div><Workflow :size="18" /><span>推荐影响</span><strong>{{ selectedStudentImpact }}</strong></div>
          <div><Eye :size="18" /><span>学生端状态</span><strong>{{ selected.auditStatus === 'passed' ? '已展示给学生' : '暂不推荐给学生' }}</strong></div>
        </div>

        <section class="student-visible-card" :class="{ visible: selected.auditStatus === 'passed' }">
          <span>学生端可见性</span>
          <strong>{{ selected.auditStatus === 'passed' ? '已同步到学生端，可作为正式学习资料' : '暂不推荐给学生，需教师处理后再同步' }}</strong>
        </section>

        <section class="sync-preview">
          <div>
            <span>同步预览</span>
            <strong>{{ selected.auditStatus === 'passed' ? '学生资源卡片显示“教师已同步到学生端”' : '学生资源卡片显示“等待教师复核/需修正”' }}</strong>
          </div>
          <div>
            <span>最近审核</span>
            <strong>{{ lastAuditRecord ? `${lastAuditRecord.operator} · ${statusLabel(lastAuditRecord.status)}` : '还没有人工审核' }}</strong>
          </div>
          <router-link to="/student/resources">
            <el-button>查看学生端资源状态</el-button>
          </router-link>
        </section>

        <el-alert v-if="selected.auditStatus === 'warning'" type="warning" show-icon :closable="false" class="risk-box">
          答案解析缺少充分课程依据，且题目难度可能高于当前学生画像。建议补充引用或重新生成。
        </el-alert>
        <el-alert v-if="selected.auditStatus !== 'passed'" type="info" show-icon :closable="false" class="risk-box">
          防幻觉机制：未通过审核的资源不会进入学习路径推荐；学生端只能看到待复核或需修正状态，避免把模型推断当作课程依据。
        </el-alert>

        <div class="citation-headline">
          <h2 class="section-title">引用来源与课程原文</h2>
          <p class="section-desc">教师审核时需要核对文档、章节、页码、相似度和原文片段；无引用内容会被视为模型推断。</p>
        </div>
        <SourceCitation :citations="selected.citations" />

        <div class="audit-actions">
          <el-button type="success" @click="setStatus(selected, 'passed')">通过并同步到学生端</el-button>
          <el-button type="warning" plain @click="setStatus(selected, 'warning')">
            <AlertTriangle :size="15" />
            要求补充引用
          </el-button>
          <el-button type="danger" plain @click="setStatus(selected, 'rejected')">
            <XCircle :size="15" />
            驳回并重新生成
          </el-button>
          <el-dropdown>
            <el-button>更多处理</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item>查看资源生成任务</el-dropdown-item>
                <el-dropdown-item>通知学生资源状态</el-dropdown-item>
                <el-dropdown-item>加入课程资源库草稿</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>

        <el-divider />
        <h2 class="section-title">审核记录</h2>
        <div v-if="!auditRecords.length" class="state-empty">
          <h3>还没有人工审核记录</h3>
          <p>选择“通过并同步到学生端”“要求补充引用”或“驳回并重新生成”后，审核人、时间和原因会写入这里。</p>
        </div>
        <div v-else class="history-list">
          <div v-for="record in auditRecords" :key="record.id">
            <strong>{{ record.operator }} · {{ statusLabel(record.status) }}</strong>
            <span>{{ record.createdAt }} · {{ record.scope === 'class' ? '班级资源库' : '当前学生' }}</span>
            <p>{{ record.reason }}</p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.head-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.audit-overview {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 640px;
  gap: 18px;
  margin-bottom: 18px;
}

.audit-overview h2 {
  margin: 10px 0 6px;
  font-size: 22px;
}

.audit-overview p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.overview-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.overview-stats div {
  display: grid;
  gap: 5px;
  min-width: 0;
  padding: 12px;
  border-right: 1px solid var(--color-border);
}

.overview-stats div:last-child {
  border-right: 0;
}

.overview-stats span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.overview-stats strong {
  overflow: hidden;
  color: var(--color-text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audit-layout {
  display: grid;
  grid-template-columns: 420px minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}

.audit-list {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.audit-item {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  padding: 14px;
  cursor: pointer;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.audit-item.active {
  border-color: #bfdbfe;
  background: #eff6ff;
}

.audit-item h3 {
  margin: 10px 0 6px;
  font-size: 16px;
}

.audit-item p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.audit-item small {
  display: block;
  margin-top: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.audit-item strong {
  color: var(--color-primary);
  font-size: 22px;
}

.resource-type {
  margin-left: 6px;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
}

.score-box {
  display: grid;
  place-items: center;
  min-width: 86px;
  height: 74px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.score-box span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.score-box strong {
  color: var(--color-primary);
  font-size: 28px;
  line-height: 1;
}

.audit-flow {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 16px 0;
}

.flow-step {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.flow-step > span {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: #fff;
  font-size: 12px;
  font-weight: 700;
}

.flow-step strong {
  display: block;
  margin-bottom: 4px;
  color: var(--color-text);
}

.flow-step p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.flow-step.done {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.flow-step.warning {
  border-color: #fed7aa;
  background: #fff7ed;
}

.flow-step.active {
  border-color: #bfdbfe;
  background: #eff6ff;
}

.auto-audit-panel {
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.panel-head-line {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.panel-head-line .el-tag {
  max-width: 420px;
  height: auto;
  padding: 6px 10px;
  line-height: 1.45;
  white-space: normal;
}

.auto-check-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.auto-check {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 5px 8px;
  padding: 12px;
  color: #92400e;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  background: #fff7ed;
}

.auto-check.passed {
  color: #15803d;
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.auto-check span {
  color: var(--color-text);
  font-weight: 700;
}

.auto-check strong {
  grid-column: 1 / -1;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.45;
}

.audit-checks {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  margin: 16px 0;
}

.student-visible-card {
  display: grid;
  gap: 5px;
  margin: 14px 0;
  padding: 12px 14px;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  background: #fff7ed;
}

.student-visible-card.visible {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.student-visible-card span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.sync-preview {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  align-items: center;
  gap: 12px;
  margin: 14px 0;
  padding: 12px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #f8fbff;
}

.sync-preview div {
  display: grid;
  gap: 4px;
}

.audit-checks div {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 5px 8px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.audit-checks strong {
  grid-column: 1 / -1;
}

.audit-checks span,
.sync-preview span,
.history-list span {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.sync-preview strong {
  color: var(--color-text);
  font-size: 13px;
}

.risk-box,
.audit-actions,
.citation-headline {
  margin-top: 14px;
}

.audit-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.audit-actions .el-button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.history-list {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}

.history-list div {
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.history-list p {
  margin: 6px 0 0;
  line-height: 1.6;
}

@media (max-width: 1100px) {
  .audit-layout,
  .audit-checks,
  .sync-preview,
  .audit-overview,
  .overview-stats,
  .audit-flow,
  .auto-check-grid {
    grid-template-columns: 1fr;
  }

  .overview-stats div {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }

  .overview-stats div:last-child {
    border-bottom: 0;
  }

  .detail-head {
    flex-direction: column;
  }
}
</style>
