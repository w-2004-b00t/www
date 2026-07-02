<script setup lang="ts">
interface FlowGuideStep {
  label: string
  desc: string
  path?: string
  status?: 'pending' | 'active' | 'done' | 'warning'
}

defineProps<{
  title: string
  description?: string
  steps: FlowGuideStep[]
  current?: number
}>()

function stepStatus(step: FlowGuideStep, index: number, current?: number) {
  if (step.status) return step.status
  if (typeof current !== 'number') return 'pending'
  if (index < current) return 'done'
  if (index === current) return 'active'
  return 'pending'
}

function statusText(status: ReturnType<typeof stepStatus>) {
  const labels = {
    pending: '未开始',
    active: '当前',
    done: '已完成',
    warning: '需补强',
  }
  return labels[status]
}
</script>

<template>
  <section class="flow-guide">
    <div class="flow-copy">
      <h2>{{ title }}</h2>
      <p v-if="description">{{ description }}</p>
    </div>
    <div class="flow-steps">
      <component
        :is="step.path ? 'router-link' : 'div'"
        v-for="(step, index) in steps"
        :key="step.label"
        class="flow-step"
        :class="stepStatus(step, index, current)"
        :to="step.path"
      >
        <div class="step-top">
          <span class="step-index">{{ index + 1 }}</span>
          <em>{{ statusText(stepStatus(step, index, current)) }}</em>
        </div>
        <strong>{{ step.label }}</strong>
        <small>{{ step.desc }}</small>
      </component>
    </div>
  </section>
</template>

<style scoped>
.flow-guide {
  display: grid;
  gap: 14px;
  margin-bottom: 18px;
  padding: 16px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.flow-copy {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 18px;
}

.flow-copy h2 {
  margin: 0;
  font-size: 18px;
}

.flow-copy p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.flow-steps {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(138px, 1fr));
  gap: 8px;
}

.flow-step {
  display: grid;
  gap: 5px;
  min-height: 96px;
  padding: 12px;
  color: var(--color-text);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.flow-step:hover {
  border-color: #bfdbfe;
  background: #eff6ff;
}

.flow-step.active {
  border-color: #2563eb;
  background: #eff6ff;
  box-shadow: inset 0 0 0 1px #bfdbfe;
}

.flow-step.done {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.flow-step.warning {
  border-color: #fed7aa;
  background: #fff7ed;
}

.step-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.step-index {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  color: var(--color-primary);
  font-size: 12px;
  font-weight: 700;
  border: 1px solid #bfdbfe;
  border-radius: 999px;
  background: #fff;
}

.step-top em {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-style: normal;
  font-weight: 700;
}

.flow-step.active .step-top em {
  color: var(--color-primary);
}

.flow-step.done .step-top em {
  color: var(--color-success);
}

.flow-step.warning .step-top em {
  color: var(--color-warning);
}

.flow-step strong {
  font-size: 14px;
}

.flow-step small {
  color: var(--color-text-secondary);
  line-height: 1.45;
}

@media (max-width: 760px) {
  .flow-copy {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
