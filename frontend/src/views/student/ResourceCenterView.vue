<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import ResourceCard from '../../components/resource/ResourceCard.vue'
import { useProfileStore } from '../../stores/profile'
import { useResourceStore } from '../../stores/resource'
import { useUiStore } from '../../stores/ui'
import type { LearningResource, ResourceFeedback, ResourceType } from '../../types/common'

const resource = useResourceStore()
const profile = useProfileStore()
const ui = useUiStore()
const showCompleted = ref(false)

const moduleOrder: ResourceType[] = ['explanation', 'mindmap', 'exercise', 'reading', 'video_script', 'lab']
const moduleLabels: Record<ResourceType, string> = {
  explanation: '讲解文档',
  mindmap: '完整导图',
  exercise: '练习题',
  reading: '拓展阅读',
  video_script: '视频演示',
  lab: '代码案例',
}

const allResources = computed(() => resource.allResources)
const centerResources = computed(() => {
  if (showCompleted.value) return allResources.value
  return allResources.value.filter((item) => !item.isCompleted && !item.isMastered)
})
const passedCount = computed(() => allResources.value.filter((item) => item.auditStatus === 'passed').length)
const pendingCount = computed(() => allResources.value.filter((item) => item.auditStatus !== 'passed').length)
const profileText = computed(() => profile.profileItems.map((item) => `${item.dimension}:${item.value}`).join(' '))

const preferenceTypeWeights: Record<string, string[]> = {
  mindmap: ['图解', '思维导图', '可视化', '全局'],
  exercise: ['例题', '练习', '题', '测评', '手算'],
  lab: ['代码', '实践任务', '实验', '实践', '实现'],
  video_script: ['短视频', '视频', '动画', '演示'],
  reading: ['拓展阅读', '阅读', '复习'],
  explanation: ['讲解', '文档', '概念', '基础'],
}

function matchCount(text: string, keywords: string[]) {
  return keywords.reduce((score, keyword) => score + (text.includes(keyword) ? 1 : 0), 0)
}

function resourceScore(item: LearningResource) {
  if (typeof item.vectorScore === 'number') {
    return Math.round(item.vectorScore * 100) + (item.auditStatus === 'passed' ? 40 : item.auditStatus === 'warning' ? -20 : -50)
  }
  const text = `${profileText.value} ${item.title} ${item.summary} ${item.fitReason || ''}`
  const typeMatches = matchCount(text, preferenceTypeWeights[item.resourceType] || [])
  const weakMatches = matchCount(text, ['线性表', '链表', '栈', '队列', '树', '图', '排序', '查找', '代码实践'])
  const auditScore = item.auditStatus === 'passed' ? 40 : item.auditStatus === 'warning' ? -20 : -50
  return auditScore + typeMatches * 18 + weakMatches * 8 + Math.round(item.qualityScore / 5)
}

const recommendableResources = computed(() => {
  const passed = centerResources.value.filter((item) => item.auditStatus === 'passed')
  return passed.length ? passed : centerResources.value.filter((item) => item.auditStatus !== 'rejected')
})
const recommendedResource = computed(() => (
  [...recommendableResources.value].sort((left, right) => resourceScore(right) - resourceScore(left))[0]
))

function resourceChapterName(item: LearningResource) {
  const metadata = item.metadata || {}
  return String(metadata.chapterName || metadata.topic || '当前章节')
}

function buildResourceModules(items: LearningResource[]) {
  return moduleOrder.map((type) => {
    const typeItems = items
    .filter((item) => item.resourceType === type)
    .sort((left, right) => resourceScore(right) - resourceScore(left))
    const best = typeItems[0]
    return {
      type,
      label: moduleLabels[type],
      resource: best,
      count: typeItems.length,
      extraCount: Math.max(0, typeItems.length - 1),
      status: best?.auditStatus || 'empty',
      citationCount: best?.citations.length || 0,
      isRecommended: best?.id === recommendedResource.value?.id,
    }
  })
}

const chapterGroups = computed(() => {
  const grouped = new Map<string, LearningResource[]>()
  for (const item of centerResources.value) {
    const name = resourceChapterName(item)
    grouped.set(name, [...(grouped.get(name) || []), item])
  }
  return [...grouped.entries()].map(([chapterName, items]) => ({
    chapterName,
    resources: items,
    modules: buildResourceModules(items),
  }))
})

const generatedModuleCount = computed(() => {
  if (!allResources.value.length) return 0
  const generatedTypes = new Set(allResources.value.map((item) => item.resourceType))
  return moduleOrder.filter((type) => generatedTypes.has(type)).length
})

async function submitFeedback(payload: { resourceId: string; type: ResourceFeedback['type'] }) {
  try {
    await resource.submitFeedback(payload.resourceId, payload.type)
    ElMessage.success('反馈已保存，后续推荐会参考这条学习信号。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源反馈保存失败')
  }
}

async function completeResource(resourceId: string) {
  try {
    await resource.completeResource(resourceId)
    await resource.loadAll(true)
    ElMessage.success('已记录学完状态；是否掌握仍由你单独确认。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源完成状态保存失败')
  }
}

async function markResourceMastered(resourceId: string) {
  try {
    await resource.markResourceMastered(resourceId)
    await resource.loadAll(true)
    ElMessage.success('已记录掌握状态，后续推荐会跳过这份资料。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资源掌握状态保存失败')
  }
}

async function attachResource(resourceId: string) {
  try {
    await resource.attachResourcesToPath([resourceId])
    ElMessage.success('已加入学习路径，学习完成和掌握状态不会自动改变。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '资料加入学习路径失败')
  }
}

function regenerateResource() {
  if (!ui.reviewMode) {
    ElMessage.info('学生端已隐藏重新生成操作；评审模式可查看单个资源重生成链路。')
    return
  }
  ElMessage.info('已记录重新生成请求。')
}

async function loadResourcesForView() {
  await resource.loadAll(true)
}

onMounted(() => {
  loadResourcesForView()
  profile.loadProfile()
})

</script>

<template>
  <div class="page resource-center-page">
    <div class="page-breadcrumb">
      <span>学生端</span>
      <span>资源中心</span>
    </div>

    <section class="panel hero-panel">
      <div>
        <span class="status-pill">本次生成资源</span>
        <h1>已生成 {{ generatedModuleCount }} / 6 类学习资源</h1>
        <p>围绕讲解、导图、练习、阅读、视频和代码 6 个模块查看资料，按需进入学习或补齐缺失模块。</p>
      </div>
      <router-link to="/student/resource-generate">
        <el-button type="primary">生成本次学习资料</el-button>
      </router-link>
    </section>

    <section class="resource-list-panel panel">
      <div class="resource-summary">
        <div>
          <strong>{{ generatedModuleCount }}</strong>
          <span>已生成模块</span>
        </div>
        <div>
          <strong>{{ passedCount }}</strong>
          <span>可直接学习</span>
        </div>
        <div>
          <strong>{{ pendingCount }}</strong>
          <span>等待教师复核</span>
        </div>
      </div>

      <div class="resource-list-head">
        <div>
          <h2>6 个生成模块 <span>{{ centerResources.length }} / {{ allResources.length }} 份资料</span></h2>
          <p>每个模块优先展示最适合当前画像的资料；推荐先学只作为模块标签，不再单独占据主区域。</p>
        </div>
        <el-switch v-model="showCompleted" active-text="显示已完成" inactive-text="隐藏已完成" />
      </div>

      <div v-if="centerResources.length" class="chapter-stack">
        <section v-for="group in chapterGroups" :key="group.chapterName" class="chapter-section">
          <div class="chapter-head">
            <h3>{{ group.chapterName }}</h3>
            <span>{{ group.resources.length }} 份资料</span>
          </div>
          <div class="module-grid">
            <article v-for="item in group.modules" :key="`${group.chapterName}-${item.type}`" class="module-card">
              <header class="module-head">
                <div>
                  <span class="module-label">{{ item.label }}</span>
                  <h3>{{ item.resource?.title || `${item.label}待生成` }}</h3>
                </div>
                <el-tag v-if="item.isRecommended" type="primary" effect="plain">推荐先学</el-tag>
                <el-tag v-else-if="item.resource" :type="item.status === 'passed' ? 'success' : 'warning'" effect="plain">
                  {{ item.status === 'passed' ? '可学习' : '待复核' }}
                </el-tag>
                <el-tag v-else type="info" effect="plain">未生成</el-tag>
              </header>

              <ResourceCard
                v-if="item.resource"
                :resource="item.resource"
                @feedback="submitFeedback"
                @regenerate="regenerateResource"
                @complete="completeResource"
                @mastery="markResourceMastered"
                @attach="attachResource"
              />

              <div v-else class="module-empty">
                <p>这个模块还没有学习资料。重新生成时可勾选“{{ item.label }}”，补齐本章节学习资源。</p>
                <router-link to="/student/resource-generate">
                  <el-button type="primary">去生成该模块</el-button>
                </router-link>
              </div>

              <footer class="module-foot">
                <span>{{ item.count ? `本类 ${item.count} 份` : '暂无资料' }}</span>
                <span>{{ item.citationCount ? `引用 ${item.citationCount} 条课程资料` : '暂无课程引用' }}</span>
                <span v-if="item.extraCount">另有 {{ item.extraCount }} 份</span>
              </footer>
            </article>
          </div>
        </section>
      </div>

      <section v-if="!allResources.length" class="state-empty">
        <h3>还没有生成学习资料</h3>
        <p>确认画像后生成一组学习资料，系统会把 6 个模块的结果放到这里。</p>
        <router-link to="/student/resource-generate">
          <el-button type="primary">生成学习资料</el-button>
        </router-link>
      </section>

      <section v-else-if="!centerResources.length" class="state-empty">
        <h3>当前没有可见资料</h3>
        <p>已生成的资料都被隐藏在“已完成/已掌握”筛选中，打开“显示已完成”即可查看。</p>
      </section>
    </section>
  </div>
</template>

<style scoped>
.resource-center-page {
  max-width: 1480px;
}

.hero-panel,
.resource-list-panel {
  margin-bottom: 16px;
}

.hero-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.hero-panel h1 {
  margin: 10px 0 8px;
  font-size: 26px;
}

.hero-panel p,
.resource-list-head p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.resource-summary span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.resource-list-panel {
  padding: 16px;
}

.resource-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.resource-summary div {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-height: 48px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.resource-summary strong {
  font-size: 22px;
}

.resource-list-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin: 0 0 14px;
}

.resource-list-head h2 {
  margin: 0 0 4px;
  font-size: 20px;
}

.resource-list-head h2 span {
  margin-left: 8px;
  color: var(--color-text-secondary);
  font-size: 14px;
  font-weight: 600;
}

.module-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.chapter-stack {
  display: grid;
  gap: 18px;
}

.chapter-section {
  display: grid;
  gap: 12px;
}

.chapter-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.chapter-head h3 {
  margin: 0;
  font-size: 18px;
}

.chapter-head span {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.module-card {
  display: grid;
  gap: 12px;
  min-width: 0;
}

.module-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  min-height: 60px;
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.module-head h3 {
  display: -webkit-box;
  margin: 4px 0 0;
  overflow: hidden;
  color: var(--color-text);
  font-size: 16px;
  line-height: 1.4;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.module-label {
  color: var(--color-primary);
  font-size: 13px;
  font-weight: 700;
}

.module-empty {
  display: grid;
  align-content: center;
  justify-items: start;
  gap: 14px;
  min-height: 210px;
  padding: 18px;
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.module-empty p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.module-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 28px;
}

.module-foot span {
  padding: 5px 8px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: #f8fafc;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.state-empty {
  display: grid;
  gap: 10px;
  justify-items: start;
  margin-top: 16px;
  padding: 22px;
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.state-empty h3,
.state-empty p {
  margin: 0;
}

.state-empty p {
  color: var(--color-text-secondary);
  line-height: 1.7;
}

@media (max-width: 1200px) {
  .hero-panel,
  .resource-list-head {
    align-items: stretch;
    flex-direction: column;
  }

  .resource-summary,
  .module-grid {
    grid-template-columns: 1fr;
  }
}
</style>
