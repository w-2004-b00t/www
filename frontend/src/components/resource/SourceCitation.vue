<script setup lang="ts">
import { computed, ref } from 'vue'
import type { SourceCitation } from '../../types/common'

defineProps<{ citations: SourceCitation[] }>()

const active = ref<SourceCitation | null>(null)
const dialogVisible = computed({
  get: () => Boolean(active.value),
  set: (value: boolean) => {
    if (!value) active.value = null
  },
})

function cleanText(value = '') {
  return value
    .replace(/Data-Structure-master\.zip![^\s，。；;]+/gi, '')
    .replace(/eduagent_local_kb_zip_[^\s，。；;]+/gi, '')
    .replace(/edugent_local_kb_zip_[^\s，。；;]+/gi, '')
    .replace(/[A-Za-z]:[\\/][^\s，。；;]+/g, '')
    .replace(/AppData\s+Local\s+Temp/gi, '')
    .replace(/chunk[_A-Za-z0-9-]+/g, '')
    .replace(/\b(return\s+OK|#endif)\b\s*;?/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function shortName(value = '') {
  const cleaned = cleanText(value)
  const parts = cleaned.split(/[\\/!]/)
  const name = parts[parts.length - 1] || cleaned || '课程资料'
  return name.length > 34 ? `${name.slice(0, 16)}...${name.slice(-14)}` : name
}

function sourcePlace(item: SourceCitation) {
  const place = cleanText(item.sourceLocation) || '课程片段'
  return item.page ? `${place} · 第 ${item.page} 页` : place
}

function confidence(item: SourceCitation) {
  if (!item.similarity) return '已引用'
  const score = Math.round(item.similarity * 100)
  if (score >= 70) return '高可信'
  if (score >= 55) return '可参考'
  return '需复核'
}

function preview(item: SourceCitation, limit = 150) {
  const text = cleanText(item.contentPreview || item.fullText || '')
  if (!text) return '该来源已作为课程知识库证据，用于支撑当前资源。'
  return text.length > limit ? `${text.slice(0, limit)}...` : text
}
</script>

<template>
  <div class="citations">
    <div class="citation-title">
      <h3>来源知识引用</h3>
      <span>{{ citations.length }} 条</span>
    </div>
    <el-alert
      v-if="!citations.length"
      type="warning"
      show-icon
      :closable="false"
      title="当前资源没有可展示的课程引用，请重新生成或上传课程资料。"
    />
    <button v-for="item in citations" :key="item.chunkId" class="citation-item" @click="active = item">
      <div class="citation-head">
        <strong :title="cleanText(item.documentName)">{{ shortName(item.documentName) }}</strong>
        <el-tag class="tag-citation" size="small" effect="plain">
          {{ confidence(item) }}
        </el-tag>
      </div>
      <span>{{ sourcePlace(item) }}</span>
      <p>{{ preview(item) }}</p>
    </button>

    <el-dialog v-model="dialogVisible" title="课程原文片段" width="640px">
      <template v-if="active">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="资料">{{ shortName(active.documentName) }}</el-descriptions-item>
          <el-descriptions-item label="位置">{{ sourcePlace(active) }}</el-descriptions-item>
          <el-descriptions-item label="检索可信度">
            {{ active.similarity ? `${Math.round(active.similarity * 100)}%` : '未返回' }}
          </el-descriptions-item>
        </el-descriptions>
        <p class="source-preview">{{ preview(active, 900) }}</p>
        <el-alert type="info" show-icon :closable="false">
          学生正文只展示学习内容；检索分数和内部片段标识仅用于溯源和教师复核。
        </el-alert>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.citations {
  display: grid;
  gap: 10px;
}

.citation-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.citation-title h3 {
  margin: 0;
  font-size: 15px;
}

.citation-title span {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.citation-item {
  width: 100%;
  padding: 12px;
  text-align: left;
  cursor: pointer;
  border: 1px solid #c7d7eb;
  border-radius: 8px;
  background: #f7fbff;
}

.citation-item:hover {
  border-color: var(--color-primary);
}

.citation-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.citation-head strong {
  min-width: 0;
  overflow: hidden;
  color: var(--color-text-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tag-citation {
  flex: 0 0 auto;
}

.citation-item span {
  display: block;
  margin-top: 4px;
  color: var(--color-text-secondary);
  font-size: 13px;
  word-break: break-word;
}

p {
  display: -webkit-box;
  margin: 8px 0 0;
  overflow: hidden;
  color: var(--color-text-secondary);
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.source-preview {
  display: block;
  max-height: 280px;
  padding: 14px;
  overflow: auto;
  border-radius: 8px;
  background: #f8fafc;
  white-space: pre-wrap;
}
</style>
