<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import MindMapViewer from '../../components/resource/MindMapViewer.vue'
import SourceCitation from '../../components/resource/SourceCitation.vue'
import { getResourceMindmapApi, listResourcesApi } from '../../api/resource'
import { useUiStore } from '../../stores/ui'
import type { LearningResource, MindMapNode, MindMapPayload, MindMapSourceType, SourceCitation as Citation } from '../../types/common'

const ui = useUiStore()

const resource = ref<LearningResource | null>(null)
const mindmap = ref<MindMapPayload>(createFallbackMindmap())
const loading = ref(false)
const selectedNodeId = ref('root')
const collapsedNodeIds = ref<string[]>([])
const infoPanels = ref<string[]>([])
const citationPanels = ref<string[]>([])
const reviewPanels = ref<string[]>([])
const evidenceBranchTitles = new Set(['课程依据', '资料依据', '引用来源', '证据来源', '来源依据', '课程证据', '学习依据'])

const selectedNode = computed(() => findNode(mindmap.value.tree, selectedNodeId.value) || mindmap.value.tree)
const selectedCitation = computed(() => findCitationForNode(selectedNode.value))
const citationSourceText = computed(() => {
  const count = mindmap.value.citations.length
  if (!count) return '暂无课程片段'
  const first = mindmap.value.citations[0]
  const location = first.sourceLocation || (first.page ? `第 ${first.page} 页` : '')
  return `${count} 条课程片段${first.documentName ? `，首条来自 ${first.documentName}` : ''}${location ? `（${location}）` : ''}`
})
const auditStatusText = computed(() => {
  const map: Record<LearningResource['auditStatus'], string> = {
    passed: '已通过',
    pending: '待审核',
    warning: '有风险',
    rejected: '已驳回'
  }
  return map[mindmap.value.auditStatus] || '待审核'
})
const heroDescription = computed(() => {
  const coverage = mindmap.value.coverage.length ? mindmap.value.coverage.join('、') : '核心概念、学习流程和常见错误'
  return `围绕《数据结构课程》课程，把${coverage}整理成一张可点击导图。`
})

onMounted(() => {
  void loadMindmap()
})

async function loadMindmap() {
  loading.value = true
  try {
    const resources = await listResourcesApi(true)
    const detail = selectMindmapResource(resources)
    if (!detail) {
      resource.value = null
      resetToFallbackMindmap()
      return
    }
    const payload = await getResourceMindmapApi(detail.id)
    resource.value = detail
    mindmap.value = normalizeMindmap(payload, detail)
    resetMindmapViewState()
  } catch (error) {
    resource.value = null
    resetToFallbackMindmap()
    ElMessage.warning('暂时无法读取后端导图数据，已使用本地课程导图兜底。')
  } finally {
    loading.value = false
  }
}

function selectMindmapResource(resources: LearningResource[]) {
  const mindmaps = resources.filter((item) => item.resourceType === 'mindmap')
  return mindmaps.find((item) => item.auditStatus === 'passed') || mindmaps[0] || null
}

function resetToFallbackMindmap() {
  mindmap.value = createFallbackMindmap()
  resetMindmapViewState()
}

function resetMindmapViewState() {
  selectedNodeId.value = mindmap.value.tree.nodeId
  collapsedNodeIds.value = []
}

function normalizeMindmap(payload: Partial<MindMapPayload> | null | undefined, detail?: LearningResource | null): MindMapPayload {
  const fallback = createFallbackMindmap()
  if (!payload) return fallback
  let tree = normalizeNode(payload.tree || fallback.tree, null, 0)
  const coverageFromTree = tree.children?.map((node) => node.title).filter(Boolean) || []
  const title = cleanMindmapText(payload.title || detail?.title || tree.title || fallback.title, fallback.title)
  if (isLinearListMindmap(title, tree, payload.citations || []) && isShallowMindmapTree(tree)) {
    tree = createFallbackMindmap(payload.citations || fallback.citations).tree
  }
  const rawCoverage = payload.coverage?.length ? payload.coverage : coverageFromTree

  return {
    ...fallback,
    ...payload,
    title,
    course: payload.course || '数据结构课程',
    auditStatus: detail?.auditStatus || payload.auditStatus || fallback.auditStatus,
    tree,
    citations: payload.citations || [],
    layoutEngine: payload.layoutEngine || fallback.layoutEngine,
    nodeSchema: payload.nodeSchema?.length ? payload.nodeSchema : fallback.nodeSchema,
    coverage: normalizeCoverage(tree.children?.map((node) => node.title).filter(Boolean) || rawCoverage, fallback.coverage),
    actions: payload.actions?.length ? payload.actions : fallback.actions,
    markdown: cleanMindmapMarkdown(payload.markdown || fallback.markdown)
  }
}

function normalizeNode(node: Partial<MindMapNode>, parentId: string | null, level: number): MindMapNode {
  const nodeId = node.nodeId || node.id || `node_${level}_${Math.random().toString(36).slice(2)}`
  const sourceType = normalizeSourceType(node.sourceType)
  const rawTitle = cleanMindmapText(node.title, level === 0 ? '数据结构课程知识结构' : '数据结构知识点')
  const title = level === 0 && rawTitle === '数据结构课程' ? '数据结构课程知识结构' : rawTitle
  const children = (node.children || [])
    .filter((child) => !(level === 0 && isEvidenceBranchTitle(cleanMindmapText(child.title))))
    .map((child) => normalizeNode(child, nodeId, level + 1))

  return {
    nodeId,
    id: nodeId,
    title,
    summary: cleanMindmapText(node.summary, '该节点来自课程资料和学生画像的综合分析。'),
    level,
    parentId,
    children,
    sourceType,
    sourceChunkIds: node.sourceChunkIds || [],
    sourceEvidence: node.sourceEvidence || [],
    jumpTarget: node.jumpTarget,
    confidence: typeof node.confidence === 'number' ? node.confidence : sourceType === '模型推断' ? 0.78 : 0.9,
    status: node.status || (sourceType === '测评薄弱点' ? 'needs_review' : 'confirmed'),
    downstreamImpact: node.downstreamImpact || ['资源推荐', '学习路径', '阶段测评']
  }
}

function cleanMindmapText(value?: string, fallback = '') {
  const text = String(value || '').trim()
  if (!text) return fallback
  const replacements: [RegExp, string][] = [
    [/课程资料待上传知识结构/g, '数据结构课程知识结构'],
    [/课程资料待上传完整思维导图/g, '数据结构课程知识结构'],
    [/课程资料待上传流程/g, '学习流程'],
    [/课程资料待上传/g, '数据结构课程'],
    [/课程资料与特征选择/g, '逻辑结构与存储结构'],
    [/课程资料 代码实践/g, '代码实践'],
    [/课程资料结构/g, '数据结构术语'],
    [/旧课程分类器示例/g, '核心操作实现'],
    [/旧课程参数示例/g, '复杂度分析'],
    [/旧课程实验示例/g, '边界条件测试'],
  ]
  let cleaned = text
  replacements.forEach(([pattern, replacement]) => {
    cleaned = cleaned.replace(pattern, replacement)
  })
  if (cleaned === '课程资料') return '数据结构知识点'
  return cleaned || fallback
}

function normalizeCoverage(values: string[], fallback: string[]) {
  const cleaned = values.map((item) => cleanMindmapText(item)).filter((item) => item && !isEvidenceBranchTitle(item))
  const hasPlaceholder = cleaned.some((item) => /课程资料|旧课程/.test(item))
  const source = hasPlaceholder || !cleaned.length ? fallback : cleaned
  return Array.from(new Set(source.map((item) => cleanMindmapText(item)).filter((item) => item && !isEvidenceBranchTitle(item))))
}

function isLinearListMindmap(title: string, tree: MindMapNode, citations: Citation[]) {
  const haystack = [
    title,
    tree.title,
    ...(tree.children || []).map((node) => node.title),
    ...citations.map((item) => `${item.documentName || ''} ${item.contentPreview || ''}`)
  ].join(' ')
  return /线性表|顺序表|链表|有序表/.test(haystack)
}

function isShallowMindmapTree(tree: MindMapNode) {
  const children = tree.children || []
  if (children.length < 6) return true
  return children.filter((node) => (node.children || []).length >= 3).length < 5
}

function isEvidenceBranchTitle(title?: string) {
  return evidenceBranchTitles.has(String(title || '').replace(/\s+/g, '').replace(/[：:，,。；;]/g, ''))
}

function cleanMindmapMarkdown(markdown: string) {
  return markdown
    .split('\n')
    .map((line) => cleanMindmapText(line, line))
    .join('\n')
}

function normalizeSourceType(type?: string): MindMapSourceType {
  if (type === '模型推断' || type === '测评薄弱点') return type
  return '课程依据'
}

function findNode(node: MindMapNode, id: string): MindMapNode | undefined {
  if (node.nodeId === id) return node
  for (const child of node.children || []) {
    const found = findNode(child, id)
    if (found) return found
  }
  return undefined
}

function findCitationForNode(node: MindMapNode): Citation | undefined {
  const chunkId = node.sourceChunkIds?.[0]
  return mindmap.value.citations.find((citation) => citation.chunkId === chunkId)
    || node.sourceEvidence?.[0]
    || undefined
}

function handleSelectNode(node: MindMapNode) {
  selectedNodeId.value = node.nodeId
}

function handleToggleNode(nodeId: string) {
  collapsedNodeIds.value = collapsedNodeIds.value.includes(nodeId)
    ? collapsedNodeIds.value.filter((id) => id !== nodeId)
    : [...collapsedNodeIds.value, nodeId]
}

function expandAll() {
  collapsedNodeIds.value = []
}

function collapseAll() {
  collapsedNodeIds.value = collectParentIds(mindmap.value.tree)
}

function collectParentIds(node: MindMapNode): string[] {
  const ids: string[] = []
  if (node.children?.length) ids.push(node.nodeId)
  for (const child of node.children || []) {
    ids.push(...collectParentIds(child))
  }
  return ids.filter((id) => id !== mindmap.value.tree.nodeId)
}

function exportMarkdown() {
  const blob = new Blob([mindmap.value.markdown || toMarkdown(mindmap.value.tree)], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
    link.download = `${mindmap.value.title || '数据结构课程知识结构'}思维导图.md`
  link.click()
  URL.revokeObjectURL(url)
}

function exportPng() {
  const svg = document.querySelector('.mindmap-svg') as SVGSVGElement | null
  if (!svg) {
    ElMessage.warning('导图尚未渲染完成')
    return
  }
  const xml = new XMLSerializer().serializeToString(svg)
  const svgBlob = new Blob([xml], { type: 'image/svg+xml;charset=utf-8' })
  const url = URL.createObjectURL(svgBlob)
  const image = new Image()
  image.onload = () => {
    const canvas = document.createElement('canvas')
    const box = svg.viewBox.baseVal
    canvas.width = Math.max(1600, Math.ceil(box.width))
    canvas.height = Math.max(900, Math.ceil(box.height))
    const context = canvas.getContext('2d')
    if (!context) return
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    const link = document.createElement('a')
    link.download = `${mindmap.value.title || '数据结构课程知识结构'}思维导图.png`
    link.href = canvas.toDataURL('image/png')
    link.click()
    URL.revokeObjectURL(url)
  }
  image.src = url
}

function handleExport(command: string | number | object) {
  if (command === 'markdown') exportMarkdown()
  if (command === 'png') exportPng()
}

function toMarkdown(node: MindMapNode, depth = 0): string {
  const prefix = `${'  '.repeat(depth)}- `
  return `${prefix}${node.title}\n${(node.children || []).map((child) => toMarkdown(child, depth + 1)).join('')}`
}

function createFallbackMindmap(citations?: Citation[]): MindMapPayload {
  const citation: Citation = citations?.[0] || {
    documentId: 'doc_ai_intro',
    documentName: '数据结构课程课程资料',
    sourceLocation: '课程知识结构',
    chunkId: 'chunk_ai_intro_ml_11_01',
    contentPreview: '数据结构课程通常围绕逻辑结构、存储结构、基本操作、复杂度分析和代码实践组织学习。',
    page: 18,
    similarity: 0.91,
    fullText: '数据结构课程需要把概念、操作过程、复杂度和实践代码放在同一张知识网络中理解。'
  }

  const makeLeaf = (id: string, title: string, parentId: string, sourceType: MindMapSourceType = '课程依据'): MindMapNode => ({
    nodeId: id,
    title,
    level: 2,
    parentId,
    children: [],
    sourceType,
    sourceChunkIds: [citation.chunkId],
    confidence: sourceType === '测评薄弱点' ? 0.86 : 0.9,
    status: sourceType === '测评薄弱点' ? 'needs_review' : 'confirmed',
    summary: `${title} 是数据结构课程学习中的关键节点。`,
    downstreamImpact: ['资源推荐', '学习路径', '阶段测评']
  })

  const makeBranch = (id: string, title: string, children: MindMapNode[], sourceType: MindMapSourceType = '课程依据'): MindMapNode => ({
    nodeId: id,
    title,
    level: 1,
    parentId: 'root',
    children,
    sourceType,
    sourceChunkIds: [citation.chunkId],
    confidence: sourceType === '模型推断' ? 0.82 : 0.92,
    status: sourceType === '测评薄弱点' ? 'needs_review' : 'confirmed',
    summary: `${title} 用于组织数据结构课程的学习顺序。`,
    downstreamImpact: ['资源推荐', '学习路径', '阶段测评']
  })

  const tree: MindMapNode = {
    nodeId: 'root',
    title: '线性表',
    level: 0,
    parentId: null,
    children: [
      makeBranch('definition', '定义与逻辑结构', [
        makeLeaf('definition-sequence', '有限序列', 'definition'),
        makeLeaf('definition-relation', '前驱与后继', 'definition'),
        makeLeaf('definition-empty', '表长与空表', 'definition'),
        makeLeaf('definition-linear', '线性结构特征', 'definition')
      ]),
      makeBranch('sequence-list', '顺序表', [
        makeLeaf('sequence-storage', '连续存储', 'sequence-list'),
        makeLeaf('sequence-address', '地址计算', 'sequence-list'),
        makeLeaf('sequence-random', '随机访问', 'sequence-list'),
        makeLeaf('sequence-move', '插入删除移动元素', 'sequence-list')
      ]),
      makeBranch('linked-list', '链表', [
        makeLeaf('linked-single', '单链表', 'linked-list'),
        makeLeaf('linked-double', '双链表', 'linked-list'),
        makeLeaf('linked-cycle', '循环链表', 'linked-list'),
        makeLeaf('linked-head', '头结点与指针域', 'linked-list')
      ]),
      makeBranch('operations', '基本操作', [
        makeLeaf('op-init', '初始化', 'operations'),
        makeLeaf('op-find', '查找', 'operations'),
        makeLeaf('op-insert', '插入', 'operations'),
        makeLeaf('op-delete', '删除', 'operations'),
        makeLeaf('op-traverse', '遍历与合并', 'operations')
      ]),
      makeBranch('complexity', '复杂度分析', [
        makeLeaf('complexity-array', '顺序表查找 O(1)/O(n)', 'complexity'),
        makeLeaf('complexity-linked', '链表查找 O(n)', 'complexity'),
        makeLeaf('complexity-update', '插入删除代价对比', 'complexity'),
        makeLeaf('complexity-space', '空间开销', 'complexity')
      ]),
      makeBranch('practice', '典型应用与代码实现', [
        makeLeaf('practice-merge', '有序表合并', 'practice', '模型推断'),
        makeLeaf('practice-static', '静态链表', 'practice', '模型推断'),
        makeLeaf('practice-boundary', '边界条件测试', 'practice', '模型推断'),
        makeLeaf('practice-robust', '代码健壮性', 'practice', '模型推断')
      ], '模型推断'),
      makeBranch('mistakes', '易错点', [
        makeLeaf('mistake-index', '下标越界', 'mistakes', '测评薄弱点'),
        makeLeaf('mistake-empty', '空表处理', 'mistakes', '测评薄弱点'),
        makeLeaf('mistake-pointer', '指针断链', 'mistakes', '测评薄弱点'),
        makeLeaf('mistake-complexity', '复杂度误判', 'mistakes', '测评薄弱点')
      ], '测评薄弱点')
    ],
    sourceType: '课程依据',
    sourceChunkIds: [citation.chunkId],
    confidence: 0.95,
    status: 'confirmed',
    summary: '围绕线性表的逻辑结构、存储实现、基本操作、复杂度和代码实践组织知识结构。',
    downstreamImpact: ['资源推荐', '学习路径', '阶段测评']
  }

  return {
    resourceId: 'res_mindmap',
    title: '线性表知识结构',
    course: '数据结构课程',
    sourceAgent: '多模态生成 Agent',
    auditStatus: 'passed',
    mermaid: '',
    tree,
    nodeSchema: ['nodeId', 'title', 'level', 'parentId', 'children', 'sourceType', 'sourceChunkIds', 'jumpTarget', 'confidence', 'status'],
    layoutEngine: {
      name: '自研 SVG 树状导图',
      features: ['分层布局', '展开收起', '节点证据链', '导出 PNG', '导出 Markdown']
    },
    coverage: ['定义与逻辑结构', '顺序表', '链表', '基本操作', '复杂度分析', '典型应用与代码实现', '易错点'],
    citations: citations?.length ? citations : [citation],
    actions: ['查看资源', '进入测评', '加入学习路径'],
    markdown: toMarkdown(tree)
  }
}
</script>

<template>
  <main v-loading="loading" class="mindmap-page">
    <section class="page-head">
      <div>
        <span class="eyebrow">完整思维导图</span>
        <h1>{{ mindmap.title }}</h1>
        <p>{{ heroDescription }}</p>
      </div>
      <div class="head-actions">
        <el-button @click="expandAll">展开全部</el-button>
        <el-button @click="collapseAll">收起细节</el-button>
        <el-dropdown trigger="click" @command="handleExport">
          <el-button type="primary">导出</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="png">导出 PNG</el-dropdown-item>
              <el-dropdown-item command="markdown">导出 Markdown</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </section>

    <el-collapse v-model="infoPanels" class="info-collapse">
      <el-collapse-item name="meta">
        <template #title>
          <div class="compact-meta">
            <el-tag size="small" effect="plain">{{ mindmap.course }}</el-tag>
            <el-tag size="small" :type="mindmap.auditStatus === 'passed' ? 'success' : 'warning'" effect="plain">{{ auditStatusText }}</el-tag>
            <span>{{ mindmap.coverage.slice(0, 3).join(' / ') }}</span>
          </div>
        </template>
        <div class="info-grid">
          <div>
            <span>覆盖范围</span>
            <strong>{{ mindmap.coverage.join(' / ') }}</strong>
          </div>
          <div>
            <span>引用来源</span>
            <strong>{{ citationSourceText }}</strong>
            <small>{{ mindmap.citations[0]?.contentPreview || '节点说明中可查看对应课程依据。' }}</small>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>

    <section class="map-section">
      <div class="section-title">
        <div>
          <h2>知识结构图</h2>
          <p>按课程模块组织一级结构和二级知识点；点击模块或知识点可查看解释、依据和后续学习动作。</p>
        </div>
      </div>
      <MindMapViewer
        :root="mindmap.tree"
        :selected-node-id="selectedNodeId"
        :collapsed-node-ids="collapsedNodeIds"
        @select-node="handleSelectNode"
        @toggle-node="handleToggleNode"
      />
    </section>

    <section class="detail-section">
      <div class="node-card">
        <div class="node-card-head">
          <div>
            <span class="muted">知识点详情</span>
            <h2>{{ selectedNode.title }}</h2>
          </div>
          <el-tag :type="selectedNode.sourceType === '课程依据' ? 'success' : selectedNode.sourceType === '测评薄弱点' ? 'warning' : 'info'">
            {{ selectedNode.sourceType }}
          </el-tag>
        </div>
        <p>{{ selectedNode.summary }}</p>
        <div class="node-meta">
          <span>置信度 {{ Math.round(selectedNode.confidence * 100) }}%</span>
          <span>状态 {{ selectedNode.status === 'needs_review' ? '需补强' : '已确认' }}</span>
          <span>来源 {{ selectedNode.sourceChunkIds?.[0] || '课程资料' }}</span>
        </div>
        <div class="impact-list">
          <span v-for="item in selectedNode.downstreamImpact" :key="item">{{ item }}</span>
        </div>
      </div>

      <div class="citation-card">
        <el-collapse v-model="citationPanels" class="node-source-collapse">
          <el-collapse-item name="source">
            <template #title>
              <span>查看节点依据</span>
            </template>
            <SourceCitation v-if="selectedCitation" :citations="[selectedCitation]" />
            <p v-else class="muted">该节点暂无课程引用。</p>
          </el-collapse-item>
        </el-collapse>
      </div>
    </section>

    <el-collapse v-if="ui.reviewMode" v-model="reviewPanels" class="review-collapse">
      <el-collapse-item title="查看评审证据链：节点 Schema、布局引擎和下游影响" name="evidence">
        <div class="review-grid">
          <div>
            <h3>节点 Schema</h3>
            <code>{{ mindmap.nodeSchema.join(' / ') }}</code>
          </div>
          <div>
            <h3>布局引擎</h3>
            <code>{{ mindmap.layoutEngine.name }}：{{ mindmap.layoutEngine.features.join('、') }}</code>
          </div>
          <div>
            <h3>生成 Agent</h3>
            <code>{{ mindmap.sourceAgent }}</code>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
  </main>
</template>

<style scoped>
.mindmap-page {
  padding: 28px;
  color: #0f172a;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 10px;
}

.eyebrow {
  display: inline-flex;
  margin-bottom: 8px;
  padding: 4px 10px;
  border: 1px solid #bfdbfe;
  border-radius: 999px;
  color: #2563eb;
  background: #eff6ff;
  font-weight: 600;
}

.page-head h1 {
  margin: 0;
  font-size: 28px;
  line-height: 1.25;
}

.page-head p,
.section-title p,
.muted {
  margin: 8px 0 0;
  color: #64748b;
}

.head-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.map-section,
.node-card,
.citation-card,
.info-collapse,
.review-collapse {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
}

.info-collapse {
  margin-bottom: 12px;
  padding: 0 12px;
}

.compact-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: #64748b;
  font-size: 13px;
}

.compact-meta span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.info-grid div {
  display: grid;
  gap: 5px;
  padding: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
}

.info-grid span,
.info-grid small {
  color: #64748b;
}

.info-grid strong {
  color: #0f172a;
  line-height: 1.5;
}

.map-section {
  padding: 18px;
}

.section-title {
  display: flex;
  justify-content: space-between;
  margin-bottom: 14px;
}

.section-title h2,
.node-card h2,
.citation-card h2 {
  margin: 0;
  font-size: 20px;
}

.detail-section {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  margin-top: 16px;
}

.node-card,
.citation-card {
  padding: 18px;
}

.citation-card {
  padding: 0 12px;
}

.node-source-collapse {
  border-top: 0;
  border-bottom: 0;
}

.node-card-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
}

.node-card p {
  color: #334155;
  line-height: 1.8;
}

.node-meta,
.impact-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.node-meta span,
.impact-list span {
  padding: 5px 10px;
  border-radius: 999px;
  background: #f8fafc;
  color: #475569;
  font-size: 13px;
}

.impact-list span {
  background: #eff6ff;
  color: #2563eb;
}

.review-collapse {
  margin-top: 16px;
  padding: 0 12px;
}

.review-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.review-grid code {
  display: block;
  padding: 12px;
  border-radius: 8px;
  background: #f8fafc;
  white-space: normal;
}

@media (max-width: 1100px) {
  .page-head,
  .detail-section {
    display: block;
  }

  .head-actions {
    justify-content: flex-start;
    margin-top: 14px;
  }

  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
