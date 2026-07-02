<script setup lang="ts">
export interface ProcessChainStep {
  key: string
  title: string
  desc: string
  status: 'pending' | 'active' | 'done' | 'warning'
}

defineProps<{ steps: ProcessChainStep[] }>()
</script>

<template>
  <div class="chain-steps">
    <div v-for="step in steps" :key="step.key" class="chain-step" :class="step.status">
      <span class="chain-dot" />
      <div>
        <strong>{{ step.title }}</strong>
        <p>{{ step.desc }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chain-steps {
  display: grid;
  gap: 10px;
}

.chain-step {
  position: relative;
  display: grid;
  grid-template-columns: 18px 1fr;
  gap: 10px;
  padding: 10px 12px 10px 10px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.chain-step.active {
  border-color: #bfdbfe;
  background: #f8fbff;
}

.chain-step.done {
  border-color: #bbf7d0;
  background: #f7fef9;
}

.chain-step.warning {
  border-color: #fed7aa;
  background: #fff7ed;
}

.chain-dot {
  width: 9px;
  height: 9px;
  margin-top: 6px;
  border: 2px solid #cbd5e1;
  border-radius: 999px;
  background: #fff;
}

.chain-step.done .chain-dot {
  border-color: var(--color-success);
  background: var(--color-success);
}

.chain-step.active .chain-dot {
  border-color: var(--color-primary);
  background: var(--color-primary);
}

.chain-step.warning .chain-dot {
  border-color: var(--color-warning);
  background: var(--color-warning);
}

.chain-step p {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.55;
}
</style>
