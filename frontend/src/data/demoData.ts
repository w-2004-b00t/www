import type { AssessmentQuestion, LearningPath, LearningResource, User } from '../types/common'

export const demoUsers: User[] = [
  { id: 'user_student_demo', username: 'student_demo', name: '学生体验账号', role: 'student', major: '计算机类', grade: '大二' },
  { id: 'user_teacher_demo', username: 'teacher_demo', name: '课程教师', role: 'teacher' },
  { id: 'user_admin_demo', username: 'admin_demo', name: '系统管理员', role: 'admin' },
]

export const demoResources: LearningResource[] = []

export const demoLearningPath: LearningPath = {
  id: 'path_real_data_required',
  title: '暂无法生成正式学习路径',
  summary: '系统未获得足够真实课程依据，因此不会使用静态样例或假数据生成路径。',
  stages: [],
  intensity: '60min',
  adjustmentHistory: [],
  profileBasis: [],
  initialReason: '请先上传真实课程资料，并完成 DeepSeek 资源生成与路径规划。',
  status: 'blocked',
  generationMode: 'strict_real_data',
  llmStatus: 'skipped',
  sourceCitations: [],
}

export const demoQuestions: AssessmentQuestion[] = []
