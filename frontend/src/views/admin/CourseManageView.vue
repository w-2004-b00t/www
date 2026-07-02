<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createCourseChapterApi, listCourseChaptersApi, publishCourseChapterApi, updateCourseChapterApi } from '../../api/course'
import type { CourseChapter, CourseOverview } from '../../types/common'

const courseId = 'course_data_structure'
const chapters = ref<CourseChapter[]>([])
const overview = ref<CourseOverview | null>(null)
const loading = ref(false)
const relationVisible = ref(false)
const selectedChapter = ref<CourseChapter | null>(null)

async function loadChapters() {
  loading.value = true
  try {
    const result = await listCourseChaptersApi(courseId)
    chapters.value = result.chapters
    overview.value = result.overview
  } finally {
    loading.value = false
  }
}

async function addChapter() {
  try {
    const { value } = await ElMessageBox.prompt('请输入章节名称，例如“搜索与启发式算法”', '新增章节', {
      confirmButtonText: '创建章节',
      cancelButtonText: '取消',
      inputPattern: /\S{2,}/,
      inputErrorMessage: '章节名称至少 2 个字符',
    })
    const result = await createCourseChapterApi(courseId, {
      name: value,
      points: [],
      prerequisites: [],
    })
    chapters.value.push(result.chapter)
    overview.value = result.overview
    ElMessage.success('章节已保存到后端，状态为草稿。')
  } catch {
    // 用户取消
  }
}

async function editKnowledge(chapter: CourseChapter) {
  try {
    const { value } = await ElMessageBox.prompt('用顿号或逗号分隔知识点', `编辑知识点：${chapter.name}`, {
      confirmButtonText: '保存知识点',
      cancelButtonText: '取消',
      inputValue: chapter.points.join('、'),
      inputPattern: /\S{2,}/,
      inputErrorMessage: '至少填写一个知识点',
    })
    const points = value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean)
    const result = await updateCourseChapterApi(courseId, chapter.id, {
      points,
      risk: points.length >= 3 ? '知识点已更新，建议继续补充课程引用和例题' : '知识点数量较少，建议继续补充',
    })
    replaceChapter(result.chapter)
    overview.value = result.overview
    ElMessage.success('知识点已写入后端，并更新引用覆盖状态。')
  } catch {
    // 用户取消
  }
}

function showPrerequisites(chapter: CourseChapter) {
  selectedChapter.value = chapter
  relationVisible.value = true
}

async function editPrerequisites() {
  if (!selectedChapter.value) return
  try {
    const availableNames = chapters.value
      .filter((item) => item.id !== selectedChapter.value?.id)
      .map((item) => item.name)
    const { value } = await ElMessageBox.prompt(
      `用顿号或逗号分隔先修章节。可选：${availableNames.join('、')}`,
      `编辑先修关系：${selectedChapter.value.name}`,
      {
        confirmButtonText: '保存关系',
        cancelButtonText: '取消',
        inputValue: selectedChapter.value.prerequisites.join('、'),
      },
    )
    const prerequisites = value
      .split(/[、,，]/)
      .map((item) => item.trim())
      .filter((item) => item && availableNames.includes(item))
    const result = await updateCourseChapterApi(courseId, selectedChapter.value.id, { prerequisites })
    replaceChapter(result.chapter)
    selectedChapter.value = result.chapter
    overview.value = result.overview
    ElMessage.success('先修关系已保存，学生知识图谱和学习路径刷新后生效。')
  } catch {
    // 用户取消
  }
}

async function publishChapter(chapter: CourseChapter) {
  try {
    const result = await publishCourseChapterApi(courseId, chapter.id)
    replaceChapter(result.chapter)
    overview.value = result.overview
    ElMessage.success('章节已发布，学生端生成资源和测评可使用该知识结构。')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '发布失败，请先补充知识点。')
  }
}

function replaceChapter(chapter: CourseChapter) {
  chapters.value = chapters.value.map((item) => (item.id === chapter.id ? chapter : item))
}

onMounted(loadChapters)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">课程管理</h1>
        <p class="page-subtitle">维护《数据结构课程》的章节、知识点和先修关系；修改会同步到学生知识图谱。</p>
      </div>
      <el-button type="primary" @click="addChapter">新增章节</el-button>
    </div>

    <section class="panel course-overview">
      <div>
        <h2 class="section-title">{{ overview?.courseName || '数据结构课程' }}</h2>
        <p class="section-desc">知识结构会影响资源生成、智能问答、个性化学习路径和薄弱点补强测评。</p>
      </div>
      <div class="course-stats">
        <div><strong>{{ overview?.chapterCount || chapters.length }}</strong><span>章节</span></div>
        <div><strong>{{ overview?.knowledgePointCount || 0 }}</strong><span>知识点</span></div>
        <div><strong>{{ overview?.chunkCount || 0 }}</strong><span>知识片段</span></div>
        <div><strong>{{ overview?.citationCoverage || 0 }}%</strong><span>引用覆盖</span></div>
      </div>
    </section>

    <el-alert class="backend-tip" type="success" show-icon :closable="false">
      当前课程章节来自后端接口 /api/admin/courses/{{ courseId }}/chapters；新增、编辑、发布都会持久化到 SQLite。
    </el-alert>

    <div v-loading="loading" class="chapter-grid">
      <article v-for="chapter in chapters" :key="chapter.id" class="chapter-card">
        <div class="chapter-head">
          <div>
            <el-tag :type="chapter.status === '已发布' ? 'success' : chapter.status === '草稿' ? 'warning' : 'info'" effect="plain">
              {{ chapter.status }}
            </el-tag>
            <h3>{{ chapter.name }}</h3>
            <span class="updated-at">更新：{{ chapter.updatedAt }}</span>
          </div>
          <el-progress type="circle" :width="64" :percentage="chapter.progress" />
        </div>
        <div class="point-list">
          <el-tag v-for="point in chapter.points" :key="point" effect="plain">{{ point }}</el-tag>
          <span v-if="!chapter.points.length" class="empty-points">还没有知识点</span>
        </div>
        <el-alert type="warning" show-icon :closable="false">{{ chapter.risk }}</el-alert>
        <div class="chapter-actions">
          <el-button size="small" type="primary" @click="editKnowledge(chapter)">编辑知识点</el-button>
          <el-button size="small" @click="showPrerequisites(chapter)">维护先修关系</el-button>
          <el-button size="small" text @click="publishChapter(chapter)">发布</el-button>
        </div>
      </article>
    </div>

    <el-dialog v-model="relationVisible" title="先修关系" width="560px">
      <template v-if="selectedChapter">
        <h3>{{ selectedChapter.name }}</h3>
        <p class="section-desc">这些先修关系会影响学习路径排序和测评题目难度。</p>
        <div class="relation-list">
          <div v-for="item in selectedChapter.prerequisites" :key="item">
            <span>先修</span>
            <strong>{{ item }}</strong>
          </div>
          <div v-if="!selectedChapter.prerequisites.length">
            <span>状态</span>
            <strong>暂未配置先修关系</strong>
          </div>
        </div>
        <el-button class="relation-edit" type="primary" @click="editPrerequisites">编辑先修关系</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.course-overview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 16px;
}

.backend-tip {
  margin-bottom: 16px;
}

.course-stats {
  display: grid;
  grid-template-columns: repeat(4, 82px);
  gap: 10px;
}

.course-stats div {
  display: grid;
  place-items: center;
  gap: 4px;
  padding: 10px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.course-stats strong {
  font-size: 20px;
}

.course-stats span,
.updated-at,
.empty-points {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.chapter-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.chapter-card {
  display: grid;
  gap: 14px;
  padding: 16px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.chapter-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

h3 {
  margin: 10px 0 0;
  font-size: 18px;
}

.point-list,
.chapter-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.relation-list {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.relation-edit {
  margin-top: 16px;
}

.relation-list div {
  display: grid;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.relation-list span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

@media (max-width: 1200px) {
  .course-overview,
  .chapter-head {
    align-items: stretch;
    flex-direction: column;
  }

  .course-stats,
  .chapter-grid {
    grid-template-columns: 1fr;
  }
}
</style>
