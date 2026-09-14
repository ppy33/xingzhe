<script setup>
import { computed, ref, watch, nextTick } from 'vue'

const props = defineProps({
  steps: { type: Array, required: true },
  running: { type: Boolean, default: false },
})

const expanded = ref(new Set())
const listEl = ref(null)

/** 把 tool_call / tool_result 配对成一条可读记录 */
const calls = computed(() => {
  const out = []
  const byId = new Map()
  for (const s of props.steps) {
    if (s.type === 'tool_call') {
      const item = {
        key: `${s.tool}-${s.ts}-${out.length}`,
        tool: s.tool,
        args: s.args || {},
        result: null,
        ts: s.ts,
        elapsed: null,
        ok: true,
      }
      out.push(item)
      byId.set(s.tool, item)
    } else if (s.type === 'tool_result') {
      // 后端按顺序返回，取最近一个未配对且同名的
      const target = byId.get(s.tool)
      if (target && !target.result) {
        target.result = s.content
        target.elapsed = s.ts && target.ts ? s.ts - target.ts : null
        try {
          const parsed = JSON.parse(s.content)
          target.ok = !parsed.error
          target.error = parsed.error || ''
          target.count = parsed.count ?? (parsed.pois ? parsed.pois.length : undefined)
        } catch {
          target.ok = true
        }
        byId.delete(s.tool)
      }
    }
  }
  return out
})

const TOOL_LABEL = {
  poi_search: '检索地点',
  poi_around: '周边搜索',
  poi_detail: '查阅详情',
  weather_now: '实时天气',
  weather_forecast: '未来天气',
  geo_geocode: '地址解析',
  route_plan: '路径规划',
  critic_review: 'Critic 审查',
  revise_plan: '行程修订',
  planner_struct: '结构化规划',
  optimizer_route: '路线优化',
}

function label(tool) {
  return TOOL_LABEL[tool] || tool
}

function argsText(args) {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join('  ')
}

function toggle(key) {
  const s = new Set(expanded.value)
  s.has(key) ? s.delete(key) : s.add(key)
  expanded.value = s
}

function pretty(content) {
  if (!content) return ''
  try {
    return JSON.stringify(JSON.parse(content), null, 2)
  } catch {
    return content
  }
}

watch(
  () => calls.value.length,
  async () => {
    await nextTick()
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
  }
)
</script>

<template>
  <div class="trace">
    <div v-if="!calls.length" class="empty">
      <p>Agent 的工具调用会实时出现在这里</p>
      <p class="hint">
        每一步都是 LLM 自己决定调用哪个工具、传什么参数——这就是 ReAct 循环。
      </p>
    </div>

    <div v-else ref="listEl" class="list">
      <div
        v-for="(c, i) in calls"
        :key="c.key"
        class="item"
        :class="{ bad: !c.ok }"
        :style="{ animationDelay: `${Math.min(i, 8) * 18}ms` }"
      >
        <div class="line" @click="toggle(c.key)">
          <span class="idx mono">{{ String(i + 1).padStart(2, '0') }}</span>
          <span class="tool">{{ label(c.tool) }}</span>
          <span class="tool-raw mono">{{ c.tool }}</span>
          <span v-if="c.count !== undefined" class="chip chip--muted">{{ c.count }} 条</span>
          <span v-if="c.elapsed !== null" class="elapsed mono">{{ c.elapsed }}ms</span>
          <span v-if="!c.ok" class="chip chip--bad">失败</span>
          <span class="caret" :class="{ open: expanded.has(c.key) }">›</span>
        </div>
        <div class="args mono">{{ argsText(c.args) }}</div>
        <div v-if="expanded.has(c.key)" class="detail">
          <div v-if="c.error" class="err mono">{{ c.error }}</div>
          <pre class="mono">{{ pretty(c.result) }}</pre>
        </div>
      </div>

      <div v-if="running" class="thinking">
        <i class="pulse" />
        <span>LLM 正在推理下一步…</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trace {
  height: 100%;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.empty {
  padding: 24px 16px;
  text-align: center;
  color: var(--text-2);
  font-size: 12.5px;
}
.hint {
  margin-top: 8px;
  font-size: 11.5px;
  color: var(--muted);
  line-height: 1.7;
}

.list {
  flex: 1;
  overflow-y: auto;
  padding: 10px;
}
.item {
  margin-bottom: 7px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.028);
  overflow: hidden;
  animation: slide-in 0.22s ease both;
}
.item.bad {
  border-color: rgba(255, 95, 109, 0.35);
}

.line {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 10px;
  cursor: pointer;
}
.line:hover {
  background: rgba(255, 255, 255, 0.035);
}
.idx {
  font-size: 10px;
  color: var(--muted);
}
.tool {
  font-size: 12px;
  font-weight: 600;
  color: #cfe0ff;
}
.tool-raw {
  font-size: 10.5px;
  color: var(--muted);
}
.elapsed {
  margin-left: auto;
  font-size: 10.5px;
  color: var(--muted);
}
.caret {
  font-size: 14px;
  color: var(--muted);
  transition: transform 0.16s;
}
.caret.open {
  transform: rotate(90deg);
}
.chip--bad {
  background: rgba(255, 95, 109, 0.14);
  color: #ff8a94;
  border: 1px solid rgba(255, 95, 109, 0.34);
}

.args {
  padding: 0 10px 7px 33px;
  font-size: 10.8px;
  color: var(--muted);
  word-break: break-all;
}

.detail {
  border-top: 1px solid var(--border);
  background: rgba(0, 0, 0, 0.3);
}
.err {
  padding: 7px 10px;
  font-size: 11px;
  color: #ff8a94;
  background: rgba(255, 95, 109, 0.09);
}
pre {
  margin: 0;
  padding: 9px 11px;
  font-size: 10.6px;
  line-height: 1.62;
  color: #9fb0c9;
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.thinking {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  font-size: 11.5px;
  color: var(--muted);
}
.pulse {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 9px var(--accent);
  animation: pulse-dot 1.15s infinite;
}
</style>
