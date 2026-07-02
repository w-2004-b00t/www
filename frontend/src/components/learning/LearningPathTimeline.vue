<script setup lang="ts">
import { computed } from 'vue'
import {
  CheckCircle2,
  Circle,
  ClipboardCheck,
  Clock3,
  FileText,
  Flag,
  PlayCircle,
} from 'lucide-vue-next'
import PathResourceWorkspace from './PathResourceWorkspace.vue'
import type { LearningPath, LearningResource } from '../../types/common'

const props = withDefaults(defineProps<{ path: LearningPath; resources?: LearningResource[] }>(), {
  resources: () => [],
})

const emit = defineEmits<{
  complete: [stageId: string]
  mastery: [stageId: string]
}>()

const stages = computed(() => props.path.stages || [])
const completedCount = computed(() => stages.value.filter((stage) => stage.status === 'completed' || stage.isCompleted || stage.isMastered).length)
const activeStage = computed(() => stages.value.find((stage) => stage.status === 'active' || stage.status === 'awaiting_assessment'))
const resourceMap = computed(() => new Map(props.resources.map((resource) => [resource.id, resource])))

function getStageResources(ids: string[] = []) {
  return ids.map((id) => resourceMap.value.get(id)).filter(Boolean) as LearningResource[]
}

function isRemedialStage(source?: string, name = '') {
  return source === 'assessment' || name.includes('补强')
}

function statusLabel(status: string, remedial: boolean, mastered?: boolean) {
  if (mastered) return '已掌握'
  if (status === 'completed') return '已完成'
  if (status === 'awaiting_assessment') return '待测评'
  if (remedial) return '需补强'
  if (status === 'active') return '进行中'
  return '未开始'
}

function statusType(status: string, remedial: boolean, mastered?: boolean) {
  if (mastered || status === 'completed') return 'success'
  if (remedial) return 'warning'
  if (status === 'active' || status === 'awaiting_assessment') return 'primary'
  return 'info'
}

const stageViewModels = computed(() =>
  stages.value.map((stage, index) => {
    const remedial = isRemedialStage(stage.source, stage.name)
    const completed = stage.status === 'completed' || stage.isCompleted
    const mastered = Boolean(stage.isMastered)
    return {
      ...stage,
      rawStage: stage,
      index,
      resources: getStageResources(stage.resources || []),
      knowledgePoints: stage.knowledgePoints || [],
      tasks: stage.tasks || [],
      completedTasks: stage.completedTasks || [],
      remedial,
      completed,
      mastered,
      active: stage.status === 'active' || stage.status === 'awaiting_assessment',
      statusLabel: statusLabel(stage.status, remedial, mastered),
      statusType: statusType(stage.status, remedial, mastered),
    }
  }),
)

const focusStage = computed(() =>
  stageViewModels.value.find((stage) => stage.active)
  || stageViewModels.value.find((stage) => !stage.completed && !stage.mastered)
  || stageViewModels.value[stageViewModels.value.length - 1],
)
</script>

<template>
  <section class="path-board">
    <div class="path-orbit-head">
      <div>
        <span class="orbit-eyebrow">Learning Mission</span>
        <h3>{{ path.title }}</h3>
        <p>{{ path.summary }}</p>
        <small v-if="path.generatedAt">生成时间：{{ path.generatedAt }}</small>
      </div>
      <div class="orbit-progress" aria-label="阶段完成进度">
        <strong>{{ completedCount }}/{{ stages.length }}</strong>
        <span>阶段完成</span>
      </div>
    </div>

    <div class="stage-orbit" aria-label="学习阶段轨道">
      <article
        v-for="stage in stageViewModels"
        :key="stage.id"
        class="orbit-node"
        :class="{ active: stage.active, done: stage.completed || stage.mastered, remedial: stage.remedial }"
      >
        <div class="node-marker">
          <CheckCircle2 v-if="stage.completed || stage.mastered" :size="20" />
          <PlayCircle v-else-if="stage.active" :size="20" />
          <Circle v-else :size="20" />
        </div>
        <span>阶段 {{ stage.index + 1 }}</span>
        <strong>{{ stage.name }}</strong>
        <small>
          <Clock3 :size="13" />
          {{ stage.days }} 天
        </small>
      </article>
    </div>

    <article v-if="focusStage" class="focus-stage" :class="{ remedial: focusStage.remedial }">
      <div class="focus-stage-head">
        <div>
          <el-tag :type="focusStage.statusType" effect="plain">{{ focusStage.statusLabel }}</el-tag>
          <h4>{{ focusStage.name }}</h4>
          <p v-if="focusStage.chapterName">
            <FileText :size="15" />
            {{ focusStage.chapterName }}
          </p>
        </div>
        <div class="stage-actions">
          <router-link v-if="focusStage.active" to="/student/assessment">
            <el-button type="primary" size="small">
              <ClipboardCheck :size="15" />
              阶段测评
            </el-button>
          </router-link>
          <el-button v-if="focusStage.active" size="small" @click="emit('complete', focusStage.id)">
            <CheckCircle2 :size="15" />
            标记完成
          </el-button>
          <el-button v-if="focusStage.active && !focusStage.mastered" size="small" type="success" plain @click="emit('mastery', focusStage.id)">
            <CheckCircle2 :size="15" />
            标记掌握
          </el-button>
        </div>
      </div>

      <div class="focus-grid">
        <section class="focus-block">
          <strong>知识点</strong>
          <p>{{ focusStage.knowledgePoints.join('、') || '暂无知识点' }}</p>
        </section>
        <section class="focus-block">
          <strong>完成标准</strong>
          <p>
            <Flag :size="15" />
            {{ focusStage.acceptance || '暂无完成标准' }}
          </p>
        </section>
      </div>

      <div class="task-strip">
        <div v-for="task in focusStage.tasks" :key="task" class="task-chip">
          <CheckCircle2 v-if="focusStage.completedTasks.includes(task) || focusStage.mastered" :size="15" />
          <Circle v-else :size="15" />
          <span>{{ task }}</span>
        </div>
      </div>

      <div v-if="focusStage.mastered" class="mastery-note">
        <CheckCircle2 :size="16" />
        <span>已掌握：后续推荐会跳过该阶段知识点。</span>
      </div>

      <PathResourceWorkspace
        :stage="focusStage.rawStage"
        :resources="focusStage.resources"
      />
    </article>

    <el-alert v-if="activeStage" type="warning" show-icon :closable="false">
      下一步：完成「{{ activeStage.name }}」后提交阶段测评。
    </el-alert>
  </section>
</template>

<style scoped>
.path-board {
  display: grid;
  gap: 16px;
  min-width: 0;
}

.path-orbit-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 136px;
  gap: 14px;
  padding: 16px;
  color: #f8fafc;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 10px;
  background:
    linear-gradient(135deg, rgba(15, 23, 42, 0.96), rgba(30, 58, 138, 0.92)),
    radial-gradient(circle at 90% 0%, rgba(20, 184, 166, 0.3), transparent 36%);
  box-shadow: 0 18px 38px rgba(15, 23, 42, 0.18);
}

.orbit-eyebrow {
  color: #93c5fd;
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0;
}

h3,
h4 {
  margin: 0;
}

h3 {
  margin-top: 4px;
  font-size: 20px;
  line-height: 1.35;
}

h4 {
  margin-top: 8px;
  font-size: 20px;
  line-height: 1.45;
}

.path-orbit-head p,
.path-orbit-head small {
  margin: 8px 0 0;
  color: #cbd5e1;
  line-height: 1.7;
}

.path-orbit-head small {
  display: block;
  font-size: 12px;
}

.orbit-progress {
  display: grid;
  place-items: center;
  align-self: stretch;
  min-height: 92px;
  border: 1px solid rgba(191, 219, 254, 0.28);
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.36);
}

.orbit-progress strong {
  color: #ffffff;
  font-size: 30px;
}

.orbit-progress span {
  color: #bfdbfe;
  font-size: 13px;
}

.stage-orbit {
  display: flex;
  gap: 12px;
  min-width: 0;
  overflow-x: auto;
  padding: 2px 2px 10px;
}

.orbit-node {
  position: relative;
  display: grid;
  align-content: start;
  gap: 7px;
  flex: 0 0 210px;
  min-height: 116px;
  padding: 14px;
  border: 1px solid #d8e0ec;
  border-radius: 10px;
  background: #f8fafc;
  color: #334155;
}

.orbit-node::after {
  content: "";
  position: absolute;
  top: 34px;
  right: -13px;
  width: 13px;
  height: 2px;
  background: #cbd5e1;
}

.orbit-node:last-child::after {
  display: none;
}

.orbit-node.active {
  color: #f8fafc;
  border-color: #38bdf8;
  background: linear-gradient(135deg, #1d4ed8, #0f766e);
  box-shadow: 0 16px 30px rgba(37, 99, 235, 0.22);
}

.orbit-node.done {
  border-color: #86efac;
  background: #f0fdf4;
}

.orbit-node.remedial:not(.active) {
  border-color: #fdba74;
  background: #fff7ed;
}

.node-marker {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  color: #2563eb;
  border-radius: 999px;
  background: #e0ecff;
}

.orbit-node.active .node-marker {
  color: #0f766e;
  background: #ccfbf1;
}

.orbit-node span {
  font-size: 12px;
  font-weight: 800;
}

.orbit-node strong {
  overflow-wrap: anywhere;
  font-size: 15px;
  line-height: 1.45;
}

.orbit-node small {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: inherit;
  opacity: 0.8;
}

.focus-stage {
  display: grid;
  gap: 14px;
  padding: 16px;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  box-shadow: 0 16px 34px rgba(15, 23, 42, 0.08);
}

.focus-stage.remedial {
  border-color: #fed7aa;
  background: linear-gradient(180deg, #fffaf5 0%, #ffffff 100%);
}

.focus-stage-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.focus-stage-head p {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 6px 0 0;
  color: var(--color-text-secondary);
}

.stage-actions {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}

.stage-actions :deep(.el-button) {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: 0;
}

.focus-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.focus-block {
  min-width: 0;
  padding: 14px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #ffffff;
}

.focus-block strong {
  color: #0f172a;
}

.focus-block p {
  display: flex;
  gap: 6px;
  margin: 8px 0 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.task-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.task-chip {
  display: inline-flex;
  align-items: flex-start;
  gap: 7px;
  max-width: 100%;
  min-height: 34px;
  padding: 7px 10px;
  color: #334155;
  border: 1px solid #dbe3ef;
  border-radius: 999px;
  background: #f8fafc;
  font-size: 13px;
  line-height: 1.5;
}

.task-chip span,
.mastery-note,
.focus-block p {
  overflow-wrap: anywhere;
  word-break: break-word;
}

.mastery-note {
  display: inline-flex;
  align-items: flex-start;
  gap: 6px;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.mastery-note {
  color: #166534;
}

@media (max-width: 900px) {
  .path-orbit-head,
  .focus-grid {
    grid-template-columns: 1fr;
  }

  .orbit-progress {
    min-height: 86px;
  }

  .focus-stage-head {
    display: grid;
  }
}

@media (max-width: 760px) {
  .orbit-node {
    flex-basis: 184px;
  }
}
</style>
