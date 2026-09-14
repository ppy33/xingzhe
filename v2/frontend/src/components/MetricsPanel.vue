<script setup>
defineProps({
  summary: { type: Object, default: null },
  recent: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['refresh'])

function fmtMs(ms) {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

function fmtTokens(n) {
  if (!n) return '0'
  if (n < 1000) return `${n}`
  return `${(n / 1000).toFixed(1)}k`
}

function kindLabel(k) {
  return k === 'trip_check' ? '体检' : '对话'
}

function statusLabel(s) {
  return s === 'ok' ? '✓' : '✕'
}

function fmtTime(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}
</script>

<template>
  <div class="metrics">
    <div class="metrics-toolbar">
      <span class="metrics-title">请求观测（M6）</span>
      <button class="tb" @click="emit('refresh')" :disabled="loading">
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </div>

    <!-- 汇总 -->
    <div v-if="summary" class="cards">
      <div class="card">
        <div class="card-num">{{ summary.requests }}</div>
        <div class="card-label">总请求</div>
      </div>
      <div class="card">
        <div class="card-num">{{ summary.ok_count }}/{{ summary.requests }}</div>
        <div class="card-label">成功</div>
      </div>
      <div class="card">
        <div class="card-num">{{ fmtMs(summary.avg_ms) }}</div>
        <div class="card-label">平均耗时</div>
      </div>
      <div class="card">
        <div class="card-num">{{ summary.avg_tools }}</div>
        <div class="card-label">平均工具数</div>
      </div>
      <div class="card">
        <div class="card-num mono">{{ fmtTokens(summary.sum_input_tokens + summary.sum_output_tokens) }}</div>
        <div class="card-label">token 总量</div>
      </div>
    </div>
    <div v-else class="empty">暂无埋点数据，跑一次对话后自动记录。</div>

    <!-- 明细 -->
    <ul v-if="recent.length" class="rows">
      <li v-for="(r, i) in recent" :key="i" class="row">
        <span class="row-status" :class="r.status === 'ok' ? 'ok' : 'err'">
          {{ statusLabel(r.status) }}
        </span>
        <span class="row-kind">{{ kindLabel(r.kind) }}</span>
        <span class="row-time mono">{{ fmtMs(r.total_ms) }}</span>
        <span class="row-tools mono">{{ r.tool_calls }} 工具</span>
        <span class="row-tokens mono">{{ fmtTokens(r.input_tokens + r.output_tokens) }} tok</span>
        <span class="row-at">{{ fmtTime(r.created_at) }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.metrics {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  overflow: auto;
  padding: 12px;
}
.metrics-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.metrics-title {
  font-size: 13px;
  color: var(--muted);
}
.tb {
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 8px;
  border: 1px solid var(--border, #333);
  background: transparent;
  color: var(--text, #eee);
  cursor: pointer;
}
.tb:hover { background: rgba(255, 255, 255, 0.06); }
.tb:disabled { opacity: 0.5; cursor: default; }

.cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.card {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--border, #333);
  border-radius: 10px;
  padding: 10px;
  text-align: center;
}
.card-num {
  font-size: 18px;
  font-weight: 600;
  color: var(--accent, #21d4a8);
}
.card-label {
  font-size: 11px;
  color: var(--muted);
  margin-top: 2px;
}
.mono { font-family: var(--mono, monospace); }

.empty {
  color: var(--muted);
  font-size: 13px;
  text-align: center;
  padding: 20px 0;
}

.rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.03);
}
.row-status {
  width: 14px;
  text-align: center;
}
.row-status.ok { color: var(--accent, #21d4a8); }
.row-status.err { color: #ff7b72; }
.row-kind {
  color: var(--text, #eee);
  width: 30px;
}
.row-time { color: var(--text, #eee); }
.row-tools { color: var(--muted); }
.row-tokens { color: var(--muted); }
.row-at {
  margin-left: auto;
  color: var(--muted);
  font-size: 11px;
}
</style>
