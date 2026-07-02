<script setup lang="ts">
import { computed } from 'vue'
import type { LabPracticePlan, LearningResource } from '../../types/common'

const props = defineProps<{ resource: LearningResource }>()

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item).trim()).filter(Boolean) : []
}

function normalizePlan(value: unknown): LabPracticePlan {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const ioSpec = raw.ioSpec && typeof raw.ioSpec === 'object' ? raw.ioSpec as Record<string, unknown> : {}
  const traceCases = Array.isArray(raw.traceCases)
    ? raw.traceCases
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
        .map((item) => ({
          name: String(item.name || '跟踪用例'),
          steps: stringList(item.steps),
          expected: String(item.expected || '记录每一步结构状态和返回值。'),
        }))
    : []

  return {
    mission: String(raw.mission || `围绕「${props.resource.title}」完成一次代码实践：明确结构定义、核心操作、输入输出和状态变化。`),
    target: String(raw.target || ''),
    concepts: stringList(raw.concepts).length ? stringList(raw.concepts) : ['数据结构定义', '初始化', '核心操作', '边界条件'],
    operations: stringList(raw.operations).length ? stringList(raw.operations) : ['定义数据结构', '初始化', '执行核心操作', '输出结果'],
    ioSpec: {
      input: stringList(ioSpec.input).length ? stringList(ioSpec.input) : ['一组小规模操作序列', '至少 1 组边界输入'],
      output: stringList(ioSpec.output).length ? stringList(ioSpec.output) : ['每一步操作后的结构状态', '操作返回值或错误状态'],
      stateFields: stringList(ioSpec.stateFields).length ? stringList(ioSpec.stateFields) : ['关键指针或下标', '当前元素个数', '返回值'],
    },
    codeMode: String(raw.codeMode || (raw.codeExcerpt ? 'source' : 'design')),
    codeSource: String(raw.codeSource || ''),
    codeExcerpt: String(raw.codeExcerpt || ''),
    sourceTasks: stringList(raw.sourceTasks),
    designTasks: stringList(raw.designTasks),
    traceCases: traceCases.length ? traceCases : [
      {
        name: '正常输入',
        steps: ['初始化结构', '执行一次写入或插入', '执行一次读取或删除', '输出最终状态'],
        expected: '状态变化与课程定义一致。',
      },
      {
        name: '边界输入',
        steps: ['初始化空结构', '直接执行读取或删除'],
        expected: '返回失败状态，并说明边界条件。',
      },
    ],
    deliverables: stringList(raw.deliverables).length ? stringList(raw.deliverables) : ['手工跟踪表', '代码骨架或源码标注', '复杂度说明'],
    acceptance: stringList(raw.acceptance).length ? stringList(raw.acceptance) : ['能说清输入输出', '能完成手工跟踪', '能解释边界条件'],
  }
}

const metadata = computed(() => props.resource.metadata || {})
const plan = computed(() => normalizePlan(metadata.value.labPlan))
const hasSourceCode = computed(() => Boolean(plan.value.codeExcerpt))
const dataStatus = computed(() => String(metadata.value.dataStatus || (hasSourceCode.value ? 'live' : 'missing_source')))
const statusText = computed(() => (
  hasSourceCode.value
    ? '已命中真实源码，可对照源码完成实践'
    : '未命中真实源码，先完成设计、伪代码和手工跟踪'
))
const workTasks = computed(() => (
  hasSourceCode.value
    ? plan.value.sourceTasks?.length ? plan.value.sourceTasks : ['标注数据结构定义', '定位初始化函数', '跟踪核心操作分支']
    : plan.value.designTasks?.length ? plan.value.designTasks : ['写出结构定义', '写出核心函数签名', '说明边界条件']
))
</script>

<template>
  <article class="lab-viewer">
    <section class="lab-hero" :class="{ 'lab-hero--missing': dataStatus === 'missing_source' }">
      <div>
        <span class="eyebrow">代码实践</span>
        <h2>这次到底练什么</h2>
        <p>{{ plan.mission }}</p>
      </div>
      <el-tag :type="hasSourceCode ? 'success' : 'warning'" effect="plain">
        {{ statusText }}
      </el-tag>
    </section>

    <section class="lab-section">
      <div class="section-head">
        <h3>操作目标</h3>
        <span>{{ plan.operations.length }} 项</span>
      </div>
      <div class="chip-grid">
        <span v-for="item in plan.operations" :key="item">{{ item }}</span>
      </div>
    </section>

    <section class="lab-grid">
      <div class="lab-section">
        <div class="section-head">
          <h3>输入输出约定</h3>
        </div>
        <div class="io-grid">
          <div>
            <strong>输入</strong>
            <ul>
              <li v-for="item in plan.ioSpec.input" :key="item">{{ item }}</li>
            </ul>
          </div>
          <div>
            <strong>输出</strong>
            <ul>
              <li v-for="item in plan.ioSpec.output" :key="item">{{ item }}</li>
            </ul>
          </div>
        </div>
      </div>

      <div class="lab-section">
        <div class="section-head">
          <h3>需要观察的状态量</h3>
        </div>
        <div class="state-list">
          <span v-for="item in plan.ioSpec.stateFields" :key="item">{{ item }}</span>
        </div>
      </div>
    </section>

    <section class="lab-section">
      <div class="section-head">
        <h3>{{ hasSourceCode ? '真实源码观察' : '代码设计任务' }}</h3>
        <span>{{ hasSourceCode ? plan.codeSource || '课程源码' : '不伪造源码' }}</span>
      </div>
      <el-alert
        v-if="!hasSourceCode"
        type="warning"
        show-icon
        :closable="false"
        title="当前知识库没有返回真实源码片段。请先完成代码骨架和伪代码设计，上传源码后可重新生成对照版实验。"
      />
      <ul class="task-list">
        <li v-for="item in workTasks" :key="item">{{ item }}</li>
      </ul>
      <pre v-if="hasSourceCode" class="code-block"><code>{{ plan.codeExcerpt }}</code></pre>
    </section>

    <section class="lab-section">
      <div class="section-head">
        <h3>手工跟踪表</h3>
        <span>先手算，再写代码</span>
      </div>
      <div class="trace-table">
        <div class="trace-head">
          <span>用例</span>
          <span>操作序列</span>
          <span>预期观察</span>
        </div>
        <div v-for="item in plan.traceCases" :key="item.name" class="trace-row">
          <strong>{{ item.name }}</strong>
          <span>{{ item.steps.join(' -> ') }}</span>
          <span>{{ item.expected }}</span>
        </div>
      </div>
    </section>

    <section class="lab-grid">
      <div class="lab-section">
        <div class="section-head">
          <h3>提交物</h3>
        </div>
        <ul class="task-list">
          <li v-for="item in plan.deliverables" :key="item">{{ item }}</li>
        </ul>
      </div>
      <div class="lab-section">
        <div class="section-head">
          <h3>验收清单</h3>
        </div>
        <ul class="task-list">
          <li v-for="item in plan.acceptance" :key="item">{{ item }}</li>
        </ul>
      </div>
    </section>
  </article>
</template>

<style scoped>
.lab-viewer {
  display: grid;
  gap: 16px;
}

.lab-hero,
.lab-section {
  border: 1px solid #d8e1ee;
  border-radius: 8px;
  background: #fff;
}

.lab-hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 18px;
  background: #f7fbff;
}

.lab-hero--missing {
  background: #fffaf0;
}

.eyebrow {
  color: #2563eb;
  font-size: 12px;
  font-weight: 700;
}

.lab-hero h2,
.section-head h3 {
  margin: 0;
  color: var(--color-text-primary);
  letter-spacing: 0;
}

.lab-hero h2 {
  margin-top: 4px;
  font-size: 24px;
}

.lab-hero p {
  margin: 8px 0 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.lab-section {
  display: grid;
  gap: 12px;
  padding: 16px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.section-head h3 {
  font-size: 18px;
}

.section-head span {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.chip-grid,
.state-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip-grid span,
.state-list span {
  padding: 7px 10px;
  border: 1px solid #c7d7eb;
  border-radius: 6px;
  background: #f8fafc;
  color: #1f2937;
  font-size: 13px;
}

.lab-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.io-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.io-grid strong {
  display: block;
  margin-bottom: 6px;
}

.task-list,
.io-grid ul {
  margin: 0;
  padding-left: 20px;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.code-block {
  max-width: 100%;
  margin: 0;
  padding: 14px 16px;
  overflow-x: auto;
  border-radius: 8px;
  background: #111827;
  color: #e5edf8;
  font-size: 13px;
  line-height: 1.65;
}

.code-block code {
  white-space: pre;
}

.trace-table {
  display: grid;
  overflow: hidden;
  border: 1px solid #d8e1ee;
  border-radius: 8px;
}

.trace-head,
.trace-row {
  display: grid;
  grid-template-columns: 140px minmax(0, 1.2fr) minmax(0, 1fr);
  gap: 12px;
  padding: 11px 12px;
}

.trace-head {
  background: #f1f5f9;
  color: #475569;
  font-size: 13px;
  font-weight: 700;
}

.trace-row {
  border-top: 1px solid #e5eaf2;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.trace-row strong {
  color: var(--color-text-primary);
}

@media (max-width: 900px) {
  .lab-hero,
  .section-head {
    align-items: stretch;
    flex-direction: column;
  }

  .lab-grid,
  .io-grid,
  .trace-head,
  .trace-row {
    grid-template-columns: 1fr;
  }

  .trace-head {
    display: none;
  }
}
</style>
