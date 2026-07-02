<template>
  <div class="demo-home">
    <section class="hero">
      <div>
        <p class="eyebrow">第十五届中国软件杯 A3 赛题</p>
        <h1>智学工坊 EduAgent Studio</h1>
        <p>基于课程资料的个性化资源生成与学习多智能体系统，演示从画像构建、知识库检索、资源生成到学习路径评估的完整闭环。</p>
        <div class="actions">
          <router-link to="/demo/flow"><el-button type="primary" size="large">开始演示</el-button></router-link>
          <router-link to="/login"><el-button size="large">进入系统</el-button></router-link>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.demo-home {
  min-height: 100vh;
  padding: 38px;
}

.hero {
  min-height: calc(100vh - 76px);
  display: flex;
  align-items: center;
  padding: 48px;
  color: #0f172a;
  background:
    linear-gradient(120deg, rgba(255,255,255,0.92), rgba(255,255,255,0.55)),
    url('/icons.svg');
  background-size: cover;
  border: 1px solid var(--color-border);
  border-radius: 8px;
}

.hero > div {
  max-width: 760px;
}

.eyebrow {
  color: var(--color-primary);
  font-weight: 700;
}

h1 {
  margin: 0;
  font-size: 54px;
  line-height: 1.05;
}

p {
  font-size: 18px;
  line-height: 1.8;
}

.actions {
  display: flex;
  gap: 12px;
}
</style>
