export type UserRole = 'student' | 'teacher' | 'admin' | 'demo'

export interface User {
  id: string
  username: string
  name: string
  role: UserRole
  major?: string
  grade?: string
}

export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface SourceCitation {
  documentId: string
  documentName: string
  sourceLocation: string
  chunkId: string
  contentPreview: string
  page?: number
  similarity?: number
  fullText?: string
}

export type MindMapSourceType = '课程依据' | '模型推断' | '测评薄弱点'

export interface MindMapNode {
  id?: string
  nodeId: string
  title: string
  summary?: string
  level: number
  parentId?: string | null
  children: MindMapNode[]
  sourceType: MindMapSourceType
  sourceChunkIds: string[]
  sourceEvidence?: SourceCitation[]
  jumpTarget?: string
  confidence: number
  status: 'draft' | 'confirmed' | 'low_confidence' | 'needs_review'
  downstreamImpact?: string[]
}

export interface MindMapPayload {
  resourceId: string
  title: string
  course: string
  sourceAgent: string
  auditStatus: LearningResource['auditStatus']
  mermaid: string
  tree: MindMapNode
  nodeSchema: string[]
  layoutEngine: {
    name: string
    features: string[]
  }
  coverage: string[]
  citations: SourceCitation[]
  actions: string[]
  markdown: string
}

export interface VideoDemoScene {
  id: string
  time: string
  timeRange: string
  title: string
  screenTitle?: string
  screenText: string
  description: string
  voiceover: string
  narration: string
  keyConcepts: string[]
  concept: string
  recordingSteps: string[]
  teachingGoal?: string
  coreExplanation?: string
  visualModel?: {
    type?: string
    description?: string
    data?: Record<string, unknown>
  }
  exampleData?: Record<string, unknown>
  operationSteps?: string[]
  formulaOrComplexity?: string
  studentTask?: string
  kind: 'intro' | 'structure' | 'operation' | 'complexity' | 'practice' | string
  citationChunkIds?: string[]
  agentEvidence?: string
}

export interface VideoDemoPayload {
  resourceId: string
  title: string
  course: string
  topic: string
  sourceAgent: string
  agentModel: string
  generationMode: string
  sourceType?: string
  generatedBy: string
  videoUrl?: string
  videoMimeType?: string
  videoDurationSeconds?: number
  videoRenderer?: string
  videoProvider?: string
    videoGenerated?: boolean
    videoStatus?: VideoDemoJobStatus
    videoError?: string | null
    currentAttempt?: VideoDemoJob | null
    schemaVersion?: string
    isCurrentVideo?: boolean
  auditStatus: LearningResource['auditStatus']
  script: string
  timeline: { timeRange: string; title: string }[]
  subtitles: { timeRange: string; text: string }[]
  scenes: VideoDemoScene[]
  citations: SourceCitation[]
  referenceSummary: string
  agentTrace: string[]
    productionNotes: string[]
    videoJob?: VideoDemoJob | null
    personalizationEvidence?: {
      userId?: string
      profileDimensions?: number
      weakPoints?: string
      resourcePreference?: string
      learningGoal?: string
      practiceLevel?: string
      activeStage?: string
      activeTasks?: string[]
      latestScore?: number | null
      assessmentWeakness?: string[]
      recentMistakes?: { knowledge?: string; wrongReason?: string; fixTask?: string }[]
    }
    llmStatus: {
    enabled: boolean
    usedLLM: boolean
    fallback: boolean
    model: string
    error?: string | null
  }
}

export type VideoDemoJobStatus =
  | 'idle'
  | 'storyboard_generating'
  | 'submitting'
  | 'queued'
  | 'rendering'
  | 'retry_wait'
  | 'downloading'
  | 'validating'
  | 'composing'
  | 'verifying'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'orphaned'

export type VideoRenderMode = 'agnes_clip' | 'animated_lesson' | 'full_hybrid'

export interface VideoRenderProfile {
  width: number
  height: number
  fps: number
  durationSeconds: number
  timeoutSeconds?: number
}

export interface VideoDemoJob {
  jobId: string
  resourceId: string
  userId?: string
  status: VideoDemoJobStatus
  provider: string
  providerTaskId?: string | null
  providerVideoId?: string | null
  providerStatus?: string
  providerProgress?: number
  providerResponseSummary?: Record<string, unknown> | null
  phase?: VideoDemoJobStatus
  retryable?: boolean
  retryCount?: number
  transientErrorCount?: number
  nextRetryAt?: string | null
  lastProviderContactAt?: string | null
  lastTransientError?: string | null
  stageTimings?: Record<string, { startedAt?: string; finishedAt?: string }>
  segmentProgress?: Record<string, number>
  reuseReason?: 'new_job' | 'running_job' | 'completed_video'
  generator?: string
  modelName?: string
  sourceType?: string
  generationMode?: string
  renderMode?: VideoRenderMode
  renderProfile?: VideoRenderProfile
  visualQuality?: 'script_like' | 'animated_lesson' | 'hybrid_video'
  storyboardLeakageScore?: number
  compositionStartedAt?: string | null
  lastHeartbeatAt?: string | null
  compositionTimeoutSeconds?: number
  compositionStage?: string
  llmStatus?: VideoDemoPayload['llmStatus']
  agentTrace?: string[]
  productionNotes?: string[]
  ffmpegNormalized?: boolean
  title?: string
  topic?: string
  videoUrl?: string | null
  fallbackVideoUrl?: string | null
  fallbackReason?: string | null
  compositionWarning?: string | null
  isPreviewVideo?: boolean
  videoMimeType?: string
  videoDurationSeconds?: number | null
  progress?: number
  stageMessage?: string
  error?: string | null
  errorCode?: string | null
  errorDetail?: string | null
  renderLogTail?: string[]
  schemaVersion?: string
  generationAttemptId?: string
  contentHash?: string
  sourceCitationIds?: string[]
  isCurrentVideo?: boolean
  remoteVideoUrl?: string | null
  downloadedAt?: string | null
  pollCount?: number
  generatedAt?: string | null
  createdAt?: string
  updatedAt?: string
  startedAt?: string | null
  finishedAt?: string | null
}

export interface StudentProfileItem {
  id: string
  dimension: string
  value: string
  confidence: number
  source: 'dialog' | 'behavior' | 'assessment' | 'manual'
  status: 'draft' | 'confirmed' | 'rejected'
  updatedAt: string
  reason?: string
  impact?: string
  version?: number
}

export interface ProfileUpdateDraft {
  id: string
  dimension: string
  oldValue?: string
  value: string
  newValue?: string
  source: 'dialog' | 'behavior' | 'assessment' | 'manual' | string
  trigger: string
  evidence: string
  confidence: number
  status: 'draft' | 'confirmed' | 'rejected'
  impact?: string
  createdAt?: string
  updatedAt?: string
}

export interface AgentStep {
  name: string
  title: string
  status: 'pending' | 'running' | 'success' | 'failed'
  summary: string
  inputSummary?: string
  outputSummary?: string
  tools?: string[]
  responsibility?: string
  confidence?: number
  auditStatus?: string
  durationMs?: number
  citations?: SourceCitation[]
  structuredOutput?: Record<string, unknown>
  errorReason?: string
  handoff?: {
    from?: string
    to?: string
    fields: string[]
    rule?: string
  }
  failureCases?: string[]
  retryStrategy?: string
  downstreamImpact?: string[]
  evidence?: {
    title: string
    value: string
    type?: 'input' | 'tool' | 'citation' | 'output' | 'risk' | 'handoff'
  }[]
  /** Backward compatibility for locally cached tasks created before downstreamImpact was introduced. */
  affects?: string[]
}

export interface GenerationErrorDetail {
  code?: string
  message?: string
  detail?: string
  agentName?: string
  reasonCode?: string
  model?: string
  rawFailure?: string
  missingRequirements?: string[]
  suggestedActions?: string[]
  retrievalNoiseSummary?: {
    chunkId?: string
    documentName?: string
    reasons?: string[]
    preview?: string
  }[]
  attempts?: {
    stage?: string
    reasonCode?: string
    message?: string
  }[]
}

export interface GenerationTaskOutput {
  resource_count?: number
  audit_passed?: number
  audit_warning?: number
  resourceIds?: string[]
  passedResourceIds?: string[]
  resources?: LearningResource[]
  learningPath?: LearningPath
  failed_agent?: string | null
  failure_reason?: string | null
  error?: string
  errorDetail?: GenerationErrorDetail
  next_actions?: string[]
  audit_status?: string
  [key: string]: unknown
}

export interface GenerationTask {
  id: string
  taskType: 'profile' | 'resource' | 'path' | 'tutor' | 'assessment'
  status: 'pending' | 'running' | 'success' | 'failed' | 'cancelled'
  progress: number
  currentAgent?: string
  message?: string
  agentSteps: AgentStep[]
  inputPayload?: Record<string, unknown>
  outputPayload?: GenerationTaskOutput
  createdAt?: string
  updatedAt?: string
  durationMs?: number
  estimatedRemainingMs?: number
  courseName?: string
  topic?: string
  profileVersion?: string
  messageProtocol?: string
  executionOrder?: string[]
  agentRuntime?: {
    framework?: string
    collaborationMode?: string
    messageProtocol?: string
    runtimeStatus?: Record<string, unknown>
  }
  events?: {
    agentName?: string
    eventType: string
    payload: Record<string, unknown>
    createdAt?: string
  }[]
}

export interface AuditRecord {
  id: string
  resourceId: string
  status: 'pending' | 'passed' | 'warning' | 'rejected'
  reason: string
  operator: string
  scope: 'student' | 'class'
  createdAt: string
}

export interface ResourceFeedback {
  id: string
  resourceId: string
  type: 'too_hard' | 'incorrect' | 'need_example' | 'helpful'
  note?: string
  createdAt: string
}

export interface PathAdjustmentRecord {
  id: string
  reason: string
  before: string
  after: string
  createdAt: string
  source?: 'profile' | 'assessment' | 'behavior' | 'manual' | 'resource'
  trigger?: string
  evidence?: string[]
  beforePath?: string[]
  afterPath?: string[]
  insertedStageId?: string
}

export interface TutorNote {
  id: string
  title: string
  content: string
  source: 'tutor' | 'manual'
  createdAt: string
}

export type ResourceType = 'explanation' | 'mindmap' | 'exercise' | 'reading' | 'lab' | 'video_script'

export interface LabTraceCase {
  name: string
  steps: string[]
  expected: string
}

export interface LabPracticePlan {
  mission: string
  target?: string
  concepts: string[]
  operations: string[]
  ioSpec: {
    input: string[]
    output: string[]
    stateFields: string[]
  }
  codeMode: 'source' | 'design' | string
  codeSource?: string
  codeExcerpt?: string
  sourceTasks?: string[]
  designTasks?: string[]
  traceCases: LabTraceCase[]
  deliverables: string[]
  acceptance: string[]
}

export interface LearningResource {
  id: string
  title: string
  resourceType: ResourceType
  summary: string
  content: string
  citations: SourceCitation[]
  auditStatus: 'pending' | 'passed' | 'warning' | 'rejected'
  qualityScore: number
  createdAt: string
  version?: number
  versionReason?: string
  fitReason?: string
  vectorScore?: number
  vectorReason?: string
  embeddingProvider?: string
  embeddingModel?: string
  vectorStore?: string
  matchedProfileDimensions?: string[]
  isViewed?: boolean
  isCompleted?: boolean
  isMastered?: boolean
  masteryEvidence?: string[]
  feedback?: ResourceFeedback[]
  auditHistory?: AuditRecord[]
  metadata?: Record<string, unknown>
}

export interface NextLearningTopic {
  chapterId: string
  chapterName: string
  chapterOrder?: number
  topic: string
  knowledgePoints?: string[]
  reason: string
  status: string
  blocked?: boolean
  blockingReason?: string
  source?: string
  evidence?: SourceCitation[]
}

export interface CourseChapter {
  id: string
  courseId: string
  name: string
  status: '已发布' | '草稿' | '建设中'
  progress: number
  points: string[]
  risk: string
  prerequisites: string[]
  citationCoverage: number
  updatedAt: string
}

export interface CourseOverview {
  courseId: string
  courseName: string
  chapterCount: number
  knowledgePointCount: number
  chunkCount: number
  citationCoverage: number
  updatedAt: string
}

export interface ResourcePracticeResult {
  score: number
  correctCount: number
  total: number
  suggestion: string
  mistakesAdded: number
  studentImpact: string
  pathImpact?: string
  reportImpact?: string
  details: {
    index: number
    stem: string
    userAnswer: string
    answer: string
    correct: boolean
    analysis: string
    knowledgePoint: string
  }[]
}

export interface LearningPathStage {
  id: string
  name: string
  days: number
  status: 'pending' | 'active' | 'awaiting_assessment' | 'completed' | 'mastered'
  chapterId?: string
  chapterName?: string
  knowledgePoints: string[]
  resources: string[]
  tasks: string[]
  acceptance: string
  completedTasks?: string[]
  isCompleted?: boolean
  isMastered?: boolean
  masteryEvidence?: string[]
  completionRequirements?: {
    missingResources: { id: string; title: string }[]
    missingExercises: { id: string; title: string }[]
    resourcesCompleted: boolean
    exercisesSubmitted: boolean
    assessmentScore?: number | null
    assessmentPassed: boolean
    assessmentPassScore: number
    stageCompleted: boolean
    allRequiredPointsMastered: boolean
    stageMastered: boolean
  }
  aiReason?: string
  checkpoint?: string
  source?: 'profile' | 'assessment' | 'behavior' | 'manual' | 'resource'
  citationChunkIds?: string[]
}

export interface LearningPath {
  id: string
  title: string
  summary: string
  stages: LearningPathStage[]
  status?: 'blocked' | 'ready' | 'generating' | 'empty' | string
  generationMode?: 'strict_real_data' | 'deepseek_path_planning' | string
  llmStatus?: 'skipped' | 'pending' | 'enhanced' | 'unavailable' | string
  sourceCitations?: SourceCitation[]
  generatedAt?: string | null
  blockingReason?: string
  intensity?: '30min' | '60min' | 'sprint'
  adjustmentHistory?: PathAdjustmentRecord[]
  profileBasis?: string[]
  initialReason?: string
  resourceCoverage?: {
    approvedTotal: number
    linkedTotal: number
    pendingTotal: number
    unlinkedResourceIds: string[]
  }
}

export interface LearningProgressRecord {
  id: string
  source: 'manual' | 'assessment' | 'resource_practice' | 'stage_complete' | string
  viewedResourceIds?: string[]
  completedStageIds: string[]
  completedResourceIds: string[]
  masteredKnowledgePoints: string[]
  masteredResourceIds: string[]
  score?: number | null
  evidence: string[]
  createdAt: string
}

export interface LearningProgress {
  viewedResourceIds: string[]
  completedStageIds: string[]
  completedResourceIds: string[]
  masteredChapterIds: string[]
  masteredKnowledgePoints: string[]
  masteredResourceIds: string[]
  records: LearningProgressRecord[]
}

export interface AssessmentQuestion {
  id: string
  type: 'single' | 'short' | 'calculation' | 'code' | 'case'
  difficulty: '基础' | '进阶' | '综合'
  knowledgePoint: string
  stem: string
  options?: string[]
  answer: string
  analysis: string
  citationChunkId?: string
  citations?: SourceCitation[]
  rubric?: string[]
  scoreWeight?: number
  order?: number
}

export interface AssessmentPaper {
  assessmentId: string
  title: string
  questions: AssessmentQuestion[]
  sourceSummary: string
  createdAt: string
}
