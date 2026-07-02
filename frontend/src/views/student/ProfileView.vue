<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import ProfileCharts from '../../components/profile/ProfileCharts.vue'
import { REQUIRED_PROFILE_DIMENSIONS, useProfileStore } from '../../stores/profile'
import type { ProfileUpdateDraft, StudentProfileItem } from '../../types/common'

const profile = useProfileStore()

const confirmedItems = computed(() => profile.profileItems.filter((item) => item.status === 'confirmed'))
const pendingUpdateDrafts = computed(() => profile.updateDrafts.filter((item) => item.status === 'draft'))
const missingItems = computed(() => {
  const existing = new Set(confirmedItems.value.map((item) => item.dimension))
  return REQUIRED_PROFILE_DIMENSIONS.filter((item) => !existing.has(item))
})
const lowConfidenceCount = computed(() => confirmedItems.value.filter((item) => item.confidence < 0.85).length)
const lowConfidenceItems = computed(() => confirmedItems.value.filter((item) => item.confidence < 0.85))
const confirmedItemMap = computed(() => new Map(confirmedItems.value.map((item) => [item.dimension, item])))
const updateDialogVisible = ref(false)
const updateForm = reactive({
  items: REQUIRED_PROFILE_DIMENSIONS.map((dimension) => ({
    dimension,
    selected: false,
    value: '',
    note: '',
  })),
})
const selectedUpdateItems = computed(() => updateForm.items.filter((item) => item.selected))
const sourceText: Record<StudentProfileItem['source'], string> = {
  dialog: '对话',
  assessment: '测评',
  behavior: '行为',
  manual: '手动',
}

const updateSourceText: Record<string, string> = {
  dialog: '对话',
  assessment: '测评',
  behavior: '行为',
  manual: '手动',
  resource_feedback: '资源反馈',
  tutor: '智能辅导',
}

const statusText: Record<StudentProfileItem['status'], string> = {
  draft: '待确认',
  confirmed: '已确认',
  rejected: '已拒绝',
}

const statusType: Record<StudentProfileItem['status'], 'success' | 'warning' | 'danger'> = {
  draft: 'warning',
  confirmed: 'success',
  rejected: 'danger',
}

const loopSteps = [
  { title: '对话更新', desc: '自然语言生成画像草稿' },
  { title: '测评更新', desc: '错题和得分生成更新建议' },
  { title: '推荐调整', desc: '资源类型和难度重新排序' },
  { title: '路径补强', desc: '插入个性化补强任务' },
]

function confidencePercent(item: StudentProfileItem | ProfileUpdateDraft) {
  return Math.round((item.confidence || 0) * 100)
}

function confidenceStatus(item: StudentProfileItem) {
  return item.confidence < 0.85 ? 'warning' : 'success'
}

function sourceLabel(source: StudentProfileItem['source']) {
  return sourceText[source] || source
}

function updateSourceLabel(source: string) {
  return updateSourceText[source] || source
}

function statusLabel(status: StudentProfileItem['status']) {
  return statusText[status] || status
}

function profileStatusType(status: StudentProfileItem['status']) {
  return statusType[status] || 'warning'
}

async function confirmUpdateDraft(id: string) {
  await profile.confirmUpdateDrafts([id])
  ElMessage.success('画像已更新，后续推荐和路径会使用新画像。')
}

async function rejectUpdateDraft(id: string) {
  await profile.rejectUpdateDraft(id)
  ElMessage.success('已暂不更新该画像建议。')
}

function resetUpdateForm(preselectedDimensions: string[] = []) {
  const preselected = new Set(preselectedDimensions)
  updateForm.items.forEach((item) => {
    item.selected = preselected.has(item.dimension)
    item.value = ''
    item.note = ''
  })
}

function openUpdateDialog(preselectedDimensions: string[] = []) {
  resetUpdateForm(preselectedDimensions)
  updateDialogVisible.value = true
}

function currentProfileItem(dimension: string) {
  return confirmedItemMap.value.get(dimension)
}

async function submitManualUpdateDrafts() {
  const items = selectedUpdateItems.value.map((item) => ({
    dimension: item.dimension,
    value: item.value.trim(),
    note: item.note.trim() || undefined,
  }))
  if (!items.length) {
    ElMessage.warning('请先选择需要更新的画像维度。')
    return
  }
  if (items.some((item) => !item.value)) {
    ElMessage.warning('请为已选择的画像维度填写新内容。')
    return
  }

  try {
    await profile.createManualUpdateDrafts(items)
    updateDialogVisible.value = false
    ElMessage.success('已生成画像更新建议，请确认后写入画像。')
  } catch {
    ElMessage.error(profile.lastError || '画像更新建议创建失败，请检查填写内容。')
  }
}

onMounted(() => {
  profile.loadProfile()
  profile.loadUpdateDrafts().catch(() => {})
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">动态学习画像</h1>
        <p class="page-subtitle">
          画像来自对话、测评、资源反馈和智能辅导记录。系统只生成更新建议，必须由学生确认后才会写入画像。
        </p>
      </div>
      <el-button type="primary" :disabled="!confirmedItems.length" @click="openUpdateDialog()">
        更新画像
      </el-button>
    </div>

    <el-alert
      v-if="profile.lastError"
      class="page-alert"
      :type="profile.usedFallback ? 'warning' : 'error'"
      show-icon
      :closable="false"
      :title="profile.lastError"
    />

    <div class="grid-3">
      <div class="panel metric-card">
        <div>
          <div class="stat-value">{{ profile.completeness }}%</div>
          <div class="stat-label">画像完整度</div>
        </div>
        <span class="metric-trend">{{ confirmedItems.length }} / {{ REQUIRED_PROFILE_DIMENSIONS.length }} 个维度</span>
      </div>
      <div class="panel metric-card">
        <div>
          <div class="stat-value">{{ pendingUpdateDrafts.length }}</div>
          <div class="stat-label">待确认更新</div>
        </div>
        <span class="metric-trend">测评、反馈和辅导触发</span>
      </div>
      <div class="panel metric-card">
        <div>
          <div class="stat-value">{{ lowConfidenceCount }}</div>
          <div class="stat-label">低置信项</div>
        </div>
        <span class="metric-trend">需要定期复核</span>
      </div>
    </div>

    <section v-if="pendingUpdateDrafts.length" class="panel profile-update-suggestions">
      <div class="suggestion-head">
        <div>
          <h2 class="section-title">画像更新建议</h2>
          <p class="section-desc">这些建议来自测评、错题、资源反馈或智能辅导。确认后才会写入画像，并影响资源推荐和学习路径。</p>
        </div>
        <el-tag type="warning" effect="plain">{{ pendingUpdateDrafts.length }} 条待确认</el-tag>
      </div>
      <div class="suggestion-grid">
        <article v-for="draft in pendingUpdateDrafts" :key="draft.id" class="suggestion-card">
          <div class="suggestion-meta">
            <el-tag effect="plain">{{ updateSourceLabel(draft.source) }}</el-tag>
            <el-tag type="warning" effect="plain">置信度 {{ confidencePercent(draft) }}%</el-tag>
          </div>
          <h3>{{ draft.dimension }}</h3>
          <p class="draft-change">
            <span>原画像</span>
            <strong>{{ draft.oldValue || '暂无' }}</strong>
          </p>
          <p class="draft-change">
            <span>建议更新</span>
            <strong>{{ draft.newValue || draft.value }}</strong>
          </p>
          <p class="draft-evidence">证据：{{ draft.evidence }}</p>
          <p class="draft-impact">将影响：{{ draft.impact || '资源推荐、学习路径和智能辅导解释方式' }}</p>
          <div class="draft-actions">
            <el-button type="primary" :loading="profile.isUpdatingDrafts" @click="confirmUpdateDraft(draft.id)">
              确认更新画像
            </el-button>
            <el-button plain :loading="profile.isUpdatingDrafts" @click="rejectUpdateDraft(draft.id)">暂不更新</el-button>
          </div>
        </article>
      </div>
    </section>

    <section v-if="missingItems.length" class="panel missing-panel">
      <div>
        <h2 class="section-title">画像仍可补充</h2>
        <p class="section-desc">补齐这些维度后，路径规划和资源推荐会更稳定。</p>
      </div>
      <div class="missing-tags">
        <el-tag v-for="item in missingItems" :key="item" type="warning" effect="plain">{{ item }}</el-tag>
      </div>
    </section>

    <el-skeleton v-if="profile.isLoading" :rows="6" animated>
      <template #template>
        <div class="state-empty">
          <h3>正在读取学习画像</h3>
          <p>正在加载已确认画像、低置信项和测评更新记录。</p>
        </div>
      </template>
    </el-skeleton>
    <section v-else-if="!confirmedItems.length" class="state-empty">
      <h3>还没有已确认画像</h3>
      <p>请先用自然语言描述本次学习目标。系统会生成画像草稿，确认后才用于路径、资源和智能辅导。</p>
      <router-link to="/student/profile-chat">
        <el-button type="primary">建立第一份画像</el-button>
      </router-link>
    </section>

    <template v-else>
      <ProfileCharts
        :items="confirmedItems"
        :required-dimensions="REQUIRED_PROFILE_DIMENSIONS"
        :completeness="profile.completeness"
      />

      <section v-if="lowConfidenceItems.length" class="panel review-panel">
        <div>
          <h2 class="section-title">需要复核的画像项</h2>
          <p class="section-desc">这些维度置信度低于 85%，建议再次对话确认，避免影响资源推荐和路径规划。</p>
        </div>
        <div class="review-tags">
          <el-tag v-for="item in lowConfidenceItems" :key="item.id" type="warning" effect="plain">
            {{ item.dimension }} · {{ confidencePercent(item) }}%
          </el-tag>
        </div>
        <el-button
          type="primary"
          plain
          @click="openUpdateDialog(lowConfidenceItems.map((item) => item.dimension))"
        >
          复核并更新
        </el-button>
      </section>

      <section class="panel profile-table-panel">
        <div class="table-head">
          <div>
            <h2 class="section-title">画像维度明细</h2>
            <p class="section-desc">展开某一行可查看推荐影响、抽取依据、版本和更新时间。</p>
          </div>
          <el-button type="primary" @click="openUpdateDialog()">更新画像</el-button>
        </div>

        <el-table class="profile-table" :data="confirmedItems" row-key="id">
          <el-table-column type="expand" width="46">
            <template #default="{ row }">
              <div class="expanded-detail">
                <div>
                  <span>推荐影响</span>
                  <strong>{{ row.impact || '影响后续学习路径、资源推荐和智能辅导。' }}</strong>
                </div>
                <div>
                  <span>抽取依据</span>
                  <strong>{{ row.reason || '来自已确认画像记录。' }}</strong>
                </div>
                <div>
                  <span>更新时间</span>
                  <strong>v{{ row.version || 1 }} · {{ row.updatedAt }}</strong>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="dimension" label="画像维度" min-width="140" />
          <el-table-column label="当前值" min-width="260" show-overflow-tooltip>
            <template #default="{ row }">
              <strong class="profile-value">{{ row.value }}</strong>
            </template>
          </el-table-column>
          <el-table-column label="置信度" min-width="160">
            <template #default="{ row }">
              <el-progress :percentage="confidencePercent(row)" :stroke-width="8" :status="confidenceStatus(row)" />
            </template>
          </el-table-column>
          <el-table-column label="来源" width="90">
            <template #default="{ row }">
              <el-tag effect="plain">{{ sourceLabel(row.source) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="profileStatusType(row.status)" effect="plain">
                {{ statusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </template>

    <section class="panel history-panel">
      <h2 class="section-title">画像如何影响学习闭环</h2>
      <div class="loop-flow">
        <template v-for="(step, index) in loopSteps" :key="step.title">
          <div class="loop-step">
            <span>{{ index + 1 }}</span>
            <strong>{{ step.title }}</strong>
            <small>{{ step.desc }}</small>
          </div>
          <div v-if="index < loopSteps.length - 1" class="loop-arrow">→</div>
        </template>
      </div>
    </section>

    <el-dialog
      v-model="updateDialogVisible"
      title="更新画像"
      width="860px"
      class="profile-update-dialog"
      destroy-on-close
    >
      <div class="manual-update-list">
        <article v-for="item in updateForm.items" :key="item.dimension" class="manual-update-item">
          <div class="manual-update-check">
            <el-checkbox v-model="item.selected" />
          </div>
          <div class="manual-update-content">
            <header>
              <div>
                <h3>{{ item.dimension }}</h3>
                <p>当前：{{ currentProfileItem(item.dimension)?.value || '暂无画像' }}</p>
              </div>
              <el-tag
                v-if="currentProfileItem(item.dimension)"
                :type="confidenceStatus(currentProfileItem(item.dimension)!)"
                effect="plain"
              >
                {{ confidencePercent(currentProfileItem(item.dimension)!) }}%
              </el-tag>
            </header>
            <el-input
              v-model="item.value"
              :disabled="!item.selected"
              type="textarea"
              :rows="2"
              resize="none"
              placeholder="填写这项画像的新内容"
            />
            <el-input
              v-model="item.note"
              :disabled="!item.selected"
              placeholder="可选：说明为什么要更新"
            />
          </div>
        </article>
      </div>
      <template #footer>
        <div class="dialog-footer">
          <span>已选择 {{ selectedUpdateItems.length }} 个维度，提交后会进入待确认更新建议。</span>
          <div>
            <el-button @click="updateDialogVisible = false">取消</el-button>
            <el-button type="primary" :loading="profile.isUpdatingDrafts" @click="submitManualUpdateDrafts">
              生成更新建议
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page-alert,
.missing-panel,
.review-panel,
.profile-table-panel,
.history-panel,
.profile-update-suggestions {
  margin-top: 18px;
}

.page-alert {
  margin-bottom: 14px;
}

.profile-update-suggestions {
  display: grid;
  gap: 14px;
  border-color: #fed7aa;
  background: #fffaf5;
}

.suggestion-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.suggestion-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.suggestion-card {
  display: grid;
  gap: 10px;
  padding: 14px;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  background: #fff;
}

.suggestion-card h3 {
  margin: 0;
  font-size: 17px;
}

.suggestion-meta,
.draft-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.draft-change,
.draft-evidence,
.draft-impact {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.draft-change {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 8px;
}

.draft-change span {
  color: #94a3b8;
}

.draft-change strong {
  color: var(--color-text-primary);
}

.draft-impact {
  padding: 10px 12px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
  color: #1d4ed8;
}

.missing-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.missing-tags,
.review-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.review-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 14px;
}

.review-tags {
  justify-content: flex-end;
}

.table-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.profile-table {
  --el-table-border-color: #e2e8f0;
  --el-table-header-bg-color: #f8fafc;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
}

.profile-value {
  font-weight: 600;
}

.manual-update-list {
  display: grid;
  gap: 12px;
  max-height: min(620px, 70vh);
  overflow-y: auto;
  padding-right: 4px;
}

.manual-update-item {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.manual-update-check {
  padding-top: 3px;
}

.manual-update-content {
  display: grid;
  gap: 10px;
}

.manual-update-content header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.manual-update-content h3 {
  margin: 0 0 4px;
  font-size: 16px;
}

.manual-update-content p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.dialog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.dialog-footer span {
  color: var(--color-text-secondary);
}

.expanded-detail {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  padding: 12px 12px 12px 46px;
  background: #f8fafc;
}

.expanded-detail div {
  display: grid;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.expanded-detail span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.expanded-detail strong {
  color: var(--color-text-primary);
  font-size: 13px;
  line-height: 1.6;
}

.loop-flow {
  display: flex;
  align-items: stretch;
  gap: 10px;
  margin-top: 14px;
}

.loop-step {
  flex: 1;
  display: grid;
  gap: 6px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.loop-step span {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  color: #2563eb;
  font-size: 12px;
  font-weight: 700;
  border: 1px solid #bfdbfe;
  border-radius: 999px;
  background: #eff6ff;
}

.loop-step small {
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.loop-arrow {
  display: grid;
  place-items: center;
  color: #94a3b8;
  font-size: 18px;
  font-weight: 700;
}

@media (max-width: 900px) {
  .suggestion-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .missing-panel,
  .review-panel,
  .table-head,
  .suggestion-head {
    align-items: stretch;
    flex-direction: column;
  }

  .review-panel {
    grid-template-columns: 1fr;
  }

  .review-tags {
    justify-content: flex-start;
  }

  .expanded-detail {
    grid-template-columns: 1fr;
    padding-left: 12px;
  }

  .dialog-footer,
  .manual-update-content header {
    align-items: stretch;
    flex-direction: column;
  }

  .loop-flow {
    flex-direction: column;
  }

  .loop-arrow {
    display: none;
  }
}
</style>
