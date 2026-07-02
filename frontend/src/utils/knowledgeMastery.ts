import type { KnowledgeGraphNode, KnowledgeMasteryStatus } from '../types/knowledgeGraph'

export interface KnowledgeMasteryMeta {
  value: KnowledgeMasteryStatus
  label: string
  color: string
}

export const knowledgeMasteryOptions: KnowledgeMasteryMeta[] = [
  { value: 'unlearned', label: '未学习', color: '#94a3b8' },
  { value: 'weak', label: '薄弱', color: '#ef6461' },
  { value: 'learning', label: '学习中', color: '#f59e0b' },
  { value: 'mastered', label: '已掌握', color: '#22a06b' },
]

export const knowledgeMasteryMeta = Object.fromEntries(
  knowledgeMasteryOptions.map((item) => [item.value, item]),
) as Record<KnowledgeMasteryStatus, KnowledgeMasteryMeta>

export function getKnowledgeMasteryStatus(
  node: Pick<KnowledgeGraphNode, 'mastery' | 'masteryStatus'>,
): KnowledgeMasteryStatus {
  if (node.masteryStatus) return node.masteryStatus
  const mastery = Math.max(0, Math.min(100, Number(node.mastery) || 0))
  if (mastery >= 75) return 'mastered'
  if (mastery >= 60) return 'learning'
  if (mastery > 0) return 'weak'
  return 'unlearned'
}

export function getKnowledgeMasteryMeta(
  node: Pick<KnowledgeGraphNode, 'mastery' | 'masteryStatus'>,
): KnowledgeMasteryMeta {
  return knowledgeMasteryMeta[getKnowledgeMasteryStatus(node)]
}
