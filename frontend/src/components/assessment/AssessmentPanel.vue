<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { submitAssessmentApi } from '../../api/assessment'
import type { AssessmentPaper, LearningPath, ProfileUpdateDraft } from '../../types/common'

const props = defineProps<{ paper: AssessmentPaper }>()

type QuestionDetail = {
  question_id: string
  knowledge_point: string
  score: number
  correct: boolean
  rubric: string
  error_reason?: string
  hit_keywords?: string[]
  missing_keywords?: string[]
}

type AssessmentResult = {
  assessmentId?: string
  score: number
  weakness: string[]
  suggestion: string
  adjustedPath?: LearningPath
  mistakes_added?: number
  path_adjustment?: {
    before?: string
    after?: string
    reason?: string
    beforePath?: string[]
    afterPath?: string[]
  }
  error_reasons?: string[]
  profile_update_drafts?: ProfileUpdateDraft[]
  profileUpdateDrafts?: ProfileUpdateDraft[]
  rubric_version?: string
  question_details?: QuestionDetail[]
}

const emit = defineEmits<{ adjusted: [result: AssessmentResult] }>()

const submitted = ref(false)
const submitting = ref(false)
const result = ref<AssessmentResult | null>(null)
const activeQuestionId = ref(props.paper.questions[0]?.id || '')
const answers = reactive<Record<string, string>>({})
const expandedAnswers = reactive<Record<string, boolean>>({})

const answeredCount = computed(() => props.paper.questions.filter((question) => isAnswered(question.id)).length)
const progressPercent = computed(() => Math.round((answeredCount.value / Math.max(props.paper.questions.length, 1)) * 100))
const estimatedMinutes = computed(() => Math.max(20, props.paper.questions.length * 3))
const objectiveQuestions = computed(() => props.paper.questions.filter((question) => question.type === 'single'))
const majorSubjectiveQuestions = computed(() => props.paper.questions.filter((question) => question.type !== 'single' && question.type !== 'case'))

function isAnswered(questionId: string) {
  return Boolean(String(answers[questionId] || '').trim())
}

function questionDetail(questionId: string) {
  return result.value?.question_details?.find((item) => item.question_id === questionId)
}

function questionStatus(questionId: string) {
  const detail = questionDetail(questionId)
  if (detail) return detail.correct ? 'success' : 'danger'
  return isAnswered(questionId) ? 'primary' : 'info'
}

function questionStatusText(questionId: string) {
  const detail = questionDetail(questionId)
  if (detail) return detail.correct ? '已得分' : '需复盘'
  return isAnswered(questionId) ? '已作答' : '未作答'
}

function toggleReferenceAnswer(questionId: string) {
  expandedAnswers[questionId] = !expandedAnswers[questionId]
}

function typeLabel(type: string) {
  const labels: Record<string, string> = {
    single: '单选题',
    short: '简答题',
    calculation: '计算题',
    code: '代码题',
    case: '综合应用题',
  }
  return labels[type] || '试题'
}

function answerPlaceholder(type: string) {
  if (type === 'code') return '请写出伪代码或代码框架，至少包含初始化、核心操作、输出和边界处理。'
  if (type === 'calculation') return '请写出复杂度判断过程，例如操作次数如何随数据规模变化。'
  if (type === 'case') return '请按“概念解释、操作追踪、复杂度分析、代码验证”的顺序组织答案。'
  return '请结合课程资料，用自己的话写出关键概念和判断依据。'
}

function scrollToQuestion(questionId: string) {
  activeQuestionId.value = questionId
  document.getElementById(`assessment-${questionId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function validateBeforeSubmit() {
  const missingObjective = objectiveQuestions.value.filter((question) => !isAnswered(question.id))
  if (missingObjective.length) {
    ElMessage.warning(`还有 ${missingObjective.length} 道单选题未作答。`)
    scrollToQuestion(missingObjective[0].id)
    return false
  }
  const missingMajor = majorSubjectiveQuestions.value.filter((question) => !isAnswered(question.id))
  if (missingMajor.length) {
    ElMessage.warning(`还有 ${missingMajor.length} 道主观题未作答，请至少完成简答、计算和代码题。`)
    scrollToQuestion(missingMajor[0].id)
    return false
  }
  return true
}

async function submit() {
  if (submitting.value || submitted.value || !validateBeforeSubmit()) return
  submitting.value = true
  try {
    result.value = await submitAssessmentApi(props.paper.assessmentId, answers)
    submitted.value = true
    emit('adjusted', result.value)
    ElMessage.success('测评已提交，系统已生成错因、错题记录和画像更新建议。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '测评提交失败，请检查后端服务后重试。')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="assessment-shell">
    <section class="panel paper-overview">
      <div>
        <el-tag type="primary" effect="plain">正式试卷</el-tag>
        <h2>{{ paper.title }}</h2>
        <p>{{ paper.sourceSummary }}</p>
      </div>
      <div class="paper-stats">
        <div>
          <span>题量</span>
          <strong>{{ paper.questions.length }} 道</strong>
        </div>
        <div>
          <span>预计用时</span>
          <strong>{{ estimatedMinutes }} 分钟</strong>
        </div>
        <div>
          <span>完成进度</span>
          <strong>{{ answeredCount }}/{{ paper.questions.length }}</strong>
        </div>
      </div>
      <el-progress :percentage="progressPercent" :stroke-width="10" />
    </section>

    <div class="assessment-layout">
      <aside class="question-nav">
        <strong>题号导航</strong>
        <div class="nav-grid">
          <button
            v-for="(question, index) in paper.questions"
            :key="question.id"
            type="button"
            :class="{ active: activeQuestionId === question.id, answered: isAnswered(question.id) }"
            @click="scrollToQuestion(question.id)"
          >
            {{ index + 1 }}
          </button>
        </div>
        <el-button type="primary" :loading="submitting" :disabled="submitted" @click="submit">
          {{ submitting ? '正在按 Rubric 评分' : '提交测评并生成反馈' }}
        </el-button>
      </aside>

      <main class="assessment-list">
        <el-card
          v-for="(question, index) in paper.questions"
          :id="`assessment-${question.id}`"
          :key="question.id"
          shadow="never"
          class="question-card"
        >
          <div class="question-head">
            <div>
              <span class="question-index">第 {{ index + 1 }} 题</span>
              <strong>{{ question.stem }}</strong>
            </div>
            <div class="question-tags">
              <el-tag>{{ question.difficulty }}</el-tag>
              <el-tag effect="plain">{{ typeLabel(question.type) }}</el-tag>
              <el-tag type="info" effect="plain">{{ question.knowledgePoint }}</el-tag>
              <el-tag :type="questionStatus(question.id)" effect="plain">{{ questionStatusText(question.id) }}</el-tag>
            </div>
          </div>

          <el-radio-group v-if="question.options" v-model="answers[question.id]" class="options">
            <el-radio v-for="option in question.options" :key="option" :value="option">{{ option }}</el-radio>
          </el-radio-group>
          <el-input
            v-else
            v-model="answers[question.id]"
            type="textarea"
            :rows="question.type === 'code' || question.type === 'case' ? 6 : 4"
            resize="vertical"
            :placeholder="answerPlaceholder(question.type)"
          />

          <section v-if="submitted" class="analysis" :class="{ wrong: questionDetail(question.id)?.correct === false }">
            <p v-if="answers[question.id]"><strong>你的答案：</strong>{{ answers[question.id] }}</p>
            <p v-if="questionDetail(question.id)">
              <strong>题目级 Rubric 评分：</strong>{{ questionDetail(question.id)?.score }} 分；
              {{ questionDetail(question.id)?.rubric }}
            </p>
            <p v-if="questionDetail(question.id)?.error_reason">
              <strong>错因：</strong>{{ questionDetail(question.id)?.error_reason }}
            </p>
            <p v-if="questionDetail(question.id)?.missing_keywords?.length">
              <strong>缺失评分点：</strong>{{ questionDetail(question.id)?.missing_keywords?.join('、') }}
            </p>
            <el-button class="reference-toggle" plain size="small" @click="toggleReferenceAnswer(question.id)">
              {{ expandedAnswers[question.id] ? '收起参考答案' : '查看参考答案与解析' }}
            </el-button>
            <div v-if="expandedAnswers[question.id]" class="reference-answer">
              <p><strong>参考答案：</strong>{{ question.answer }}</p>
              <p><strong>解析：</strong>{{ question.analysis }}</p>
            </div>
            <p v-if="expandedAnswers[question.id] && question.citations?.length">
              <strong>引用来源：</strong>
              {{ question.citations.map((item) => `${item.documentName} ${item.sourceLocation}`).join('；') }}
            </p>
          </section>
        </el-card>

        <el-alert v-if="result" type="warning" show-icon :closable="false" class="result-alert">
          <p><strong>本次得分：</strong>{{ result.score }} 分</p>
          <p><strong>薄弱点：</strong>{{ result.weakness.length ? result.weakness.join('、') : '暂无明显薄弱点' }}</p>
          <p><strong>路径调整：</strong>{{ result.suggestion }}</p>
          <p v-if="result.profileUpdateDrafts?.length || result.profile_update_drafts?.length">
            <strong>画像更新建议：</strong>已生成
            {{ (result.profileUpdateDrafts || result.profile_update_drafts || []).length }} 条，需到学习画像页确认后写入。
          </p>
          <p v-if="result.rubric_version"><strong>评分规则：</strong>{{ result.rubric_version }}</p>
        </el-alert>

        <section v-if="result?.question_details?.length" class="score-panel">
          <h3>题目级 Rubric 评分</h3>
          <div v-for="detail in result.question_details" :key="detail.question_id" class="score-line">
            <div>
              <strong>{{ detail.question_id }} · {{ detail.knowledge_point }}</strong>
              <span>{{ detail.rubric }}</span>
            </div>
            <el-tag :type="detail.correct ? 'success' : 'warning'" effect="plain">{{ detail.score }} 分</el-tag>
            <p v-if="detail.error_reason">{{ detail.error_reason }}</p>
            <small v-if="detail.hit_keywords?.length">命中：{{ detail.hit_keywords.join('、') }}</small>
            <small v-if="detail.missing_keywords?.length">缺失：{{ detail.missing_keywords.join('、') }}</small>
          </div>
        </section>
      </main>
    </div>
  </div>
</template>

<style scoped>
.assessment-shell {
  display: grid;
  gap: 16px;
}

.paper-overview {
  display: grid;
  gap: 14px;
}

.paper-overview h2 {
  margin: 10px 0 6px;
  font-size: 22px;
}

.paper-overview p,
.analysis p,
.result-alert p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.paper-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.paper-stats div {
  display: grid;
  gap: 6px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.paper-stats span,
.question-index {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.assessment-layout {
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.question-nav {
  position: sticky;
  top: 84px;
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.nav-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}

.nav-grid button {
  height: 34px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: #f8fafc;
  color: var(--color-text);
  cursor: pointer;
}

.nav-grid button.answered {
  border-color: #93c5fd;
  background: #eff6ff;
  color: #1d4ed8;
}

.nav-grid button.active {
  border-color: #2563eb;
  background: #2563eb;
  color: #fff;
}

.assessment-list {
  display: grid;
  gap: 14px;
}

.question-card {
  scroll-margin-top: 88px;
}

.question-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.question-head > div:first-child {
  display: grid;
  gap: 6px;
}

.question-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

.options {
  display: grid;
  gap: 8px;
}

.analysis {
  display: grid;
  gap: 6px;
  margin-top: 12px;
  padding: 12px;
  border: 1px solid #bbf7d0;
  border-radius: 8px;
  background: #f0fdf4;
}

.analysis.wrong {
  border-color: #fecaca;
  background: #fef2f2;
}

.reference-toggle {
  justify-self: start;
  margin-top: 4px;
}

.reference-answer {
  display: grid;
  gap: 6px;
  padding-top: 8px;
  border-top: 1px dashed currentColor;
}

.result-alert {
  margin-top: 4px;
}

.score-panel {
  display: grid;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.score-panel h3 {
  margin: 0;
  font-size: 16px;
}

.score-line {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 6px 12px;
  padding-top: 10px;
  border-top: 1px solid var(--color-border);
}

.score-line:first-of-type {
  border-top: 0;
}

.score-line span,
.score-line p,
.score-line small {
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.score-line p,
.score-line small {
  grid-column: 1 / -1;
  margin: 0;
}

@media (max-width: 900px) {
  .paper-stats,
  .assessment-layout {
    grid-template-columns: 1fr;
  }

  .question-nav {
    position: static;
  }

  .question-head {
    flex-direction: column;
  }

  .question-tags {
    justify-content: flex-start;
  }
}
</style>
