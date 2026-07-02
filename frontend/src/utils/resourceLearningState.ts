import type { LearningResource } from '../types/common'

export type ResourceLearningStatus = 'pending' | 'learning' | 'completed' | 'mastered'

export interface ResourceLearningState {
  status: ResourceLearningStatus
  label: '待学习' | '学习中' | '已学完' | '已掌握'
  canStart: boolean
  canComplete: boolean
  canMaster: boolean
  startLabel: '开始学习' | '正在学习' | '已学完' | '已掌握'
  completeLabel: '标记已学完' | '已学完' | '已掌握'
  masterLabel: '确认掌握' | '已掌握'
  startDisabledReason: string
  completeDisabledReason: string
  masterDisabledReason: string
}

export function getResourceLearningState(resource?: LearningResource | null): ResourceLearningState {
  const canStudy = resource?.auditStatus === 'passed'
  const status: ResourceLearningStatus = resource?.isMastered
    ? 'mastered'
    : resource?.isCompleted
      ? 'completed'
      : resource?.isViewed
        ? 'learning'
        : 'pending'

  const label = {
    pending: '待学习',
    learning: '学习中',
    completed: '已学完',
    mastered: '已掌握',
  }[status] as ResourceLearningState['label']

  const canStart = Boolean(canStudy && status === 'pending')
  const canComplete = Boolean(canStudy && status === 'learning')
  const canMaster = Boolean(canStudy && status === 'completed')

  return {
    status,
    label,
    canStart,
    canComplete,
    canMaster,
    startLabel: status === 'pending' ? '开始学习' : status === 'learning' ? '正在学习' : status === 'completed' ? '已学完' : '已掌握',
    completeLabel: status === 'mastered' ? '已掌握' : status === 'completed' ? '已学完' : '标记已学完',
    masterLabel: status === 'mastered' ? '已掌握' : '确认掌握',
    startDisabledReason: !canStudy
      ? '当前资源尚未通过审核'
      : status === 'learning'
        ? '当前资源正在学习中'
        : status === 'completed'
          ? '当前资源已经学完'
          : status === 'mastered'
            ? '当前资源已经确认掌握'
            : '',
    completeDisabledReason: !canStudy
      ? '当前资源尚未通过审核'
      : status === 'pending'
        ? '请先开始学习当前资源'
        : status === 'completed'
          ? '当前资源已经学完'
          : status === 'mastered'
            ? '当前资源已经确认掌握'
            : '',
    masterDisabledReason: !canStudy
      ? '当前资源尚未通过审核'
      : status === 'pending' || status === 'learning'
        ? '请先标记当前资源已学完'
        : status === 'mastered'
          ? '当前资源已经确认掌握'
          : '',
  }
}
