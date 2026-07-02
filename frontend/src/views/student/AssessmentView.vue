<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import PageActionGuide from '../../components/common/PageActionGuide.vue'
import AssessmentPanel from '../../components/assessment/AssessmentPanel.vue'
import { generateAssessmentApi } from '../../api/assessment'
import { useProfileStore } from '../../stores/profile'
import { useResourceStore } from '../../stores/resource'
import type { AssessmentPaper, LearningPath, ProfileUpdateDraft } from '../../types/common'

const router = useRouter()
const resource = useResourceStore()
const profile = useProfileStore()
const profileUpdateReady = ref(false)
const latestWeakness = ref<string[]>([])
const pathCompare = ref<{ before: string; after: string; reason: string } | null>(null)
const paper = ref<AssessmentPaper | null>(null)
const loadingQuestions = ref(false)
const loadError = ref('')

async function loadQuestions() {
  loadingQuestions.value = true
  loadError.value = ''
  try {
    paper.value = await generateAssessmentApi()
  } catch (error) {
    paper.value = null
    loadError.value = error instanceof Error ? error.message : '正式测评题生成失败，请先确认课程资料和学习资源已生成。'
    ElMessage.warning(loadError.value)
  } finally {
    loadingQuestions.value = false
  }
}

function handleAdjusted(result: {
  weakness: string[]
  suggestion: string
  adjustedPath?: LearningPath
  assessmentId?: string
  mistakes_added?: number
  error_reasons?: string[]
  profile_update_drafts?: ProfileUpdateDraft[]
  profileUpdateDrafts?: ProfileUpdateDraft[]
  path_adjustment?: { before?: string; after?: string; reason?: string; beforePath?: string[]; afterPath?: string[] }
}) {
  const before = resource.learningPath.stages.find((stage) => stage.status === 'active')?.name || '原学习阶段'
  if (result.adjustedPath) {
    resource.setLearningPath(result.adjustedPath)
  } else {
    ElMessage.warning('测评已提交，但后端未返回路径调整结果，正在重新同步学习路径。')
    resource.loadAll().catch(() => {
      ElMessage.error('学习路径同步失败，请稍后刷新页面。')
    })
  }
  const after = resource.learningPath.stages.find((stage) => stage.status === 'active')?.name || '测评后补强任务'
  pathCompare.value = {
    before: result.path_adjustment?.before || before,
    after: result.path_adjustment?.after || after,
    reason:
      result.path_adjustment?.reason ||
      `计算题错误率较高，学习评估 Agent 识别薄弱点：${result.weakness.join('、')}，因此先插入补强任务。`,
  }
  latestWeakness.value = result.weakness
  const drafts = result.profile_update_drafts || result.profileUpdateDrafts || []
  profileUpdateReady.value = drafts.length > 0
  profile.loadUpdateDrafts().catch(() => {
    ElMessage.warning('测评结果已生成，但画像更新建议加载失败，请稍后在学习画像页查看。')
  })
}

function openProfileUpdateDrafts() {
  router.push('/student/profile')
}

onMounted(loadQuestions)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">数据结构课程阶段测评</h1>
        <p class="page-subtitle">
          系统会基于真实课程资料、已生成学习资源和当前学习路径生成正式试卷，提交后形成错因、错题和路径调整建议。
        </p>
      </div>
    </div>

    <PageActionGuide
      title="先完成阶段测评，再查看错因和路径变化"
      description="测评提交后会自动生成错因分析、错题本和学习路径调整记录，并形成画像更新草稿。"
      current-action="当前要做：先作答，再看系统如何识别薄弱点并插入补强任务。"
      primary-label="查看学习路径"
      primary-to="/student/learning-path"
      secondary-label="查看错题本"
      secondary-to="/student/mistakes"
      status="warning"
    />

    <section v-if="loadingQuestions" class="panel loading-panel">
      <el-skeleton :rows="4" animated />
      <p>正在根据《数据结构课程》真实资料生成正式阶段测评题。</p>
    </section>
    <section v-else-if="loadError" class="panel empty-panel">
      <el-alert type="warning" show-icon :closable="false" title="暂时无法生成正式测评">
        <p>{{ loadError }}</p>
        <p>系统不会使用静态演示题或假数据。请先生成学习资料，或到资源中心确认已有可引用的课程资料。</p>
      </el-alert>
      <div class="empty-actions">
        <router-link to="/student/resource-generate"><el-button type="primary">去生成学习资料</el-button></router-link>
        <router-link to="/student/resources"><el-button>查看资源中心</el-button></router-link>
        <el-button :loading="loadingQuestions" @click="loadQuestions">重新生成试卷</el-button>
      </div>
    </section>
    <AssessmentPanel v-else-if="paper" :paper="paper" @adjusted="handleAdjusted" />

    <section v-if="pathCompare" class="panel compare-panel">
      <div>
        <el-tag type="warning" effect="plain">路径已动态调整</el-tag>
        <h2>测评前后路径对比</h2>
        <p>{{ pathCompare.reason }}</p>
      </div>
      <div class="compare-grid">
        <div>
          <span>原路径</span>
          <strong>{{ pathCompare.before }}</strong>
        </div>
        <div>
          <span>新路径</span>
          <strong>{{ pathCompare.after }}</strong>
        </div>
      </div>
    </section>

    <section v-if="profileUpdateReady" class="panel profile-update">
      <div>
        <el-tag type="warning" effect="plain">画像更新建议</el-tag>
        <h2>本次测评显示你在 {{ latestWeakness.join('、') }} 上仍不稳定</h2>
        <p>系统已生成画像更新草稿。确认后，它会影响后续学习路径、资源推荐和智能辅导回答方式。</p>
      </div>
      <el-button type="primary" @click="openProfileUpdateDrafts">查看画像更新建议</el-button>
    </section>
  </div>
</template>

<style scoped>
.profile-update {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 18px;
  border-color: #fed7aa;
  background: #fffaf5;
}

.loading-panel {
  display: grid;
  gap: 10px;
}

.loading-panel p,
.empty-panel p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.empty-panel {
  display: grid;
  gap: 14px;
}

.empty-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.compare-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 16px;
  margin-top: 18px;
  border-color: #fed7aa;
  background: #fffaf5;
}

.compare-panel h2,
.profile-update h2 {
  margin: 10px 0 6px;
  font-size: 18px;
}

.compare-panel p,
.profile-update p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.compare-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.compare-grid div {
  display: grid;
  gap: 6px;
  padding: 12px;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  background: #fff;
}

.compare-grid span {
  color: var(--color-text-secondary);
  font-size: 13px;
}

@media (max-width: 900px) {
  .profile-update,
  .compare-panel {
    align-items: stretch;
    grid-template-columns: 1fr;
    flex-direction: column;
  }

  .compare-grid {
    grid-template-columns: 1fr;
  }
}
</style>
