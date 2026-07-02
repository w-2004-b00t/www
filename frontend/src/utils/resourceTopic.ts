const RESOURCE_TOPIC_SUFFIXES = [
  '完整思维导图',
  '完整导图',
  '思维导图',
  '分层练习题',
  '练习题',
  '视频演示方案',
  '三分钟视频脚本',
  '视频演示',
  '拓展阅读路径',
  '拓展阅读',
  '代码实践实验',
  '代码实验',
  '实操案例',
  '代码案例',
  '讲解文档',
  '知识结构',
]

const RESOURCE_TOPIC_SUFFIX_PATTERN = new RegExp(RESOURCE_TOPIC_SUFFIXES.join('|'), 'g')

export function cleanGenerationTopic(value: unknown, fallback = '线性表') {
  let text = String(value || '').trim()
  for (let index = 0; index < 8; index += 1) {
    const next = text.replace(RESOURCE_TOPIC_SUFFIX_PATTERN, '').replace(/\s+/g, ' ').trim()
    if (next === text) break
    text = next
  }
  return text || fallback
}

export function cleanGenerationTarget(value: unknown, topic: string) {
  const raw = String(value || '').trim()
  if (!raw) return `重新生成资料：围绕${topic}生成学习资料`
  const cleaned = raw.replace(RESOURCE_TOPIC_SUFFIX_PATTERN, '').replace(/\s+/g, ' ').trim()
  const suffixCount = RESOURCE_TOPIC_SUFFIXES.reduce((count, suffix) => count + (raw.split(suffix).length - 1), 0)
  if (!cleaned || suffixCount >= 2 || raw.startsWith('重新生成资料：')) {
    return `重新生成资料：围绕${topic}生成学习资料`
  }
  return cleaned
}
