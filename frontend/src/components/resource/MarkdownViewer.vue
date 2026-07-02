<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import markdownItKatex from 'markdown-it-katex'
import 'katex/dist/katex.min.css'

const props = defineProps<{ content: string; variant?: 'default' | 'tutorial' }>()

interface TocItem {
  id: string
  level: number
  title: string
}

function slugHeading(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^\u4e00-\u9fffa-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

const toc = computed<TocItem[]>(() => {
  const counts = new Map<string, number>()
  return (props.content || '')
    .split('\n')
    .map((line) => {
      const match = /^(#{2,3})\s+(.+?)\s*$/.exec(line)
      if (!match) return null
      const base = slugHeading(match[2]) || 'section'
      const count = counts.get(base) || 0
      counts.set(base, count + 1)
      return {
        id: count ? `${base}-${count + 1}` : base,
        level: match[1].length,
        title: match[2].replace(/`/g, ''),
      }
    })
    .filter((item): item is TocItem => Boolean(item))
})

const md: MarkdownIt = new MarkdownIt({
  html: false,
  linkify: true,
  highlight(str: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      return `<pre><code class="hljs">${hljs.highlight(str, { language: lang }).value}</code></pre>`
    }
    return `<pre><code class="hljs">${md.utils.escapeHtml(str)}</code></pre>`
  },
}).use(markdownItKatex)

md.renderer.rules.heading_open = (tokens, idx, options, _env, self) => {
  const inline = tokens[idx + 1]
  const title = inline?.content || ''
  const id = slugHeading(title) || `section-${idx}`
  tokens[idx].attrSet('id', id)
  return self.renderToken(tokens, idx, options)
}

const rendered = computed(() => md.render(props.content || ''))
</script>

<template>
  <article class="markdown-doc" :class="{ 'markdown-doc--tutorial': variant === 'tutorial' }">
    <nav v-if="variant === 'tutorial' && toc.length" class="doc-toc" aria-label="文档目录">
      <strong>目录</strong>
      <a
        v-for="item in toc"
        :key="item.id"
        :href="`#${item.id}`"
        :class="`toc-level-${item.level}`"
      >
        {{ item.title }}
      </a>
    </nav>
    <div class="markdown-body" v-html="rendered" />
  </article>
</template>

<style scoped>
.markdown-doc {
  display: grid;
  gap: 18px;
}

.doc-toc {
  display: grid;
  gap: 8px;
  padding: 14px 16px;
  border: 1px solid #dbe3ef;
  border-radius: 8px;
  background: #f8fafc;
}

.doc-toc strong {
  color: var(--color-text-primary);
  font-size: 14px;
}

.doc-toc a {
  color: #315f9d;
  font-size: 14px;
  line-height: 1.5;
  text-decoration: none;
}

.doc-toc a:hover {
  text-decoration: underline;
}

.doc-toc .toc-level-3 {
  padding-left: 14px;
  font-size: 13px;
}

.markdown-body {
  color: var(--color-text-primary);
  font-size: 15px;
  line-height: 1.78;
  overflow-wrap: anywhere;
}

.markdown-body :deep(h1) {
  margin: 0 0 18px;
  font-size: 30px;
  line-height: 1.28;
  letter-spacing: 0;
}

.markdown-body :deep(h2) {
  margin: 28px 0 12px;
  padding-top: 4px;
  font-size: 21px;
  line-height: 1.35;
  letter-spacing: 0;
}

.markdown-body :deep(h3) {
  margin: 20px 0 10px;
  font-size: 17px;
  line-height: 1.45;
  letter-spacing: 0;
}

.markdown-body :deep(p) {
  margin: 0 0 12px;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 0 0 14px;
  padding-left: 22px;
}

.markdown-body :deep(li) {
  margin: 5px 0;
}

.markdown-body :deep(blockquote) {
  margin: 14px 0;
  padding: 10px 14px;
  border-left: 4px solid #2f80ed;
  border-radius: 6px;
  background: #f5f8ff;
  color: var(--color-text-secondary);
}

.markdown-body :deep(code) {
  padding: 2px 5px;
  border-radius: 4px;
  background: #eef2f7;
  color: #334155;
  font-size: 0.92em;
  white-space: pre-wrap;
  word-break: break-word;
}

.markdown-body :deep(pre) {
  max-width: 100%;
  margin: 16px 0;
  padding: 0;
  overflow-x: auto;
  border: 1px solid #1f2937;
  border-radius: 8px;
  background: #111827;
  scrollbar-color: #64748b transparent;
  scrollbar-width: thin;
}

.markdown-body :deep(pre code) {
  display: block;
  min-width: max-content;
  padding: 14px 16px;
  background: transparent;
  color: #e5edf8;
  font-size: 13px;
  line-height: 1.65;
  white-space: pre;
  word-break: normal;
}

.markdown-body :deep(table) {
  width: 100%;
  max-width: 100%;
  margin: 16px 0;
  border-collapse: collapse;
  font-size: 14px;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  padding: 9px 10px;
  border: 1px solid #dbe3ef;
  vertical-align: top;
}

.markdown-body :deep(th) {
  background: #f1f5f9;
  font-weight: 700;
}

.markdown-doc--tutorial .markdown-body {
  max-width: 920px;
  font-size: 16px;
  line-height: 1.85;
}

.markdown-doc--tutorial .markdown-body :deep(h2) {
  margin-top: 34px;
  padding-top: 12px;
  border-top: 1px solid #e5eaf2;
}

.markdown-doc--tutorial .markdown-body :deep(h2:first-of-type) {
  border-top: 0;
  margin-top: 22px;
}
</style>
