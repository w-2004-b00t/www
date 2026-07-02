<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  confirmAdminDocumentApi,
  listAdminDocumentChunksApi,
  listAdminDocumentsApi,
  parseAdminDocumentApi,
  type AdminDocument,
  type AdminKnowledgeChunk,
} from '../../api/admin'

const docs = ref<AdminDocument[]>([])
const chunks = ref<AdminKnowledgeChunk[]>([])
const loading = ref(false)
const parsing = ref(false)
const drawerVisible = ref(false)
const selectedDoc = ref<AdminDocument | null>(null)
const filename = ref('数据结构课程-课程资料待上传补充讲义.md')
const pastedContent = ref('')

const totalChunks = computed(() => docs.value.reduce((sum, item) => sum + Number(item.chunks || 0), 0))
const maxCoverage = computed(() => Math.max(...docs.value.map((item) => Number(item.coverage || 0)), 0))

async function loadDocs() {
  loading.value = true
  try {
    docs.value = await listAdminDocumentsApi()
  } finally {
    loading.value = false
  }
}

async function parseDocument() {
  parsing.value = true
  try {
    const result = await parseAdminDocumentApi(filename.value, pastedContent.value.trim() || undefined)
    docs.value = result.documents
    chunks.value = result.chunks
    selectedDoc.value = result.document
    drawerVisible.value = true
    ElMessage.success('课程资料已解析入库，知识片段和引用页码已保存到后端。')
  } finally {
    parsing.value = false
  }
}

async function confirmDoc(row: AdminDocument) {
  const result = await confirmAdminDocumentApi(row.id)
  docs.value = result.documents
  ElMessage.success('资料已确认，可作为学生端资源生成和引用溯源依据。')
}

async function showChunks(row: AdminDocument) {
  selectedDoc.value = row
  chunks.value = await listAdminDocumentChunksApi(row.id)
  drawerVisible.value = true
}

onMounted(loadDocs)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">知识库资料</h1>
        <p class="page-subtitle">围绕《数据结构课程》维护真实课程资料，解析后的片段会进入知识检索 Agent 和资源生成引用链。</p>
      </div>
      <el-button type="primary" :loading="parsing" @click="parseDocument">解析并入库课程资料</el-button>
    </div>

    <div class="metrics-row">
      <div class="metric-card">
        <span>资料数量</span>
        <strong>{{ docs.length }}</strong>
      </div>
      <div class="metric-card">
        <span>知识片段</span>
        <strong>{{ totalChunks }}</strong>
      </div>
      <div class="metric-card">
        <span>最高引用覆盖</span>
        <strong>{{ maxCoverage }}%</strong>
      </div>
    </div>

    <div class="document-layout">
      <section class="panel">
        <h2 class="section-title">解析课程资料</h2>
        <p class="section-desc">可粘贴 Markdown/纯文本讲义；如果留空，系统会使用内置《数据结构课程》原创讲义完成初始化。</p>
        <el-input v-model="filename" class="field" placeholder="资料文件名，例如：数据结构课程-课程资料待上传.md" />
        <el-input
          v-model="pastedContent"
          class="field"
          type="textarea"
          :rows="10"
          placeholder="在这里粘贴真实课程讲义内容。留空时使用系统内置原创讲义进行演示初始化。"
        />
        <div class="actions">
          <el-button type="primary" :loading="parsing" @click="parseDocument">解析并写入知识库</el-button>
          <el-button @click="pastedContent = ''">清空内容</el-button>
        </div>
        <el-alert class="hint" type="info" show-icon :closable="false">
          学生端生成的讲解文档、导图、练习题和视频分镜会优先引用已确认资料片段。
        </el-alert>
      </section>

      <section class="panel">
        <h2 class="section-title">入库流程</h2>
        <el-steps direction="vertical" :active="4" finish-status="success" class="steps">
          <el-step title="解析文本结构" description="识别章节、页码、标题、公式、代码块和知识点。" />
          <el-step title="切分知识片段" description="按章节语义切片，保留 chunkId 与来源。" />
          <el-step title="生成向量索引" description="写入 BGE-M3/本地向量兜底和 SQLite 片段表。" />
          <el-step title="支持引用溯源" description="资源内容可定位到文档、章节、页码和原文片段。" />
        </el-steps>
      </section>
    </div>

    <section class="panel doc-table">
      <div class="section-head">
        <div>
          <h2 class="section-title">资料入库状态</h2>
          <p class="section-desc">教师确认后，知识片段才作为学生端正式可信引用来源。</p>
        </div>
      </div>
      <el-table v-loading="loading" :data="docs" empty-text="还没有课程资料，请先解析并入库一份讲义。">
        <el-table-column prop="name" label="资料名称" min-width="260" />
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="row.status === '已入库' || row.status === 'parsed' ? 'success' : 'warning'" effect="plain">
              {{ row.status === 'parsed' ? '已解析' : row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="chunks" label="片段数" width="100" />
        <el-table-column label="引用覆盖" min-width="180">
          <template #default="{ row }">
            <el-progress :percentage="row.coverage" />
          </template>
        </el-table-column>
        <el-table-column prop="issue" label="处理提示" min-width="240" />
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="showChunks(row)">查看片段</el-button>
            <el-button size="small" type="primary" @click="confirmDoc(row)">确认可用</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <el-drawer v-model="drawerVisible" size="52%" :title="selectedDoc?.name || '知识片段'">
      <div class="chunk-list">
        <article v-for="chunk in chunks" :key="chunk.chunkId" class="chunk-card">
          <div class="chunk-top">
            <strong>{{ chunk.section }}</strong>
            <el-tag effect="plain">第 {{ chunk.page }} 页</el-tag>
          </div>
          <p>{{ chunk.content }}</p>
          <div class="chunk-meta">
            <el-tag v-for="keyword in chunk.keywords" :key="keyword" size="small" effect="plain">{{ keyword }}</el-tag>
            <span>{{ chunk.embeddingStatus === 'indexed' ? '已向量化' : '待向量化' }}</span>
          </div>
        </article>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.metrics-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}

.metric-card {
  padding: 16px 18px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.metric-card span {
  display: block;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.metric-card strong {
  display: block;
  margin-top: 8px;
  font-size: 28px;
}

.document-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(320px, 0.95fr);
  gap: 18px;
}

.field,
.hint,
.doc-table {
  margin-top: 14px;
}

.actions {
  display: flex;
  gap: 10px;
  margin-top: 14px;
}

.steps {
  margin-top: 16px;
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.chunk-list {
  display: grid;
  gap: 12px;
}

.chunk-card {
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.chunk-top,
.chunk-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.chunk-card p {
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.chunk-meta {
  justify-content: flex-start;
  flex-wrap: wrap;
  color: var(--color-text-tertiary);
  font-size: 12px;
}

@media (max-width: 1100px) {
  .metrics-row,
  .document-layout {
    grid-template-columns: 1fr;
  }
}
</style>
