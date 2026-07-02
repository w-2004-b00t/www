<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createClassRemedialTaskApi, getAdminAnalyticsApi, type AdminAnalytics } from '../../api/admin'

const analytics = ref<AdminAnalytics | null>(null)

async function loadAnalytics() {
  analytics.value = await getAdminAnalyticsApi()
}

async function createRemedialTask() {
  const result = await createClassRemedialTaskApi()
  ElMessage.success(`${result.title} 已创建，覆盖 ${result.studentCount} 名学生。`)
}

function recommendResources() {
  ElMessage.success('已把通过审核的分层练习推荐到班级学习路径。')
}

onMounted(loadAnalytics)
</script>

<template>
  <div v-if="analytics" class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">学生学习分析</h1>
        <p class="page-subtitle">面向教师展示班级共性薄弱点、资源使用效果和需要干预的学生。</p>
      </div>
      <el-button type="primary" @click="createRemedialTask">生成周报</el-button>
    </div>

    <div class="grid-3">
      <div class="panel metric-card"><div><div class="stat-value">{{ analytics.metrics.students }}</div><div class="stat-label">班级学生</div></div><span class="metric-trend">{{ analytics.metrics.active }} 人活跃</span></div>
      <div class="panel metric-card"><div><div class="stat-value">{{ analytics.metrics.averageMastery }}%</div><div class="stat-label">平均掌握度</div></div><span class="metric-trend">来自测评记录</span></div>
      <div class="panel metric-card"><div><div class="stat-value">{{ analytics.metrics.intervention }}</div><div class="stat-label">需干预学生</div></div><span class="metric-trend">课程资料集中</span></div>
    </div>

    <div class="analysis-layout">
      <section class="panel">
        <h2 class="section-title">班级共性薄弱点</h2>
        <div class="weak-list">
          <div v-for="item in analytics.weakPoints" :key="item.name">
            <div class="weak-head"><strong>{{ item.name }}</strong><span>{{ item.value }}%</span></div>
            <el-progress :percentage="item.value" status="warning" />
            <p>建议：{{ item.action }}</p>
          </div>
        </div>
      </section>

      <section class="panel">
        <h2 class="section-title">学生路径状态</h2>
        <el-table :data="analytics.students">
          <el-table-column prop="name" label="学生" />
          <el-table-column prop="progress" label="当前阶段" min-width="150" />
          <el-table-column label="掌握度">
            <template #default="{ row }"><el-progress :percentage="row.mastery" /></template>
          </el-table-column>
          <el-table-column prop="risk" label="状态" />
        </el-table>
      </section>
    </div>

    <section class="panel suggestion-panel">
      <h2 class="section-title">教学干预建议</h2>
      <p>{{ analytics.suggestion }} 建议教师发布一份 15 分钟补强任务，并把已通过审核的分层练习加入本周学习路径。</p>
      <div class="suggestion-actions">
        <el-button type="primary" @click="createRemedialTask">创建班级补强任务</el-button>
        <el-button @click="recommendResources">推荐已审核资源</el-button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.analysis-layout {
  display: grid;
  grid-template-columns: 420px minmax(0, 1fr);
  gap: 18px;
  margin-top: 18px;
}

.weak-list {
  display: grid;
  gap: 16px;
  margin-top: 16px;
}

.weak-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.weak-list p,
.suggestion-panel p {
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.suggestion-panel {
  margin-top: 18px;
  border-color: #bfdbfe;
  background: #f8fbff;
}

.suggestion-actions {
  display: flex;
  gap: 10px;
}

@media (max-width: 1100px) {
  .analysis-layout {
    grid-template-columns: 1fr;
  }
}
</style>
