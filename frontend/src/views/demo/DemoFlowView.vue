<script setup lang="ts">
import { computed, onMounted } from 'vue'
import AgentProgress from '../../components/agent/AgentProgress.vue'
import FlowGuide from '../../components/common/FlowGuide.vue'
import LearningPathTimeline from '../../components/learning/LearningPathTimeline.vue'
import ProfileCard from '../../components/profile/ProfileCard.vue'
import ResourceCard from '../../components/resource/ResourceCard.vue'
import SourceCitation from '../../components/resource/SourceCitation.vue'
import { useAuthStore } from '../../stores/auth'
import { useProfileStore } from '../../stores/profile'
import { useResourceStore } from '../../stores/resource'
import { useTaskStore } from '../../stores/task'

const auth = useAuthStore()
const profile = useProfileStore()
const resource = useResourceStore()
const task = useTaskStore()

const reviewGuideSteps = [
  { label: '演示入口', desc: '说明项目定位和赛题主线', path: '/demo/flow' },
  { label: 'Agent 协作', desc: '展开输入、工具、输出和引用', path: '/student/resource-generate' },
  { label: '资源结果', desc: '查看 6 类学习资源和引用证据', path: '/student/resources' },
  { label: '教师审核', desc: '通过、驳回或要求补充引用', path: '/admin/audit' },
  { label: '路径调整', desc: '展示测评前后路径变化', path: '/student/learning-path' },
  { label: '报告闭环', desc: '查看错因、薄弱点和下一步建议', path: '/student/report' },
]

const sevenMinuteScript = [
  { time: '0:00-0:40', title: '首次进入与学习起点', point: '确认《数据结构课程》课程，输入课程资料学习目标，证明不是默认假数据起步。', route: '/student/onboarding' },
  { time: '0:40-1:30', title: '对话画像抽取', point: '展示画像草稿、置信度、低置信确认和画像影响。', route: '/student/profile-chat' },
  { time: '1:30-2:30', title: '多智能体资源生成', point: '展开一个 Agent 的输入、工具、输出和结构化 JSON。', route: '/student/resource-generate' },
  { time: '2:30-3:20', title: 'RAG 引用溯源', point: '点击引用查看原文片段、页码和相似度。', route: '/student/resources/res_explanation' },
  { time: '3:20-4:10', title: '路径动态调整', point: '测评后展示原路径与新路径对比。', route: '/student/assessment' },
  { time: '4:10-5:00', title: '智能辅导闭环', point: '回答带引用、置信度、推断标记，保存笔记或加入错题本。', route: '/student/tutor' },
  { time: '5:00-5:50', title: '教师审核同步', point: '教师填写原因通过/驳回，学生资源状态同步变化。', route: '/admin/audit' },
  { time: '5:50-6:40', title: '学习报告', point: '展示报告数据来源，并一键创建补强任务。', route: '/student/report' },
]

const firstResource = computed(() => resource.resources[0])

onMounted(() => {
  if (!auth.isLoggedIn) auth.quickLogin('student')
  profile.loadProfile()
  resource.loadAll()
})

function startDemo() {
  task.startResourceTask('课程资料待上传', '掌握课程资料与课程资料的手算步骤，并完成 3 道补强练习', [
    'explanation',
    'mindmap',
    'exercise',
    'reading',
    'lab',
    'video_script',
  ])
}
</script>

<template>
  <div class="page demo-flow">
    <section class="demo-hero">
      <div>
        <el-tag type="primary">中国软件杯 A3 赛题演示端</el-tag>
        <h1>智学工坊 EduAgent Studio</h1>
        <p>
          演示端只展示技术亮点；学生端负责真实学习任务，教师端负责资源审核，避免把技术细节压给学生。
        </p>
      </div>
      <div class="hero-actions">
        <el-button type="primary" size="large" @click="startDemo">启动演示任务</el-button>
        <router-link to="/student/dashboard"><el-button size="large">进入学生端</el-button></router-link>
      </div>
    </section>

    <FlowGuide
      title="评委演示路径"
      description="评委只需要按这条路线点击，就能看到多智能体、RAG 引用、教师审核和学习闭环。"
      :steps="reviewGuideSteps"
      :current="0"
    />

    <section class="panel script-panel">
      <div class="section-head">
        <div>
          <h2>7 分钟评委演示路线</h2>
          <p class="muted">按“一个动作改变后续结果”的逻辑讲，避免只展示静态页面。</p>
        </div>
        <el-tag type="success" effect="plain">学生端 / 教师端 / 演示端已分离</el-tag>
      </div>
      <div class="script-grid">
        <router-link v-for="item in sevenMinuteScript" :key="item.time" :to="item.route" class="script-item">
          <strong>{{ item.time }}</strong>
          <span>{{ item.title }}</span>
          <p>{{ item.point }}</p>
        </router-link>
      </div>
    </section>

    <div class="metric-grid">
      <div class="metric-card"><strong>8</strong><span>画像维度</span></div>
      <div class="metric-card"><strong>6</strong><span>资源类型</span></div>
      <div class="metric-card"><strong>7</strong><span>智能体角色</span></div>
      <div class="metric-card"><strong>RAG</strong><span>逐段溯源</span></div>
    </div>

    <div class="grid-2">
      <section class="panel">
        <h2>示例输入</h2>
        <p class="input-text">
          我正在学课程资料，课程资料不会算，想用图解和题目复习，今天只有 45 分钟。
        </p>
        <h2>动态学生画像</h2>
        <div class="mini-grid">
          <ProfileCard v-for="item in profile.profileItems.slice(0, 4)" :key="item.id" :item="item" />
        </div>
      </section>
      <AgentProgress :task="task.activeTask" />
    </div>

    <section v-if="firstResource" class="panel">
      <div class="section-head">
        <div>
          <h2>RAG 引用溯源</h2>
          <p class="muted">评委点击引用后，应能看到原文片段、页码、相似度；未绑定引用的内容标记为模型推断。</p>
        </div>
        <router-link :to="`/student/resources/${firstResource.id}`"><el-button>打开资源详情</el-button></router-link>
      </div>
      <SourceCitation :citations="firstResource.citations" />
    </section>

    <section class="panel">
      <div class="section-head">
        <div>
          <h2>生成资源</h2>
          <p class="muted">讲解文档、思维导图、练习题、拓展阅读、代码实操案例、短视频脚本。</p>
        </div>
        <el-tag type="success">自动审核 + 引用溯源 + 单项反馈</el-tag>
      </div>
      <div class="resource-grid">
        <ResourceCard v-for="item in resource.resources" :key="item.id" :resource="item" />
      </div>
    </section>

    <div class="grid-2">
      <LearningPathTimeline :path="resource.learningPath" />
      <section class="panel">
        <h2>阶段测评</h2>
        <p class="muted">阶段测评只使用真实课程资料动态生成试卷；无课程引用时会阻断，不展示静态假题。</p>
        <router-link to="/student/assessment"><el-button type="primary">进入正式阶段测评</el-button></router-link>
      </section>
    </div>
  </div>
</template>

<style scoped>
.demo-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 18px;
  padding: 24px;
  color: #0f172a;
  background: #fff;
  border: 1px solid var(--color-border);
  border-radius: 8px;
}

.demo-hero h1 {
  margin: 12px 0 8px;
  font-size: 34px;
  line-height: 1.15;
}

.demo-hero p {
  max-width: 840px;
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.8;
}

.hero-actions,
.section-head {
  display: flex;
  gap: 10px;
}

.hero-actions {
  flex-shrink: 0;
}

.section-head {
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 14px;
}

.section-head h2 {
  margin: 0;
}

.script-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.script-item {
  display: grid;
  gap: 6px;
  padding: 12px;
  color: var(--color-text);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.script-item:hover {
  border-color: #bfdbfe;
  background: #eff6ff;
}

.script-item strong {
  color: var(--color-primary);
}

.script-item span {
  font-weight: 700;
}

.script-item p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}

.metric-card {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 16px;
  background: #fff;
  border: 1px solid var(--color-border);
  border-radius: 8px;
}

.metric-card strong {
  font-size: 28px;
  color: var(--color-primary);
}

.metric-card span {
  color: var(--color-text-secondary);
}

.input-text {
  padding: 14px;
  line-height: 1.8;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
}

.mini-grid,
.resource-grid {
  display: grid;
  gap: 12px;
}

.mini-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.resource-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.panel {
  margin-bottom: 18px;
}

@media (max-width: 1200px) {
  .demo-hero,
  .hero-actions,
  .section-head {
    align-items: stretch;
    flex-direction: column;
  }

  .metric-grid,
  .resource-grid,
  .mini-grid,
  .script-grid {
    grid-template-columns: 1fr;
  }
}
</style>
