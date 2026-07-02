<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getModelConfigApi, rollbackModelConfigApi, saveModelConfigApi, type ModelConfig } from '../../api/admin'

const activePrompt = ref<'audit' | 'resource' | 'tutor'>('audit')
const config = ref<ModelConfig | null>(null)
const saving = ref(false)

const activePromptLabel = computed(() => {
  if (activePrompt.value === 'audit') return '内容审核'
  if (activePrompt.value === 'resource') return '资源生成'
  return '智能辅导'
})

async function loadConfig() {
  config.value = await getModelConfigApi()
  activePrompt.value = config.value.activePrompt
}

async function saveConfig() {
  if (!config.value) return
  saving.value = true
  try {
    config.value = await saveModelConfigApi({ ...config.value, activePrompt: activePrompt.value, version: 'v2.2' })
    ElMessage.success('模型与提示词配置已发布到后端。')
  } finally {
    saving.value = false
  }
}

async function rollback() {
  config.value = await rollbackModelConfigApi()
  activePrompt.value = config.value.activePrompt
  ElMessage.warning('已回滚到上一稳定配置。')
}

function compareVersion() {
  ElMessage.info(`当前版本 ${config.value?.version || '-'} 与上一版本相比：强化了引用校验和高风险拦截。`)
}

onMounted(loadConfig)
</script>

<template>
  <div v-if="config" class="page">
    <section class="config-hero">
      <div>
        <span class="eyebrow">管理员配置中心</span>
        <h1>发布前先检查内容审核策略</h1>
        <p>当前风险主要来自引用缺失。发布配置前，请确认审核智能体提示词、质量阈值和高风险拦截策略。</p>
        <div class="hero-meta">
          <span>配置版本 {{ config.version }}</span>
          <span>调用成功率 98.6%</span>
          <span>平均任务耗时 8.4s</span>
          <span>当前编辑：{{ activePromptLabel }}</span>
        </div>
      </div>
      <el-button type="primary" size="large" :loading="saving" @click="saveConfig">发布配置</el-button>
    </section>

    <div class="config-layout">
      <section class="panel">
        <h2 class="section-title">Agent 模型策略</h2>
        <div class="agent-config-list">
          <article v-for="agent in config.agents" :key="agent.name">
            <div>
              <h3>{{ agent.name }}</h3>
              <p>{{ agent.model }} · Temperature {{ agent.temp }}</p>
            </div>
            <div class="agent-tags">
              <el-tag :type="agent.status === '稳定' ? 'success' : 'warning'" effect="plain">{{ agent.status }}</el-tag>
              <el-tag effect="plain">{{ agent.guard }}</el-tag>
            </div>
          </article>
        </div>
      </section>

      <section class="panel">
        <div class="section-head">
          <div>
            <h2 class="section-title">提示词版本</h2>
            <p class="section-desc">保存后会影响后续 Agent 任务的审核和生成策略。</p>
          </div>
          <el-segmented v-model="activePrompt" :options="[
            { label: '内容审核', value: 'audit' },
            { label: '资源生成', value: 'resource' },
            { label: '智能辅导', value: 'tutor' },
          ]" />
        </div>
        <el-input v-model="config.prompts[activePrompt]" class="prompt-box" type="textarea" :rows="12" />
        <div class="prompt-actions">
          <el-button type="primary" :loading="saving" @click="saveConfig">保存为 v2.2</el-button>
          <el-button @click="compareVersion">对比上一版本</el-button>
          <el-button @click="rollback">回滚</el-button>
        </div>
      </section>
    </div>

    <section class="panel policy-panel">
      <h2 class="section-title">质量与安全阈值</h2>
      <div class="policy-grid">
        <div>
          <strong>{{ config.thresholds.citationCoverage }}%</strong>
          <span>最低引用覆盖</span>
          <el-slider v-model="config.thresholds.citationCoverage" />
        </div>
        <div>
          <strong>{{ config.thresholds.lowConfidence }}%</strong>
          <span>低置信确认阈值</span>
          <el-slider v-model="config.thresholds.lowConfidence" />
        </div>
        <div>
          <strong>{{ config.thresholds.autoPassScore }}</strong>
          <span>自动通过质量分</span>
          <el-slider v-model="config.thresholds.autoPassScore" />
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.config-hero,
.config-layout,
.section-head,
.agent-config-list article,
.hero-meta,
.agent-tags,
.prompt-actions {
  display: flex;
}

.config-hero {
  align-items: center;
  justify-content: space-between;
  gap: 22px;
  margin-bottom: 18px;
  padding: 22px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}

.eyebrow {
  color: var(--color-primary);
  font-size: 13px;
  font-weight: 700;
}

.config-hero h1 {
  margin: 7px 0 8px;
  font-size: 26px;
}

.config-hero p,
.section-desc {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.hero-meta,
.agent-tags,
.prompt-actions {
  flex-wrap: wrap;
  gap: 8px;
}

.hero-meta {
  margin-top: 12px;
}

.hero-meta span {
  padding: 4px 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: #f8fafc;
}

.config-layout {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
  gap: 18px;
}

.section-head {
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.agent-config-list {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

.agent-config-list article {
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
}

.agent-config-list h3 {
  margin: 0 0 6px;
}

.agent-config-list p {
  margin: 0;
  color: var(--color-text-secondary);
}

.prompt-actions,
.policy-panel {
  margin-top: 14px;
}

.policy-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-top: 14px;
}

.policy-grid div {
  display: grid;
  gap: 6px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #f8fafc;
}

.policy-grid strong {
  font-size: 24px;
}

@media (max-width: 1100px) {
  .config-hero,
  .section-head,
  .agent-config-list article {
    align-items: stretch;
    flex-direction: column;
  }

  .config-layout,
  .policy-grid {
    grid-template-columns: 1fr;
  }
}
</style>
