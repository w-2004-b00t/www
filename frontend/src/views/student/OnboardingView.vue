<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { BookOpenCheck, CheckCircle2, ChevronDown, Clock3, Sparkles, Target } from 'lucide-vue-next'
import { useOnboardingStore } from '../../stores/onboarding'
import { useProfileStore } from '../../stores/profile'

const router = useRouter()
const onboarding = useOnboardingStore()
const profile = useProfileStore()
const showAdvanced = ref(false)

const timePresets = [30, 45, 60, 90]
const preferenceOptions = ['图解', '例题', '代码实践', '短视频', '拓展阅读']
const promptExamples = [
  '我想弄懂课程资料怎么算，公式容易混，最好有例题和代码，今天有 45 分钟。',
  '我在复习课程资料，课程资料和课程资料总是分不清，希望先看图解再做练习。',
  '我想完成《数据结构课程》作业，需要掌握课程资料待上传，并用 课程资料 做一个小实验。',
]

const activeCourseName = computed(() => onboarding.selectedCourse?.name || '数据结构课程')
const description = computed({
  get: () => onboarding.studyGoal,
  set: (value: string) => {
    onboarding.studyGoal = value
  },
})
const missingItems = computed(() => {
  const items: string[] = []
  if (!description.value.trim()) items.push('学习目标')
  if (!onboarding.weakPoint.trim()) items.push('薄弱点')
  if (!onboarding.preference.length) items.push('资源偏好')
  if (!onboarding.dailyMinutes) items.push('可用时间')
  return items
})
const readiness = computed(() => {
  let score = 30
  if (description.value.trim()) score += 40
  if (onboarding.weakPoint.trim()) score += 12
  if (onboarding.preference.length) score += 12
  if (onboarding.dailyMinutes) score += 6
  return Math.min(100, score)
})
const readinessText = computed(() => {
  if (!missingItems.value.length) return '信息足够生成画像草稿'
  return `还缺：${missingItems.value.join(' / ')}`
})

onMounted(() => {
  onboarding.loadCourses()
})

function togglePreference(item: string) {
  onboarding.preference = onboarding.preference.includes(item)
    ? onboarding.preference.filter((value) => value !== item)
    : [...onboarding.preference, item]
}

function useExample(example: string) {
  description.value = example
}

async function startLearning() {
  const userText = description.value.trim()
  if (!userText) {
    ElMessage.warning('请先用一句话描述今天想学什么')
    return
  }

  onboarding.complete()
  const preferenceText = onboarding.preference.length ? onboarding.preference.join('、') : '暂未选择'
  const message = [
    `课程：${activeCourseName.value}`,
    `学生描述：${userText}`,
    `补充薄弱点：${onboarding.weakPoint || '等待系统从描述中识别'}`,
    `资源偏好：${preferenceText}`,
    `今天可用时间：${onboarding.dailyMinutes} 分钟`,
  ].join('\n')

  await profile.extractFromMessage(message)
  router.push('/student/profile-chat')
}
</script>

<template>
  <div class="page onboarding-page">
    <header class="onboarding-head">
      <div>
        <div class="eyebrow">学生端 / 学习起点</div>
        <h1>今天你想学什么？</h1>
        <p>用自己的话描述学习任务，系统会抽取画像草稿，确认后再生成资源、路径和测评。</p>
      </div>
      <div class="readiness-card">
        <span>画像准备度</span>
        <strong>{{ readiness }}%</strong>
        <small>{{ readinessText }}</small>
      </div>
    </header>

    <section class="course-strip">
      <div class="course-pill">
        <BookOpenCheck :size="17" />
        <span>当前课程</span>
        <strong>《{{ activeCourseName }}》</strong>
      </div>
      <div class="flow-mini">
        <span class="active">学习起点</span>
        <i />
        <span>画像确认</span>
        <i />
        <span>资源与路径</span>
      </div>
    </section>

    <section class="start-card">
      <div class="card-title">
        <Sparkles :size="18" />
        <div>
          <h2>描述你的学习任务</h2>
          <p>不用按模板填写，越接近日常表达，画像智能体越能识别真实需求。</p>
        </div>
      </div>

      <el-input
        v-model="description"
        class="natural-input"
        type="textarea"
        :rows="7"
        resize="none"
        placeholder="例如：我想弄懂课程资料怎么算，公式容易混，最好有例题和代码，今天有 45 分钟。"
      />

      <div class="assist-row">
        <span>快速示例</span>
        <button v-for="example in promptExamples" :key="example" type="button" @click="useExample(example)">
          {{ example }}
        </button>
      </div>

      <div class="quick-options">
        <div>
          <label>
            <Clock3 :size="15" />
            今天可学习时间
          </label>
          <div class="chip-row">
            <button
              v-for="item in timePresets"
              :key="item"
              type="button"
              :class="{ selected: onboarding.dailyMinutes === item }"
              @click="onboarding.dailyMinutes = item"
            >
              {{ item }} 分钟
            </button>
          </div>
        </div>

        <div>
          <label>
            <Target :size="15" />
            资源偏好
          </label>
          <div class="chip-row">
            <button
              v-for="item in preferenceOptions"
              :key="item"
              type="button"
              :class="{ selected: onboarding.preference.includes(item) }"
              @click="togglePreference(item)"
            >
              {{ item }}
            </button>
          </div>
        </div>
      </div>

      <div class="advanced-toggle">
        <button type="button" @click="showAdvanced = !showAdvanced">
          补充薄弱点
          <ChevronDown :size="15" :class="{ open: showAdvanced }" />
        </button>
        <span>可选。你不填，系统也会从描述中自动识别。</span>
      </div>

      <div v-if="showAdvanced" class="advanced-panel">
        <el-input
          v-model="onboarding.weakPoint"
          placeholder="例如：课程资料公式、课程资料待上传、课程资料、课程资料 参数"
        />
      </div>

      <footer class="start-actions">
        <div class="next-note">
          <CheckCircle2 :size="16" />
          下一步：进入画像确认页，低置信推断需要你确认后才会写入。
        </div>
        <el-button type="primary" size="large" :loading="profile.isExtracting" @click="startLearning">
          生成画像草稿
        </el-button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.onboarding-page {
  width: min(1180px, calc(100vw - 64px));
  margin: 0 auto;
  padding: 28px 0 40px;
}

.onboarding-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 18px;
}

.eyebrow {
  color: var(--color-text-secondary);
  font-size: 13px;
  margin-bottom: 8px;
}

.onboarding-head h1 {
  margin: 0;
  font-size: 28px;
  line-height: 1.2;
}

.onboarding-head p {
  margin: 10px 0 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.readiness-card {
  display: grid;
  gap: 3px;
  min-width: 180px;
  padding: 12px 14px;
  text-align: right;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.readiness-card span,
.readiness-card small {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.readiness-card strong {
  color: var(--color-primary);
  font-size: 24px;
}

.course-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: #fff;
}

.course-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.course-pill svg {
  color: var(--color-primary);
}

.course-pill strong {
  color: var(--color-text-primary);
  font-size: 14px;
}

.flow-mini {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--color-text-secondary);
  font-size: 13px;
  white-space: nowrap;
}

.flow-mini span.active {
  color: var(--color-primary);
  font-weight: 700;
}

.flow-mini i {
  width: 34px;
  height: 1px;
  background: var(--color-border);
}

.start-card {
  padding: 24px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: #fff;
}

.card-title {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-bottom: 16px;
}

.card-title svg {
  margin-top: 4px;
  color: var(--color-primary);
}

.card-title h2 {
  margin: 0;
  font-size: 19px;
}

.card-title p {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.natural-input :deep(.el-textarea__inner) {
  min-height: 180px !important;
  padding: 16px;
  border-radius: 10px;
  line-height: 1.7;
  font-size: 15px;
}

.assist-row {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.assist-row span,
.quick-options label,
.advanced-toggle span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.assist-row button {
  padding: 9px 12px;
  color: var(--color-text-secondary);
  text-align: left;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
  cursor: pointer;
}

.assist-row button:hover {
  color: var(--color-primary);
  border-color: #bfdbfe;
  background: #eff6ff;
}

.quick-options {
  display: grid;
  grid-template-columns: minmax(0, 0.75fr) minmax(0, 1.25fr);
  gap: 18px;
  margin-top: 22px;
}

.quick-options label {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 9px;
  font-weight: 700;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip-row button {
  height: 34px;
  padding: 0 13px;
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
}

.chip-row button.selected {
  color: var(--color-primary);
  border-color: #bfdbfe;
  background: #eff6ff;
}

.advanced-toggle {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--color-border);
}

.advanced-toggle button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 11px;
  color: var(--color-text-primary);
  font-weight: 700;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
}

.advanced-toggle svg {
  transition: transform 0.15s ease;
}

.advanced-toggle svg.open {
  transform: rotate(180deg);
}

.advanced-panel {
  margin-top: 12px;
}

.start-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-top: 22px;
}

.next-note {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.next-note svg {
  color: #16a34a;
}

@media (max-width: 960px) {
  .onboarding-page {
    width: calc(100vw - 32px);
  }

  .onboarding-head,
  .course-strip,
  .start-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .readiness-card {
    text-align: left;
  }

  .flow-mini {
    flex-wrap: wrap;
    white-space: normal;
  }

  .quick-options {
    grid-template-columns: 1fr;
  }
}
</style>
