import { apiGet } from './client'
import type { KnowledgeGraphData, KnowledgeGraphNode, KnowledgeRemedialStep } from '../types/knowledgeGraph'

export function getKnowledgeGraphApi(courseId = 'course_data_structure') {
  return apiGet<KnowledgeGraphData>(`/knowledge/graph?course_id=${encodeURIComponent(courseId)}`)
}

export function getKnowledgeNodeApi(nodeId: string, courseId = 'course_data_structure') {
  return apiGet<KnowledgeGraphNode & {
    upstream: unknown[]
    downstream: unknown[]
    remedialPath: KnowledgeRemedialStep[]
  }>(`/knowledge/nodes/${encodeURIComponent(nodeId)}?course_id=${encodeURIComponent(courseId)}`)
}

export function getKnowledgeRemedialPathApi(nodeId: string, courseId = 'course_data_structure') {
  return apiGet<{ targetNodeId: string; steps: KnowledgeRemedialStep[] }>(
    `/knowledge/nodes/${encodeURIComponent(nodeId)}/remedial-path?course_id=${encodeURIComponent(courseId)}`,
  )
}
