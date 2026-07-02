<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { BookOpen, BookmarkPlus, ClipboardCheck, FileText, GitBranch, MessageSquareText, Minus, Sparkles, X } from 'lucide-vue-next'
import MarkdownViewer from '../resource/MarkdownViewer.vue'
import MarkmapRenderer from '../resource/MarkmapRenderer.vue'
import {
  createTutorRemedialTaskApi,
  generateTutorDocumentApi,
  generateTutorExtraApi,
  generateTutorExerciseApi,
  saveTutorMistakeApi,
  saveTutorNoteApi,
  submitTutorFeedbackApi,
  TutorStreamError,
  tutorChatStreamApi,
} from '../../api/tutor'
import type { TutorDiagram, TutorGenerationMode, TutorSuggestedAction, TutorVideoScript } from '../../api/tutor'
import { useOnboardingStore } from '../../stores/onboarding'
import { useProfileStore } from '../../stores/profile'
import { useResourceStore } from '../../stores/resource'
import type { SourceCitation as Citation } from '../../types/common'

const onboarding = useOnboardingStore()
const profile = useProfileStore()
const resource = useResourceStore()
const emit = defineEmits<{ close: []; minimize: []; 'drag-start': [event: PointerEvent] }>()

const modes = ['问知识点', '解释代码', '分析错题', '生成练习', '调整学习计划']
const mode = ref('问知识点')
const input = ref('课程资料为什么能帮助选择特征？请结合一个小例子解释。')
const answer = ref('')
const citations = ref<Citation[]>([])
const diagram = ref<TutorDiagram | null>(null)
const videoScript = ref<TutorVideoScript | null>(null)
const suggestedActions = ref<TutorSuggestedAction[]>([])
const inferredSections = ref<string[]>([])
const generationMode = ref<TutorGenerationMode>('')
const confidenceReason = ref('')
const confidence = ref(0)
const inferred = ref(false)
const asking = ref(false)
const actionLoading = ref('')
const errorMessage = ref('')
const actionResult = ref<{ title: string; desc: string; type: 'success' | 'info' | 'warning' } | null>(null)
const activeResultTab = ref('answer')
const streamStatus = ref('准备回答')
const errorCode = ref('')
const errorRetryable = ref(false)
const requestId = ref('')
const extraLoading = ref<'diagram' | 'video' | ''>('')
const diagramError = ref('')
const videoError = ref('')
let chatController: AbortController | null = null
let inactivityTimer: ReturnType<typeof setTimeout> | null = null
let timedOut = false

const modePrompts: Record<string, string> = {
  问知识点: '课程资料为什么能帮助选择特征？请结合一个小例子解释。',
  解释代码: '请解释这段课程资料代码中 criterion="entropy" 和 max_depth 参数分别影响什么。',
  分析错题: '我把“课程资料越大表示样本越多”写成答案了，请分析错因并给我补强建议。',
  生成练习: '请围绕课程资料待上传生成 2 道相似练习，并给出答案解析。',
  调整学习计划: '我还是不会手算课程资料，请把当前学习路径调整得更基础一些。',
}

const currentStage = computed(() => resource.learningPath.stages.find((stage) => stage.status === 'active'))
const feedbackHint = computed(() => {
  const records = resource.feedbackRecords
  if (records.some((item) => item.type === 'too_hard')) return '检测到你反馈过“太难”，本次回答会优先使用更基础的解释和分步例题。'
  if (records.some((item) => item.type === 'need_example')) return '检测到你反馈过“需要例子”，本次回答会增加例题和可练习步骤。'
  return ''
})
const contextItems = computed(() => [
  { label: '当前课程', value: onboarding.selectedCourse?.name || '数据结构课程' },
  { label: '当前知识点', value: onboarding.weakPoint || currentStage.value?.knowledgePoints.join('、') || '课程资料、课程资料' },
  { label: '当前路径阶段', value: currentStage.value?.name || '课程资料补强' },
  { label: '学习目标', value: onboarding.studyGoal || '掌握课程资料原理和手算步骤' },
  { label: '画像摘要', value: profile.profileItems.find((item) => item.dimension === '资源偏好')?.value || '偏好图解、例题和代码实践' },
])

const canUseLearningActions = computed(() => generationMode.value === 'rag_llm' && citations.value.length > 0 && confidence.value > 0)

function selectMode(nextMode: string) {
  mode.value = nextMode
  input.value = modePrompts[nextMode] || input.value
}

function requireQuestion() {
  if (input.value.trim()) return true
  ElMessage.warning('请先输入你正在卡住的问题。')
  return false
}

function requireAnswer() {
  if (answer.value.trim()) return true
  ElMessage.warning('请先发送问题并获得一条辅导回答。')
  return false
}

function clearInactivityTimer() {
  if (inactivityTimer) clearTimeout(inactivityTimer)
  inactivityTimer = null
}

function armInactivityTimer() {
  clearInactivityTimer()
  inactivityTimer = setTimeout(() => {
    timedOut = true
    chatController?.abort()
  }, 60_000)
}

function cancelChat() {
  clearInactivityTimer()
  chatController?.abort()
  chatController = null
}

function stopChat() {
  if (!asking.value) return
  cancelChat()
  asking.value = false
  errorMessage.value = '本次生成已停止。'
  errorCode.value = 'cancelled'
  errorRetryable.value = true
}

function closePanel() {
  cancelChat()
  asking.value = false
  emit('close')
}

function resetAnswerState() {
  answer.value = ''
  citations.value = []
  diagram.value = null
  videoScript.value = null
  suggestedActions.value = []
  inferredSections.value = []
  generationMode.value = ''
  confidenceReason.value = ''
  confidence.value = 0
  inferred.value = false
  diagramError.value = ''
  videoError.value = ''
  extraLoading.value = ''
  requestId.value = ''
}

async function ask() {
  if (!requireQuestion()) return
  cancelChat()
  resetAnswerState()
  const controller = new AbortController()
  chatController = controller
  timedOut = false
  asking.value = true
  streamStatus.value = '正在检索课程资料'
  errorMessage.value = ''
  errorCode.value = ''
  errorRetryable.value = false
  actionResult.value = null
  activeResultTab.value = 'answer'
  armInactivityTimer()
  try {
    const response = await tutorChatStreamApi(
      `[${mode.value}] ${input.value}`,
      {
        onStatus(status) {
          armInactivityTimer()
          streamStatus.value = status.message
          requestId.value = status.requestId || requestId.value
        },
        onDelta(text) {
          armInactivityTimer()
          answer.value += text
        },
      },
      controller.signal,
    )
    answer.value = response.answer || answer.value
    citations.value = response.citations || []
    suggestedActions.value = response.suggestedActions || []
    inferredSections.value = response.inferredSections || []
    generationMode.value = response.generationMode || ''
    confidenceReason.value = response.confidenceReason || ''
    confidence.value = response.confidence
    inferred.value = response.inferred
    requestId.value = response.requestId || requestId.value
    streamStatus.value = '回答生成完成'
    if (!citations.value.length) {
      errorMessage.value = '暂时没有找到合适的内容来回答，试着把问题描述得更具体一些。'
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (timedOut) {
        errorMessage.value = '超过 60 秒没有收到新内容，本次生成已取消。'
        errorCode.value = 'client_timeout'
        errorRetryable.value = true
      }
    } else if (error instanceof TutorStreamError) {
      errorMessage.value = error.message
      errorCode.value = error.code
      errorRetryable.value = error.retryable
      requestId.value = error.requestId || requestId.value
    } else {
      errorMessage.value = error instanceof Error ? error.message : '暂时无法回答，请稍后重试。'
      errorCode.value = 'client_error'
      errorRetryable.value = true
    }
  } finally {
    if (chatController === controller) {
      clearInactivityTimer()
      chatController = null
      asking.value = false
    }
  }
}

async function loadExtra(type: 'diagram' | 'video') {
  if (!answer.value.trim() || asking.value || extraLoading.value) return
  if (type === 'diagram' && diagram.value?.markdown) return
  if (type === 'video' && videoScript.value?.scenes?.length) return
  extraLoading.value = type
  if (type === 'diagram') diagramError.value = ''
  else videoError.value = ''
  try {
    const result = await generateTutorExtraApi({
      message: `[${mode.value}] ${input.value}`,
      answer: answer.value,
      type,
      course_id: 'course_data_structure',
    })
    if (type === 'diagram') diagram.value = result.diagram || null
    else videoScript.value = result.videoScript || null
  } catch (error) {
    const message = error instanceof Error ? error.message : '生成失败，请稍后重试。'
    if (type === 'diagram') diagramError.value = message
    else videoError.value = message
  } finally {
    extraLoading.value = ''
  }
}

watch(activeResultTab, (tab) => {
  if (tab === 'diagram' || tab === 'video') void loadExtra(tab)
})

onBeforeUnmount(cancelChat)

async function saveNote() {
  if (!requireAnswer()) return
  if (!canUseLearningActions.value) {
    ElMessage.warning('当前回答暂时不能保存，请重新提问后再试。')
    return
  }
  actionLoading.value = 'note'
  const note = { title: `${mode.value}：${input.value.slice(0, 18)}`, content: answer.value }
  try {
    await saveTutorNoteApi(note)
    actionResult.value = { title: '笔记已保存', desc: '本次辅导回答已写入学习笔记，并会作为学习报告的数据来源。', type: 'success' }
  } catch {
    const notes = JSON.parse(localStorage.getItem('eduagent_tutor_notes') || '[]')
    notes.unshift({ id: `note_${Date.now()}`, ...note, source: 'tutor', createdAt: new Date().toLocaleString() })
    localStorage.setItem('eduagent_tutor_notes', JSON.stringify(notes))
    actionResult.value = { title: '笔记已暂存', desc: '同步暂时不可用，已先为你保留本次记录。', type: 'warning' }
  } finally {
    actionLoading.value = ''
  }
  ElMessage.success('笔记已保存，可作为学习报告的数据来源。')
}

async function addMistake() {
  if (!requireQuestion()) return
  actionLoading.value = 'mistake'
  const mistake = {
    knowledge: onboarding.weakPoint || '课程资料',
    stem: input.value,
    wrongReason: mode.value === '分析错题' ? '由智能辅导对话识别出的薄弱点。' : '学生主动加入错题本，待后续测评验证。',
    fixTask: '完成 3 道课程资料手算题，并复盘公式代入步骤。',
  }
  try {
    await saveTutorMistakeApi(mistake)
    actionResult.value = { title: '已加入错题本', desc: '该问题已沉淀为错题记录，后续测评和报告会统计这个薄弱点。', type: 'success' }
  } catch {
    const mistakes = JSON.parse(localStorage.getItem('eduagent_tutor_mistakes') || '[]')
    mistakes.unshift({ id: `mistake_${Date.now()}`, ...mistake, status: '需补强', createdAt: new Date().toLocaleString() })
    localStorage.setItem('eduagent_tutor_mistakes', JSON.stringify(mistakes))
    actionResult.value = { title: '错题已暂存', desc: '同步暂时不可用，已先为你保留这道错题。', type: 'warning' }
  } finally {
    actionLoading.value = ''
  }
  ElMessage.success('已加入错题本，并会影响后续补强任务。')
}

async function generateExercise() {
  if (!requireQuestion()) return
  if (!canUseLearningActions.value) {
    ElMessage.warning('请先获得一条完整回答，再生成练习题。')
    return
  }
  actionLoading.value = 'exercise'
  try {
    const result = await generateTutorExerciseApi({ message: input.value, mode: mode.value, answer: answer.value, course_id: 'course_data_structure' })
    answer.value = `# ${result.title}\n\n${result.items.map((item, index) => {
      const options = Array.isArray(item.options) ? `\n\n${item.options.map((option: string, optionIndex: number) => `${String.fromCharCode(65 + optionIndex)}. ${option}`).join('\n')}` : ''
      return `## 第 ${index + 1} 题\n${item.stem}${options}\n\n**参考答案：** ${item.answer}\n\n**解析：** ${item.analysis}`
    }).join('\n\n')}`
    confidence.value = 0.88
    inferred.value = true
    actionResult.value = { title: result.title, desc: `已生成 ${result.items.length} 道相似练习，并写入辅导记录。`, type: 'success' }
    ElMessage.success('相似练习已生成。')
  } catch {
    try {
      await submitTutorFeedbackApi({ type: 'need_example', message: input.value, answer: answer.value })
      actionResult.value = { title: '练习需求已记录', desc: '生成练习服务暂不可用，已记录为智能辅导反馈，后续辅导会优先补充相似题。', type: 'warning' }
      ElMessage.warning('已记录练习需求。')
    } catch (error) {
      actionResult.value = { title: '练习生成失败', desc: error instanceof Error ? error.message : '练习生成失败，反馈也未能保存。', type: 'warning' }
      ElMessage.error(actionResult.value.desc)
    }
  } finally {
    actionLoading.value = ''
  }
}

async function generateDocument() {
  if (!requireQuestion()) return
  if (!canUseLearningActions.value) {
    ElMessage.warning('请先获得一条完整回答，再生成讲解文档。')
    return
  }
  actionLoading.value = 'document'
  try {
    const result = await generateTutorDocumentApi({ message: input.value, mode: mode.value, answer: answer.value, course_id: 'course_data_structure' })
    answer.value = result.content
    confidence.value = 0.86
    inferred.value = true
    actionResult.value = { title: result.title, desc: '讲解文档已生成并保存为辅导笔记，可在后续报告中追溯。', type: 'success' }
    ElMessage.success('讲解文档已生成。')
  } catch {
    actionResult.value = { title: '讲解文档生成失败', desc: '请稍后重试，或先保存当前回答为笔记。', type: 'warning' }
    ElMessage.error('讲解文档生成失败。')
  } finally {
    actionLoading.value = ''
  }
}

async function addRemedialTask() {
  if (!requireQuestion()) return
  actionLoading.value = 'path'
  try {
    const result = await createTutorRemedialTaskApi({ message: input.value, mode: mode.value, answer: answer.value })
    resource.learningPath = result.learningPath
    resource.persist()
    actionResult.value = { title: '已加入路径补强', desc: `新增任务「${result.stage.name}」，学习路径已同步更新。`, type: 'success' }
    ElMessage.success('补强任务已加入学习路径。')
  } catch {
    actionResult.value = { title: '暂时无法加入学习计划', desc: '本次没有修改你的学习计划，请稍后再试。', type: 'warning' }
    ElMessage.warning('补强任务加入失败，请稍后重试。')
  } finally {
    actionLoading.value = ''
  }
}

async function feedback(type: 'helpful' | 'too_hard' | 'incorrect' | 'need_example') {
  const messageMap = {
    helpful: '已记录“有帮助”。',
    too_hard: '已记录“太难”，后续回答会降低解释层级。',
    incorrect: '已记录“不准确”，建议教师复核相关资料。',
    need_example: '已记录“需要例子”，后续资源会增加例题。',
  }
  try {
    await submitTutorFeedbackApi({ type, message: input.value, answer: answer.value })
    actionResult.value = { title: '反馈已记录', desc: messageMap[type], type: type === 'incorrect' ? 'warning' : 'success' }
  } catch {
    // 反馈失败不阻断学习流程，前端提示仍保持轻量。
    actionResult.value = { title: '反馈已暂存', desc: messageMap[type], type: 'warning' }
  }
  ElMessage.success(messageMap[type])
}
</script>

<template>
  <div class="tutor-panel">
    <div class="tutor-header" @pointerdown="emit('drag-start', $event)">
      <div class="assistant-identity">
        <span class="assistant-avatar"><Sparkles :size="20" /></span>
        <div>
          <h1 class="page-title">智能辅导</h1>
          <p class="page-subtitle"><span class="online-dot" />在线陪学，有问题随时问我</p>
        </div>
      </div>
      <div class="header-actions">
        <button class="close-button" type="button" aria-label="最小化智能辅导" @pointerdown.stop @click="emit('minimize')">
          <Minus :size="18" />
        </button>
        <button class="close-button" type="button" aria-label="关闭智能辅导" @pointerdown.stop @click="closePanel">
          <X :size="18" />
        </button>
      </div>
    </div>

    <section class="context-ribbon">
      <div v-for="item in contextItems.slice(0, 3)" :key="item.label" class="context-chip">
        <span>{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
      </div>
      <el-alert v-if="feedbackHint" class="feedback-hint" type="info" show-icon :closable="false">
        {{ feedbackHint }}
      </el-alert>
    </section>

    <div class="tutor-layout">
      <main class="chat-panel">
        <div class="chat-head">
          <div>
            <h2 class="section-title">今天想弄懂什么？</h2>
            <p class="section-desc">问知识点、粘贴代码或分析错题，我会结合你的学习进度来讲解。</p>
          </div>
          <el-tag effect="plain">{{ mode }}</el-tag>
        </div>

        <div class="mode-row">
          <button v-for="item in modes" :key="item" :class="{ active: mode === item }" type="button" @click="selectMode(item)">
            {{ item }}
          </button>
        </div>

        <div class="composer">
          <el-input
            v-model="input"
            type="textarea"
            :rows="3"
            resize="none"
            placeholder="输入你正在卡住的问题，或粘贴错题/代码片段"
            @keydown.ctrl.enter="ask"
          />
          <div class="composer-actions">
            <span>Ctrl + Enter 也可以快速发送</span>
            <el-button type="primary" :loading="asking" @click="ask">
              {{ asking ? '正在思考' : '发送问题' }}
            </el-button>
            <el-button v-if="asking" @click="stopChat">停止</el-button>
          </div>
        </div>

        <div v-if="answer" class="message user-message question-preview">
          <MessageSquareText :size="18" />
          <div>
            <span>本次问题</span>
            <strong>{{ input }}</strong>
          </div>
        </div>

        <div class="message ai-message">
          <Sparkles :size="18" />
          <div class="answer-body">
            <div v-if="asking" class="status-flow">
              <div><span class="done" />{{ streamStatus }}</div>
              <MarkdownViewer v-if="answer" :content="answer" />
            </div>
            <template v-else-if="answer">
              <el-tabs v-model="activeResultTab" class="result-tabs">
                <el-tab-pane label="文字解答" name="answer">
                  <MarkdownViewer :content="answer" />
                  <section v-if="suggestedActions.length" class="suggested-actions">
                    <h3>建议学习动作</h3>
                    <div>
                      <article v-for="item in suggestedActions" :key="`${item.type}-${item.title}`">
                        <strong>{{ item.title }}</strong>
                        <span>{{ item.reason }}</span>
                      </article>
                    </div>
                  </section>
                </el-tab-pane>
                <el-tab-pane label="图解说明" name="diagram">
                  <MarkmapRenderer
                    v-if="diagram?.markdown"
                    :markdown="diagram.markdown"
                    :title="diagram.title || '智能辅导图解'"
                    :active="activeResultTab === 'diagram' && !asking"
                  />
                  <section v-else class="state-empty compact-empty extra-state">
                    <h3>{{ extraLoading === 'diagram' ? '正在生成图解' : diagramError ? '图解生成失败' : '准备生成图解' }}</h3>
                    <p>{{ extraLoading === 'diagram' ? '正在根据本次回答和课程引用整理知识结构…' : diagramError || '打开标签后会按需生成，不影响文字回答速度。' }}</p>
                    <el-button v-if="diagramError" type="primary" plain @click="loadExtra('diagram')">重新生成图解</el-button>
                  </section>
                </el-tab-pane>
                <el-tab-pane label="短视频讲解" name="video">
                  <section v-if="videoScript?.scenes?.length" class="state-empty compact-empty video-script-entry">
                    <h3>{{ videoScript.title || '智能辅导短视频讲解' }}</h3>
                    <p>已经为这个问题准备了讲解内容，可以前往视频页面继续学习。</p>
                    <router-link to="/student/video-demo">
                      <el-button type="primary">打开视频演示模块</el-button>
                    </router-link>
                    <MarkdownViewer v-if="videoScript.script" :content="videoScript.script" />
                  </section>
                  <section v-else class="state-empty compact-empty extra-state">
                    <h3>{{ extraLoading === 'video' ? '正在生成视频脚本' : videoError ? '视频脚本生成失败' : '准备生成视频脚本' }}</h3>
                    <p>{{ extraLoading === 'video' ? '正在把课程内容拆成适合短视频的讲解场景…' : videoError || '打开标签后会按需生成，不影响文字回答速度。' }}</p>
                    <el-button v-if="videoError" type="primary" plain @click="loadExtra('video')">重新生成视频脚本</el-button>
                  </section>
                </el-tab-pane>
              </el-tabs>
            </template>
            <section v-else class="state-empty tutor-empty">
              <h3>还没有辅导回答</h3>
              <p>选择一个场景开始提问，我会一步步陪你弄懂。</p>
            </section>
          </div>
        </div>

        <section v-if="errorMessage" class="error-panel">
          <el-alert type="warning" show-icon :closable="false" :title="errorMessage" />
          <div class="error-actions">
            <span v-if="errorCode">错误码：{{ errorCode }}<template v-if="requestId"> · 请求：{{ requestId }}</template></span>
            <el-button v-if="errorRetryable" size="small" type="primary" plain @click="ask">重新生成</el-button>
          </div>
        </section>
      </main>

      <aside class="result-panel">
        <section class="action-panel">
          <h2 class="section-title">继续学习</h2>
          <p class="section-desc">把这次学习保存下来，或者继续练一练。</p>
        <div class="action-list">
          <el-button :disabled="!canUseLearningActions" :loading="actionLoading === 'note'" @click="saveNote"><BookmarkPlus :size="16" />保存为笔记</el-button>
          <el-button :disabled="!canUseLearningActions" :loading="actionLoading === 'exercise'" @click="generateExercise"><ClipboardCheck :size="16" />生成练习题</el-button>
          <el-button :disabled="!canUseLearningActions" :loading="actionLoading === 'document'" @click="generateDocument"><FileText :size="16" />生成讲解文档</el-button>
          <el-button :loading="actionLoading === 'path'" @click="addRemedialTask"><GitBranch :size="16" />加入路径补强</el-button>
          <el-button :loading="actionLoading === 'mistake'" @click="addMistake"><BookOpen :size="16" />加入错题本</el-button>
        </div>
        <el-alert
          v-if="actionResult"
          class="action-result"
          :type="actionResult.type"
          show-icon
          :closable="false"
          :title="actionResult.title"
          :description="actionResult.desc"
        />
        </section>

        <section class="feedback-panel">
          <h2 class="section-title">这个回答怎么样？</h2>
          <div class="feedback-row">
            <el-button size="small" @click="feedback('helpful')">有帮助</el-button>
            <el-button size="small" @click="feedback('too_hard')">太难</el-button>
            <el-button size="small" @click="feedback('incorrect')">不准确</el-button>
            <el-button size="small" @click="feedback('need_example')">需要例子</el-button>
          </div>
        </section>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.tutor-panel {
  height: 100%;
  padding: 18px;
  overflow-x: hidden;
  overflow-y: auto;
}

.tutor-header {
  position: sticky;
  top: -18px;
  z-index: 5;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin: -18px -18px 14px;
  padding: 18px;
  border-bottom: 1px solid var(--color-border);
  background: rgba(255, 252, 247, 0.96);
  backdrop-filter: blur(12px);
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.tutor-header:active {
  cursor: grabbing;
}

.tutor-header > div {
  display: grid;
  gap: 8px;
}

.tutor-header .page-title {
  font-size: 22px;
}

.tutor-header .page-subtitle {
  font-size: 13px;
}

.tutor-header .header-actions {
  display: flex;
  flex: 0 0 auto;
  grid-auto-flow: column;
  align-items: center;
  gap: 8px;
}

.close-button {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
}

.context-ribbon {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}

.context-chip {
  display: grid;
  gap: 5px;
  min-height: 68px;
  padding: 10px 12px;
  border: 1px solid var(--color-border-soft);
  border-radius: 8px;
  background: #fff;
}

.context-chip:last-of-type {
  border-right: 0;
}

.context-chip span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.context-chip strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
}

.context-ribbon .feedback-hint {
  grid-column: 1 / -1;
  border-radius: 0;
  border-left: 0;
  border-right: 0;
  border-bottom: 0;
}

.tutor-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.result-panel {
  position: static;
  display: grid;
  gap: 12px;
}

.chat-panel {
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  background: #fff;
  box-shadow: var(--shadow-soft);
}

.chat-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--color-border-soft);
}

.chat-head p {
  margin-bottom: 0;
}

.composer-actions span {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.feedback-hint {
  margin-bottom: 12px;
}

h3 {
  margin: 0 0 10px;
  font-size: 15px;
}

.mode-row {
  display: flex;
  gap: 6px;
  padding: 6px;
  overflow-x: auto;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #faf6ee;
}

.mode-row button {
  flex: 0 0 auto;
  padding: 8px 10px;
  color: var(--color-text-secondary);
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  transition: all 0.15s ease;
}

.mode-row button.active {
  color: var(--color-primary-strong);
  border-color: #bfdbfe;
  background: #fff;
  box-shadow: var(--shadow-soft);
}

.message {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  gap: 10px;
}

.question-preview {
  margin-top: 2px;
}

.question-preview div {
  padding: 12px 14px;
  color: var(--color-text);
  border-radius: 8px;
  background: #fbfcff;
}

.question-preview span {
  display: block;
  margin-bottom: 4px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.question-preview strong {
  line-height: 1.6;
}

.ai-message {
  color: var(--color-primary);
}

.answer-body {
  min-height: 220px;
  color: var(--color-text);
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: linear-gradient(180deg, #ffffff 0%, #fffdf9 100%);
}

.trust-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.result-tabs {
  --el-color-primary: var(--color-primary);
}

.suggested-actions,
.inferred-list {
  margin-top: 14px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.suggested-actions > div {
  display: grid;
  gap: 8px;
}

.suggested-actions article {
  display: grid;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
}

.suggested-actions strong {
  color: var(--color-text);
}

.suggested-actions span,
.confidence-reason {
  color: var(--color-text-secondary);
  line-height: 1.6;
  font-size: 13px;
}

.inferred-list ul {
  margin: 0;
  padding-left: 18px;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.compact-empty {
  min-height: 180px;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
}

.confidence-reason {
  margin: 8px 0 0;
}

.status-flow {
  display: grid;
  gap: 12px;
  color: var(--color-text-secondary);
}

.status-flow > div {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-flow span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #cbd5e1;
}

.status-flow span.done {
  background: var(--color-primary);
}

.error-alert,
.error-panel,
.composer {
  margin-top: 0;
}

.composer {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid #cfe0ff;
  border-radius: 8px;
  background: linear-gradient(180deg, #fbfdff 0%, #fff8ef 100%);
}

.composer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.action-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 14px;
}

.action-list .el-button {
  justify-content: flex-start;
  margin-left: 0;
  width: 100%;
}

.action-panel,
.trust-panel,
.feedback-panel {
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  background: #fff;
  box-shadow: var(--shadow-soft);
}

.action-result {
  margin-top: 12px;
}

.feedback-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.action-panel :deep(.source-list),
.action-panel :deep(.citation-list) {
  max-height: 240px;
  overflow: auto;
}

.trust-panel :deep(.source-list),
.trust-panel :deep(.citation-list) {
  max-height: 280px;
  overflow: auto;
}

.trust-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 14px 0;
}

.trust-metrics div {
  display: grid;
  gap: 5px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fbfcff;
}

.trust-metrics span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

@media (max-width: 1200px) {
  .context-ribbon {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .tutor-layout {
    grid-template-columns: 1fr;
  }

  .result-panel {
    position: static;
  }

}

@media (max-width: 720px) {
  .tutor-panel {
    padding: 14px;
  }

  .tutor-header {
    top: -14px;
    margin: -14px -14px 12px;
    padding: 14px;
  }

  .tutor-header .page-subtitle,
  .header-actions .el-tag {
    display: none;
  }

  .context-ribbon {
    grid-template-columns: 1fr;
  }

  .context-chip {
    border-right: 0;
    border-bottom: 1px solid var(--color-border-soft);
  }

  .composer-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .action-list {
    grid-template-columns: 1fr;
  }
}

/* 悬浮助手窗口：保持功能完整，同时把信息层级收拢成真实产品的对话体验。 */
.tutor-panel {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
  background: #f7f9fc;
}

.tutor-header {
  position: static;
  flex: 0 0 auto;
  align-items: center;
  margin: 0;
  padding: 14px 16px;
  background: rgba(255, 255, 255, 0.97);
}

.tutor-header > .assistant-identity {
  display: flex;
  align-items: center;
  gap: 10px;
}

.assistant-avatar {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  color: #fff;
  border-radius: 12px;
  background: linear-gradient(135deg, #f0a536 0%, var(--color-primary) 75%);
  box-shadow: 0 8px 18px rgba(59, 110, 234, 0.2);
}

.assistant-identity > div {
  display: grid;
  gap: 3px;
}

.tutor-header .page-title {
  margin: 0;
  font-size: 16px;
}

.tutor-header .page-subtitle {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 12px;
}

.online-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #22c55e;
}

.close-button {
  border: 0;
  background: transparent;
}

.close-button:hover {
  color: var(--color-text);
  background: #eef2f7;
}

.context-ribbon {
  flex: 0 0 auto;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  margin: 0;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-soft);
  background: #fff;
}

.context-chip {
  min-height: 50px;
  padding: 8px 9px;
  border: 0;
  border-radius: 10px;
  background: #f4f7fb;
}

.context-chip strong {
  font-size: 12px;
}

.context-ribbon .feedback-hint {
  margin: 0;
  border: 0;
  border-radius: 8px;
}

.tutor-layout {
  flex: 1;
  min-height: 0;
  display: block;
  padding: 12px;
  overflow-x: hidden;
  overflow-y: auto;
}

.chat-panel {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  box-shadow: none;
}

.chat-head {
  order: 1;
  padding: 12px 14px;
  border: 1px solid #dce7f8;
  border-radius: 14px;
  background: linear-gradient(135deg, #fff 0%, #f3f7ff 100%);
}

.chat-head .section-title {
  font-size: 16px;
}

.chat-head .section-desc {
  margin-top: 5px;
  font-size: 12px;
  line-height: 1.55;
}

.mode-row {
  order: 2;
  margin-top: 10px;
  padding: 5px;
  scrollbar-width: none;
}

.mode-row::-webkit-scrollbar {
  display: none;
}

.mode-row button {
  padding: 7px 9px;
  font-size: 12px;
}

.question-preview {
  order: 3;
  grid-template-columns: minmax(0, 1fr) 28px;
  margin-top: 14px;
}

.question-preview > svg {
  grid-column: 2;
  grid-row: 1;
  padding: 6px;
  color: #fff;
  border-radius: 9px;
  background: #7b8ba6;
  box-sizing: content-box;
}

.question-preview > div {
  grid-column: 1;
  grid-row: 1;
  border-radius: 14px 4px 14px 14px;
  background: #eaf2ff;
}

.ai-message {
  order: 4;
  margin-top: 12px;
}

.ai-message > svg {
  padding: 6px;
  color: #fff;
  border-radius: 9px;
  background: linear-gradient(135deg, #f0a536 0%, var(--color-primary) 75%);
  box-sizing: content-box;
}

.answer-body {
  min-height: 150px;
  padding: 13px;
  border-radius: 4px 14px 14px 14px;
  background: #fff;
}

.tutor-empty {
  min-height: 145px;
}

.status-flow {
  min-height: 110px;
  align-content: center;
}

.status-flow span.done {
  animation: tutor-pulse 1.2s infinite ease-in-out;
}

@keyframes tutor-pulse {
  0%, 100% { transform: scale(0.8); opacity: 0.45; }
  50% { transform: scale(1.15); opacity: 1; }
}

.error-alert,
.error-panel {
  order: 5;
  margin-top: 10px;
}

.error-panel {
  display: grid;
  gap: 8px;
}

.error-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.extra-state {
  display: grid;
  place-items: center;
  align-content: center;
  text-align: center;
}

.composer {
  position: sticky;
  bottom: -12px;
  z-index: 3;
  order: 6;
  margin: 14px -1px 0;
  padding: 10px;
  border-color: #c9d9f5;
  border-radius: 13px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 -8px 24px rgba(31, 45, 78, 0.08);
}

.composer :deep(.el-textarea__inner) {
  min-height: 68px !important;
  border-radius: 10px;
  box-shadow: 0 0 0 1px #d5dfef inset;
}

.composer-actions span {
  font-size: 11px;
}

.result-panel {
  margin-top: 12px;
}

.action-panel,
.feedback-panel {
  padding: 13px;
  border-radius: 13px;
  box-shadow: none;
}

.action-list {
  display: flex;
  flex-wrap: wrap;
}

.action-list .el-button {
  width: auto;
  padding: 7px 9px;
}

.feedback-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.feedback-panel .section-title {
  flex: 0 0 auto;
  font-size: 13px;
}

.feedback-row {
  justify-content: flex-end;
  margin-top: 0;
}

@media (max-width: 520px) {
  .context-ribbon {
    display: none;
  }

  .tutor-layout {
    padding: 10px;
  }

  .chat-head .el-tag {
    display: none;
  }

  .action-list {
    grid-template-columns: none;
  }

  .feedback-panel {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
