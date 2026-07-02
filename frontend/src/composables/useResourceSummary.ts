import { computed, type Ref } from 'vue'
import type { LearningResource } from '../types/common'

export function useResourceSummary(resources: Ref<LearningResource[]> | LearningResource[]) {
  const list = computed(() => (Array.isArray(resources) ? resources : resources.value))
  const passedResources = computed(() => list.value.filter((item) => item.auditStatus === 'passed'))
  const blockedResources = computed(() => list.value.filter((item) => item.auditStatus !== 'passed'))
  const recommendableResources = computed(() => {
    if (passedResources.value.length) return passedResources.value
    return list.value.filter((item) => item.auditStatus !== 'rejected')
  })
  const typePriority: Record<string, number> = {
    mindmap: 6,
    explanation: 5,
    exercise: 4,
    video_script: 3,
    lab: 2,
    reading: 1,
  }
  const scoreResource = (item: LearningResource) => (
    (item.auditStatus === 'passed' ? 100 : item.auditStatus === 'warning' ? 20 : 0)
    + (typePriority[item.resourceType] || 0) * 10
    + Math.round((item.qualityScore || 0) / 5)
    + (item.fitReason ? 6 : 0)
    + Math.min(item.citations?.length || 0, 5) * 2
  )
  const recommendedResource = computed(() => (
    [...recommendableResources.value].sort((left, right) => scoreResource(right) - scoreResource(left))[0]
  ))
  const auditSummary = computed(() => `${passedResources.value.length} 个已通过，${blockedResources.value.length} 个待教师处理`)

  return {
    auditSummary,
    blockedResources,
    passedResources,
    recommendableResources,
    recommendedResource,
  }
}
