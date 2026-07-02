<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Connection, Refresh, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import {
  getKnowledgeGraphApi,
  getKnowledgeRemedialPathApi,
} from '../../api/knowledge'
import KnowledgeGraphCanvas from '../../components/knowledge/KnowledgeGraphCanvas.vue'
import type {
  KnowledgeDifficulty,
  KnowledgeGraphData,
  KnowledgeGraphNode,
  KnowledgeNodeType,
  KnowledgeRemedialStep,
} from '../../types/knowledgeGraph'
import {
  getKnowledgeMasteryMeta,
  knowledgeMasteryOptions,
} from '../../utils/knowledgeMastery'

const router = useRouter()
const emptyGraph = (): KnowledgeGraphData => ({
  courseId: 'course_data_structure',
  courseName: '数据结构课程知识图谱',
  updatedAt: '',
  nodes: [],
  edges: [],
})
const graph = ref<KnowledgeGraphData>(emptyGraph())
const canvasRef = ref<InstanceType<typeof KnowledgeGraphCanvas> | null>(null)
const loading = ref(false)
const loadError = ref('')
const keyword = ref('')
const selectedNode = ref<KnowledgeGraphNode | null>(null)
const visibleTypes = ref<KnowledgeNodeType[]>(['course', 'chapter', 'concept', 'operation', 'application'])
const difficulty = ref<KnowledgeDifficulty | ''>('')
const chapterId = ref('')
const remedialSteps = ref<KnowledgeRemedialStep[]>([])

const typeOptions: Array<{ value: KnowledgeNodeType; label: string; color: string }> = [
  { value: 'course', label: '课程', color: '#8b5cf6' },
  { value: 'chapter', label: '章节', color: '#3b82f6' },
  { value: 'concept', label: '概念', color: '#22a06b' },
  { value: 'operation', label: '算法 / 操作', color: '#f59e0b' },
  { value: 'application', label: '应用', color: '#ef6461' },
]
const chapters = computed(() => graph.value.nodes.filter((node) => node.type === 'chapter'))
const displayGraph = computed<KnowledgeGraphData>(() => {
  const ids = new Set(
    graph.value.nodes
      .filter((node) => !chapterId.value || node.chapterId === chapterId.value || node.id === chapterId.value || node.type === 'course')
      .filter((node) => !difficulty.value || node.difficulty === difficulty.value || ['course', 'chapter'].includes(node.type))
      .map((node) => node.id),
  )
  return {
    ...graph.value,
    nodes: graph.value.nodes.filter((node) => ids.has(node.id)),
    edges: graph.value.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)),
  }
})
const relatedEdges = computed(() => graph.value.edges.filter(
  (edge) => edge.source === selectedNode.value?.id || edge.target === selectedNode.value?.id,
))
const masteryMeta = computed(() => selectedNode.value ? getKnowledgeMasteryMeta(selectedNode.value) : null)
const totalMinutes = computed(() => remedialSteps.value.reduce((sum, item) => sum + item.estimatedMinutes, 0))

onMounted(loadGraph)

watch(selectedNode, (node) => {
  void loadRemedialPath(node)
}, { immediate: true })

async function loadGraph() {
  loading.value = true
  loadError.value = ''
  try {
    graph.value = await getKnowledgeGraphApi()
  } catch (error) {
    graph.value = emptyGraph()
    loadError.value = error instanceof Error ? error.message : '知识图谱加载失败。'
    ElMessage.error(loadError.value)
  } finally {
    selectedNode.value = graph.value.nodes.find((node) => node.id === selectedNode.value?.id)
      || graph.value.nodes.find((node) => node.type === 'concept')
      || graph.value.nodes[0]
      || null
    loading.value = false
  }
}

async function loadRemedialPath(node: KnowledgeGraphNode | null) {
  if (!node?.id) {
    remedialSteps.value = []
    return
  }
  try {
    remedialSteps.value = (await getKnowledgeRemedialPathApi(node.id)).steps
  } catch {
    remedialSteps.value = []
  }
}

function getRelatedNodeName(edge: { source: string; target: string }) {
  const id = edge.source === selectedNode.value?.id ? edge.target : edge.source
  return graph.value.nodes.find((node) => node.id === id)?.name || id
}

function openResourceGeneration() {
  const node = selectedNode.value
  if (!node) return
  router.push({
    path: '/student/resource-generate',
    query: {
      topic: node.name,
      chapterId: node.chapterId || '',
      chapterName: chapters.value.find((item) => item.id === node.chapterId)?.name || '',
    },
  })
}

function getTypeLabel(node: KnowledgeGraphNode) {
  return typeOptions.find((item) => item.value === node.type)?.label || node.type
}
</script>

<template>
  <main class="page knowledge-page">
    <header class="page-header">
      <div>
        <div class="eyebrow"><el-icon><Connection /></el-icon> KNOWLEDGE GRAPH</div>
        <h1 class="page-title">知识图谱</h1>
        <p class="page-subtitle">课程结构、真实资料、测评薄弱点与补强路径已经汇聚到同一张图中。</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="loadGraph">刷新图谱</el-button>
    </header>

    <el-alert v-if="loadError" class="status-alert" type="error" show-icon :closable="false">
      {{ loadError }} 请确认后端知识图谱接口已启动后重试。
    </el-alert>

    <section class="summary-grid">
      <article><span>知识节点</span><strong>{{ graph.stats?.nodeCount ?? graph.nodes.length }}</strong></article>
      <article><span>关系数量</span><strong>{{ graph.stats?.edgeCount ?? graph.edges.length }}</strong></article>
      <article><span>资料覆盖率</span><strong>{{ graph.stats?.sourceCoverage ?? 0 }}%</strong></article>
      <article><span>薄弱节点</span><strong>{{ graph.stats?.masteryDistribution?.weak ?? 0 }}</strong></article>
    </section>

    <section class="graph-workspace panel" v-loading="loading">
      <div class="graph-toolbar">
        <el-input v-model="keyword" :prefix-icon="Search" clearable placeholder="搜索知识点、算法或应用" />
        <el-select v-model="chapterId" clearable placeholder="全部章节">
          <el-option v-for="item in chapters" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-select v-model="difficulty" clearable placeholder="全部难度">
          <el-option v-for="item in ['基础', '进阶', '综合']" :key="item" :label="item" :value="item" />
        </el-select>
        <el-button @click="canvasRef?.resetView()">重置视图</el-button>
      </div>
      <div class="filter-row">
        <el-checkbox-group v-model="visibleTypes">
          <el-checkbox-button v-for="item in typeOptions" :key="item.value" :value="item.value">
            <i :style="{ background: item.color }" />{{ item.label }}
          </el-checkbox-button>
        </el-checkbox-group>
        <div class="mastery-legend" aria-label="掌握程度颜色说明">
          <span v-for="item in knowledgeMasteryOptions" :key="item.value">
            <i :style="{ background: item.color }" />{{ item.label }}
          </span>
        </div>
      </div>

      <div class="workspace-body">
        <div v-if="graph.nodes.length" class="canvas-wrap">
          <KnowledgeGraphCanvas
            ref="canvasRef"
            :data="displayGraph"
            :keyword="keyword"
            :visible-types="visibleTypes"
            :selected-node-id="selectedNode?.id"
            @select="selectedNode = $event"
          />
          <div class="canvas-hint">节点颜色代表掌握状态 · 滚轮缩放 · 拖拽画布 · 点击查看详情</div>
        </div>

        <el-empty
          v-else
          class="graph-empty"
          description="暂无可展示的真实知识图谱。请先上传课程资料，或由教师维护章节与知识点。"
        />

        <aside v-if="selectedNode" class="node-panel">
          <div class="node-heading">
            <div>
              <div class="node-type">{{ getTypeLabel(selectedNode) }}</div>
              <h2>{{ selectedNode.name }}</h2>
            </div>
            <el-tag :color="masteryMeta?.color" effect="dark">{{ masteryMeta?.label }}</el-tag>
          </div>
          <p>{{ selectedNode.description }}</p>

          <div class="attribute-grid">
            <div><span>难度</span><strong>{{ selectedNode.difficulty || '基础' }}</strong></div>
            <div><span>重要度</span><strong>{{ selectedNode.importance || 3 }} / 5</strong></div>
            <div><span>预计学习</span><strong>{{ selectedNode.estimatedMinutes || 0 }} 分钟</strong></div>
            <div><span>学习资源</span><strong>{{ selectedNode.resourceCount || 0 }} 份</strong></div>
          </div>

          <div class="mastery-card">
            <div><span>当前掌握度</span><strong>{{ selectedNode.mastery || 0 }}%</strong></div>
            <el-progress
              :percentage="selectedNode.mastery || 0"
              :show-text="false"
              :stroke-width="8"
            />
            <small>根据测评结果和学习进度自动计算</small>
          </div>

          <div v-if="selectedNode.masteryBreakdown" class="detail-block">
            <span>掌握度依据</span>
            <ul>
              <li><strong>学习路径</strong><em>{{ selectedNode.masteryBreakdown.pathScore }}%</em></li>
              <li>
                <strong>测评结果</strong>
                <em>{{ selectedNode.masteryBreakdown.assessmentScore ?? '暂无' }}<template v-if="selectedNode.masteryBreakdown.assessmentScore !== null">%</template></em>
              </li>
              <li><strong>计算规则</strong><em>{{ selectedNode.masteryBreakdown.formula }}</em></li>
            </ul>
          </div>

          <div class="action-grid">
            <el-button type="primary" @click="openResourceGeneration">生成补强讲解</el-button>
            <el-button @click="router.push({ path: '/student/assessment', query: { topic: selectedNode.name } })">进入练习测评</el-button>
          </div>

          <div v-if="remedialSteps.length" class="detail-block">
            <span>最短补强路径 · 约 {{ totalMinutes }} 分钟</span>
            <ol class="path-list">
              <li v-for="step in remedialSteps" :key="step.nodeId">
                <em>{{ step.order }}</em>
                <div><strong>{{ step.name }}</strong><small>{{ step.reason }} · {{ step.estimatedMinutes }} 分钟</small></div>
              </li>
            </ol>
          </div>

          <div class="detail-block">
            <span>课程来源（{{ selectedNode.sourceRefs?.length || 0 }}）</span>
            <ul>
              <li v-for="source in selectedNode.sourceRefs || []" :key="source.chunkId || `${source.documentName}-${source.page}`">
                <strong>{{ source.documentName }}</strong>
                <em>第 {{ source.page || 1 }} 页</em>
              </li>
              <li v-if="!selectedNode.sourceRefs?.length"><strong>暂无可追溯课程引用</strong></li>
            </ul>
          </div>

          <div class="detail-block">
            <span>直接关联（{{ relatedEdges.length }}）</span>
            <ul>
              <li v-for="edge in relatedEdges" :key="edge.id || `${edge.source}-${edge.target}`">
                <strong>{{ getRelatedNodeName(edge) }}</strong>
                <em>{{ edge.relation }}</em>
              </li>
            </ul>
          </div>
        </aside>
      </div>
    </section>
  </main>
</template>

<style scoped>
.knowledge-page { max-width: 1580px; }
.eyebrow { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; color: var(--color-primary); font-size: 12px; font-weight: 800; letter-spacing: .12em; }
.status-alert { margin-bottom: 14px; }
.summary-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 14px; }
.summary-grid article { display: grid; gap: 4px; padding: 14px 16px; border: 1px solid var(--color-border); border-radius: 8px; background: rgba(255,255,255,.88); box-shadow: var(--shadow-soft); }
.summary-grid span, .mastery-card span, .detail-block > span, .attribute-grid span { color: var(--color-text-secondary); font-size: 12px; }
.summary-grid strong { font-size: 18px; }
.graph-workspace { padding: 0; overflow: hidden; }
.graph-toolbar { display: grid; grid-template-columns: minmax(220px, 1fr) 190px 150px auto; gap: 10px; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--color-border); background: #fbfcff; }
.filter-row { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px; padding: 10px 16px; border-bottom: 1px solid var(--color-border); background: #fff; }
.filter-row i { display: inline-block; width: 8px; height: 8px; margin-right: 6px; border-radius: 50%; }
.mastery-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; color: var(--color-text-secondary); font-size: 13px; }
.mastery-legend span { display: inline-flex; align-items: center; white-space: nowrap; }
.workspace-body { display: grid; grid-template-columns: minmax(0, 1fr) 360px; min-height: 690px; }
.canvas-wrap { position: relative; min-width: 0; background: radial-gradient(circle at center, #fff 0, #f8faff 72%, #f3f6fb 100%); }
.canvas-wrap::before { position: absolute; inset: 0; content: ''; pointer-events: none; opacity: .35; background-image: radial-gradient(#cbd5e1 1px, transparent 1px); background-size: 22px 22px; }
.graph-empty { grid-column: 1 / -1; min-height: 620px; }
.canvas-hint { position: absolute; left: 16px; bottom: 14px; padding: 6px 10px; color: var(--color-text-secondary); font-size: 12px; border: 1px solid var(--color-border); border-radius: 999px; background: rgba(255,255,255,.9); }
.node-panel { overflow: auto; max-height: 690px; padding: 22px; border-left: 1px solid var(--color-border); background: #fff; }
.node-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.node-type { display: inline-flex; padding: 4px 9px; color: var(--color-primary-strong); font-size: 12px; border-radius: 999px; background: var(--color-primary-soft); }
.node-panel h2 { margin: 10px 0 6px; font-size: 23px; }
.node-panel > p { margin: 0 0 16px; color: var(--color-text-secondary); line-height: 1.65; }
.attribute-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.attribute-grid div { display: grid; gap: 4px; padding: 10px; border: 1px solid var(--color-border-soft); border-radius: 7px; background: #fafbfc; }
.mastery-card { display: grid; gap: 10px; margin-top: 14px; padding: 13px; border-radius: 8px; background: #f8faff; }
.mastery-card > div { display: flex; justify-content: space-between; }
.mastery-card small { color: var(--color-text-secondary); font-size: 12px; }
.action-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 14px; }
.action-grid :deep(.el-button) { width: 100%; margin-left: 0; }
.detail-block { display: grid; gap: 8px; margin-top: 18px; }
.detail-block ul, .path-list { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.detail-block li { display: flex; justify-content: space-between; gap: 8px; padding: 9px 10px; border: 1px solid var(--color-border-soft); border-radius: 7px; }
.detail-block li strong { overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.detail-block li em { flex: none; color: var(--color-text-secondary); font-size: 12px; font-style: normal; }
.path-list li { justify-content: flex-start; }
.path-list li > em { display: grid; flex: 0 0 24px; height: 24px; place-items: center; color: #fff; border-radius: 50%; background: var(--color-primary); }
.path-list div { display: grid; gap: 3px; min-width: 0; }
.path-list small { color: var(--color-text-secondary); }
@media (max-width: 1100px) {
  .summary-grid { grid-template-columns: repeat(2, 1fr); }
  .graph-toolbar { grid-template-columns: 1fr 1fr; }
  .workspace-body { grid-template-columns: 1fr; }
  .node-panel { max-height: none; border-top: 1px solid var(--color-border); border-left: 0; }
}
@media (max-width: 520px) {
  .action-grid { grid-template-columns: 1fr; }
}
</style>
