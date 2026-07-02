<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import type { UserRole } from '../../types/common'
import { readUserBool } from '../../utils/storage'

type AuthMode = 'login' | 'register'
type RegisterRole = 'student' | 'teacher'

const router = useRouter()
const auth = useAuthStore()

const mode = ref<AuthMode>('login')
const username = ref('')
const password = ref('')
const name = ref('')
const registerRole = ref<RegisterRole>('student')
const major = ref('计算机科学与技术')
const grade = ref('大二')
const isSubmitting = ref(false)

const title = computed(() => (mode.value === 'login' ? '登录账号' : '注册账号'))
const submitText = computed(() => (mode.value === 'login' ? '登录' : '注册并进入系统'))

function entryByRole(role: UserRole) {
  if (role === 'teacher') return '/admin/dashboard'
  if (role === 'admin') return '/admin/model-config'
  return readUserBool('eduagent_onboarded') ? '/student/dashboard' : '/student/onboarding'
}

function switchMode(nextMode: AuthMode) {
  mode.value = nextMode
  if (nextMode === 'login') {
    username.value = ''
    password.value = ''
    return
  }
  username.value = ''
  password.value = ''
  name.value = ''
}

function validateForm() {
  if (!username.value.trim()) {
    ElMessage.warning('请输入用户名')
    return false
  }
  if (!password.value.trim()) {
    ElMessage.warning('请输入密码')
    return false
  }
  if (mode.value === 'register' && !name.value.trim()) {
    ElMessage.warning('请输入姓名')
    return false
  }
  return true
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
}

async function submit() {
  if (!validateForm()) return
  isSubmitting.value = true
  try {
    const user = mode.value === 'register'
      ? await auth.register({
        username: username.value.trim(),
        password: password.value,
        name: name.value.trim(),
        role: registerRole.value,
        major: registerRole.value === 'student' ? major.value.trim() : undefined,
        grade: registerRole.value === 'student' ? grade.value.trim() : undefined,
      })
      : await auth.login(username.value.trim(), password.value)

    ElMessage.success(`${mode.value === 'login' ? '登录' : '注册'}成功`)
    router.push(entryByRole(user.role))
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-shell">
      <header class="brand">
        <div class="brand-mark">智</div>
        <div>
          <strong>智学工坊</strong>
          <span>EduAgent Studio</span>
        </div>
      </header>

      <section class="login-card">
        <div class="card-head">
          <div>
            <h1>{{ title }}</h1>
            <p>{{ mode === 'login' ? '使用账号密码进入对应工作台。' : '创建账号后进入对应身份通道。' }}</p>
          </div>
          <div class="mode-switch">
            <button type="button" :class="{ active: mode === 'login' }" @click="switchMode('login')">
              登录
            </button>
            <button type="button" :class="{ active: mode === 'register' }" @click="switchMode('register')">
              注册
            </button>
          </div>
        </div>

        <div class="form-area">
          <label v-if="mode === 'register'" class="form-field">
            <span>姓名</span>
            <el-input v-model="name" size="large" placeholder="请输入姓名" />
          </label>

          <label v-if="mode === 'register'" class="form-field">
            <span>职业身份</span>
            <el-segmented
              v-model="registerRole"
              :options="[
                { label: '学生', value: 'student' },
                { label: '教师', value: 'teacher' },
              ]"
            />
          </label>

          <label class="form-field">
            <span>用户名</span>
            <el-input v-model="username" size="large" placeholder="请输入用户名" />
          </label>

          <label class="form-field">
            <span>密码</span>
            <el-input v-model="password" size="large" type="password" show-password placeholder="请输入密码" />
          </label>

          <template v-if="mode === 'register' && registerRole === 'student'">
            <label class="form-field">
              <span>专业</span>
              <el-input v-model="major" size="large" placeholder="例如：计算机科学与技术" />
            </label>

            <label class="form-field">
              <span>年级</span>
              <el-input v-model="grade" size="large" placeholder="例如：大二" />
            </label>
          </template>
        </div>

        <el-button class="primary-action" type="primary" size="large" :loading="isSubmitting" @click="submit">
          {{ submitText }}
        </el-button>
      </section>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 32px;
  background:
    radial-gradient(circle at 50% 0%, rgba(37, 99, 235, 0.08), transparent 34%),
    linear-gradient(135deg, #f8fafc, #eef2ff 58%, #ecfeff);
}

.login-shell {
  width: min(100%, 480px);
  display: grid;
  gap: 18px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  color: #fff;
  font-weight: 800;
  background: var(--color-primary);
  border-radius: 10px;
}

.brand strong,
.brand span {
  display: block;
}

.brand strong {
  font-size: 20px;
}

.brand span,
.card-head p {
  color: var(--color-text-secondary);
}

.login-card {
  display: grid;
  gap: 20px;
  padding: 32px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 24px 70px rgba(15, 23, 42, 0.12);
}

.card-head {
  display: grid;
  gap: 16px;
}

.card-head h1 {
  margin: 0 0 8px;
  font-size: 28px;
  line-height: 1.2;
}

.card-head p {
  margin: 0;
  line-height: 1.7;
}

.mode-switch {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  padding: 4px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.mode-switch button {
  height: 36px;
  cursor: pointer;
  color: var(--color-text-secondary);
  font-weight: 700;
  border: 0;
  border-radius: 6px;
  background: transparent;
}

.mode-switch button.active {
  color: var(--color-primary);
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
}

.form-area {
  display: grid;
  gap: 14px;
}

.form-field {
  display: grid;
  gap: 7px;
}

.form-field span {
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 600;
}

.primary-action {
  width: 100%;
}

@media (max-width: 640px) {
  .login-page {
    padding: 20px;
  }

  .login-card {
    padding: 24px;
  }
}
</style>
