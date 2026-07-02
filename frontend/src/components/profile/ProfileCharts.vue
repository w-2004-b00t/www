<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { StudentProfileItem } from '../../types/common'

const props = defineProps<{
  items: StudentProfileItem[]
  requiredDimensions: string[]
  completeness: number
}>()

const radarRef = ref<HTMLElement>()
const sourceRef = ref<HTMLElement>()
let radarChart: ECharts | null = null
let sourceChart: ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const sourceText: Record<StudentProfileItem['source'], string> = {
  dialog: '对话抽取',
  assessment: '测评更新',
  behavior: '行为记录',
  manual: '手动修改',
}

const confirmedItems = computed(() => props.items.filter((item) => item.status === 'confirmed'))

const confidenceRows = computed(() =>
  props.requiredDimensions.map((dimension) => {
    const item = confirmedItems.value.find((profile) => profile.dimension === dimension)
    return {
      dimension,
      value: item?.value || '待补充',
      confidence: item ? Math.round(item.confidence * 100) : 0,
      source: item ? sourceText[item.source] || item.source : '未识别',
      risk: item ? item.confidence < 0.85 : true,
    }
  }),
)

const sourceRows = computed(() => {
  const countMap = new Map<string, number>()
  confirmedItems.value.forEach((item) => {
    const label = sourceText[item.source] || item.source
    countMap.set(label, (countMap.get(label) || 0) + 1)
  })
  return Array.from(countMap.entries()).map(([name, value]) => ({ name, value }))
})

const averageConfidence = computed(() => {
  if (!confirmedItems.value.length) return 0
  const total = confirmedItems.value.reduce((sum, item) => sum + item.confidence, 0)
  return Math.round((total / confirmedItems.value.length) * 100)
})

const weakDimensions = computed(() => confidenceRows.value.filter((item) => item.risk))

function renderRadar() {
  if (!radarRef.value) return
  radarChart ||= echarts.init(radarRef.value)
  const radarRows = confidenceRows.value.length
    ? confidenceRows.value
    : [{ dimension: '待补充画像', confidence: 0 }]
  const indicators = radarRows.map((item) => ({
    name: item.dimension.replace(' / ', '\n'),
    max: 100,
  }))
  const values = radarRows.map((item) => item.confidence)
  const option: EChartsOption = {
    color: ['#2563eb'],
    tooltip: { trigger: 'item' },
    radar: {
      indicator: indicators,
      radius: '64%',
      center: ['50%', '52%'],
      splitNumber: 4,
      scale: false,
      axisName: {
        color: '#475569',
        fontSize: 12,
        lineHeight: 16,
      },
      splitLine: { lineStyle: { color: '#e2e8f0' } },
      splitArea: { areaStyle: { color: ['#ffffff', '#f8fafc'] } },
      axisLine: { lineStyle: { color: '#cbd5e1' } },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: values,
            name: '画像置信度',
            areaStyle: { color: 'rgba(37, 99, 235, 0.14)' },
            lineStyle: { width: 2 },
            symbolSize: 5,
          },
        ],
      },
    ],
  }
  radarChart.setOption(option)
}

function renderSource() {
  if (!sourceRef.value) return
  sourceChart ||= echarts.init(sourceRef.value)
  const option: EChartsOption = {
    color: ['#2563eb', '#16a34a', '#0891b2', '#d97706'],
    tooltip: { trigger: 'item' },
    legend: {
      bottom: 0,
      left: 'center',
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: '#64748b', fontSize: 12 },
    },
    series: [
      {
        name: '画像来源',
        type: 'pie',
        radius: ['48%', '72%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        label: {
          formatter: '{b}\n{c}项',
          color: '#334155',
          fontSize: 12,
        },
        labelLine: { length: 8, length2: 6 },
        data: sourceRows.value.length ? sourceRows.value : [{ name: '未识别', value: 1 }],
      },
    ],
  }
  sourceChart.setOption(option)
}

function renderCharts() {
  nextTick(() => {
    renderRadar()
    renderSource()
  })
}

function resizeCharts() {
  radarChart?.resize()
  sourceChart?.resize()
}

onMounted(() => {
  renderCharts()
  resizeObserver = new ResizeObserver(resizeCharts)
  if (radarRef.value) resizeObserver.observe(radarRef.value)
  if (sourceRef.value) resizeObserver.observe(sourceRef.value)
})

watch(() => props.items, renderCharts, { deep: true })
watch(() => props.requiredDimensions, renderCharts, { deep: true })

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  radarChart?.dispose()
  sourceChart?.dispose()
})
</script>

<template>
  <section class="profile-visual panel">
    <div class="visual-head">
      <div>
        <h2 class="section-title">画像可视化概览</h2>
        <p class="section-desc">用图表查看画像完整度、置信度、来源和需要复核的维度。</p>
      </div>
      <div class="visual-summary">
        <div>
          <strong>{{ completeness }}%</strong>
          <span>完整度</span>
        </div>
        <div>
          <strong>{{ averageConfidence }}%</strong>
          <span>平均置信度</span>
        </div>
        <div>
          <strong>{{ weakDimensions.length }}</strong>
          <span>需复核</span>
        </div>
      </div>
    </div>

    <div class="visual-grid">
      <div class="chart-card">
        <div class="chart-title">
          <strong>画像维度雷达图</strong>
          <span>越接近外圈，说明该维度越稳定</span>
        </div>
        <div ref="radarRef" class="chart-box"></div>
      </div>

      <div class="chart-card">
        <div class="chart-title">
          <strong>画像来源分布</strong>
          <span>区分对话、测评、行为和手动修改</span>
        </div>
        <div ref="sourceRef" class="chart-box"></div>
      </div>

      <div class="dimension-panel">
        <div class="chart-title">
          <strong>维度状态</strong>
          <span>低于 85% 建议再次确认</span>
        </div>
        <div class="dimension-list">
          <div v-for="item in confidenceRows" :key="item.dimension" class="dimension-row">
            <div class="dimension-meta">
              <strong>{{ item.dimension }}</strong>
              <span>{{ item.source }}</span>
            </div>
            <div class="dimension-progress">
              <el-progress
                :percentage="item.confidence"
                :stroke-width="8"
                :status="item.risk ? 'warning' : 'success'"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.profile-visual {
  margin-top: 18px;
}

.visual-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.visual-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(82px, 1fr));
  gap: 8px;
  min-width: 300px;
}

.visual-summary div {
  display: grid;
  gap: 4px;
  padding: 10px 12px;
  text-align: center;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.visual-summary strong {
  color: #2563eb;
  font-size: 22px;
}

.visual-summary span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.visual-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr) minmax(300px, 0.9fr);
  gap: 14px;
  margin-top: 16px;
}

.chart-card,
.dimension-panel {
  min-height: 330px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.chart-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.chart-title span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.chart-box {
  width: 100%;
  height: 280px;
}

.dimension-list {
  display: grid;
  gap: 10px;
  max-height: 280px;
  overflow: auto;
  padding-right: 4px;
}

.dimension-row {
  display: grid;
  grid-template-columns: 118px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
}

.dimension-meta {
  display: grid;
  gap: 3px;
}

.dimension-meta strong {
  color: var(--color-text-primary);
  font-size: 13px;
}

.dimension-meta span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.dimension-progress :deep(.el-progress__text) {
  min-width: 36px;
  color: var(--color-text-secondary);
}

@media (max-width: 1280px) {
  .visual-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .dimension-panel {
    grid-column: 1 / -1;
  }
}

@media (max-width: 760px) {
  .visual-head {
    flex-direction: column;
  }

  .visual-summary,
  .visual-grid {
    grid-template-columns: 1fr;
    width: 100%;
    min-width: 0;
  }

  .dimension-row {
    grid-template-columns: 1fr;
  }
}
</style>
