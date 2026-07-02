import { defineStore } from 'pinia'
import { loginApi, registerApi, type RegisterPayload } from '../api/auth'
import { demoUsers } from '../data/demoData'
import type { User, UserRole } from '../types/common'
import { readJson, readText, removeAllScopedKeys, removeKeys, writeJson, writeText } from '../utils/storage'

interface AuthState {
  user: User | null
  token: string | null
}

function saveSession(target: AuthState, user: User, token: string) {
  removeAllScopedKeys([
    'eduagent_data_structure_resources',
    'eduagent_data_structure_learning_path',
    'eduagent_data_structure_learning_progress',
  ])
  target.user = user
  target.token = token
  writeJson('eduagent_user', user)
  writeText('eduagent_token', token)
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    user: readJson<User | null>('eduagent_user', null),
    token: readText('eduagent_token') || null,
  }),
  getters: {
    isLoggedIn: (state) => Boolean(state.user && state.token),
    role: (state): UserRole | null => state.user?.role || null,
  },
  actions: {
    async login(username: string, password = '123456', role?: UserRole) {
      const response = await loginApi(username, password, role)
      const user = response.user
      const token = response.access_token
      saveSession(this, user, token)
      return user
    },
    async quickLogin(role: UserRole) {
      const user = demoUsers.find((item) => item.role === role) || demoUsers[0]
      return this.login(user.username, '123456', role)
    },
    async register(payload: RegisterPayload) {
      const response = await registerApi(payload)
      const user = response.user
      const token = response.access_token
      saveSession(this, user, token)
      return user
    },
    logout() {
      this.user = null
      this.token = null
      removeKeys(['eduagent_user', 'eduagent_token'])
    },
  },
})
