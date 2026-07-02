<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import TutorPanel from '../components/tutor/TutorPanel.vue'
import { usePersistentBoolean } from '../composables/usePersistentBoolean'
import { useAuthStore } from '../stores/auth'
import { useUiStore } from '../stores/ui'
import {
  Collection,
  Connection,
  DataAnalysis,
  Expand,
  Fold,
  House,
  MagicStick,
  Promotion,
  Reading,
  User,
} from '@element-plus/icons-vue'

const auth = useAuthStore()
const ui = useUiStore()
const route = useRoute()
const router = useRouter()
const SIDEBAR_COLLAPSED_KEY = 'eduagent_student_sidebar_collapsed'
const TUTOR_POSITION_KEY = 'eduagent_tutor_position'
const TUTOR_VIEWPORT_GAP = 8
const TUTOR_DRAG_THRESHOLD = 4
const collapsed = usePersistentBoolean(SIDEBAR_COLLAPSED_KEY)
const tutorOpen = ref(false)
const tutorMinimized = ref(false)
const tutorWindowRef = ref<HTMLElement | null>(null)
const tutorMinimizedRef = ref<HTMLElement | null>(null)
const tutorPosition = ref({ x: 0, y: 0 })
const tutorPositionReady = ref(false)
const tutorDragging = ref(false)
const suppressMinimizedClick = ref(false)

let dragPointerId: number | null = null
let dragStartPointer = { x: 0, y: 0 }
let dragStartPosition = { x: 0, y: 0 }

const displayUserName = computed(() => auth.user?.username || auth.user?.name || '未登录用户')
const tutorPositionStyle = computed(() =>
  tutorPositionReady.value
    ? {
        left: `${tutorPosition.value.x}px`,
        top: `${tutorPosition.value.y}px`,
        right: 'auto',
        bottom: 'auto',
      }
    : undefined,
)

const menuGroups = [
  {
    index: 'start',
    label: '学习起点',
    icon: House,
    children: [
      { path: '/student/onboarding', label: '建立学习任务' },
      { path: '/student/dashboard', label: '今日学习' },
    ],
  },
  {
    index: 'profile',
    label: '对话画像',
    icon: User,
    children: [
      { path: '/student/profile-chat', label: '建立学习画像' },
      { path: '/student/profile', label: '画像档案' },
    ],
  },
  {
    index: 'resources',
    label: '资源生成',
    icon: MagicStick,
    children: [
      { path: '/student/resource-generate', label: '生成学习资料' },
      { path: '/student/resources', label: '资源中心' },
    ],
  },
  {
    index: 'multimodal',
    label: '多模态资源',
    icon: Collection,
    children: [
      { path: '/student/mindmap', label: '完整思维导图' },
      { path: '/student/video-demo', label: '视频动画演示' },
    ],
  },
  {
    index: 'knowledge',
    label: '知识网络',
    icon: Connection,
    children: [
      { path: '/student/knowledge-graph', label: '知识图谱' },
    ],
  },
  {
    index: 'path',
    label: '学习路径',
    icon: Reading,
    children: [
      { path: '/student/learning-path', label: '个性化路径' },
    ],
  },
  {
    index: 'assessment',
    label: '学习评估',
    icon: DataAnalysis,
    children: [
      { path: '/student/assessment', label: '阶段测评' },
      { path: '/student/mistakes', label: '错题本' },
      { path: '/student/report', label: '学习报告' },
    ],
  },
]

const activePath = computed(() => {
  const path = router.currentRoute.value.path
  if (path.startsWith('/student/resources/')) return '/student/resources'
  return path
})

const defaultOpeneds = computed(() =>
  menuGroups
    .filter((group) => group.children.some((item) => item.path === activePath.value))
    .map((group) => group.index),
)

onMounted(() => {
  ui.setReviewMode(false)
  restoreTutorPosition()
  window.addEventListener('resize', keepTutorInViewport)
  if (tutorOpen.value || tutorMinimized.value) {
    void ensureTutorPosition()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', keepTutorInViewport)
  stopTutorDrag()
})

watch(
  () => route.query.tutor,
  async (value) => {
    if (value !== 'open') return
    tutorOpen.value = true
    tutorMinimized.value = false
    void ensureTutorPosition()
    const { tutor: _tutor, ...query } = route.query
    await router.replace({ path: route.path, query, hash: route.hash })
  },
  { immediate: true },
)

function logout() {
  auth.logout()
  router.push('/login')
}

function openDemoFlow() {
  ui.setReviewMode(true)
  router.push('/demo/flow')
}

function openTutor() {
  tutorOpen.value = true
  tutorMinimized.value = false
  void ensureTutorPosition()
}

function minimizeTutor() {
  tutorOpen.value = false
  tutorMinimized.value = true
  void ensureTutorPosition()
}

function closeTutor() {
  tutorOpen.value = false
  tutorMinimized.value = false
}

function getActiveTutorElement() {
  return tutorOpen.value ? tutorWindowRef.value : tutorMinimizedRef.value
}

function clampTutorPosition(position: { x: number; y: number }, element = getActiveTutorElement()) {
  const rect = element?.getBoundingClientRect()
  const width = rect?.width || Math.min(480, Math.max(0, window.innerWidth - 32))
  const height = rect?.height || Math.min(680, Math.max(0, window.innerHeight - 44))
  const maxX = Math.max(TUTOR_VIEWPORT_GAP, window.innerWidth - width - TUTOR_VIEWPORT_GAP)
  const maxY = Math.max(TUTOR_VIEWPORT_GAP, window.innerHeight - height - TUTOR_VIEWPORT_GAP)

  return {
    x: Math.min(Math.max(position.x, TUTOR_VIEWPORT_GAP), maxX),
    y: Math.min(Math.max(position.y, TUTOR_VIEWPORT_GAP), maxY),
  }
}

function defaultTutorPosition(element = getActiveTutorElement()) {
  const rect = element?.getBoundingClientRect()
  const width = rect?.width || Math.min(480, Math.max(0, window.innerWidth - 32))
  const height = rect?.height || Math.min(680, Math.max(0, window.innerHeight - 44))
  const offset = window.innerWidth <= 860 ? 8 : 22

  return clampTutorPosition(
    {
      x: window.innerWidth - width - offset,
      y: window.innerHeight - height - offset,
    },
    element,
  )
}

function restoreTutorPosition() {
  try {
    const stored = JSON.parse(localStorage.getItem(TUTOR_POSITION_KEY) || 'null')
    if (Number.isFinite(stored?.x) && Number.isFinite(stored?.y)) {
      tutorPosition.value = { x: stored.x, y: stored.y }
      tutorPositionReady.value = true
    }
  } catch {
    localStorage.removeItem(TUTOR_POSITION_KEY)
  }
}

function saveTutorPosition() {
  localStorage.setItem(TUTOR_POSITION_KEY, JSON.stringify(tutorPosition.value))
}

async function ensureTutorPosition() {
  await nextTick()
  const element = getActiveTutorElement()
  if (!element) return

  tutorPosition.value = tutorPositionReady.value
    ? clampTutorPosition(tutorPosition.value, element)
    : defaultTutorPosition(element)
  tutorPositionReady.value = true
}

function keepTutorInViewport() {
  if (!tutorPositionReady.value || (!tutorOpen.value && !tutorMinimized.value)) return
  tutorPosition.value = clampTutorPosition(tutorPosition.value)
  saveTutorPosition()
}

function startTutorDrag(event: PointerEvent) {
  if (event.button !== 0 || dragPointerId !== null) return
  event.preventDefault()
  dragPointerId = event.pointerId
  dragStartPointer = { x: event.clientX, y: event.clientY }
  dragStartPosition = { ...tutorPosition.value }
  tutorDragging.value = true
  suppressMinimizedClick.value = false
  window.addEventListener('pointermove', moveTutor)
  window.addEventListener('pointerup', stopTutorDrag)
  window.addEventListener('pointercancel', stopTutorDrag)
}

function moveTutor(event: PointerEvent) {
  if (event.pointerId !== dragPointerId) return
  const deltaX = event.clientX - dragStartPointer.x
  const deltaY = event.clientY - dragStartPointer.y
  if (Math.hypot(deltaX, deltaY) >= TUTOR_DRAG_THRESHOLD) {
    suppressMinimizedClick.value = true
  }
  tutorPosition.value = clampTutorPosition({
    x: dragStartPosition.x + deltaX,
    y: dragStartPosition.y + deltaY,
  })
}

function stopTutorDrag(event?: PointerEvent) {
  if (event && dragPointerId !== null && event.pointerId !== dragPointerId) return
  if (dragPointerId !== null && tutorPositionReady.value) {
    saveTutorPosition()
  }
  dragPointerId = null
  tutorDragging.value = false
  window.removeEventListener('pointermove', moveTutor)
  window.removeEventListener('pointerup', stopTutorDrag)
  window.removeEventListener('pointercancel', stopTutorDrag)
}

function restoreMinimizedTutor() {
  if (suppressMinimizedClick.value) {
    suppressMinimizedClick.value = false
    return
  }
  openTutor()
}
</script>

<template>
  <div class="app-shell student-shell" :class="{ collapsed }">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">智</div>
        <div class="brand-copy">
          <div class="brand-title">智学工坊</div>
          <div class="brand-subtitle">EduAgent Studio</div>
        </div>
        <el-tooltip :content="collapsed ? '展开导航' : '收起导航'" placement="right">
          <el-button class="sidebar-toggle" text @click="collapsed = !collapsed">
            <el-icon><component :is="collapsed ? Expand : Fold" /></el-icon>
          </el-button>
        </el-tooltip>
      </div>

      <el-menu
        :default-active="activePath"
        :default-openeds="defaultOpeneds"
        router
        unique-opened
        class="side-menu"
        :collapse="collapsed"
        :collapse-transition="false"
      >
        <el-sub-menu v-for="group in menuGroups" :key="group.index" :index="group.index">
          <template #title>
            <el-icon><component :is="group.icon" /></el-icon>
            <span>{{ group.label }}</span>
          </template>
          <el-menu-item v-for="item in group.children" :key="item.path" :index="item.path">
            <span>{{ item.label }}</span>
          </el-menu-item>
        </el-sub-menu>
      </el-menu>

      <div class="demo-entry" :class="{ collapsed }">
        <el-tooltip content="查看比赛演示路线" placement="right" :disabled="!collapsed">
          <el-button class="demo-button" plain @click="openDemoFlow">
            <el-icon><DataAnalysis /></el-icon>
            <span>比赛演示</span>
          </el-button>
        </el-tooltip>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="topbar-left">
          <div class="course-context">
            <strong>数据结构课程</strong>
            <span class="muted">高校智能学习工作台</span>
          </div>
        </div>
        <div class="user-block">
          <span class="current-user">当前用户：{{ displayUserName }}</span>
          <el-button size="small" @click="logout">退出</el-button>
        </div>
      </header>
      <router-view />
    </main>

    <el-tooltip content="打开智能辅导" placement="left" :disabled="tutorOpen || tutorMinimized">
      <button
        class="tutor-launcher"
        :class="{ hidden: tutorOpen || tutorMinimized }"
        type="button"
        aria-label="打开智能辅导"
        @click="openTutor"
      >
        <el-icon><Promotion /></el-icon>
        <span>智能辅导</span>
      </button>
    </el-tooltip>

    <section
      v-show="tutorOpen"
      ref="tutorWindowRef"
      class="tutor-window"
      :class="{ dragging: tutorDragging }"
      :style="tutorPositionStyle"
      aria-label="智能辅导窗口"
    >
      <TutorPanel @drag-start="startTutorDrag" @minimize="minimizeTutor" @close="closeTutor" />
    </section>

    <button
      v-if="tutorMinimized"
      ref="tutorMinimizedRef"
      class="tutor-minimized"
      :class="{ dragging: tutorDragging }"
      :style="tutorPositionStyle"
      type="button"
      aria-label="恢复智能辅导窗口"
      @pointerdown="startTutorDrag"
      @click="restoreMinimizedTutor"
    >
      <span class="tutor-minimized-icon"><el-icon><Promotion /></el-icon></span>
      <span>
        <strong>智能辅导</strong>
        <small>点击继续对话</small>
      </span>
    </button>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
  --current-sidebar-width: var(--sidebar-width);
}

.app-shell.collapsed {
  --current-sidebar-width: 72px;
}

.sidebar {
  position: sticky;
  top: 0;
  width: var(--current-sidebar-width);
  height: 100vh;
  flex: 0 0 var(--current-sidebar-width);
  display: flex;
  flex-direction: column;
  padding: 16px 8px;
  background: linear-gradient(180deg, #ffffff 0%, #fff8ed 100%);
  border-right: 1px solid var(--color-border);
  transition: width 0.2s ease, flex-basis 0.2s ease;
  overflow: hidden;
}

.brand {
  display: flex;
  gap: 10px;
  align-items: center;
  min-height: 62px;
  padding: 8px 8px 16px;
}

.sidebar-toggle {
  margin-left: auto;
  width: 30px;
  height: 30px;
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  color: #fff;
  font-weight: 700;
  background: linear-gradient(135deg, #f0a536 0%, var(--color-primary) 100%);
  border-radius: 8px;
  box-shadow: 0 8px 18px rgba(59, 110, 234, 0.22);
}

.brand-title {
  font-weight: 700;
  color: var(--color-text);
}

.brand-subtitle {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.brand-copy {
  min-width: 0;
  white-space: nowrap;
  transition: opacity 0.15s ease;
}

.collapsed .brand {
  display: grid;
  justify-items: center;
  gap: 8px;
  padding-left: 0;
  padding-right: 0;
}

.collapsed .brand-copy {
  width: 0;
  opacity: 0;
  overflow: hidden;
}

.collapsed .sidebar-toggle {
  margin-left: 0;
}

.side-menu {
  border-right: 0;
  background: transparent;
}

.side-menu:not(.el-menu--collapse) {
  width: 100%;
}

.side-menu.el-menu--collapse {
  width: 56px;
}

.side-menu :deep(.el-sub-menu__title) {
  height: 46px;
  margin: 4px 0;
  border-radius: 8px;
  color: var(--color-text);
  font-weight: 600;
}

.side-menu :deep(.el-sub-menu__title:hover),
.side-menu :deep(.el-sub-menu.is-opened > .el-sub-menu__title) {
  color: var(--color-primary-strong);
  background: #eef4ff;
}

.side-menu :deep(.el-menu-item) {
  height: 38px;
  margin: 2px 0 2px 34px;
  border-radius: 8px;
  color: var(--color-text-secondary);
  font-size: 14px;
}

.side-menu :deep(.el-menu-item.is-active) {
  color: var(--color-primary-strong);
  background: #eef4ff;
  font-weight: 600;
}

.side-menu.el-menu--collapse :deep(.el-menu-item),
.side-menu.el-menu--collapse :deep(.el-sub-menu__title) {
  margin-left: 0;
}

.demo-entry {
  margin-top: auto;
  padding: 10px 6px 0;
  border-top: 1px solid var(--color-border);
}

.demo-button {
  width: 100%;
  justify-content: flex-start;
  border-radius: 8px;
}

.demo-button span {
  margin-left: 6px;
}

.demo-entry.collapsed .demo-button {
  justify-content: center;
  padding: 8px;
}

.demo-entry.collapsed .demo-button span {
  display: none;
}

.main {
  flex: 1;
  min-width: 0;
}

.tutor-launcher {
  position: fixed;
  right: 22px;
  bottom: 28px;
  z-index: 30;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  border: 0;
  border-radius: 999px;
  background: linear-gradient(135deg, #f0a536 0%, var(--color-primary) 72%);
  box-shadow: 0 12px 28px rgba(59, 110, 234, 0.32);
  cursor: pointer;
  transition: transform 0.18s ease, opacity 0.18s ease, box-shadow 0.18s ease;
}

.tutor-launcher:hover {
  transform: translateY(-2px);
  box-shadow: 0 16px 32px rgba(59, 110, 234, 0.38);
}

.tutor-launcher.hidden {
  pointer-events: none;
  opacity: 0;
}

.tutor-window {
  position: fixed;
  right: 22px;
  bottom: 22px;
  z-index: 40;
  width: min(480px, calc(100vw - 32px));
  height: min(680px, calc(100vh - 44px));
  overflow: hidden;
  border: 1px solid rgba(59, 110, 234, 0.18);
  border-radius: 18px;
  background: #f7f9fc;
  box-shadow: 0 24px 64px rgba(31, 45, 78, 0.24), 0 8px 24px rgba(59, 110, 234, 0.14);
}

.tutor-window.dragging,
.tutor-minimized.dragging {
  user-select: none;
}

.tutor-minimized {
  position: fixed;
  right: 22px;
  bottom: 22px;
  z-index: 40;
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 188px;
  padding: 10px 14px 10px 10px;
  color: var(--color-text);
  text-align: left;
  border: 1px solid rgba(59, 110, 234, 0.2);
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 16px 38px rgba(31, 45, 78, 0.2);
  cursor: grab;
  touch-action: none;
}

.tutor-minimized.dragging {
  cursor: grabbing;
}

.tutor-minimized-icon {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  color: #fff;
  border-radius: 12px;
  background: linear-gradient(135deg, #f0a536 0%, var(--color-primary) 75%);
}

.tutor-minimized > span:last-child {
  display: grid;
  gap: 2px;
}

.tutor-minimized strong {
  font-size: 14px;
}

.tutor-minimized small {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  height: var(--header-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 24px;
  background: rgba(255, 252, 247, 0.9);
  border-bottom: 1px solid var(--color-border);
  backdrop-filter: blur(12px);
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.course-context {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.course-context strong {
  color: var(--color-text);
}

.user-block {
  display: flex;
  align-items: center;
  gap: 12px;
}

.current-user {
  color: var(--color-text);
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
}

@media (max-width: 860px) {
  .current-user,
  .course-context .muted {
    display: none;
  }

  .tutor-launcher {
    right: 14px;
    bottom: 18px;
  }

  .tutor-window {
    right: 8px;
    bottom: 8px;
    width: calc(100vw - 16px);
    height: calc(100dvh - 16px);
    border-radius: 16px;
  }

  .tutor-minimized {
    right: 14px;
    bottom: 18px;
  }
}
</style>
