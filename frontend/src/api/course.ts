import { apiGet, apiPost } from './client'
import type { CourseChapter, CourseOverview } from '../types/common'

export interface CourseOption {
  id: string
  name: string
  description: string
  status: string
}

export function listCoursesApi() {
  return apiGet<CourseOption[]>('/courses')
}

export function listCourseChaptersApi(courseId: string) {
  return apiGet<{ overview: CourseOverview; chapters: CourseChapter[] }>(`/admin/courses/${courseId}/chapters`)
}

export function createCourseChapterApi(courseId: string, payload: { name: string; points: string[]; prerequisites: string[] }) {
  return apiPost<{ overview: CourseOverview; chapter: CourseChapter }>(`/admin/courses/${courseId}/chapters`, payload)
}

export function updateCourseChapterApi(
  courseId: string,
  chapterId: string,
  payload: { name?: string; points?: string[]; prerequisites?: string[]; risk?: string },
) {
  return apiPost<{ overview: CourseOverview; chapter: CourseChapter }>(`/admin/courses/${courseId}/chapters/${chapterId}`, payload)
}

export function publishCourseChapterApi(courseId: string, chapterId: string) {
  return apiPost<{ overview: CourseOverview; chapter: CourseChapter }>(`/admin/courses/${courseId}/chapters/${chapterId}/publish`, {})
}
