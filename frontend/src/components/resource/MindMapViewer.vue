<script setup lang="ts">
import { computed } from 'vue'
import type { MindMapNode, MindMapSourceType } from '../../types/common'

const props = withDefaults(
  defineProps<{
    root?: MindMapNode
    content?: string
    selectedNodeId?: string
    collapsedNodeIds?: string[]
  }>(),
  {
    collapsedNodeIds: () => [],
  },
)

const emit = defineEmits<{
  (event: 'select-node', node: MindMapNode): void
  (event: 'toggle-node', nodeId: string): void
}>()

type RenderBranch = MindMapNode & {
  color: string
  x: number
  y: number
  rowHeight: number
  childrenVisible: MindMapNode[]
}

const SVG_WIDTH = 1440
const ROOT_X = 265
const ROOT_WIDTH = 250
const ROOT_HEIGHT = 104
const ROOT_RIGHT = ROOT_X + ROOT_WIDTH
const BRANCH_X = 650
const BRANCH_WIDTH = 260
const BRANCH_HEIGHT = 58
const LEAF_X = 990
const LEAF_WIDTH = 320
const LEAF_HEIGHT = 42
const TOP_PADDING = 78
const ROW_GAP = 30
const CHILD_GAP = 52
const COLLAPSED_ROW_HEIGHT = 86
const MIN_EXPANDED_HEIGHT = 164

const palette = ['#2563eb', '#0f766e', '#7c3aed', '#b45309', '#be123c', '#047857', '#4338ca']
const branchOrder = ['定义与逻辑结构', '核心概念', '顺序表', '链表', '基本操作', '复杂度分析', '典型应用与代码实现', '易错点', '常见错误', '学习路径']
const evidenceBranchTitles = new Set(['课程依据', '资料依据', '引用来源', '证据来源', '来源依据', '课程证据', '学习依据'])

function makeNode(options: {
  nodeId: string
  title: string
  level: number
  parentId?: string | null
  children?: MindMapNode[]
  sourceType?: MindMapSourceType
  confidence?: number
  summary?: string
  status?: MindMapNode['status']
}): MindMapNode {
  return {
    nodeId: options.nodeId,
    title: options.title,
    level: options.level,
    parentId: options.parentId ?? null,
    children: options.children ?? [],
    sourceType: options.sourceType ?? '课程依据',
    sourceChunkIds: [],
    sourceEvidence: [],
    jumpTarget: '/student/resources',
    confidence: options.confidence ?? 0.9,
    status: options.status ?? 'confirmed',
    summary: options.summary ?? `${options.title} 是线性表学习中的关键知识点。`,
    downstreamImpact: ['资源推荐', '学习路径', '阶段测评'],
  }
}

function leaf(nodeId: string, title: string, parentId: string) {
  return makeNode({ nodeId, title, parentId, level: 2 })
}

function branch(nodeId: string, title: string, children: MindMapNode[]) {
  return makeNode({ nodeId, title, level: 1, parentId: 'root', children })
}

const fallbackRoot = makeNode({
  nodeId: 'root',
  title: '线性表',
  level: 0,
  summary: '围绕线性表的逻辑结构、存储实现、基本操作、复杂度和代码实践组织知识结构。',
  children: [
    branch('definition', '定义与逻辑结构', [
      leaf('definition-sequence', '有限序列', 'definition'),
      leaf('definition-relation', '前驱与后继', 'definition'),
      leaf('definition-empty', '表长与空表', 'definition'),
      leaf('definition-linear', '线性结构特征', 'definition'),
    ]),
    branch('sequence-list', '顺序表', [
      leaf('sequence-storage', '连续存储', 'sequence-list'),
      leaf('sequence-address', '地址计算', 'sequence-list'),
      leaf('sequence-random', '随机访问', 'sequence-list'),
      leaf('sequence-move', '插入删除移动元素', 'sequence-list'),
    ]),
    branch('linked-list', '链表', [
      leaf('linked-single', '单链表', 'linked-list'),
      leaf('linked-double', '双链表', 'linked-list'),
      leaf('linked-cycle', '循环链表', 'linked-list'),
      leaf('linked-head', '头结点与指针域', 'linked-list'),
    ]),
    branch('operations', '基本操作', [
      leaf('op-init', '初始化', 'operations'),
      leaf('op-find', '查找', 'operations'),
      leaf('op-insert', '插入', 'operations'),
      leaf('op-delete', '删除', 'operations'),
      leaf('op-traverse', '遍历与合并', 'operations'),
    ]),
    branch('complexity', '复杂度分析', [
      leaf('complexity-array', '顺序表查找 O(1)/O(n)', 'complexity'),
      leaf('complexity-linked', '链表查找 O(n)', 'complexity'),
      leaf('complexity-update', '插入删除代价对比', 'complexity'),
      leaf('complexity-space', '空间开销', 'complexity'),
    ]),
    branch('practice', '典型应用与代码实现', [
      leaf('practice-merge', '有序表合并', 'practice'),
      leaf('practice-static', '静态链表', 'practice'),
      leaf('practice-boundary', '边界条件测试', 'practice'),
      leaf('practice-robust', '代码健壮性', 'practice'),
    ]),
    branch('mistakes', '易错点', [
      leaf('mistake-index', '下标越界', 'mistakes'),
      leaf('mistake-empty', '空表处理', 'mistakes'),
      leaf('mistake-pointer', '指针断链', 'mistakes'),
      leaf('mistake-complexity', '复杂度误判', 'mistakes'),
    ]),
  ],
})

const rootNode = computed(() => props.root ?? fallbackRoot)
const collapsedIds = computed(() => props.collapsedNodeIds ?? [])
const isCollapsed = (nodeId: string) => collapsedIds.value.includes(nodeId)
const isSelected = (node: MindMapNode) => props.selectedNodeId === node.nodeId

function normalizedTitle(title?: string) {
  return String(title || '').replace(/\s+/g, '').replace(/[：:，,。；;]/g, '')
}

function isEvidenceBranch(title?: string) {
  return evidenceBranchTitles.has(normalizedTitle(title))
}

function branchRank(title?: string) {
  const text = String(title || '')
  const exact = branchOrder.indexOf(text)
  if (exact >= 0) return exact
  const fuzzy = branchOrder.findIndex((item) => text.includes(item) || item.includes(text))
  return fuzzy >= 0 ? fuzzy : branchOrder.length
}

function displayLabel(value?: string, maxLength = 16) {
  const text = String(value || '').trim()
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}...` : text
}

const branches = computed<RenderBranch[]>(() => {
  const source = rootNode.value.children?.length ? rootNode.value.children : fallbackRoot.children
  const visibleSource = [...(source ?? [])]
    .filter((node) => !isEvidenceBranch(node.title))
    .sort((a, b) => branchRank(a.title) - branchRank(b.title))
  let cursorY = TOP_PADDING

  return visibleSource.map((node, index) => {
    const childrenVisible = isCollapsed(node.nodeId) ? [] : (node.children ?? [])
    const childCount = Math.max(childrenVisible.length, 1)
    const rowHeight = childrenVisible.length
      ? Math.max(MIN_EXPANDED_HEIGHT, (childCount - 1) * CHILD_GAP + 104)
      : COLLAPSED_ROW_HEIGHT
    const y = cursorY + rowHeight / 2
    cursorY += rowHeight + ROW_GAP

    return {
      ...node,
      color: palette[index % palette.length],
      x: BRANCH_X,
      y,
      rowHeight,
      childrenVisible,
    }
  })
})

const viewBoxHeight = computed(() => {
  const last = branches.value[branches.value.length - 1]
  if (!last) return 760
  return Math.max(760, last.y + last.rowHeight / 2 + TOP_PADDING)
})

const rootY = computed(() => viewBoxHeight.value / 2)

function selectNode(node: MindMapNode) {
  emit('select-node', node)
}

function toggleNode(nodeId: string) {
  emit('toggle-node', nodeId)
}

function rootPath(node: RenderBranch) {
  const startX = ROOT_RIGHT - 4
  const startY = rootY.value
  const c1x = startX + 88
  const c2x = node.x - 116
  return `M ${startX} ${startY} C ${c1x} ${startY}, ${c2x} ${node.y}, ${node.x - 16} ${node.y}`
}

function childY(branchNode: RenderBranch, index: number, total: number) {
  if (total <= 1) return branchNode.y
  const middle = (total - 1) / 2
  return branchNode.y + (index - middle) * CHILD_GAP
}

function branchToLeafPath(branchNode: RenderBranch, y: number) {
  const startX = branchNode.x + BRANCH_WIDTH
  const c1x = startX + 62
  const c2x = LEAF_X - 72
  return `M ${startX} ${branchNode.y} C ${c1x} ${branchNode.y}, ${c2x} ${y}, ${LEAF_X} ${y}`
}

function sourceClass(sourceType?: MindMapSourceType) {
  return {
    'source-course': sourceType === '课程依据',
    'source-model': sourceType === '模型推断',
    'source-weak': sourceType === '测评薄弱点',
  }
}
</script>

<template>
  <div class="mindmap-viewer">
    <svg
      class="mindmap-svg"
      :viewBox="`0 0 ${SVG_WIDTH} ${viewBoxHeight}`"
      role="img"
      :aria-label="`${rootNode.title || '线性表'}思维导图`"
      preserveAspectRatio="xMidYMin meet"
    >
      <rect x="0" y="0" :width="SVG_WIDTH" :height="viewBoxHeight" rx="18" fill="#ffffff" />

      <g
        class="root-node"
        :class="{ selected: props.selectedNodeId === rootNode.nodeId }"
        role="button"
        tabindex="0"
        @click="selectNode(rootNode)"
      >
        <rect
          :x="ROOT_X"
          :y="rootY - ROOT_HEIGHT / 2"
          :width="ROOT_WIDTH"
          :height="ROOT_HEIGHT"
          rx="18"
          fill="#ffffff"
          stroke="#fb7a55"
          stroke-width="5"
        />
        <text class="root-kicker" :x="ROOT_X + ROOT_WIDTH / 2" :y="rootY - 22" text-anchor="middle">数据结构课程</text>
        <text class="root-title" :x="ROOT_X + ROOT_WIDTH / 2" :y="rootY + 10" text-anchor="middle">
          {{ displayLabel(rootNode.title || '线性表', 9) }}
        </text>
        <text class="root-meta" :x="ROOT_X + ROOT_WIDTH / 2" :y="rootY + 40" text-anchor="middle">
          {{ branches.length }} 个模块 / {{ branches.reduce((sum, item) => sum + (item.children?.length || 0), 0) }} 个知识点
        </text>
      </g>

      <g
        v-for="branchNode in branches"
        :key="branchNode.nodeId"
        class="branch-group"
        :class="[{ selected: isSelected(branchNode) }, sourceClass(branchNode.sourceType)]"
      >
        <path class="root-curve" :d="rootPath(branchNode)" :stroke="branchNode.color" />

        <g role="button" tabindex="0" @click="selectNode(branchNode)">
          <title>{{ branchNode.title }}</title>
          <rect
            :x="branchNode.x"
            :y="branchNode.y - BRANCH_HEIGHT / 2"
            :width="BRANCH_WIDTH"
            :height="BRANCH_HEIGHT"
            rx="10"
            fill="#ffffff"
            :stroke="isSelected(branchNode) ? '#2563eb' : '#d8dee9'"
            :stroke-width="isSelected(branchNode) ? 2.4 : 1.2"
          />
          <rect
            :x="branchNode.x"
            :y="branchNode.y - BRANCH_HEIGHT / 2"
            width="6"
            :height="BRANCH_HEIGHT"
            rx="3"
            :fill="branchNode.color"
          />
          <circle :cx="branchNode.x + 28" :cy="branchNode.y" r="5" :fill="branchNode.color" />
          <text class="branch-title" :x="branchNode.x + 46" :y="branchNode.y - 4">
            {{ displayLabel(branchNode.title, 12) }}
          </text>
          <text class="branch-meta" :x="branchNode.x + 46" :y="branchNode.y + 19">
            {{ branchNode.children?.length || 0 }} 个知识点
          </text>
        </g>

        <g
          class="toggle-dot"
          role="button"
          tabindex="0"
          @click.stop="toggleNode(branchNode.nodeId)"
        >
          <rect :x="branchNode.x + BRANCH_WIDTH - 34" :y="branchNode.y - 14" width="24" height="24" rx="6" fill="#f8fafc" stroke="#cbd5e1" />
          <line :x1="branchNode.x + BRANCH_WIDTH - 28" :y1="branchNode.y - 2" :x2="branchNode.x + BRANCH_WIDTH - 16" :y2="branchNode.y - 2" stroke="#475569" stroke-width="2" stroke-linecap="round" />
          <line
            v-if="isCollapsed(branchNode.nodeId)"
            :x1="branchNode.x + BRANCH_WIDTH - 22"
            :y1="branchNode.y - 8"
            :x2="branchNode.x + BRANCH_WIDTH - 22"
            :y2="branchNode.y + 4"
            stroke="#475569"
            stroke-width="2"
            stroke-linecap="round"
          />
        </g>

        <g
          v-for="(child, childIndex) in branchNode.childrenVisible"
          :key="child.nodeId"
          class="leaf-group"
          :class="[{ selected: isSelected(child) }, sourceClass(child.sourceType)]"
          role="button"
          tabindex="0"
          @click="selectNode(child)"
        >
          <title>{{ child.title }}</title>
          <path
            class="leaf-curve"
            :d="branchToLeafPath(branchNode, childY(branchNode, childIndex, branchNode.childrenVisible.length))"
            :stroke="branchNode.color"
          />
          <rect
            :x="LEAF_X"
            :y="childY(branchNode, childIndex, branchNode.childrenVisible.length) - LEAF_HEIGHT / 2"
            :width="LEAF_WIDTH"
            :height="LEAF_HEIGHT"
            rx="8"
            fill="#ffffff"
            :stroke="isSelected(child) ? '#2563eb' : '#e2e8f0'"
            :stroke-width="isSelected(child) ? 2.2 : 1.1"
          />
          <circle :cx="LEAF_X + 20" :cy="childY(branchNode, childIndex, branchNode.childrenVisible.length)" r="4" :fill="branchNode.color" />
          <text class="leaf-title" :x="LEAF_X + 38" :y="childY(branchNode, childIndex, branchNode.childrenVisible.length) + 6">
            {{ displayLabel(child.title, 18) }}
          </text>
        </g>
      </g>
    </svg>
  </div>
</template>

<style scoped>
.mindmap-viewer {
  width: 100%;
  height: clamp(620px, 72vh, 860px);
  overflow: auto;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #ffffff;
}

.mindmap-svg {
  min-width: 1120px;
  width: 100%;
  height: auto;
  display: block;
}

.root-node,
.branch-group g,
.leaf-group {
  cursor: pointer;
}

.root-node rect,
.branch-group rect,
.leaf-group rect {
  filter: drop-shadow(0 8px 18px rgba(15, 23, 42, 0.055));
}

.root-node.selected rect {
  stroke: #2563eb;
}

.root-kicker {
  fill: #64748b;
  font-size: 15px;
  font-weight: 650;
}

.root-title {
  fill: #0f172a;
  font-size: 32px;
  font-weight: 760;
}

.root-meta {
  fill: #475569;
  font-size: 15px;
  font-weight: 600;
}

.root-curve {
  fill: none;
  stroke-width: 5;
  stroke-linecap: round;
  stroke-linejoin: round;
  opacity: 0.72;
}

.leaf-curve {
  fill: none;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
  opacity: 0.4;
}

.branch-title {
  fill: #0f172a;
  font-size: 21px;
  font-weight: 720;
}

.branch-meta {
  fill: #64748b;
  font-size: 13px;
  font-weight: 560;
}

.leaf-title {
  fill: #1e293b;
  font-size: 17px;
  font-weight: 610;
}

.source-model .branch-title,
.source-model .leaf-title {
  font-style: italic;
}

.source-weak rect {
  fill: #fff7ed;
}

@media (max-width: 900px) {
  .mindmap-viewer {
    height: 640px;
  }
}
</style>
