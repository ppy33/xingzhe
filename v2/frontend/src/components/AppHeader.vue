<script setup>
defineProps({
  status: { type: Object, required: true },
  stats: { type: Object, required: true },
  demo: { type: Boolean, default: false },
})
const emit = defineEmits(['open-settings', 'toggle-demo'])
</script>

<template>
  <header class="hdr">
    <div class="brand">
      <div class="logo">行</div>
      <div class="brand-text">
        <div class="name">行者 <span class="ver">v2</span></div>
        <div class="sub">智能旅游助手 · Multi-Agent</div>
      </div>
    </div>

    <div class="stats">
      <div class="stat">
        <span class="k">模型</span>
        <span class="v mono">{{ status.model || '—' }}</span>
      </div>
      <div class="stat">
        <span class="k">工具调用</span>
        <span class="v mono accent">{{ stats.toolCalls }}</span>
      </div>
      <div class="stat">
        <span class="k">发现地点</span>
        <span class="v mono teal">{{ stats.poiCount }}</span>
      </div>
      <div class="stat">
        <span class="k">耗时</span>
        <span class="v mono">{{ stats.elapsed }}</span>
      </div>
    </div>

    <div class="conn">
      <span class="dot" :class="status.ok ? 'on' : 'off'" />
      <span>{{ status.ok ? '后端已连接' : '后端未连接' }}</span>
    </div>

    <div class="btns">
      <button class="hbtn" :class="{ on: demo }" @click="emit('toggle-demo')">
        {{ demo ? '退出演示' : '演示模式' }}
      </button>
      <button class="hbtn" @click="emit('open-settings')">模型设置</button>
    </div>
  </header>
</template>

<style scoped>
.hdr {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  height: 62px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
  background: rgba(7, 11, 22, 0.72);
  backdrop-filter: blur(18px);
  flex-shrink: 0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
}
.logo {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: 17px;
  color: #fff;
  background: linear-gradient(135deg, var(--accent), var(--teal));
  box-shadow: 0 4px 16px rgba(79, 140, 255, 0.35);
}
.name {
  font-size: 15.5px;
  font-weight: 650;
  letter-spacing: 0.5px;
}
.ver {
  font-size: 10.5px;
  color: #9dc0ff;
  background: var(--accent-soft);
  border: 1px solid rgba(79, 140, 255, 0.3);
  padding: 1px 6px;
  border-radius: 999px;
  margin-left: 3px;
  vertical-align: 2px;
}
.sub {
  font-size: 11.5px;
  color: var(--muted);
}

.stats {
  display: flex;
  gap: 26px;
}
.stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
}
.stat .k {
  font-size: 10.5px;
  color: var(--muted);
  letter-spacing: 0.4px;
}
.stat .v {
  font-size: 14px;
  font-weight: 600;
}
.accent {
  color: #7fabff;
}
.teal {
  color: #4fe0bf;
}

.conn {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  color: var(--text-2);
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.dot.on {
  background: var(--teal);
  box-shadow: 0 0 9px var(--teal);
}
.dot.off {
  background: var(--danger);
  box-shadow: 0 0 9px var(--danger);
}

.btns {
  display: flex;
  gap: 8px;
}
.hbtn {
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 12px;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border);
  color: var(--text-2);
}
.hbtn:hover {
  background: rgba(255, 255, 255, 0.1);
}
.hbtn.on {
  color: #4fe0bf;
  border-color: var(--teal);
}

@media (max-width: 1180px) {
  .stats {
    display: none;
  }
}
</style>
