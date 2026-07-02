<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { usePersistentBoolean } from '../composables/usePersistentBoolean'
import { useAuthStore } from '../stores/auth'
import { Cpu, DataAnalysis, DataBoard, Document, Expand, Files, Finished, Fold, Setting } from '@element-plus/icons-vue'

const router = useRouter()
const auth = useAuthStore()
const SIDEBAR_COLLAPSED_KEY = 'eduagent_admin_sidebar_collapsed'
const collapsed = usePersistentBoolean(SIDEBAR_COLLAPSED_KEY)
const activePath = computed(() => router.currentRoute.value.path)
const isAdmin = computed(() => auth.role === 'admin')
const displayUserName = computed(() => auth.user?.username || auth.user?.name || '未登录用户')

const teacherMenu = [
  { path: '/admin/dashboard', label: '教学工作台', icon: DataBoard },
  { path: '/admin/courses', label: '课程管理', icon: Files },
  { path: '/admin/documents', label: '知识库资料', icon: Document },
  { path: '/admin/audit', label: '资源审核', icon: Finished },
  { path: '/admin/analytics', label: '学生分析', icon: DataAnalysis },
  { path: '/admin/tasks', label: '智能体任务', icon: Cpu },
]

const adminMenu = [
  { path: '/admin/dashboard', label: '系统概览', icon: DataBoard },
  { path: '/admin/tasks', label: '智能体任务', icon: Cpu },
  { path: '/admin/model-config', label: '模型与提示词', icon: Setting },
  { path: '/admin/documents', label: '知识库状态', icon: Document },
  { path: '/admin/audit', label: '审核策略', icon: Finished },
]

const menu = computed(() => (isAdmin.value ? adminMenu : teacherMenu))
const consoleTitle = computed(() => (isAdmin.value ? '系统治理与智能体运维' : '课程知识库与资源审核'))
const brandTitle = computed(() => (isAdmin.value ? '平台管理端' : '教学管理端'))
const brandMark = computed(() => (isAdmin.value ? '管' : '教'))
const brandSubtitle = computed(() => (isAdmin.value ? 'Ops & Governance' : 'Knowledge & Audit'))

async function switchToStudent() {
  await auth.quickLogin('student')
  router.push('/student/resource-generate')
}

async function switchRole() {
  if (isAdmin.value) {
    await auth.quickLogin('teacher')
    router.push('/admin/dashboard')
  } else {
    await auth.quickLogin('admin')
    router.push('/admin/model-config')
  }
}
</script>

<template>
  <div class="app-shell admin-shell" :class="{ collapsed }">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">{{ brandMark }}</div>
        <div class="brand-copy">
          <div class="brand-title">{{ brandTitle }}</div>
          <div class="brand-subtitle">{{ brandSubtitle }}</div>
        </div>
      </div>
      <el-menu :default-active="activePath" router class="side-menu" :collapse="collapsed" :collapse-transition="false">
        <el-menu-item v-for="item in menu" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </el-menu-item>
      </el-menu>
    </aside>
    <main class="main">
      <header class="topbar">
        <div class="topbar-left">
          <el-button class="collapse-btn" text @click="collapsed = !collapsed">
            <el-icon><component :is="collapsed ? Expand : Fold" /></el-icon>
          </el-button>
          <div class="topbar-title">
            <strong>{{ consoleTitle }}</strong>
            <el-tag size="small" effect="plain">{{ isAdmin ? '管理员视图' : '教师视图' }}</el-tag>
          </div>
        </div>
        <div class="user-block">
          <span class="current-user">当前用户：{{ displayUserName }}</span>
          <el-button size="small" @click="switchRole">{{ isAdmin ? '教师端' : '管理员端' }}</el-button>
          <el-button size="small" @click="switchToStudent">学生端</el-button>
        </div>
      </header>
      <router-view />
    </main>
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
  padding: 16px 8px;
  background: linear-gradient(180deg, #ffffff 0%, #fbf7ef 100%);
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

.brand-mark {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  color: #fff;
  font-weight: 700;
  background: linear-gradient(135deg, #0f766e 0%, #3b6eea 100%);
  border-radius: 8px;
  box-shadow: 0 8px 18px rgba(15, 118, 110, 0.22);
}

.brand-title {
  font-weight: 700;
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
  justify-content: center;
  padding-left: 0;
  padding-right: 0;
}

.collapsed .brand-copy {
  width: 0;
  opacity: 0;
  overflow: hidden;
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

.side-menu :deep(.el-menu-item) {
  height: 44px;
  margin: 4px 0;
  border-radius: 8px;
  color: var(--color-text-secondary);
  font-weight: 600;
}

.side-menu :deep(.el-menu-item:hover),
.side-menu :deep(.el-menu-item.is-active) {
  color: var(--color-primary-strong);
  background: #eef4ff;
}

.main {
  flex: 1;
  min-width: 0;
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

.topbar-title,
.topbar-left,
.user-block {
  display: flex;
  align-items: center;
  gap: 12px;
}

.topbar-left {
  min-width: 0;
}

.collapse-btn {
  width: 34px;
  height: 34px;
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.current-user {
  color: var(--color-text);
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
}

@media (max-width: 860px) {
  .user-block {
    gap: 8px;
  }

  .current-user {
    display: none;
  }
}
</style>
