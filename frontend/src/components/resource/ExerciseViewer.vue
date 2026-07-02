<script setup lang="ts">
import { computed } from 'vue'

interface Exercise {
  type: string
  difficulty: string
  stem: string
  options?: string[]
  answer: string
  analysis: string
}

const props = defineProps<{
  content: string
  modelValue?: Record<string, string>
  resultDetails?: {
    index: number
    correct: boolean
    userAnswer?: string
    answer?: string
    analysis?: string
    knowledgePoint?: string
  }[]
}>()
const emit = defineEmits<{ 'update:modelValue': [value: Record<string, string>] }>()

const exercises = computed<Exercise[]>(() => {
  try {
    const parsed = JSON.parse(props.content)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
})

function resultType(index: number) {
  const detail = props.resultDetails?.find((item) => item.index === index)
  if (!detail) return ''
  return detail.correct ? 'success' : 'danger'
}

function resultText(index: number) {
  const detail = props.resultDetails?.find((item) => item.index === index)
  if (!detail) return ''
  return detail.correct ? '已答对' : '需复盘'
}

function updateAnswer(index: number, answer: string) {
  emit('update:modelValue', {
    ...(props.modelValue || {}),
    [String(index)]: answer,
  })
}

function hasResult(index: number) {
  return Boolean(props.resultDetails?.some((item) => item.index === index))
}

function typeLabel(type: string) {
  const labels: Record<string, string> = {
    single: '选择题',
    short: '简答题',
    calculation: '计算题',
    code: '代码题',
    case: '综合应用题',
  }
  return labels[type] || '练习题'
}

function answerPlaceholder(type: string) {
  if (type === 'code') {
    return '请输入数据结构实践代码，建议包含初始化、核心操作和结果输出。'
  }
  if (type === 'calculation') {
    return '请写出复杂度分析过程，例如：循环执行 n 次，所以时间复杂度为 O(n)。'
  }
  if (type === 'case') {
    return '请写出你的结构选择和理由，例如：需要频繁插入删除时可考虑链式存储。'
  }
  return '请用自己的话作答。建议写出核心概念、关键步骤或计算依据。'
}

function resultDetail(index: number) {
  return props.resultDetails?.find((item) => item.index === index)
}
</script>

<template>
  <div class="exercise-list">
    <section class="exercise-overview">
      <div>
        <strong>本资源包含 5 类练习题</strong>
        <span>选择题、简答题、计算题、代码题和综合应用题会一起判分，错题会写入错题本。</span>
      </div>
      <el-tag effect="plain">{{ exercises.length }} 道题</el-tag>
    </section>
    <el-card v-for="(item, index) in exercises" :key="index" shadow="never" class="exercise-card">
      <div class="exercise-head">
        <div>
          <span class="question-index">第 {{ index + 1 }} 题</span>
          <strong>{{ item.stem }}</strong>
        </div>
        <div class="exercise-tags">
          <el-tag>{{ item.difficulty }}</el-tag>
          <el-tag effect="plain">{{ typeLabel(item.type) }}</el-tag>
          <el-tag v-if="resultText(index)" :type="resultType(index)" effect="plain">{{ resultText(index) }}</el-tag>
        </div>
      </div>
      <el-radio-group
        v-if="item.options"
        class="options"
        :model-value="modelValue?.[String(index)]"
        @update:model-value="updateAnswer(index, String($event))"
      >
        <el-radio v-for="option in item.options" :key="option" :value="option">{{ option }}</el-radio>
      </el-radio-group>
      <div v-else class="short-answer">
        <el-input
          type="textarea"
          :rows="item.type === 'code' ? 6 : 4"
          resize="vertical"
          :model-value="modelValue?.[String(index)] || ''"
          :placeholder="answerPlaceholder(item.type)"
          @update:model-value="updateAnswer(index, String($event))"
        />
      </div>
      <section v-if="hasResult(index)" class="answer-panel" :class="resultType(index)">
        <p v-if="resultDetail(index)?.userAnswer"><strong>你的答案：</strong>{{ resultDetail(index)?.userAnswer }}</p>
        <p><strong>参考答案：</strong>{{ resultDetail(index)?.answer || item.answer }}</p>
        <p><strong>解析：</strong>{{ resultDetail(index)?.analysis || item.analysis }}</p>
        <p v-if="resultDetail(index)?.knowledgePoint"><strong>关联薄弱点：</strong>{{ resultDetail(index)?.knowledgePoint }}</p>
      </section>
      <p v-else class="answer-tip">提交本资源练习后显示参考答案和解析。</p>
    </el-card>
  </div>
</template>

<style scoped>
.exercise-list {
  display: grid;
  gap: 12px;
}

.exercise-overview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
}

.exercise-overview div {
  display: grid;
  gap: 4px;
}

.exercise-overview span {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.exercise-card {
  border-radius: 8px;
}

.exercise-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.exercise-head > div:first-child {
  display: grid;
  gap: 6px;
}

.question-index {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.exercise-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.options {
  display: grid;
  gap: 8px;
  margin-bottom: 10px;
}

.short-answer {
  margin-bottom: 12px;
}

.short-answer :deep(textarea) {
  font-family: inherit;
}

.exercise-card:has(.short-answer) .short-answer :deep(textarea) {
  line-height: 1.7;
}

.answer-panel {
  display: grid;
  gap: 6px;
  margin-top: 12px;
  padding: 12px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
}

.answer-panel.success {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.answer-panel.danger {
  border-color: #fecaca;
  background: #fef2f2;
}

.answer-panel p,
.answer-tip {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.answer-tip {
  padding-top: 10px;
  border-top: 1px solid var(--color-border);
  font-size: 13px;
}
</style>
