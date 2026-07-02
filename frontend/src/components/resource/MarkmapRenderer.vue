<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Transformer } from 'markmap-lib'
import { Markmap } from 'markmap-view'
import type { IMarkmapOptions } from 'markmap-view'

const props = withDefaults(defineProps<{
  markdown?: string
  title?: string
  active?: boolean
}>(), {
  markdown: '',
  title: '数据结构课程知识结构',
  active: true,
})

const svgRef = ref<SVGSVGElement | null>(null)
const stageRef = ref<HTMLElement | null>(null)
const renderError = ref('')
const transformer = new Transformer()
let markmap: Markmap | null = null
let resizeObserver: ResizeObserver | null = null
let intersectionObserver: IntersectionObserver | null = null
let renderSequence = 0
let fitTimer: ReturnType<typeof setTimeout> | null = null
const isIntersecting = ref(false)

const fallbackMarkdown = `# 数据结构课程知识结构
## 先修知识
### 算法基础
### 复杂度分析
### 指针与引用
## 核心概念
### 线性表
### 栈和队列
### 树与二叉树
### 图结构
## 学习流程
### 阅读定义
### 手工跟踪
### 代码实现
### 练习测评
## 常见错误
### 忽略边界条件
### 混淆逻辑结构和存储结构
### 复杂度分析不完整
## 代码实践
### 初始化结构
### 实现核心操作
### 设计边界测试
## 测评闭环
### 完成阶段测评
### 记录错因
### 插入补强任务`

function normalizeMarkdown(value?: string) {
  const text = (value || '').trim()
  if (!text) return fallbackMarkdown
  if (text.startsWith('#')) return text
  return `# ${props.title}\n${text}`
}

function hasUsableSize() {
  if (!props.active || !isIntersecting.value || document.visibilityState !== 'visible') return false
  if (!stageRef.value?.isConnected || !stageRef.value.offsetParent || !svgRef.value?.isConnected) return false
  const rect = stageRef.value?.getBoundingClientRect()
  const svgRect = svgRef.value?.getBoundingClientRect()
  return Boolean(
    rect &&
    svgRect &&
    Number.isFinite(rect.width) &&
    Number.isFinite(rect.height) &&
    Number.isFinite(svgRect.width) &&
    Number.isFinite(svgRect.height) &&
    rect.width > 20 &&
    rect.height > 20 &&
    svgRect.width > 20 &&
    svgRect.height > 20
  )
}

async function waitForUsableSize(sequence: number) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (sequence !== renderSequence) return false
    if (hasUsableSize()) return true
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
  }
  return false
}

async function safeFit() {
  if (!markmap || !hasUsableSize()) return
  try {
    await markmap.fit()
    renderError.value = ''
  } catch {
    renderError.value = '图解画布暂时无法适配，请切换标签或调整窗口后重试。'
  }
}

function scheduleFit() {
  if (!props.active || !isIntersecting.value) return
  if (fitTimer) clearTimeout(fitTimer)
  fitTimer = setTimeout(() => {
    void safeFit()
  }, 80)
}

async function renderMarkmap() {
  const sequence = ++renderSequence
  if (!props.active) return
  await nextTick()
  if (!svgRef.value || !(await waitForUsableSize(sequence))) return
  let root
  try {
    root = transformer.transform(normalizeMarkdown(props.markdown)).root
  } catch {
    renderError.value = '图解内容格式不完整，暂时无法渲染。'
    return
  }
  const options: Partial<IMarkmapOptions> = {
    autoFit: false,
    duration: 220,
    fitRatio: 0.92,
    initialExpandLevel: 4,
    maxWidth: 280,
    paddingX: 12,
    spacingHorizontal: 92,
    spacingVertical: 12,
  }
  try {
    if (!markmap) {
      markmap = Markmap.create(svgRef.value, options, root)
    } else {
      await markmap.setData(root, options)
    }
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
    await safeFit()
  } catch {
    renderError.value = '图解渲染失败，请稍后重试。'
  }
}

function fitView() {
  void safeFit()
}

function resetScale() {
  if (!markmap || !hasUsableSize()) return
  void markmap.rescale(1)
}

watch(() => props.markdown, () => {
  if (props.active) void renderMarkmap()
})

watch(() => props.active, (active) => {
  renderSequence += 1
  if (fitTimer) {
    clearTimeout(fitTimer)
    fitTimer = null
  }
  if (active) void renderMarkmap()
})

onMounted(() => {
  if (stageRef.value) {
    resizeObserver = new ResizeObserver(() => {
      if (hasUsableSize()) scheduleFit()
    })
    resizeObserver.observe(stageRef.value)
    intersectionObserver = new IntersectionObserver((entries) => {
      isIntersecting.value = Boolean(entries[0]?.isIntersecting)
      if (isIntersecting.value && props.active) void renderMarkmap()
    }, { threshold: 0.01 })
    intersectionObserver.observe(stageRef.value)
  }
})

onBeforeUnmount(() => {
  renderSequence += 1
  if (fitTimer) clearTimeout(fitTimer)
  resizeObserver?.disconnect()
  resizeObserver = null
  intersectionObserver?.disconnect()
  intersectionObserver = null
  markmap?.destroy()
  markmap = null
})
</script>

<template>
  <div class="markmap-renderer">
    <div class="markmap-toolbar">
      <div>
        <strong>Markmap 交互导图</strong>
        <span>由 Markdown 知识结构实时渲染，支持缩放、拖拽和节点展开。</span>
      </div>
      <div class="toolbar-actions">
        <el-button size="small" @click="fitView">适配画布</el-button>
        <el-button size="small" @click="resetScale">重置缩放</el-button>
      </div>
    </div>
    <el-alert v-if="renderError" class="markmap-error" type="warning" :closable="false" :title="renderError" />
    <div ref="stageRef" class="markmap-stage">
      <svg ref="svgRef" class="markmap-svg" />
    </div>
  </div>
</template>

<style scoped>
.markmap-renderer {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #ffffff;
  overflow: hidden;
}

.markmap-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border-bottom: 1px solid #e2e8f0;
  background: #f8fafc;
}

.markmap-toolbar strong {
  display: block;
  color: #0f172a;
  font-size: 15px;
}

.markmap-toolbar span {
  display: block;
  margin-top: 3px;
  color: #64748b;
  font-size: 12px;
}

.toolbar-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.markmap-stage {
  width: 100%;
  height: min(68vh, 720px);
  min-height: 560px;
  background: #ffffff;
}

.markmap-error {
  margin: 12px 16px 0;
}

.markmap-svg {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
