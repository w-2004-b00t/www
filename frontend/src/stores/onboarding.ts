import { defineStore } from 'pinia'
import { listCoursesApi, type CourseOption } from '../api/course'
import {
  readUserBool,
  readUserJson,
  readUserNumber,
  readUserText,
  removeUserKeys,
  writeUserJson,
  writeUserText,
} from '../utils/storage'

const AI_INTRO_COURSE: CourseOption = {
  id: 'course_data_structure',
  name: '数据结构课程',
  description: '暂无真实课程资料，请教师上传数据结构课程文件后生成学习资源。',
  status: 'active',
}

const STORAGE_PREFIX = 'eduagent_data_structure'

interface OnboardingState {
  completed: boolean
  courses: CourseOption[]
  studyGoal: string
  weakPoint: string
  preference: string[]
  dailyMinutes: number
}

function readScopedState() {
  return {
    completed: readUserBool(`${STORAGE_PREFIX}_onboarded`),
    studyGoal: readUserText(`${STORAGE_PREFIX}_goal`),
    weakPoint: readUserText(`${STORAGE_PREFIX}_weak_point`),
    preference: readUserJson<string[]>(`${STORAGE_PREFIX}_preference`, ['图解', '例题', '代码实践']),
    dailyMinutes: readUserNumber(`${STORAGE_PREFIX}_daily_minutes`, 45),
  }
}

export const useOnboardingStore = defineStore('onboarding', {
  state: (): OnboardingState => ({
    ...readScopedState(),
    courses: [AI_INTRO_COURSE],
  }),
  getters: {
    selectedCourse: (state) => state.courses.find((course) => course.id === AI_INTRO_COURSE.id) || AI_INTRO_COURSE,
    courseId: () => AI_INTRO_COURSE.id,
    isEmptyStart: (state) => !state.completed || !state.studyGoal,
  },
  actions: {
    hydrate() {
      Object.assign(this, readScopedState())
    },
    async loadCourses() {
      this.hydrate()
      try {
        const courses = await listCoursesApi()
        this.courses = courses.filter((course) => course.id === AI_INTRO_COURSE.id)
        if (!this.courses.length) this.courses = [AI_INTRO_COURSE]
      } catch {
        this.courses = [AI_INTRO_COURSE]
      }
      writeUserText(`${STORAGE_PREFIX}_course`, AI_INTRO_COURSE.id)
    },
    selectCourse(courseId: string) {
      if (courseId !== AI_INTRO_COURSE.id) return
      writeUserText(`${STORAGE_PREFIX}_course`, AI_INTRO_COURSE.id)
    },
    complete() {
      this.completed = true
      writeUserText(`${STORAGE_PREFIX}_onboarded`, true)
      writeUserText(`${STORAGE_PREFIX}_course`, AI_INTRO_COURSE.id)
      writeUserText(`${STORAGE_PREFIX}_goal`, this.studyGoal)
      writeUserText(`${STORAGE_PREFIX}_weak_point`, this.weakPoint)
      writeUserJson(`${STORAGE_PREFIX}_preference`, this.preference)
      writeUserText(`${STORAGE_PREFIX}_daily_minutes`, this.dailyMinutes)
    },
    reset() {
      this.completed = false
      this.studyGoal = ''
      this.weakPoint = ''
      removeUserKeys([
        `${STORAGE_PREFIX}_onboarded`,
        `${STORAGE_PREFIX}_goal`,
        `${STORAGE_PREFIX}_weak_point`,
        `${STORAGE_PREFIX}_preference`,
        `${STORAGE_PREFIX}_daily_minutes`,
      ])
      writeUserText(`${STORAGE_PREFIX}_course`, AI_INTRO_COURSE.id)
    },
  },
})
