<script setup lang="ts">
import type { StudentProfileItem } from '../../types/common'

defineProps<{ item: StudentProfileItem }>()

const sourceText: Record<StudentProfileItem['source'], string> = {
  dialog: '对话',
  assessment: '测评',
  behavior: '行为',
  manual: '手动修改',
}

const statusText: Record<StudentProfileItem['status'], string> = {
  draft: '待确认',
  confirmed: '已确认',
  rejected: '已拒绝',
}

const statusType: Record<StudentProfileItem['status'], 'success' | 'warning' | 'danger'> = {
  draft: 'warning',
  confirmed: 'success',
  rejected: 'danger',
}
</script>

<template>
  <div class="profile-card">
    <div class="profile-head">
      <strong>{{ item.dimension }}</strong>
      <div class="profile-tags">
        <el-tag size="small" :type="item.confidence >= 0.85 ? 'success' : 'warning'">
          置信度 {{ Math.round(item.confidence * 100) }}%
        </el-tag>
        <el-tag size="small" :type="statusType[item.status]" effect="plain">
          {{ statusText[item.status] }}
        </el-tag>
      </div>
    </div>
    <p>{{ item.value }}</p>
    <div v-if="item.impact" class="impact">推荐影响：{{ item.impact }}</div>
    <div v-if="item.reason" class="reason">抽取依据：{{ item.reason }}</div>
    <div class="profile-meta">
      <span>来源：{{ sourceText[item.source] || item.source }}</span>
      <span>v{{ item.version || 1 }} · {{ item.updatedAt }}</span>
    </div>
  </div>
</template>

<style scoped>
.profile-card {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 14px;
  background: #fff;
}

.profile-head,
.profile-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.profile-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

p {
  margin: 10px 0;
  line-height: 1.6;
}

.impact,
.reason {
  margin-top: 8px;
  padding: 8px 10px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.55;
  border-radius: 8px;
  background: #f8fafc;
}

.impact {
  color: #0369a1;
  background: #f0f9ff;
}

.profile-meta {
  margin-top: 10px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
</style>
