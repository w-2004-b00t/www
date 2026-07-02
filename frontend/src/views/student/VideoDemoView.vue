<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Clipboard, Download, RefreshCw, Wand2 } from 'lucide-vue-next'
import { API_BASE_URL } from '../../api/client'
import {
  generateResourceVideoDemoApi,
  getResourceVideoDemoApi,
  getResourceVideoDemoStatusApi,
  retryResourceVideoDemoApi,
} from '../../api/resource'
import MarkdownViewer from '../../components/resource/MarkdownViewer.vue'
import { useResourceStore } from '../../stores/resource'
import type { VideoDemoJob, VideoDemoJobStatus, VideoDemoPayload, VideoRenderMode } from '../../types/common'

const resource = useResourceStore()
const loading = ref(false)
const generating = ref(false)
const evidenceOpen = ref(false)
const logsOpen = ref(false)
const errorMessage = ref('')
const videoPayload = ref<VideoDemoPayload | null>(null)
const currentJob = ref<VideoDemoJob | null>(null)
const selectedRenderMode = ref<VideoRenderMode>('animated_lesson')
let pollTimer: number | undefined
let pollDelayMs = 3000
let pollInFlight = false

const renderModeOptions: Array<{ label: string; value: VideoRenderMode; desc: string }> = [
  { label: '教学动画', value: 'animated_lesson', desc: '稳定生成完整讲解' },
  { label: '快速片段', value: 'agnes_clip', desc: '预览动态素材' },
  { label: '正式成片', value: 'full_hybrid', desc: '动态素材 + 教学动画' },
]

const videoResource = computed(() => resource.resources.find((item) => item.resourceType === 'video_script'))
const videoResourceId = computed(() => videoResource.value?.id || '')
const hasVideoResource = computed(() => Boolean(videoResourceId.value))
const personalization = computed(() => videoPayload.value?.personalizationEvidence)
const effectiveStatus = computed<VideoDemoJobStatus>(() => {
  if (currentJob.value?.status === 'completed' && currentJob.value.schemaVersion !== 'knowledge_video_v2') return 'idle'
  return normalizeStatus(currentJob.value?.status || videoPayload.value?.videoStatus || 'idle')
})
const isWorking = computed(() => [
  'storyboard_generating',
  'submitting',
  'queued',
  'rendering',
  'retry_wait',
  'downloading',
  'validating',
  'composing',
  'verifying',
].includes(effectiveStatus.value))
const hasVerifiedVideo = computed(() => Boolean(
  currentJob.value?.status === 'completed'
  && currentJob.value?.schemaVersion === 'knowledge_video_v2'
  && (currentJob.value?.videoUrl || videoPayload.value?.videoUrl),
))
const videoSourceUrl = computed(() => (hasVerifiedVideo.value ? absoluteMediaUrl(currentJob.value?.videoUrl || videoPayload.value?.videoUrl || '') : ''))
const compositionWarning = computed(() => currentJob.value?.compositionWarning || '')
const renderLogTail = computed(() => currentJob.value?.renderLogTail || [])
const generateButtonText = computed(() => {
  if (selectedRenderMode.value === 'full_hybrid') return '生成正式成片'
  if (selectedRenderMode.value === 'agnes_clip') return '生成快速片段'
  return '生成教学动画'
})
const renderModeLabel = computed(() => {
  if (currentJob.value && !currentJob.value.renderMode) return '正式成片（旧任务）'
  const mode = currentJob.value?.renderMode || selectedRenderMode.value
  return renderModeOptions.find((item) => item.value === mode)?.label || mode
})
const renderProfileText = computed(() => {
  const profile = currentJob.value?.renderProfile
  if (!profile) return ''
  return `${profile.width}x${profile.height} / ${profile.fps}fps / ${profile.durationSeconds}s`
})
const segmentRows = computed(() => {
  const progress = currentJob.value?.segmentProgress || {}
  return [
    { key: 'agnes', label: '动态素材', value: progress.agnes ?? 0 },
    { key: 'validation', label: '下载校验', value: progress.validation ?? 0 },
    { key: 'composition', label: '教学动画合成', value: progress.composition ?? 0 },
  ]
})
const generationSource = computed(() => {
  const status = currentJob.value?.llmStatus || videoPayload.value?.llmStatus
  const mode = currentJob.value?.generationMode || videoPayload.value?.generationMode || ''
  if (status?.fallback || mode.includes('blocked_static')) return '已阻断静态模板'
  if (currentJob.value?.renderMode === 'animated_lesson') return 'DeepSeek 教学镜头 + 本地动画'
  if (mode.includes('agnes')) return 'DeepSeek 教学镜头 + 动态素材'
  if (status?.usedLLM || mode.includes('deepseek')) return 'DeepSeek 教学镜头'
  return '等待生成'
})
const generationSourceType = computed<'success' | 'warning' | 'info'>(() => {
  if (generationSource.value === '已阻断静态模板') return 'warning'
  if (generationSource.value.includes('DeepSeek')) return 'success'
  return 'info'
})
const statusMeta = computed(() => {
  const map: Record<VideoDemoJobStatus, { label: string; desc: string; type: 'info' | 'success' | 'warning' | 'danger' }> = {
    idle: { label: '未生成', desc: '点击生成后会基于真实课程引用生成知识点教学 MP4，画面包含结构图、操作过程、复杂度和练习。', type: 'info' },
    storyboard_generating: { label: '生成内容', desc: currentJob.value?.stageMessage || 'DeepSeek 正在生成教学镜头数据。', type: 'warning' },
    submitting: { label: '提交中', desc: currentJob.value?.stageMessage || '正在提交动态素材任务。', type: 'warning' },
    queued: { label: '排队中', desc: currentJob.value?.stageMessage || '视频任务已创建，等待生成。', type: 'warning' },
    rendering: { label: '生成中', desc: currentJob.value?.stageMessage || `正在使用${generationSource.value}生成知识点教学视频。`, type: 'warning' },
    retry_wait: { label: '自动重试', desc: currentJob.value?.stageMessage || '临时网络错误，系统会自动重试当前阶段。', type: 'warning' },
    downloading: { label: '下载片段', desc: currentJob.value?.stageMessage || '正在可靠下载关键动态素材。', type: 'warning' },
    validating: { label: '媒体校验', desc: currentJob.value?.stageMessage || '正在使用 FFprobe 校验视频文件。', type: 'warning' },
    composing: { label: '合成成片', desc: currentJob.value?.stageMessage || '本地动画与 FFmpeg 正在合成 3 分钟教学视频。', type: 'warning' },
    verifying: { label: '转存中', desc: currentJob.value?.stageMessage || '视频已生成，正在转存为本地 MP4。', type: 'warning' },
    completed: { label: '已完成', desc: currentJob.value?.compositionWarning || `本次知识点教学 MP4 已由${generationSource.value}生成并保存到本地媒体库。`, type: currentJob.value?.compositionWarning ? 'warning' : 'success' },
    failed: { label: '生成失败', desc: currentJob.value?.errorDetail || currentJob.value?.error || videoPayload.value?.videoError || '视频生成失败，未产出可播放 MP4。', type: 'danger' },
    cancelled: { label: '已取消', desc: currentJob.value?.stageMessage || '视频生成任务已取消。', type: 'info' },
    orphaned: { label: '待恢复', desc: currentJob.value?.stageMessage || '任务执行中断，等待后台恢复。', type: 'warning' },
  }
  return map[effectiveStatus.value]
})

function absoluteMediaUrl(url: string) {
  if (!url) return ''
  if (/^https?:\/\//i.test(url)) return url
  return `${new URL(API_BASE_URL).origin}${url}`
}

function normalizeStatus(status: string): VideoDemoJobStatus {
  if (status === 'running') return 'rendering'
  if ([
    'idle', 'storyboard_generating', 'submitting', 'queued', 'rendering', 'retry_wait',
    'downloading', 'validating', 'composing', 'verifying', 'completed', 'failed',
    'cancelled', 'orphaned',
  ].includes(status)) {
    return status as VideoDemoJobStatus
  }
  return 'idle'
}

function stopPolling() {
  if (pollTimer) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

async function loadVideoDemo() {
  loading.value = true
  errorMessage.value = ''
  try {
    await resource.loadAll(true)
    if (!hasVideoResource.value) {
      videoPayload.value = null
      currentJob.value = null
      throw new Error('当前账号还没有视频演示资源，请先在资源生成页生成“视频演示”。')
    }
    videoPayload.value = await getResourceVideoDemoApi(videoResourceId.value)
    currentJob.value = videoPayload.value.currentAttempt || videoPayload.value.videoJob || null
    if (currentJob.value && isWorking.value) startPolling(currentJob.value.jobId)
  } catch (error) {
    videoPayload.value = null
    currentJob.value = null
    errorMessage.value = error instanceof Error ? error.message : '视频状态读取失败'
  } finally {
    loading.value = false
  }
}

async function generateVideo() {
  if (!hasVideoResource.value) {
    errorMessage.value = '当前账号还没有视频演示资源，请先在资源生成页生成“视频演示”。'
    return
  }
  generating.value = true
  errorMessage.value = ''
  stopPolling()
  try {
    currentJob.value = await generateResourceVideoDemoApi(videoResourceId.value, { mode: selectedRenderMode.value })
    videoPayload.value = null
    startPolling(currentJob.value.jobId)
  } catch (error) {
    const message = error instanceof Error ? error.message : '知识点教学 MP4 生成启动失败'
    await loadVideoDemo()
    errorMessage.value = message
  } finally {
    generating.value = false
  }
}

function startPolling(jobId: string) {
  stopPolling()
  pollDelayMs = 3000
  void pollJob(jobId)
}

function schedulePoll(jobId: string) {
  stopPolling()
  pollTimer = window.setTimeout(() => {
    void pollJob(jobId)
  }, pollDelayMs)
  pollDelayMs = Math.min(10000, Math.round(pollDelayMs * 1.35))
}

async function copyFailure() {
  const text = currentJob.value?.errorDetail || currentJob.value?.error || errorMessage.value
  if (!text) return
  await navigator.clipboard?.writeText(text)
}

async function pollJob(jobId: string) {
  if (!videoResourceId.value || pollInFlight) return
  pollInFlight = true
  try {
    currentJob.value = await getResourceVideoDemoStatusApi(videoResourceId.value, jobId)
    if (['completed', 'failed', 'cancelled', 'orphaned'].includes(currentJob.value.status)) {
      stopPolling()
      videoPayload.value = await getResourceVideoDemoApi(videoResourceId.value)
      currentJob.value = videoPayload.value.currentAttempt || videoPayload.value.videoJob || currentJob.value
    } else {
      schedulePoll(jobId)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '视频生成状态轮询失败'
    schedulePoll(jobId)
  } finally {
    pollInFlight = false
  }
}

async function retryCurrentStage() {
  if (!videoResourceId.value || !currentJob.value) return
  generating.value = true
  errorMessage.value = ''
  try {
    currentJob.value = await retryResourceVideoDemoApi(videoResourceId.value, currentJob.value.jobId)
    startPolling(currentJob.value.jobId)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '当前阶段重试失败'
  } finally {
    generating.value = false
  }
}

onMounted(loadVideoDemo)
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="page video-page">
    <div class="page-header">
      <div>
        <span class="status-pill">教学视频生成</span>
        <h1 class="page-title">{{ videoPayload?.title || '课程资料待上传 3 分钟教学视频' }}</h1>
        <p class="page-subtitle">
          真实课程引用驱动 DeepSeek 输出教学镜头数据，系统生成结构图、操作过程、复杂度和练习题视频。
        </p>
      </div>
      <div class="head-actions">
        <div class="mode-picker" role="group" aria-label="视频生成模式">
          <button
            v-for="option in renderModeOptions"
            :key="option.value"
            type="button"
            :class="{ active: selectedRenderMode === option.value }"
            :disabled="isWorking || generating"
            @click="selectedRenderMode = option.value"
          >
            <strong>{{ option.label }}</strong>
            <span>{{ option.desc }}</span>
          </button>
        </div>
        <el-button :icon="RefreshCw" :loading="loading" @click="loadVideoDemo">刷新状态</el-button>
        <el-button type="primary" :icon="Wand2" :loading="generating" :disabled="!hasVideoResource || isWorking" @click="generateVideo">
          {{ generateButtonText }}
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="errorMessage"
      class="state-alert"
      type="error"
      show-icon
      :closable="false"
      title="无法生成知识点教学视频"
      :description="errorMessage"
    />

    <section class="panel status-panel">
      <div class="status-copy">
        <el-tag :type="statusMeta.type" effect="plain">{{ statusMeta.label }}</el-tag>
        <div>
          <strong>{{ statusMeta.desc }}</strong>
          <span>生成来源：{{ generationSource }}</span>
          <span v-if="currentJob?.providerTaskId">任务 ID：{{ currentJob.providerTaskId }}</span>
          <span v-if="currentJob?.providerVideoId">视频 ID：{{ currentJob.providerVideoId }}</span>
          <span v-if="currentJob?.progress !== undefined">进度：{{ currentJob.progress }}%</span>
          <span>渲染模式：{{ renderModeLabel }}</span>
          <span v-if="renderProfileText">渲染规格：{{ renderProfileText }}</span>
          <span v-if="currentJob?.compositionStage">合成阶段：{{ currentJob.compositionStage }}</span>
          <span v-if="currentJob?.lastHeartbeatAt">最后心跳：{{ currentJob.lastHeartbeatAt }}</span>
          <span v-if="currentJob?.retryCount">自动重试：{{ currentJob.retryCount }} 次</span>
          <span v-if="currentJob?.nextRetryAt && effectiveStatus === 'retry_wait'">下次重试：{{ currentJob.nextRetryAt }}</span>
          <span v-if="currentJob?.startedAt">开始：{{ currentJob.startedAt }}</span>
          <span v-if="currentJob?.finishedAt">结束：{{ currentJob.finishedAt }}</span>
          <div v-if="effectiveStatus === 'failed' || effectiveStatus === 'orphaned'" class="failure-actions">
            <el-button size="small" :icon="Clipboard" @click="copyFailure">复制错误</el-button>
            <el-button
              size="small"
              type="primary"
              :icon="Wand2"
              :loading="generating"
              :disabled="!hasVideoResource"
              @click="currentJob?.retryable ? retryCurrentStage() : generateVideo()"
            >
              {{ currentJob?.retryable ? '重试当前阶段' : '重新生成' }}
            </el-button>
          </div>
        </div>
      </div>
      <div v-if="isWorking" class="progress-line">
        <div :style="{ width: `${currentJob?.progress || 12}%` }" />
      </div>
      <div v-if="currentJob?.segmentProgress" class="segment-progress">
        <article v-for="segment in segmentRows" :key="segment.key">
          <span>{{ segment.label }}</span>
          <strong>{{ segment.value }}%</strong>
          <div><i :style="{ width: `${segment.value}%` }" /></div>
        </article>
      </div>
      <button v-if="renderLogTail.length" class="log-trigger" type="button" @click="logsOpen = !logsOpen">
        {{ logsOpen ? '收起日志' : '查看渲染日志' }}
      </button>
      <pre v-if="logsOpen && renderLogTail.length" class="render-log">{{ renderLogTail.join('\n') }}</pre>
    </section>

    <section v-if="!loading" class="panel video-player-panel">
      <div class="panel-head">
        <div>
          <h2 class="section-title">知识点教学成片</h2>
          <p class="section-desc">主流程只播放已校验并转存到本地媒体库的 MP4；视频主体必须包含知识讲解、动态图示、例题操作和字幕。</p>
        </div>
        <div class="panel-tags">
          <el-tag :type="generationSourceType" effect="plain">{{ generationSource }}</el-tag>
          <el-tag effect="plain">{{ renderModeLabel }}</el-tag>
        </div>
      </div>

      <el-alert
        v-if="compositionWarning"
        class="composition-warning"
        type="warning"
        show-icon
        :closable="false"
        title="已生成可播放片段"
        :description="compositionWarning"
      />

      <div v-if="videoSourceUrl" class="video-shell">
        <video class="real-video" :src="videoSourceUrl" controls preload="metadata" playsinline />
        <div class="video-caption">
          <strong>{{ videoPayload?.videoRenderer || '知识点教学视频，本地媒体转存 MP4' }}</strong>
          <span>
            视觉质量：{{ currentJob?.visualQuality || 'animated_lesson' }}；
            制作痕迹评分：{{ currentJob?.storyboardLeakageScore ?? 0 }}；
            MP4 输出：{{ currentJob?.downloadedAt ? '已转存' : '待转存' }}；
            远端任务：{{ currentJob?.providerTaskId || '待创建' }}。
          </span>
          <a :href="videoSourceUrl" download>
            <el-button :icon="Download">下载 MP4</el-button>
          </a>
        </div>
      </div>

      <div v-else class="empty-state">
        <h3>{{ effectiveStatus === 'failed' ? '本次未生成新 MP4' : '还没有本次知识点教学 MP4' }}</h3>
        <p v-if="effectiveStatus === 'failed'">
          当前生成尝试失败，主播放器不会继续播放旧结果；请修复错误后重新生成。
        </p>
        <p v-else-if="hasVideoResource">选择生成模式后开始渲染；推荐先生成完整教学动画，正式版可叠加动态素材。</p>
        <p v-else>当前账号还没有后端视频演示资源，请先在资源生成页生成“视频演示”。</p>
        <el-button type="primary" :icon="Wand2" :loading="generating" :disabled="!hasVideoResource || isWorking" @click="generateVideo">
          {{ generateButtonText }}
        </el-button>
      </div>
    </section>

    <section v-if="videoPayload" class="panel evidence-panel">
      <button class="evidence-trigger" type="button" @click="evidenceOpen = !evidenceOpen">
        <span>
          <strong>生成依据</strong>
          <small>查看 DeepSeek 教学内容、课程引用、画像依据和视频生成任务状态；主播放器不展示制作脚本。</small>
        </span>
        <el-tag effect="plain">{{ evidenceOpen ? '收起' : '展开' }}</el-tag>
      </button>

      <div v-if="evidenceOpen" class="evidence-content">
        <div class="evidence-grid">
          <article>
            <span>当前路径阶段</span>
            <strong>{{ personalization?.activeStage || '未进入路径' }}</strong>
          </article>
          <article>
            <span>最近测评</span>
            <strong>{{ personalization?.latestScore ?? '暂无' }}</strong>
          </article>
          <article>
            <span>生成状态</span>
            <strong>{{ effectiveStatus }}</strong>
          </article>
          <article>
            <span>素材来源</span>
            <strong>{{ generationSource }}</strong>
          </article>
          <article>
            <span>生成模式</span>
            <strong>{{ renderModeLabel }} · {{ currentJob?.generationMode || videoPayload.generationMode }}</strong>
          </article>
          <article v-if="renderProfileText">
            <span>渲染规格</span>
            <strong>{{ renderProfileText }}</strong>
          </article>
          <article>
            <span>视频来源</span>
            <strong>{{ currentJob?.provider || videoPayload.videoProvider || '本地教学动画' }}</strong>
          </article>
          <article>
            <span>视觉质量</span>
            <strong>{{ currentJob?.visualQuality || '待生成' }}</strong>
          </article>
          <article>
            <span>制作痕迹评分</span>
            <strong>{{ currentJob?.storyboardLeakageScore ?? 0 }}</strong>
          </article>
        </div>

        <div class="storyboard">
          <h2 class="section-title">教学时间轴</h2>
          <div class="timeline">
            <article v-for="scene in videoPayload.scenes" :key="scene.id">
              <span>{{ scene.timeRange }}</span>
              <strong>{{ scene.title }}</strong>
              <p>{{ scene.coreExplanation || scene.screenText }}</p>
            </article>
          </div>
        </div>

        <div class="trace-grid">
          <article>
            <h2 class="section-title">多模态创作 Agent</h2>
            <ul>
              <li v-for="item in videoPayload.agentTrace" :key="item">{{ item }}</li>
            </ul>
          </article>
          <article>
            <h2 class="section-title">课程引用</h2>
            <ul>
              <li v-for="citation in videoPayload.citations" :key="citation.chunkId">
                {{ citation.documentName }} · {{ citation.sourceLocation }} · 第 {{ citation.page }} 页
              </li>
            </ul>
          </article>
        </div>

        <div class="script-card">
          <h2 class="section-title">讲稿补充材料</h2>
          <MarkdownViewer :content="videoPayload.script" />
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.video-page {
  max-width: 1560px;
  padding: 28px 34px 40px;
}

.page-header {
  align-items: center;
}

.head-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: flex-end;
}

.mode-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.mode-picker button {
  min-width: 132px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 8px 10px;
  color: #334155;
  background: #fff;
  text-align: left;
  cursor: pointer;
}

.mode-picker button.active {
  border-color: #2563eb;
  color: #1d4ed8;
  background: #eff6ff;
}

.mode-picker button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.mode-picker strong,
.mode-picker span {
  display: block;
}

.mode-picker span {
  margin-top: 2px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.state-alert,
.status-panel,
.video-player-panel,
.evidence-panel {
  margin-top: 16px;
}

.status-panel {
  display: grid;
  gap: 14px;
  border-color: #bfdbfe;
  background: #eff6ff;
}

.status-copy {
  display: flex;
  align-items: center;
  gap: 14px;
}

.status-copy strong,
.status-copy span {
  display: block;
}

.status-copy span,
.section-desc,
.video-caption span,
.evidence-trigger small,
.evidence-grid span,
.timeline span,
.timeline p {
  color: var(--color-text-secondary);
}

.progress-line {
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: #dbeafe;
}

.progress-line div {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #2563eb, #38bdf8);
  transition: width 0.25s ease;
}

.segment-progress {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.segment-progress article {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.segment-progress span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.segment-progress strong {
  color: #0f172a;
}

.segment-progress div {
  height: 6px;
  overflow: hidden;
  border-radius: 999px;
  background: #dbeafe;
}

.segment-progress i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: #2563eb;
  transition: width 0.25s ease;
}

.panel-head,
.video-caption,
.evidence-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.panel-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.video-shell {
  margin-top: 12px;
  overflow: hidden;
  border: 1px solid #dbeafe;
  border-radius: 8px;
  background: #0f172a;
}

.composition-warning {
  margin-top: 12px;
}

.real-video {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #020617;
}

.video-caption {
  padding: 12px 14px;
  background: #fff;
}

.video-caption strong,
.video-caption span {
  display: block;
}

.empty-state {
  display: grid;
  justify-items: center;
  gap: 10px;
  padding: 40px 16px;
  color: var(--color-text-secondary);
  text-align: center;
}

.failure-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.log-trigger {
  width: fit-content;
  border: 0;
  padding: 0;
  color: #2563eb;
  font-weight: 700;
  background: transparent;
  cursor: pointer;
}

.render-log {
  max-height: 220px;
  overflow: auto;
  margin: 0;
  padding: 12px;
  border-radius: 8px;
  color: #e2e8f0;
  background: #0f172a;
  white-space: pre-wrap;
}

.evidence-trigger {
  width: 100%;
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.evidence-trigger strong,
.evidence-trigger small {
  display: block;
}

.evidence-content {
  display: grid;
  gap: 18px;
  margin-top: 18px;
}

.evidence-grid,
.trace-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.trace-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.evidence-grid article,
.trace-grid article,
.timeline article,
.script-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 14px;
  background: #f8fafc;
}

.evidence-grid strong,
.timeline strong {
  display: block;
  margin-top: 6px;
}

.timeline {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}

.trace-grid ul {
  margin: 10px 0 0;
  padding-left: 18px;
}

@media (max-width: 920px) {
  .page-header,
  .panel-head,
  .video-caption,
  .status-copy {
    align-items: flex-start;
    flex-direction: column;
  }

  .evidence-grid,
  .trace-grid,
  .timeline,
  .segment-progress {
    grid-template-columns: 1fr;
  }
}
</style>
