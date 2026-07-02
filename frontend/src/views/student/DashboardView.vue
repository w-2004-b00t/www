<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import FlowGuide from '../../components/common/FlowGuide.vue'
import { useOnboardingStore } from '../../stores/onboarding'
import { useResourceStore } from '../../stores/resource'

const router = useRouter()
const onboarding = useOnboardingStore()
const resource = useResourceStore()

const activeStage = computed(() => resource.learningPath.stages.find((item) => item.status === 'active'))
const nextTopic = computed(() => resource.nextTopic)
const hasLearningTask = computed(() => Boolean(activeStage.value && !nextTopic.value.blocked))
const studentGuideSteps = [
  { label: '学习起点', desc: '填写课程目标和薄弱点', path: '/student/onboarding' },
  { label: '对话画像', desc: '抽取并确认画像维度', path: '/student/profile-chat' },
  { label: '资源生成', desc: '生成讲解、导图、视频和练习', path: '/student/resource-generate' },
  { label: '学习路径', desc: '按阶段完成资源与检查点', path: '/student/learning-path' },
  { label: '阶段测评', desc: '提交答案并生成错因', path: '/student/assessment' },
  { label: '学习报告', desc: '查看闭环建议和补强任务', path: '/student/report' },
]
const currentGuideStep = computed(() => {
  if (onboarding.isEmptyStart) return 0
  if (!resource.resources.length) return 2
  return 3
})

onMounted(() => {
  onboarding.loadCourses()
  resource.loadAll()
})
</script>

<template>
  <div class="page">
    <div class="page-breadcrumb">
      <span>学生端</span>
      <strong>今日学习</strong>
    </div>

    <FlowGuide
      title="学生完整学习流程"
      description="按这个顺序走，就能从画像建立到资源学习、测评反馈和报告闭环。"
      :steps="studentGuideSteps"
      :current="currentGuideStep"
    />

    <section v-if="onboarding.isEmptyStart" class="empty-workbench">
      <div>
        <span class="status-pill warning">首次使用</span>
        <h3>还没有学习计划</h3>
        <p>本系统服务于《数据结构课程》。请先填写本次学习目标，系统会生成画像草稿，经你确认后再规划第一份学习路径。</p>
        <el-button type="primary" size="large" @click="router.push('/student/onboarding')">建立学习起点</el-button>
      </div>
    </section>

    <template v-else>
    <div class="page-header workbench-head">
      <div>
        <span class="soft-tag">高校智能学习工作台</span>
        <h1 class="page-title">{{ hasLearningTask ? `今天建议先完成「${activeStage?.name}」` : '暂无正式学习任务' }}</h1>
        <p class="page-subtitle">
          当前课程：{{ onboarding.selectedCourse?.name || '数据结构课程' }}。系统只根据真实课程资料、学习路径和进度记录安排下一步。
        </p>
      </div>
    </div>

    <div class="dashboard-layout">
      <section class="panel today-task primary-task">
        <div class="task-top">
          <span class="status-pill">今日推荐任务</span>
          <span class="muted">预计 {{ onboarding.dailyMinutes }} 分钟</span>
        </div>
        <h2>{{ hasLearningTask ? activeStage?.name : '等待正式路径生成' }}</h2>
        <p>{{ hasLearningTask ? activeStage?.acceptance : (nextTopic.blockingReason || nextTopic.reason || '请先生成带引用的学习资料和正式学习路径。') }}</p>
        <div class="task-list">
          <label v-for="task in activeStage?.tasks || []" :key="task">
            <el-checkbox />
            <span>{{ task }}</span>
          </label>
        </div>
        <div class="recommend-reason">
          <strong>推荐原因</strong>
          <p>{{ hasLearningTask ? (nextTopic.reason || activeStage?.aiReason || '来自当前正式学习路径。') : '当前没有足够真实资料生成推荐。' }}</p>
        </div>
        <div class="task-actions">
          <el-button type="primary" size="large" @click="router.push(hasLearningTask ? '/student/resources' : '/student/resource-generate')">
            {{ hasLearningTask ? '开始学习' : '去生成学习资料' }}
          </el-button>
        </div>
      </section>

      <section class="panel weakness-card">
        <span>下一步推荐</span>
        <strong>{{ nextTopic.blocked ? '暂无正式推荐' : (nextTopic.topic || nextTopic.chapterName) }}</strong>
        <p>{{ nextTopic.blockingReason || nextTopic.reason || '完成当前阶段后会重新计算下一步。' }}</p>
      </section>
    </div>
    </template>
  </div>
</template>

<style scoped>
.today-task p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.task-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.workbench-head {
  align-items: center;
}

.dashboard-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 18px;
  align-items: start;
}

.task-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.today-task h2 {
  margin: 12px 0 8px;
  font-size: 22px;
}

.task-list {
  display: grid;
  gap: 10px;
  margin: 18px 0;
}

.task-list label {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.recommend-reason {
  display: grid;
  gap: 6px;
  margin: 18px 0;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.recommend-reason p,
.weakness-card p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.weakness-card {
  display: grid;
  gap: 8px;
}

.weakness-card span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.weakness-card strong {
  font-size: 20px;
}

@media (max-width: 980px) {
  .dashboard-layout {
    align-items: stretch;
    flex-direction: column;
    grid-template-columns: 1fr;
  }
}
</style>
