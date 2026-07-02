import { apiGet, apiPost } from './client'
import type { User, UserRole } from '../types/common'

export interface LoginResponse {
  access_token: string
  token_type: string
  user: User
}

export interface RegisterPayload {
  username: string
  password: string
  name: string
  role: Extract<UserRole, 'student' | 'teacher'>
  major?: string
  grade?: string
}

export function loginApi(username: string, password = '123456', role?: UserRole) {
  return apiPost<LoginResponse>('/auth/login', { username, password, role })
}

export function registerApi(payload: RegisterPayload) {
  return apiPost<LoginResponse>('/auth/register', payload)
}

export function meApi() {
  return apiGet<User>('/auth/me')
}
