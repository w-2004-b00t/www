export type KnowledgeNodeType = 'course' | 'chapter' | 'concept' | 'operation' | 'application'
export type KnowledgeMasteryStatus = 'unlearned' | 'weak' | 'learning' | 'mastered'
export type KnowledgeDifficulty = '基础' | '进阶' | '综合'
export type KnowledgeRelationType = 'contains' | 'prerequisite' | 'related' | 'applies_to' | 'supports'

export interface KnowledgeSourceRef {
  documentId?: string
  documentName: string
  sourceLocation?: string
  chunkId?: string
  page?: number
  contentPreview?: string
}

export interface KnowledgeGraphNode {
  id: string
  name: string
  type: KnowledgeNodeType
  description: string
  chapterId?: string
  difficulty?: KnowledgeDifficulty
  importance?: number
  estimatedMinutes?: number
  mastery?: number
  masteryStatus?: KnowledgeMasteryStatus
  masteryEvidence?: string[]
  masteryBreakdown?: {
    pathScore: number
    assessmentScore: number | null
    finalScore: number
    matchedStageIds: string[]
    matchedStageNames: string[]
    matchedResourceIds: string[]
    assessmentCount: number
    formula: string
    evidence: string[]
  }
  symbolSize?: number
  source?: string
  sourceRefs?: KnowledgeSourceRef[]
  resourceCount?: number
  resourceIds?: string[]
  tags?: string[]
}

export interface KnowledgeGraphEdge {
  id?: string
  source: string
  target: string
  relation: string
  type?: KnowledgeRelationType
  weight?: number
  direction?: 'directed' | 'undirected'
  sourceType?: string
  verified?: boolean
  autoManaged?: boolean
}

export interface KnowledgeGraphData {
  courseId: string
  courseName: string
  updatedAt: string
  nodes: KnowledgeGraphNode[]
  edges: KnowledgeGraphEdge[]
  stats?: {
    nodeCount: number
    edgeCount: number
    sourceCoverage: number
    masteryDistribution: Partial<Record<KnowledgeMasteryStatus, number>>
    orphanCount: number
    unverifiedEdgeCount: number
  }
  relationTypes?: { value: KnowledgeRelationType; label: string }[]
}

export interface KnowledgeRemedialStep {
  order: number
  nodeId: string
  name: string
  mastery: number
  masteryStatus: KnowledgeMasteryStatus
  estimatedMinutes: number
  reason: string
}
