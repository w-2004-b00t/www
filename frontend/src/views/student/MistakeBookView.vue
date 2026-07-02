<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import PageActionGuide from '../../components/common/PageActionGuide.vue'
import {
  generateSimilarMistakeApi,
  listTutorMistakesApi,
  submitMistakeCorrectionApi,
  submitMistakeVerificationApi,
  type TutorMistakeRecord,
} from '../../api/tutor'

const mistakes = ref<TutorMistakeRecord[]>([])
const loading = ref(false)
const activeId = ref('')
const submittingId = ref('')
const correctionAnswers = reactive<Record<string, string>>({})
const verificationAnswers = reactive<Record<string, Record<string, string>>>({})

const counts = computed(() => ({
  correction: mistakes.value.filter((item) => item.status === '待订正' || item.status === '订正中').length,
  verification: mistakes.value.filter((item) => item.status === '待验证').length,
  mastered: mistakes.value.filter((item) => item.status === '已掌握').length,
}))

async function loadMistakes() {
  loading.value = true
  try {
    mistakes.value = await listTutorMistakesApi()
  } finally {
    loading.value = false
  }
}

function replaceMistake(updated: TutorMistakeRecord) {
  mistakes.value = mistakes.value.map((item) => (item.id === updated.id ? updated : item))
}

function openWorkbench(item: TutorMistakeRecord) {
  activeId.value = activeId.value === item.id ? '' : item.id
  if (!(item.id in correctionAnswers)) correctionAnswers[item.id] = ''
  if (!verificationAnswers[item.id]) verificationAnswers[item.id] = {}
}

async function submitCorrection(item: TutorMistakeRecord) {
  const answer = correctionAnswers[item.id]?.trim()
  if (!answer) {
    ElMessage.warning('请先完成原题订正。')
    return
  }
  submittingId.value = item.id
  try {
    const response = await submitMistakeCorrectionApi(item.id, answer, item.version)
    replaceMistake(response.mistake)
    if (response.result.correct) {
      ElMessage.success(`原题订正 ${response.result.score} 分，已解锁变式题验证。`)
    } else {
      ElMessage.warning(`本次 ${response.result.score} 分，请根据缺失评分点继续订正。`)
    }
  } finally {
    submittingId.value = ''
  }
}

async function startVerification(item: TutorMistakeRecord) {
  submittingId.value = item.id
  try {
    const response = await generateSimilarMistakeApi(item.id, item.version)
    replaceMistake(response.mistake)
    verificationAnswers[item.id] ||= {}
    activeId.value = item.id
    ElMessage.success(
      response.generationMode === 'rag_llm'
        ? '已基于课程资料生成 2 道 AI 变式题。'
        : 'AI 暂不可用，已生成 2 道可信规则变式题。',
    )
  } finally {
    submittingId.value = ''
  }
}

async function submitVerification(item: TutorMistakeRecord) {
  const answers = verificationAnswers[item.id] || {}
  if (item.verificationQuestions.some((question) => !String(answers[question.id] || '').trim())) {
    ElMessage.warning('请完成全部变式题后再提交。')
    return
  }
  submittingId.value = item.id
  try {
    const response = await submitMistakeVerificationApi(item.id, answers, item.version)
    replaceMistake(response.mistake)
    response.passed
      ? ElMessage.success('原题与变式题均已通过，系统已判定掌握。')
      : ElMessage.warning('变式题尚未全部通过，请查看反馈后再次作答。')
  } finally {
    submittingId.value = ''
  }
}

function statusType(status: TutorMistakeRecord['status']) {
  if (status === '已掌握') return 'success'
  if (status === '待验证') return 'primary'
  if (status === '订正中') return 'danger'
  return 'warning'
}

function typeLabel(type: string) {
  return {
    single: '单选题',
    short: '简答题',
    calculation: '计算题',
    code: '代码题',
    case: '综合应用题',
  }[type] || '简答题'
}

onMounted(loadMistakes)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">错题本</h1>
        <p class="page-subtitle">先解决原错题，再通过变式题验证是否真正掌握。</p>
      </div>
      <router-link to="/student/assessment">
        <el-button type="primary">开始阶段测评</el-button>
      </router-link>
    </div>

    <PageActionGuide
      title="先重做原题，解决错误后再做变式题"
      description="原题达到 70 分才会解锁变式题；变式题全部通过后，系统才会自动判定已掌握。"
      current-action="当前要做：打开一条待订正错题，用自己的答案重新完成它。"
      primary-label="开始阶段测评"
      primary-to="/student/assessment"
      secondary-label="查看学习报告"
      secondary-to="/student/report"
    />

    <section class="panel">
      <div class="mistake-toolbar">
        <div>
          <h2 class="section-title">订正进度</h2>
          <p class="section-desc">掌握状态只由实际作答结果更新。</p>
        </div>
        <div class="progress-stats">
          <el-tag type="warning" effect="plain">待订正 {{ counts.correction }}</el-tag>
          <el-tag type="primary" effect="plain">待验证 {{ counts.verification }}</el-tag>
          <el-tag type="success" effect="plain">已掌握 {{ counts.mastered }}</el-tag>
        </div>
      </div>

      <div v-loading="loading" class="mistake-list">
        <el-empty v-if="!loading && !mistakes.length" description="还没有错题，先完成一次阶段测评吧。" />
        <article v-for="item in mistakes" :key="item.id" class="mistake-card">
          <div class="mistake-head">
            <div class="title-block">
              <div class="tag-row">
                <el-tag effect="plain">{{ item.knowledge }}</el-tag>
                <el-tag type="info" effect="plain">{{ typeLabel(item.type) }}</el-tag>
                <el-tag v-if="item.generationMode" :type="item.generationMode === 'rag_llm' ? 'primary' : 'info'" effect="plain">
                  {{ item.generationMode === 'rag_llm' ? 'AI 变式题' : '规则变式题' }}
                </el-tag>
              </div>
              <h3>{{ item.stem }}</h3>
            </div>
            <el-tag :type="statusType(item.status)" effect="plain">{{ item.status }}</el-tag>
          </div>

          <div class="mistake-summary">
            <div>
              <span>原作答</span>
              <p>{{ item.userAnswer || '未记录' }}</p>
            </div>
            <div>
              <span>参考答案</span>
              <p>{{ item.answer || '暂无参考答案' }}</p>
            </div>
            <div>
              <span>错因</span>
              <p>{{ item.wrongReason }}</p>
            </div>
          </div>

          <div class="mistake-actions">
            <el-button
              v-if="item.status === '待订正' || item.status === '订正中'"
              type="primary"
              @click="openWorkbench(item)"
            >
              重做原题
            </el-button>
            <el-button
              v-else-if="item.status === '待验证' && !item.verificationQuestions.length"
              type="primary"
              :loading="submittingId === item.id"
              @click="startVerification(item)"
            >
              开始变式验证
            </el-button>
            <el-button
              v-else-if="item.status === '待验证'"
              type="primary"
              @click="openWorkbench(item)"
            >
              继续变式验证
            </el-button>
            <el-button v-else type="success" plain @click="openWorkbench(item)">查看订正记录</el-button>
          </div>

          <section v-if="activeId === item.id" class="workbench">
            <template v-if="item.status === '待订正' || item.status === '订正中'">
              <h4>第一步：重新完成原题</h4>
              <el-radio-group
                v-if="item.options?.length"
                v-model="correctionAnswers[item.id]"
                class="answer-options"
              >
                <el-radio v-for="option in item.options" :key="option" :value="option">{{ option }}</el-radio>
              </el-radio-group>
              <el-input
                v-else
                v-model="correctionAnswers[item.id]"
                type="textarea"
                :rows="item.type === 'code' ? 7 : 4"
                placeholder="请用自己的话重新作答。提交后才会显示参考答案和解析。"
              />
              <el-button
                type="primary"
                :loading="submittingId === item.id"
                @click="submitCorrection(item)"
              >
                提交原题订正
              </el-button>
            </template>

            <section v-if="item.latestCorrection" class="feedback-panel" :class="{ passed: item.latestCorrection.correct }">
              <h4>最近一次订正：{{ item.latestCorrection.score }} 分</h4>
              <p><strong>你的答案：</strong>{{ item.latestCorrection.answer }}</p>
              <p v-if="item.latestCorrection.missingKeywords.length">
                <strong>缺失评分点：</strong>{{ item.latestCorrection.missingKeywords.join('、') }}
              </p>
              <p v-if="item.latestCorrection.errorReason"><strong>反馈：</strong>{{ item.latestCorrection.errorReason }}</p>
              <p><strong>解析：</strong>{{ item.analysis }}</p>
            </section>

            <template v-if="item.status === '待验证' && item.verificationQuestions.length">
              <h4>第二步：完成变式题，验证知识迁移</h4>
              <section v-for="(question, index) in item.verificationQuestions" :key="question.id" class="verification-question">
                <strong>变式题 {{ index + 1 }}：{{ question.stem }}</strong>
                <el-input
                  v-model="verificationAnswers[item.id][question.id]"
                  type="textarea"
                  :rows="4"
                  placeholder="请独立作答，全部完成后统一提交。"
                />
              </section>
              <el-button
                type="primary"
                :loading="submittingId === item.id"
                @click="submitVerification(item)"
              >
                提交变式题验证
              </el-button>
            </template>

            <section v-if="item.latestVerification" class="feedback-panel" :class="{ passed: item.latestVerification.passed }">
              <h4>{{ item.latestVerification.passed ? '变式题验证通过' : '变式题仍需复盘' }}</h4>
              <div v-for="(result, index) in item.latestVerification.results" :key="result.questionId">
                <p><strong>第 {{ index + 1 }} 题：</strong>{{ result.score }} 分</p>
                <p v-if="result.missingKeywords.length">缺失：{{ result.missingKeywords.join('、') }}</p>
              </div>
            </section>

            <section v-if="item.status === '已掌握'" class="mastery-panel">
              <h4>掌握证据</h4>
              <p v-for="evidence in item.masteryEvidence" :key="evidence">{{ evidence }}</p>
            </section>
          </section>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.mistake-toolbar,
.mistake-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.progress-stats,
.tag-row,
.mistake-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.mistake-list {
  display: grid;
  gap: 14px;
  margin-top: 16px;
}

.mistake-card {
  padding: 18px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: #fff;
}

.title-block {
  min-width: 0;
}

.mistake-head h3 {
  margin: 10px 0 0;
  font-size: 17px;
  line-height: 1.6;
}

.mistake-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin: 14px 0;
}

.mistake-summary > div,
.feedback-panel,
.mastery-panel {
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.mistake-summary span {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.mistake-summary p,
.feedback-panel p,
.mastery-panel p {
  margin: 6px 0 0;
  line-height: 1.6;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.workbench {
  display: grid;
  gap: 14px;
  margin-top: 16px;
  padding: 16px;
  border: 1px solid #bfdbfe;
  border-radius: 10px;
  background: #f8fbff;
}

.workbench h4 {
  margin: 0;
}

.answer-options {
  display: grid;
  gap: 8px;
}

.feedback-panel {
  border-color: #fecaca;
  background: #fff7f7;
}

.feedback-panel.passed,
.mastery-panel {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.verification-question {
  display: grid;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

@media (max-width: 900px) {
  .mistake-toolbar,
  .mistake-head {
    align-items: stretch;
    flex-direction: column;
  }

  .mistake-summary {
    grid-template-columns: 1fr;
  }
}
</style>
