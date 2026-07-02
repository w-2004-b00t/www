<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import PageActionGuide from '../../components/common/PageActionGuide.vue'
import { getAssessmentReportApi, type AssessmentReportData } from '../../api/assessment'
import { useResourceStore } from '../../stores/resource'

const resource = useResourceStore()
const report = ref<AssessmentReportData | null>(null)
const loading = ref(false)
const period = ref('week')

const sources = computed(() => report.value?.dataSources)
const latest = computed(() => report.value?.assessmentSummary.latest || null)
const pathLogs = computed(() => report.value?.pathAdjustments || [])
const mistakes = computed(() => report.value?.mistakeSummary || [])
const mistakeAnalytics = computed(() => report.value?.mistakeAnalytics)
const weakPoints = computed(() => report.value?.assessmentSummary.weakPoints || [])
const hasReportData = computed(() => {
  const data = sources.value
  if (!data) return false
  return data.assessmentResults + data.resourceFeedback + data.resourcePracticeRecords + data.pathAdjustments + data.mistakeRecords > 0
})

const mastery = computed(() => {
  if (!report.value) return []
  const baseScore = Math.max(0, Math.min(100, Math.round((report.value.mastery || 0) * 100)))
  const rows = weakPoints.value.map((name) => ({
    name,
    value: baseScore,
    status: baseScore >= 80 ? '稳定' : '需复盘',
  }))
  if (mistakes.value.length) {
    const value = mistakeAnalytics.value?.masteryRate || 0
    rows.push({
      name: '错题掌握',
      value,
      status: value >= 80 ? '稳定' : value > 0 ? '进行中' : '待订正',
    })
  }
  return rows
})

async function loadReport() {
  loading.value = true
  try {
    await resource.loadAll()
    report.value = await getAssessmentReportApi()
  } catch (error) {
    ElMessage.warning(error instanceof Error ? error.message : '学习报告服务暂不可用')
  } finally {
    loading.value = false
  }
}

onMounted(loadReport)
</script>

<template>
  <div class="page">
    <div class="page-breadcrumb">
      <span>学生端</span>
      <span>学习报告</span>
      <strong>真实学习状态汇总</strong>
    </div>

    <div class="page-header">
      <div>
        <h1 class="page-title">学习报告：由测评、错题和路径调整生成</h1>
        <p class="page-subtitle">本页直接读取后端报告接口，提交测评后会同步更新错因、错题本、路径调整和报告数据来源。</p>
      </div>
      <div class="head-actions">
        <el-segmented
          v-model="period"
          :options="[
            { label: '今日', value: 'today' },
            { label: '本周', value: 'week' },
            { label: '本月', value: 'month' },
          ]"
        />
        <el-button :loading="loading" @click="loadReport">刷新报告</el-button>
      </div>
    </div>

    <PageActionGuide
      title="先看本周结论，再决定下一步"
      description="报告会告诉你哪一块最弱、错因是什么、接下来该先做什么。"
      current-action="当前要做：先读关键结论，再回到学习路径或错题本完成补强。"
      primary-label="查看调整后的学习路径"
      primary-to="/student/learning-path"
      secondary-label="查看错题本"
      secondary-to="/student/mistakes"
    />

    <section v-if="loading" class="panel">
      <el-skeleton :rows="5" animated />
    </section>

    <section v-else-if="!hasReportData" class="empty-workbench">
      <div>
        <h3>还没有可汇总的学习数据</h3>
        <p>完成一次阶段测评或提交资源练习后，系统会保存评分、错因、错题和路径调整记录，并生成学习报告。</p>
        <router-link to="/student/assessment">
          <el-button type="primary">开始阶段测评</el-button>
        </router-link>
      </div>
    </section>

    <template v-else>
      <section class="panel report-summary">
        <div>
          <el-tag type="warning" effect="plain">本周关键结论</el-tag>
          <h2>{{ report?.summary }}</h2>
          <p>平均测评分：{{ report?.assessmentSummary.averageScore }}；薄弱点：{{ weakPoints.length ? weakPoints.join('、') : '暂无后端识别记录' }}。</p>
        </div>
        <router-link to="/student/learning-path">
          <el-button type="primary">查看调整后的学习路径</el-button>
        </router-link>
      </section>

      <section class="grid-3">
        <div class="panel metric-card">
          <div>
            <div class="stat-value">{{ sources?.assessmentResults || 0 }}</div>
            <div class="stat-label">测评记录</div>
          </div>
          <span class="metric-trend">来自 /api/assessments/submit</span>
        </div>
        <div class="panel metric-card">
          <div>
            <div class="stat-value">{{ sources?.mistakeRecords || 0 }}</div>
            <div class="stat-label">错题记录</div>
          </div>
          <span class="metric-trend">已写入错题本</span>
        </div>
        <div class="panel metric-card">
          <div>
            <div class="stat-value">{{ sources?.pathAdjustments || 0 }}</div>
            <div class="stat-label">路径调整</div>
          </div>
          <span class="metric-trend">测评后动态插入补强任务</span>
        </div>
      </section>

      <section class="grid-3">
        <div class="panel metric-card">
          <div>
            <div class="stat-value">{{ mistakeAnalytics?.pendingCorrection || 0 }}</div>
            <div class="stat-label">待订正错题</div>
          </div>
          <span class="metric-trend">先解决原题</span>
        </div>
        <div class="panel metric-card">
          <div>
            <div class="stat-value">{{ mistakeAnalytics?.masteryRate || 0 }}%</div>
            <div class="stat-label">错题掌握率</div>
          </div>
          <span class="metric-trend">{{ mistakeAnalytics?.mastered || 0 }}/{{ mistakeAnalytics?.total || 0 }} 已掌握</span>
        </div>
        <div class="panel metric-card">
          <div>
            <div class="stat-value">{{ mistakeAnalytics?.verificationPassRate || 0 }}%</div>
            <div class="stat-label">变式验证通过率</div>
          </div>
          <span class="metric-trend">平均订正 {{ mistakeAnalytics?.averageCorrectionAttempts || 0 }} 次</span>
        </div>
      </section>

      <section class="panel source-panel">
        <h2 class="section-title">报告数据来源</h2>
        <p class="section-desc">每一项结论都来自后端保存的学习状态，不再只是前端静态文案。</p>
        <div class="source-table">
          <div class="source-head">
            <span>来源</span>
            <span>数量</span>
            <span>对产品状态的影响</span>
          </div>
          <div class="source-line">
            <strong>阶段测评</strong>
            <span>{{ sources?.assessmentResults || 0 }} 条</span>
            <span>生成错因、薄弱点、画像更新草稿和路径调整记录。</span>
          </div>
          <div class="source-line">
            <strong>错题本</strong>
            <span>{{ sources?.mistakeRecords || 0 }} 条</span>
            <span>保存题干、学生答案、标准答案、错因和补强任务。</span>
          </div>
          <div class="source-line">
            <strong>资源反馈 / 练习</strong>
            <span>{{ sources?.resourceFeedback || 0 }} 条反馈，{{ sources?.resourcePracticeRecords || 0 }} 次练习</span>
            <span>影响资源效果分析和后续推荐。</span>
          </div>
          <div class="source-line">
            <strong>学习路径</strong>
            <span>{{ sources?.pathAdjustments || 0 }} 条调整</span>
            <span>展示测评前后路径对比和补强任务插入原因。</span>
          </div>
        </div>
      </section>

      <div class="report-layout">
        <section class="panel">
          <h2 class="section-title">知识点掌握矩阵</h2>
          <div v-if="mastery.length" class="mastery-list">
            <div v-for="item in mastery" :key="item.name">
              <div class="mastery-head">
                <strong>{{ item.name }}</strong>
                <span>{{ item.status }} · {{ item.value }}%</span>
              </div>
              <el-progress :percentage="item.value" :status="item.value >= 80 ? 'success' : item.value < 65 ? 'warning' : undefined" />
            </div>
          </div>
          <p v-else class="section-desc">暂无后端识别的知识点掌握记录。</p>
        </section>

        <section class="panel">
          <h2 class="section-title">最近一次测评</h2>
          <div v-if="latest" class="latest-card">
            <strong>{{ latest.score }} 分</strong>
            <p>薄弱点：{{ latest.weakness?.join('、') || '暂无' }}</p>
            <p>错因：{{ latest.errorReasons?.join('；') || '暂无明显错因' }}</p>
            <small>记录 ID：{{ latest.id }}</small>
          </div>
          <p v-else class="section-desc">暂无测评记录。</p>
        </section>

        <section class="panel">
          <h2 class="section-title">路径调整记录</h2>
          <div class="log-list">
            <div v-for="(item, index) in pathLogs.slice(0, 4)" :key="index" class="log-card">
              <strong>{{ (item as any).trigger || '学习路径调整' }}</strong>
              <p>{{ (item as any).reason }}</p>
              <small>{{ (item as any).before }} → {{ (item as any).after }}</small>
            </div>
          </div>
        </section>

        <section class="panel">
          <h2 class="section-title">下一步建议</h2>
          <div class="action-list">
            <router-link v-for="item in report?.next_actions || []" :key="item" to="/student/learning-path">
              <el-button>{{ item }}</el-button>
            </router-link>
            <router-link to="/student/mistakes">
              <el-button type="primary">查看错题本</el-button>
            </router-link>
          </div>
        </section>

        <section class="panel">
          <h2 class="section-title">高频薄弱知识点</h2>
          <div v-if="mistakeAnalytics?.knowledgeBreakdown.length" class="log-list">
            <div v-for="item in mistakeAnalytics.knowledgeBreakdown" :key="item.knowledge" class="log-card">
              <strong>{{ item.knowledge }} · {{ item.total }} 题</strong>
              <p>待订正 {{ item.pendingCorrection }}，待验证 {{ item.pendingVerification }}，已掌握 {{ item.mastered }}</p>
            </div>
          </div>
          <p v-else class="section-desc">暂无错题知识点统计。</p>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.head-actions,
.action-list {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.report-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 16px;
}

.report-summary h2 {
  margin: 10px 0 6px;
  font-size: 20px;
}

.report-summary p,
.section-desc {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.source-panel,
.report-layout {
  margin-top: 16px;
}

.source-table {
  margin-top: 14px;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 8px;
}

.source-head,
.source-line {
  display: grid;
  grid-template-columns: 0.8fr 0.7fr 1.8fr;
  gap: 12px;
  align-items: center;
  padding: 12px;
}

.source-head {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 700;
  background: #f8fafc;
  border-bottom: 1px solid var(--color-border);
}

.source-line {
  border-bottom: 1px solid var(--color-border);
}

.source-line:last-child {
  border-bottom: 0;
}

.source-line span {
  color: var(--color-text-secondary);
  line-height: 1.55;
}

.report-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}

.mastery-list,
.log-list {
  display: grid;
  gap: 14px;
}

.mastery-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.mastery-head span,
.latest-card small,
.log-card small {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.latest-card,
.log-card {
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.latest-card p,
.log-card p {
  margin: 8px 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

@media (max-width: 1100px) {
  .report-summary,
  .report-layout,
  .source-head,
  .source-line {
    grid-template-columns: 1fr;
  }

  .report-summary {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
