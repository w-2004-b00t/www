import type { LearningResource, ResourceType } from '../types/common'

export const RESOURCE_TYPE_LABELS: Record<ResourceType, string> = {
  explanation: '讲解文档',
  mindmap: '思维导图',
  exercise: '练习题',
  reading: '拓展阅读',
  lab: '实操案例',
  video_script: '视频演示',
}

export const RESOURCE_TYPE_DEFAULTS: Record<ResourceType, { difficulty: string; minutes: number; fit: string }> = {
  explanation: { difficulty: '基础', minutes: 12, fit: '适合先补概念和公式直觉' },
  mindmap: { difficulty: '基础', minutes: 8, fit: '适合先建立知识结构' },
  exercise: { difficulty: '进阶', minutes: 18, fit: '适合巩固薄弱点和测评前练习' },
  reading: { difficulty: '综合', minutes: 20, fit: '适合复盘和拓展阅读' },
  lab: { difficulty: '综合', minutes: 30, fit: '适合代码实践和实验记录' },
  video_script: { difficulty: '基础', minutes: 10, fit: '适合快速预习和演示复述' },
}

export const RESOURCE_TYPE_OPTIONS = Object.entries(RESOURCE_TYPE_LABELS).map(([value, label]) => ({
  value: value as ResourceType,
  label,
}))

export const DEFAULT_RESOURCE_TYPES: ResourceType[] = [
  'explanation',
  'mindmap',
  'exercise',
  'reading',
  'video_script',
  'lab',
]

export const AUDIT_STATUS_META: Record<
  LearningResource['auditStatus'],
  { label: string; shortLabel: string; type: 'success' | 'warning' | 'danger' | 'info' }
> = {
  passed: { label: '已通过，可学习', shortLabel: '已通过', type: 'success' },
  pending: { label: '等待教师审核', shortLabel: '待审核', type: 'warning' },
  warning: { label: '需教师复核', shortLabel: '需复核', type: 'warning' },
  rejected: { label: '已驳回，需重生成', shortLabel: '已驳回', type: 'danger' },
}

export function getAuditSyncText(resource: LearningResource) {
  if (resource.auditStatus === 'passed') return resource.auditHistory?.length ? '教师已同步到学生端' : '内容审核通过，已同步学生端'
  if (resource.auditStatus === 'warning') return '引用或答案需复核，暂不进入学习路径'
  if (resource.auditStatus === 'rejected') return '教师已驳回，需要重新生成'
  return '等待教师审核后再同步到路径'
}

export function getResourceMetaText(resource: LearningResource) {
  const defaults = RESOURCE_TYPE_DEFAULTS[resource.resourceType]
  return `${defaults.minutes} 分钟 / ${defaults.difficulty} / ${resource.citations.length} 条引用`
}
