<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import { GraphChart } from 'echarts/charts'
import { LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts, type EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { KnowledgeGraphData, KnowledgeGraphNode, KnowledgeNodeType } from '../../types/knowledgeGraph'
import {
  getKnowledgeMasteryMeta,
  getKnowledgeMasteryStatus,
  knowledgeMasteryOptions,
} from '../../utils/knowledgeMastery'

use([GraphChart, LegendComponent, TitleComponent, TooltipComponent, CanvasRenderer])

const props = defineProps<{
  data: KnowledgeGraphData
  visibleTypes: KnowledgeNodeType[]
  keyword: string
  selectedNodeId?: string
}>()

const emit = defineEmits<{
  select: [node: KnowledgeGraphNode]
}>()

const containerRef = ref<HTMLElement | null>(null)
let chart: ECharts | null = null

const visibleData = computed(() => {
  const allowed = new Set(props.visibleTypes)
  const keyword = props.keyword.trim().toLowerCase()
  const directMatches = new Set(
    props.data.nodes
      .filter((node) => !keyword || `${node.name} ${node.description} ${(node.tags || []).join(' ')}`.toLowerCase().includes(keyword))
      .map((node) => node.id),
  )
  const connected = new Set(directMatches)
  if (keyword) {
    props.data.edges.forEach((edge) => {
      if (directMatches.has(edge.source)) connected.add(edge.target)
      if (directMatches.has(edge.target)) connected.add(edge.source)
    })
  }
  const nodes = props.data.nodes.filter((node) => allowed.has(node.type) && (!keyword || connected.has(node.id)))
  const nodeIds = new Set(nodes.map((node) => node.id))
  return {
    nodes,
    edges: props.data.edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target)),
  }
})

function renderGraph() {
  if (!chart) return
  const categories = knowledgeMasteryOptions.map((item) => ({
    name: item.label,
    itemStyle: { color: item.color },
    status: item.value,
  }))
  const nodes = visibleData.value.nodes.map((node) => {
    const masteryStatus = getKnowledgeMasteryStatus(node)
    const masteryMeta = getKnowledgeMasteryMeta(node)
    return {
      ...node,
      masteryStatus,
      category: knowledgeMasteryOptions.findIndex((item) => item.value === masteryStatus),
      symbolSize: node.symbolSize || (node.type === 'concept' ? 44 : 40),
      itemStyle: {
        color: masteryMeta.color,
        borderColor: node.id === props.selectedNodeId ? '#172033' : '#ffffff',
        borderWidth: node.id === props.selectedNodeId ? 4 : 2,
        shadowBlur: node.id === props.selectedNodeId ? 18 : 8,
        shadowColor: 'rgba(31, 41, 55, .16)',
      },
    }
  })
  const option: EChartsCoreOption = {
    animationDurationUpdate: 500,
    tooltip: {
      trigger: 'item',
      borderWidth: 0,
      backgroundColor: 'rgba(23, 32, 51, .94)',
      textStyle: { color: '#fff' },
      formatter: (params: unknown) => {
        const item = params as { dataType?: string; data?: KnowledgeGraphNode & { relation?: string } }
        if (item.dataType === 'edge') return item.data?.relation || '关联'
        if (!item.data) return ''
        const masteryMeta = getKnowledgeMasteryMeta(item.data)
        return [
          `<strong>${item.data.name || ''}</strong>`,
          `${masteryMeta.label} · ${item.data.mastery || 0}%`,
          item.data.description || '点击查看节点详情',
        ].join('<br/>')
      },
    },
    legend: {
      top: 14,
      left: 18,
      data: categories.map((item) => item.name),
      textStyle: { color: '#657187' },
    },
    series: [{
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: true,
      data: nodes,
      links: visibleData.value.edges.map((edge) => ({
        ...edge,
        value: edge.relation,
        symbol: edge.direction === 'undirected' ? ['none', 'none'] : ['none', 'arrow'],
        symbolSize: 8,
      })),
      categories,
      force: { repulsion: 320, gravity: 0.08, edgeLength: [90, 165], friction: 0.62 },
      label: { show: true, position: 'right', color: '#26334d', fontSize: 12, formatter: '{b}' },
      edgeLabel: { show: true, color: '#8490a5', fontSize: 10, formatter: (params: unknown) => (params as { data?: { relation?: string } }).data?.relation || '' },
      lineStyle: { color: 'source', opacity: 0.55, width: 1.5, curveness: 0.08 },
      emphasis: { focus: 'adjacency', lineStyle: { width: 3, opacity: 0.9 } },
    }],
  }
  chart.setOption(option, true)
}

function resetView() {
  chart?.dispatchAction({ type: 'restore' })
  chart?.resize()
}

defineExpose({ resetView })

onMounted(() => {
  if (!containerRef.value) return
  chart = init(containerRef.value)
  chart.on('click', (params: unknown) => {
    const item = params as { dataType?: string; data?: KnowledgeGraphNode }
    if (item.dataType === 'node' && item.data?.id) emit('select', item.data)
  })
  renderGraph()
})

useResizeObserver(containerRef, () => chart?.resize())
watch(() => [props.data, props.visibleTypes, props.keyword, props.selectedNodeId], () => void nextTick(renderGraph), { deep: true })
onBeforeUnmount(() => {
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="containerRef" class="graph-canvas" aria-label="课程知识图谱画布" />
</template>

<style scoped>
.graph-canvas {
  width: 100%;
  height: 100%;
  min-height: 620px;
}
</style>
