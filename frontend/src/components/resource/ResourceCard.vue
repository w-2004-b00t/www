<script setup lang="ts">
import { computed } from 'vue'
import type { Component } from 'vue'
import { BookOpen, CheckCircle2, Clapperboard, ClipboardCheck, Code2, FileText, GitBranch, MoreHorizontal } from 'lucide-vue-next'
import type { LearningResource, ResourceFeedback } from '../../types/common'
import { AUDIT_STATUS_META, getAuditSyncText, getResourceMetaText, RESOURCE_TYPE_DEFAULTS, RESOURCE_TYPE_LABELS } from '../../constants/resourceMeta'
import { useUiStore } from '../../stores/ui'
import { getResourceLearningState } from '../../utils/resourceLearningState'

const props = withDefaults(defineProps<{ resource: LearningResource; variant?: 'default' | 'featured' }>(), {
  variant: 'default',
})
const emit = defineEmits<{
  feedback: [payload: { resourceId: string; type: ResourceFeedback['type'] }]
  regenerate: [resourceId: string]
  complete: [resourceId: string]
  mastery: [resourceId: string]
  attach: [resourceId: string]
}>()

const ui = useUiStore()

const resourceIcons: Record<LearningResource['resourceType'], Component> = {
  explanation: FileText,
  mindmap: GitBranch,
  exercise: ClipboardCheck,
  reading: BookOpen,
  lab: Code2,
  video_script: Clapperboard,
}

const syncText = computed(() => getAuditSyncText(props.resource))
const metaText = computed(() => getResourceMetaText(props.resource))
const canStudy = computed(() => props.resource.auditStatus === 'passed')
const learningState = computed(() => getResourceLearningState(props.resource))
const fitText = computed(() => props.resource.fitReason || RESOURCE_TYPE_DEFAULTS[props.resource.resourceType].fit)
const auditLabel = computed(() => (canStudy.value ? '可学习' : AUDIT_STATUS_META[props.resource.auditStatus].label))
const progressText = computed(() => canStudy.value ? learningState.value.label : '')
const progressHint = computed(() => {
  if (learningState.value.status === 'mastered') return '已确认掌握，后续推荐会跳过这份资料。'
  if (learningState.value.status === 'completed') return '已记录学完；确认掌握后，后续推荐才会跳过。'
  if (learningState.value.status === 'learning') return '已开始学习；读完后可标记已学完。'
  if (canStudy.value) return '请先打开资源详情页开始学习。'
  return syncText.value
})
const vectorText = computed(() => {
  if (props.resource.vectorScore === undefined) return ''
  return `画像匹配 ${Math.round(props.resource.vectorScore * 100)}% / ${props.resource.embeddingProvider || 'Embedding'} / ${props.resource.vectorStore || 'VectorStore'}`
})
</script>

<template>
  <article class="resource-card" :class="[`resource-card--${variant}`, resource.auditStatus, { completed: resource.isCompleted || resource.isMastered }]">
    <div class="resource-head">
      <span class="type-pill">
        <component :is="resourceIcons[resource.resourceType]" :size="16" />
        {{ RESOURCE_TYPE_LABELS[resource.resourceType] }}
      </span>
      <el-tag v-if="progressText" type="success" effect="plain">
        {{ progressText }}
      </el-tag>
      <el-tag v-else :type="AUDIT_STATUS_META[resource.auditStatus].type" effect="plain">
        {{ auditLabel }}
      </el-tag>
    </div>

    <router-link :to="`/student/resources/${resource.id}`" class="resource-title">
      {{ resource.title }}
    </router-link>
    <p>{{ resource.summary }}</p>

    <div class="resource-meta-line">
      <span>{{ metaText }}</span>
      <span>质量 {{ resource.qualityScore }}</span>
    </div>

    <div class="student-reason">
      <span>适合你：{{ fitText }}</span>
      <small>{{ progressHint }}</small>
    </div>

    <div v-if="ui.reviewMode && (vectorText || resource.vectorReason)" class="review-evidence">
      <strong v-if="vectorText">{{ vectorText }}</strong>
      <span v-if="resource.vectorReason">{{ resource.vectorReason }}</span>
    </div>

    <div class="resource-actions">
      <router-link v-if="learningState.canStart" :to="`/student/resources/${resource.id}`">
        <el-button size="small" type="primary">
          <CheckCircle2 :size="15" />
          {{ learningState.startLabel }}
        </el-button>
      </router-link>
      <el-tooltip v-else :content="learningState.startDisabledReason">
        <span class="action-wrap">
          <el-button size="small" type="primary" disabled>
            <CheckCircle2 :size="15" />
            {{ learningState.startLabel }}
          </el-button>
        </span>
      </el-tooltip>
      <el-button size="small" :disabled="!canStudy" @click="emit('attach', resource.id)">
        <GitBranch :size="15" />
        加入路径
      </el-button>
      <el-tooltip :disabled="learningState.canComplete" :content="learningState.completeDisabledReason">
        <span class="action-wrap">
          <el-button
            size="small"
            plain
            :disabled="!learningState.canComplete"
            @click="emit('complete', resource.id)"
          >
            <CheckCircle2 :size="15" />
            {{ learningState.completeLabel }}
          </el-button>
        </span>
      </el-tooltip>
      <el-tooltip :disabled="learningState.canMaster" :content="learningState.masterDisabledReason">
        <span class="action-wrap">
          <el-button
            size="small"
            type="success"
            plain
            :disabled="!learningState.canMaster"
            @click="emit('mastery', resource.id)"
          >
            <CheckCircle2 :size="15" />
            {{ learningState.masterLabel }}
          </el-button>
        </span>
      </el-tooltip>
      <el-dropdown trigger="click">
        <el-button size="small" text class="more-button">
          <MoreHorizontal :size="16" />
          更多
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item>查看引用证据</el-dropdown-item>
            <el-dropdown-item @click="emit('feedback', { resourceId: resource.id, type: 'helpful' })">有帮助</el-dropdown-item>
            <el-dropdown-item @click="emit('feedback', { resourceId: resource.id, type: 'too_hard' })">太难</el-dropdown-item>
            <el-dropdown-item @click="emit('feedback', { resourceId: resource.id, type: 'incorrect' })">不准确</el-dropdown-item>
            <el-dropdown-item @click="emit('feedback', { resourceId: resource.id, type: 'need_example' })">需要例子</el-dropdown-item>
            <el-dropdown-item divided @click="emit('regenerate', resource.id)">重新生成这份资料</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </article>
</template>

<style scoped>
.resource-card {
  display: grid;
  align-content: start;
  gap: 10px;
  min-height: 210px;
  padding: 16px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
  transition: box-shadow 0.2s, border-color 0.2s;
}

.resource-card.completed {
  border-color: #bbf7d0;
  background: #f8fff9;
}

.resource-card--featured {
  min-height: 0;
  padding: 18px;
  border-color: #bfdbfe;
}

.resource-card:hover {
  border-color: #bfdbfe;
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.06);
}

.resource-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.type-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-primary);
  font-size: 13px;
  font-weight: 600;
}

.resource-title {
  display: block;
  margin: 0;
  color: var(--color-text);
  font-size: 16px;
  line-height: 1.45;
  font-weight: 700;
}

.resource-card--featured .resource-title {
  font-size: 19px;
}

p {
  display: -webkit-box;
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
  overflow: hidden;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.resource-meta-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.resource-meta-line span:not(:last-child)::after {
  content: "/";
  margin-left: 8px;
  color: var(--color-text-weak);
}

.student-reason {
  display: grid;
  gap: 4px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f8fafc;
}

.student-reason span {
  color: var(--color-text);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.45;
}

.student-reason small {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.review-evidence {
  display: grid;
  gap: 4px;
  padding: 8px 0 0;
  border-top: 1px solid var(--color-border);
}

.review-evidence strong {
  color: #2563eb;
  font-size: 12px;
}

.review-evidence span {
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.45;
}

.resource-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.resource-actions .el-button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.action-wrap {
  display: inline-flex;
}

.more-button {
  color: var(--color-text-secondary);
}
</style>
