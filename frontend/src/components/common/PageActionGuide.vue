<script setup lang="ts">
defineProps<{
  title: string
  description: string
  currentAction: string
  primaryLabel: string
  primaryTo?: string
  secondaryLabel?: string
  secondaryTo?: string
  status?: 'info' | 'success' | 'warning' | 'danger'
}>()

const statusLabel = {
  info: '当前任务',
  success: '已完成',
  warning: '需要处理',
  danger: '异常',
}
</script>

<template>
  <section class="page-action-guide" :class="status || 'info'">
    <div class="guide-copy">
      <span>{{ statusLabel[status || 'info'] }}</span>
      <h2>{{ title }}</h2>
      <p>{{ description }}</p>
      <strong>{{ currentAction }}</strong>
    </div>
    <div class="guide-actions">
      <router-link v-if="primaryTo" :to="primaryTo">
        <el-button type="primary">{{ primaryLabel }}</el-button>
      </router-link>
      <el-button v-else type="primary">
        {{ primaryLabel }}
      </el-button>
      <router-link v-if="secondaryTo && secondaryLabel" :to="secondaryTo">
        <el-button>{{ secondaryLabel }}</el-button>
      </router-link>
    </div>
  </section>
</template>

<style scoped>
.page-action-guide {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 16px;
  padding: 16px 18px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #f8fbff;
}

.page-action-guide.success {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.page-action-guide.warning {
  border-color: #fed7aa;
  background: #fff7ed;
}

.page-action-guide.danger {
  border-color: #fecaca;
  background: #fef2f2;
}

.guide-copy {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.guide-copy span {
  color: var(--color-primary);
  font-size: 12px;
  font-weight: 700;
}

.success .guide-copy span {
  color: var(--color-success);
}

.warning .guide-copy span {
  color: var(--color-warning);
}

.danger .guide-copy span {
  color: var(--color-danger);
}

.guide-copy h2 {
  margin: 0;
  font-size: 17px;
  line-height: 1.35;
}

.guide-copy p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.guide-copy strong {
  color: var(--color-text);
  line-height: 1.55;
}

.guide-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
  flex-shrink: 0;
}

@media (max-width: 860px) {
  .page-action-guide {
    align-items: stretch;
    flex-direction: column;
  }

  .guide-actions {
    justify-content: flex-start;
  }
}
</style>
