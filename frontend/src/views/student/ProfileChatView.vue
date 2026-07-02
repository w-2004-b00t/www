<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import FlowGuide from '../../components/common/FlowGuide.vue'
import {
  getProfileDialogSessionApi,
  resetProfileDialogSessionApi,
  saveProfileDialogSessionApi,
} from '../../api/profile'
import type { ProfileDialogMessage, ProfileDialogSession } from '../../api/profile'
import {
  MIN_PROFILE_DIMENSIONS_TO_EXTRACT,
  REQUIRED_PROFILE_DIMENSIONS,
  useProfileStore,
} from '../../stores/profile'
import { useUiStore } from '../../stores/ui'
import type { StudentProfileItem } from '../../types/common'
import { readUserJson, removeUserKeys, writeUserJson } from '../../utils/storage'

const router = useRouter()
const profile = useProfileStore()
const ui = useUiStore()

const profileItems = computed(() => profile.profileItems)
const draftItems = computed(() => profile.draftItems)

const PROFILE_DIALOG_SESSION_KEY = 'eduagent_profile_dialog_session'
const INITIAL_AGENT_MESSAGE = '我是画像 Agent。请先用自己的话说明专业年级、正在学的数据结构内容、卡住的地方、偏好的资料形式和可用时间；我会边听边追问，不会替你编画像。'

const messages = ref<ProfileDialogMessage[]>([
  {
    role: 'agent',
    text: INITIAL_AGENT_MESSAGE,
  },
])
const studentReply = ref('')
const rawProfileInput = ref('')
const showAllMessages = ref(false)
const conversationRef = ref<HTMLElement | null>(null)
const showDraftDetails = ref<string[]>([])
const saveCompleted = ref(false)
const saveFailed = ref(false)
const acknowledgedLowConfidence = ref<string[]>([])
const coveredDimensions = ref<string[]>([])
const missingDimensions = ref<string[]>([...REQUIRED_PROFILE_DIMENSIONS])
const nextQuestionTitle = ref('先介绍你的学习情况')
const canExtract = ref(false)

const answeredMessages = computed(() => messages.value.filter((message) => message.role === 'student'))
const visibleMessages = computed(() => (showAllMessages.value ? messages.value : messages.value.slice(-5)))
const requiredLabels = REQUIRED_PROFILE_DIMENSIONS
const progressPercent = computed(() => Math.round((coveredDimensions.value.length / requiredLabels.length) * 100))
const isReadyToExtract = computed(
  () => canExtract.value && coveredDimensions.value.length >= MIN_PROFILE_DIMENSIONS_TO_EXTRACT,
)
const collectedLabels = computed(() => coveredDimensions.value)
const missingLabels = computed(() => requiredLabels.filter((label) => !collectedLabels.value.includes(label)))
const replyPlaceholder = computed(() =>
  missingLabels.value.length
    ? `请补充：${missingLabels.value.slice(0, 3).join('、')}。例如说明你的真实情况，不确定就写“不确定”。`
    : '核心信息已覆盖，可以生成画像草稿；也可以继续补充细节。',
)

const summaryDimensions = [
  '专业背景',
  '年级 / 学习阶段',
  '知识基础',
  '学习目标',
  '薄弱知识点',
  '资源偏好',
  '可用学习时间',
  '实践能力水平',
  '易错点',
]
const summaryDraftItems = computed(() =>
  summaryDimensions
    .map((dimension) => draftItems.value.find((item) => item.dimension === dimension))
    .filter(Boolean) as StudentProfileItem[],
)

const lowConfidenceDrafts = computed(() => draftItems.value.filter((item) => (item.confidence ?? 0) < 0.85))
const hasUnconfirmedLowConfidence = computed(() =>
  lowConfidenceDrafts.value.some((item) => !acknowledgedLowConfidence.value.includes(item.id)),
)
const hasRegisteredIdentityProfile = computed(() =>
  ['专业背景', '年级 / 学习阶段'].every((dimension) =>
    profileItems.value.some((item) => item.dimension === dimension && item.status === 'confirmed' && item.value),
  ),
)

const agentStep = computed(() => {
  if (profile.isSaving) return '正在保存画像'
  if (profile.isExtracting) return '正在调用 DeepSeek 抽取画像'
  if (profile.isThinking) return '画像 Agent 正在分析回答'
  if (draftItems.value.length) return '等待学生确认画像'
  if (isReadyToExtract.value) return '核心信息已足够，可以生成画像草稿'
  return '等待学生描述学习情况'
})

async function scrollConversationToBottom() {
  await nextTick()
  const container = conversationRef.value
  if (container) {
    container.scrollTop = container.scrollHeight
  }
}

watch(
  () => [messages.value.length, showAllMessages.value],
  () => {
    void scrollConversationToBottom()
  },
)

function cleanDimensions(values: string[]) {
  return values.filter((dimension) => requiredLabels.includes(dimension))
}

function buildCurrentSession(): ProfileDialogSession {
  return {
    messages: messages.value,
    rawProfileInput: rawProfileInput.value,
    coveredDimensions: cleanDimensions(coveredDimensions.value),
    missingDimensions: cleanDimensions(missingDimensions.value),
    nextQuestionTitle: nextQuestionTitle.value,
    canExtract: canExtract.value,
    draftItems: profile.draftItems,
    saveCompleted: saveCompleted.value,
  }
}

function hasRestorableSession(session: ProfileDialogSession | null) {
  if (!session) return false
  return Boolean(
    session.messages?.length > 1 ||
    session.rawProfileInput ||
    session.coveredDimensions?.length ||
    session.draftItems?.length,
  )
}

function applyDialogSession(session: ProfileDialogSession | null) {
  if (!hasRestorableSession(session)) return false
  messages.value = session?.messages?.length ? session.messages : [{ role: 'agent', text: INITIAL_AGENT_MESSAGE }]
  rawProfileInput.value = session?.rawProfileInput || ''
  coveredDimensions.value = cleanDimensions(session?.coveredDimensions || [])
  missingDimensions.value = cleanDimensions(session?.missingDimensions || [])
  if (!missingDimensions.value.length) {
    missingDimensions.value = requiredLabels.filter((label) => !coveredDimensions.value.includes(label))
  }
  nextQuestionTitle.value = session?.nextQuestionTitle || '先介绍你的学习情况'
  canExtract.value = Boolean(session?.canExtract)
  profile.draftItems = session?.draftItems || []
  saveCompleted.value = Boolean(session?.saveCompleted)
  saveFailed.value = false
  return true
}

function resetDialogState() {
  messages.value = [{ role: 'agent', text: INITIAL_AGENT_MESSAGE }]
  studentReply.value = ''
  rawProfileInput.value = ''
  saveCompleted.value = false
  saveFailed.value = false
  acknowledgedLowConfidence.value = []
  profile.draftItems = []
  profile.lastError = ''
  profile.streamStatus = ''
  profile.streamText = ''
  profile.dialogTurn = null
  coveredDimensions.value = []
  missingDimensions.value = [...REQUIRED_PROFILE_DIMENSIONS]
  nextQuestionTitle.value = '先介绍你的学习情况'
  canExtract.value = false
}

async function syncDialogSession() {
  const session = buildCurrentSession()
  writeUserJson(PROFILE_DIALOG_SESSION_KEY, session)
  try {
    await saveProfileDialogSessionApi(session)
  } catch {
    // Keep the local draft as an offline recovery point.
  }
}

async function clearDialogSession() {
  removeUserKeys([PROFILE_DIALOG_SESSION_KEY])
  try {
    await resetProfileDialogSessionApi()
  } catch {
    // Local clearing is enough to prevent this browser from replaying stale questions.
  }
}

async function loadDialogSession() {
  const cachedSession = readUserJson<ProfileDialogSession | null>(PROFILE_DIALOG_SESSION_KEY, null)
  const restoredFromCache = applyDialogSession(cachedSession)
  try {
    const serverSession = await getProfileDialogSessionApi()
    if (hasRestorableSession(serverSession)) {
      applyDialogSession(serverSession)
      writeUserJson(PROFILE_DIALOG_SESSION_KEY, serverSession)
    } else if (restoredFromCache && cachedSession) {
      await saveProfileDialogSessionApi(cachedSession)
    } else {
      removeUserKeys([PROFILE_DIALOG_SESSION_KEY])
    }
  } catch {
    if (restoredFromCache) {
      profile.streamStatus = '已恢复本地画像对话草稿，后端可用后会继续同步。'
    }
  } finally {
    void scrollConversationToBottom()
  }
}

onMounted(async () => {
  await profile.loadProfile()
  await loadDialogSession()
})

function rebuildRawInput() {
  const answers = answeredMessages.value
    .map((message, index) => `第 ${index + 1} 次回答：${message.text}`)
    .join('\n')
  rawProfileInput.value = `课程：数据结构课程\n${answers}`.trim()
}

async function submitReply() {
  const text = studentReply.value.trim()
  if (!text) {
    ElMessage.warning('请先用自己的话回答当前问题。')
    return false
  }

  messages.value.push({ role: 'student', text })
  studentReply.value = ''
  rebuildRawInput()

  try {
    const turn = await profile.runDialogTurn(messages.value, coveredDimensions.value, requiredLabels)
    coveredDimensions.value = turn.coveredDimensions
    missingDimensions.value = turn.missingDimensions
    nextQuestionTitle.value = turn.nextQuestionTitle
    canExtract.value = turn.canExtract
    messages.value.push({ role: 'agent', text: turn.assistantMessage })
    await syncDialogSession()
    return true
  } catch {
    const lastIndex = messages.value.length - 1
    if (messages.value[lastIndex]?.role === 'student' && messages.value[lastIndex]?.text === text) {
      messages.value.splice(lastIndex, 1)
    }
    studentReply.value = text
    rebuildRawInput()
    ElMessage.error('画像 Agent 暂不可用，本轮回答未推进，也不会生成假画像。')
    return false
  }
}

async function extractDraft() {
  if (studentReply.value.trim()) {
    const submitted = await submitReply()
    if (!submitted) {
      return
    }
  }
  rebuildRawInput()

  if (!isReadyToExtract.value) {
    ElMessage.warning(`请至少补充 ${MIN_PROFILE_DIMENSIONS_TO_EXTRACT} 个画像维度，再生成画像草稿。`)
    return
  }

  saveCompleted.value = false
  saveFailed.value = false
  acknowledgedLowConfidence.value = []
  try {
    await profile.extractAndConfirmFromMessage(rawProfileInput.value)
    saveCompleted.value = true
    saveFailed.value = false
    await clearDialogSession()
    ElMessage.success('画像已生成并保存，正在进入画像档案。')
    router.push('/student/profile')
  } catch {
    saveFailed.value = true
    await syncDialogSession()
    ElMessage.error('画像生成或保存失败，未写入画像。')
  }
}

async function confirmDrafts() {
  if (!draftItems.value.length) {
    ElMessage.warning('请先生成画像草稿。')
    return
  }

  if (hasUnconfirmedLowConfidence.value) {
    acknowledgedLowConfidence.value = lowConfidenceDrafts.value.map((item) => item.id)
  }

  try {
    await profile.confirmDrafts()
    saveCompleted.value = true
    saveFailed.value = false
    await clearDialogSession()
    ElMessage.success('画像已保存，将用于资源推荐和学习路径。')
  } catch {
    saveFailed.value = true
    ElMessage.error('画像保存失败，请重试。')
  }
}

async function restartConversation() {
  resetDialogState()
  await clearDialogSession()
}

function addQuickText(text: string) {
  studentReply.value = studentReply.value ? `${studentReply.value}，${text}` : text
}

function useQuestionHint() {
  studentReply.value = [
    '专业：',
    '年级：',
    '知识基础：',
    '学习目标：',
    '薄弱点/易错点：',
    '资源偏好：',
    '可用学习时间：',
    '实践能力：',
  ].join('\n')
}

function confidencePercent(item: StudentProfileItem) {
  return Math.round((item.confidence ?? 0) * 100)
}

function sourceLabel(source?: string) {
  const map: Record<string, string> = {
    dialog: '对话',
    assessment: '测评',
    behavior: '行为',
    manual: '手动修改',
  }
  return map[source || ''] || source || '对话'
}

function updateDraftValue(id: string, value: string) {
  const item = draftItems.value.find((draft) => draft.id === id)
  if (item) item.value = value
  void syncDialogSession()
}

function removeDraft(id: string) {
  profile.draftItems = draftItems.value.filter((item) => item.id !== id)
  void syncDialogSession()
}

</script>

<template>
  <main class="profile-chat-page">
    <div class="page-breadcrumb">学生端 / 对话画像 / 自然语言画像</div>

    <section class="profile-chat-hero panel">
      <div>
        <p class="eyebrow">对话式学习画像</p>
          <h1>先聊清楚你的学习情况</h1>
          <p>
          画像 Agent 会根据你的回答动态追问，只在信息足够且经你确认后写入画像。
          </p>
      </div>
      <div class="progress-summary">
        <span>对话进度</span>
        <strong>{{ coveredDimensions.length }} / {{ requiredLabels.length }}</strong>
        <el-progress :percentage="progressPercent" :show-text="false" />
      </div>
    </section>

    <el-alert
      v-if="profile.lastError"
      class="state-alert"
      type="warning"
      :closable="false"
      :title="profile.lastError"
      show-icon
    />

    <section v-if="saveCompleted" class="panel success-panel">
      <div>
        <p class="eyebrow">画像已确认</p>
        <h2>可以开始生成个性化学习资料</h2>
        <p>画像会影响资源类型、难度、学习路径、智能辅导回答方式和测评补强任务。</p>
      </div>
      <div class="success-actions">
        <el-button type="primary" @click="router.push('/student/resource-generate')">生成本次学习资料</el-button>
        <el-button @click="router.push('/student/profile')">查看画像档案</el-button>
      </div>
    </section>

    <section v-if="saveFailed" class="panel failed-panel">
      <div>
        <p class="eyebrow">保存失败</p>
        <h2>画像草稿还没有写入后端</h2>
        <p>为避免假画像进入推荐流程，请在后端恢复后重试保存。</p>
      </div>
      <div>
        <el-button type="primary" @click="confirmDrafts">重试保存</el-button>
      </div>
    </section>

    <div class="chat-layout">
      <section class="panel chat-main">
        <header class="section-head">
          <div>
            <p class="eyebrow">当前问题</p>
            <h2>{{ nextQuestionTitle }}</h2>
          </div>
          <el-tag round>{{ agentStep }}</el-tag>
        </header>

        <div ref="conversationRef" class="conversation-shell">
          <div class="message-list">
          <article
            v-for="(message, index) in visibleMessages"
            :key="`${message.role}-${index}-${message.text}`"
            class="message-bubble"
            :class="message.role"
          >
            <span>{{ message.role === 'agent' ? '画像 Agent' : '你' }}</span>
            <p>{{ message.text }}</p>
          </article>
        </div>

        <button
          v-if="messages.length > visibleMessages.length"
          class="plain-toggle"
          type="button"
          @click="showAllMessages = !showAllMessages"
        >
          {{ showAllMessages ? '收起历史对话' : `展开全部 ${messages.length} 条对话` }}
        </button>

        </div>

        <div class="chat-composer">
        <div class="quick-row">
          <span>字段模板</span>
          <el-button size="small" @click="addQuickText('专业：\n年级：')">专业年级</el-button>
          <el-button size="small" @click="addQuickText('学习目标：')">学习目标</el-button>
          <el-button size="small" @click="addQuickText('薄弱点/易错点：')">薄弱易错</el-button>
          <el-button size="small" @click="addQuickText('资源偏好：')">资源偏好</el-button>
          <el-button size="small" @click="addQuickText('可用学习时间：')">学习时间</el-button>
          <el-button size="small" @click="addQuickText('实践能力：')">实践能力</el-button>
        </div>

        <div class="reply-box">
          <el-input
            v-model="studentReply"
            type="textarea"
            :rows="3"
            resize="none"
            :placeholder="replyPlaceholder"
            @keydown.enter.exact.prevent="submitReply"
          />
          <div class="reply-actions">
            <el-button text @click="useQuestionHint">插入完整字段模板</el-button>
            <div>
              <el-button text @click="restartConversation">重新开始</el-button>
              <el-button
                type="primary"
                :loading="profile.isThinking || profile.isExtracting"
                @click="isReadyToExtract ? extractDraft() : submitReply()"
              >
                {{ isReadyToExtract ? '生成画像并保存' : '发送给画像 Agent' }}
              </el-button>
            </div>
          </div>
        </div>

        </div>

        <div class="bottom-actions">
          <el-button @click="router.push('/student/onboarding')">返回学习起点</el-button>
        </div>

        <el-collapse v-if="ui.reviewMode" class="review-evidence">
          <el-collapse-item title="查看评审证据链：DeepSeek、SSE、结构化输出" name="evidence">
            <FlowGuide
              title="画像 Agent 证据"
              description="评审模式下展示画像抽取的输入、流式状态和结构化输出；学生模式默认隐藏。"
              :steps="[
                { label: '读取输入', desc: '汇总学生对话、历史画像、测评和错题上下文。', status: 'done' },
                { label: '动态追问', desc: '每轮由 DeepSeek 判断覆盖维度并生成下一问。', status: profile.isThinking ? 'active' : 'done' },
                { label: '调用 DeepSeek', desc: '信息完整后抽取 10 个画像维度。', status: profile.isExtracting ? 'active' : 'pending' },
                { label: '生成草稿', desc: '输出待学生确认的画像项、置信度、来源和推荐影响。', status: draftItems.length ? 'done' : 'pending' },
              ]"
            />
            <div class="evidence-grid">
              <div>
                <h4>提交给画像 Agent 的输入</h4>
                <pre>{{ rawProfileInput || '等待学生回答后生成。' }}</pre>
              </div>
              <div>
                <h4>SSE 流式状态</h4>
                <pre>{{ profile.streamText || profile.streamStatus || '等待抽取。' }}</pre>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </section>

      <aside class="side-column">
        <section class="panel side-card">
          <p class="eyebrow">画像收集进度</p>
          <h3>{{ coveredDimensions.length }} / {{ requiredLabels.length }} 个维度</h3>
          <el-progress :percentage="progressPercent" />
          <div class="tag-block">
            <span>已获得</span>
            <el-tag v-for="label in collectedLabels" :key="label" size="small">{{ label }}</el-tag>
            <span v-if="!collectedLabels.length" class="muted">等待回答</span>
          </div>
          <div class="tag-block">
            <span>待完善</span>
            <el-tag v-for="label in missingLabels" :key="label" size="small" type="warning">{{ label }}</el-tag>
            <span v-if="!missingLabels.length" class="muted">核心信息已足够</span>
          </div>
          <p class="muted model-state">{{ profile.streamStatus || '等待画像 Agent 分析。' }}</p>
        </section>
      </aside>
    </div>

    <section v-if="draftItems.length" class="panel draft-panel">
      <header class="section-head">
        <div>
          <p class="eyebrow">画像草稿</p>
          <h2>{{ hasRegisteredIdentityProfile ? '系统已沿用注册信息，以下为需要确认的画像项' : '请确认系统识别是否准确' }}</h2>
          <p>
            {{ hasRegisteredIdentityProfile
              ? '专业和年级已按注册信息写入画像；低置信项需要你确认或修改后再保存。'
              : '低置信项不会直接用于推荐，需要你确认或修改后再保存。' }}
          </p>
        </div>
        <el-tag type="success" round>{{ draftItems.length }} 个画像维度</el-tag>
      </header>

      <div class="summary-grid">
        <article v-for="item in summaryDraftItems" :key="item.id" class="summary-card">
          <div>
            <span>{{ item.dimension }}</span>
            <strong>{{ item.value }}</strong>
          </div>
          <el-tag :type="(item.confidence ?? 0) < 0.85 ? 'warning' : 'success'" round>
            置信度 {{ confidencePercent(item) }}%
          </el-tag>
        </article>
      </div>

      <el-alert
        v-if="lowConfidenceDrafts.length"
        class="state-alert"
        type="warning"
        :closable="false"
        title="存在低置信画像项，请在下方展开后确认或修改。"
        show-icon
      />

      <el-collapse v-model="showDraftDetails">
        <el-collapse-item title="修改画像详情" name="details">
          <div class="draft-detail-list">
            <article v-for="item in draftItems" :key="item.id" class="draft-item">
              <header>
                <div>
                  <h3>{{ item.dimension }}</h3>
                  <p>{{ item.reason || '根据本次对话和学习上下文抽取。' }}</p>
                </div>
                <el-tag :type="(item.confidence ?? 0) < 0.85 ? 'warning' : 'success'" round>
                  {{ confidencePercent(item) }}%
                </el-tag>
              </header>
              <el-input
                :model-value="item.value"
                type="textarea"
                :rows="2"
                resize="none"
                @update:model-value="updateDraftValue(item.id, $event)"
              />
              <div class="draft-meta">
                <span>来源：{{ sourceLabel(item.source) }}</span>
                <span>影响：{{ item.impact || '资源推荐和学习路径' }}</span>
                <el-button text type="danger" @click="removeDraft(item.id)">删除</el-button>
              </div>
            </article>
          </div>
        </el-collapse-item>
      </el-collapse>

      <footer class="confirm-bar">
        <p>确认后将用于生成学习路径、推荐资源、调整智能辅导回答和后续测评补强。</p>
        <el-button type="success" :loading="profile.isSaving" @click="confirmDrafts">确认并用于推荐</el-button>
      </footer>
    </section>
  </main>
</template>

<style scoped>
.profile-chat-page {
  max-width: 1400px;
  margin: 0 auto;
  padding: 28px;
}

.page-breadcrumb {
  color: #64748b;
  font-size: 13px;
  margin-bottom: 16px;
}

.panel {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.profile-chat-hero,
.success-panel,
.failed-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 26px;
  margin-bottom: 18px;
}

.eyebrow {
  margin: 0 0 8px;
  color: #2563eb;
  font-size: 13px;
  font-weight: 600;
}

h1,
h2,
h3,
p {
  margin-top: 0;
}

h1 {
  margin-bottom: 10px;
  font-size: 28px;
}

h2 {
  margin-bottom: 8px;
  font-size: 22px;
}

h3 {
  margin-bottom: 8px;
  font-size: 17px;
}

p {
  color: #64748b;
  line-height: 1.7;
}

.progress-summary {
  min-width: 190px;
  padding: 16px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.progress-summary span,
.tag-block span {
  color: #64748b;
  font-size: 13px;
}

.progress-summary strong {
  display: block;
  margin: 8px 0;
  color: #0f172a;
  font-size: 28px;
}

.state-alert {
  margin-bottom: 18px;
}

.chat-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 18px;
  align-items: stretch;
}

.chat-main {
  display: flex;
  flex-direction: column;
  height: min(620px, calc(100vh - 170px));
  min-height: 520px;
  max-height: none;
  padding: 0;
  overflow: hidden;
}

.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding: 22px 22px 16px;
  margin-bottom: 0;
  border-bottom: 1px solid #e2e8f0;
}

.conversation-shell {
  flex: 1 1 0;
  min-height: 0;
  max-height: 360px;
  padding: 18px 22px 14px;
  overflow-y: auto;
  overscroll-behavior: contain;
  scroll-behavior: smooth;
  scrollbar-gutter: stable;
  background: #ffffff;
}

.message-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 0 0 4px;
  min-height: auto;
  justify-content: flex-start;
}

.message-bubble {
  max-width: 78%;
  padding: 14px 16px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.message-bubble span {
  display: block;
  margin-bottom: 6px;
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
}

.message-bubble p {
  margin: 0;
  color: #0f172a;
}

.message-bubble.agent {
  align-self: flex-start;
  background: #f8fafc;
}

.message-bubble.student {
  align-self: flex-end;
  background: #eff6ff;
  border-color: #bfdbfe;
}

.plain-toggle {
  margin-top: 12px;
  padding: 0;
  color: #2563eb;
  background: transparent;
  border: 0;
  cursor: pointer;
  font-weight: 600;
}

.chat-composer {
  flex-shrink: 0;
  padding: 14px 22px 18px;
  background: #f8fafc;
  border-top: 1px solid #e2e8f0;
}

.quick-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 10px;
}

.quick-row span {
  color: #64748b;
  font-size: 13px;
}

.reply-box {
  padding: 12px;
  border: 1px solid #dbe4f0;
  border-radius: 8px;
  background: #ffffff;
}

.reply-box :deep(.el-textarea__inner) {
  min-height: 86px !important;
  padding: 2px 0 8px;
  border: 0;
  box-shadow: none;
  background: #ffffff;
}

.reply-actions,
.bottom-actions,
.confirm-bar,
.success-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.reply-actions {
  margin-top: 12px;
}

.bottom-actions {
  flex-shrink: 0;
  margin-top: 0;
  padding: 0 22px 18px;
  background: #f8fafc;
}

.review-evidence {
  flex-shrink: 0;
  margin: 0 22px 18px;
  max-height: 180px;
  overflow-y: auto;
}

.evidence-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-top: 14px;
}

pre {
  min-height: 140px;
  max-height: 280px;
  overflow: auto;
  padding: 12px;
  color: #0f172a;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  white-space: pre-wrap;
}

.side-column {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.side-card {
  padding: 18px;
}

.tag-block {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.muted {
  color: #94a3b8;
}

.draft-panel {
  margin-top: 18px;
  padding: 22px;
}

.model-state {
  margin: 14px 0 0;
  font-size: 13px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.summary-card {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 14px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
}

.summary-card span {
  display: block;
  margin-bottom: 6px;
  color: #64748b;
  font-size: 13px;
}

.summary-card strong {
  display: block;
  color: #0f172a;
  line-height: 1.5;
}

.draft-detail-list {
  display: grid;
  gap: 12px;
}

.draft-item {
  padding: 16px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.draft-item header,
.draft-meta {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.draft-meta {
  align-items: center;
  margin-top: 10px;
  color: #64748b;
  font-size: 13px;
}

.confirm-bar {
  margin-top: 18px;
  padding-top: 18px;
  border-top: 1px solid #e2e8f0;
}

@media (max-width: 1100px) {
  .chat-layout,
  .evidence-grid,
  .summary-grid {
    grid-template-columns: 1fr;
  }

  .chat-main {
    height: auto;
    min-height: 0;
  }

  .conversation-shell {
    max-height: 360px;
  }

  .profile-chat-hero,
  .success-panel,
  .failed-panel {
    align-items: stretch;
    flex-direction: column;
  }

  .message-bubble {
    max-width: 100%;
  }
}
</style>
