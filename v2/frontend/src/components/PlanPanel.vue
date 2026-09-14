<script setup>
import { computed, ref, watch } from 'vue'
import { marked } from 'marked'

const props = defineProps({
  plan: { type: String, default: '' },
  planData: { type: Object, default: null },
  review: { type: Object, default: null },
  weather: { type: Array, default: () => [] },
  running: { type: Boolean, default: false },
})

// ---------- 审查报告 ----------
const reviewOpen = ref(true)
const DIM_META = {
  weather: { icon: '🌦️', label: '天气' },
  budget: { icon: '💰', label: '预算' },
  time: { icon: '⏰', label: '时间' },
  geo: { icon: '🗺️', label: '地理' },
  other: { icon: '📝', label: '其他' },
}
function dimMeta(d) { return DIM_META[d] || DIM_META.other }
function sevLabel(s) {
  return { high: '高', medium: '中', low: '低' }[s] || s
}
const sevCount = computed(() => {
  const c = { high: 0, medium: 0, low: 0 }
  for (const it of props.review?.issues || []) {
    if (c[it.severity] !== undefined) c[it.severity]++
  }
  return c
})

// ---------- 视图切换（结构化 / Markdown） ----------
const hasStructured = computed(() => !!props.planData && Array.isArray(props.planData?.days))
const view = ref('structured')
watch(hasStructured, (v) => {
  view.value = v ? 'structured' : 'markdown'
}, { immediate: true })

const html = computed(() => {
  if (!props.plan) return ''
  return marked.parse(props.plan, { breaks: true, gfm: true })
})

// ---------- Markdown 工具 ----------
function download() {
  const blob = new Blob([props.plan], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `行者行程-${new Date().toISOString().slice(0, 10)}.md`
  a.click()
  URL.revokeObjectURL(url)
}
async function copy() {
  try { await navigator.clipboard.writeText(props.plan) } catch { /* 忽略权限失败 */ }
}
function doPrint() { window.print() }

// ---------- 结构化 JSON 下载 ----------
function downloadJson() {
  if (!props.planData) return
  const blob = new Blob([JSON.stringify(props.planData, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `行者行程-${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(url)
}

// ---------- 天气条 ----------
const WICON = {
  晴: '☀️', 多云: '⛅', 阴: '☁️', 阵雨: '🌦️', 雷阵雨: '⛈️',
  小雨: '🌧️', 中雨: '🌧️', 大雨: '🌧️', 暴雨: '⛈️',
  小雪: '🌨️', 中雪: '🌨️', 大雪: '❄️', 雾: '🌫️', 霾: '😷',
}
function icon(w) { return WICON[w] || '🌤️' }

// ---------- 结构化视图渲染辅助 ----------
const days = computed(() => props.planData?.days || [])
const overview = computed(() => props.planData?.overview || '')
const title = computed(() => props.planData?.title || '')
const transportation = computed(() => props.planData?.transportation || '')
const budget = computed(() => props.planData?.budget || [])
const tips = computed(() => props.planData?.tips || [])

const CAT_ICON = {
  sight: '🏛️',
  food: '🍜',
  stay: '🛏️',
  transit: '🚉',
  other: '📍',
}
function catIcon(c) { return CAT_ICON[c] || CAT_ICON.other }

function fmtCost(p) {
  if (p?.cost == null || p.cost === '') return ''
  // 已数字化就直接展示；来自工具的字符串也兼容
  if (typeof p.cost === 'number') return `人均 ¥${p.cost}`
  return `人均 ¥${p.cost}`
}

function fmtRating(p) {
  if (!p?.rating) return ''
  return `★${p.rating}`
}

function fmtDuration(it) {
  if (!it?.duration_min) return ''
  if (it.duration_min < 60) return `${it.duration_min}分钟`
  const h = Math.floor(it.duration_min / 60), m = it.duration_min % 60
  return m ? `${h}h${m}m` : `${h}h`
}

// 预算合计提示
const totalBudgetLine = computed(() => budget.value.find((b) => /合计|总计|总费用|预算/i.test(b.category)))
</script>

<template>
  <div class="plan">
    <!-- 天气条 -->
    <div v-if="weather.length" class="weather">
      <div v-for="d in weather" :key="d.date" class="wday">
        <span class="wdate">{{ d.date.slice(5) }} {{ d.week ? '周' + d.week : '' }}</span>
        <span class="wicon">{{ icon(d.day_weather) }}</span>
        <span class="wtxt">{{ d.day_weather }}</span>
        <span class="wtemp mono">{{ d.temp }}</span>
      </div>
    </div>

    <!-- 空态 -->
    <div v-if="!plan && !running" class="empty">
      <div class="empty-icon">🗺️</div>
      <p class="t">行程还没生成</p>
      <p class="d">在左侧说一句需求，Agent 会先查天气、再搜景点、算交通，最后排出行程表。</p>
    </div>

    <div v-else-if="(!plan && running) || (running && !planData && !plan)" class="empty">
      <div class="loader" />
      <p class="d">正在生成行程…（地图上的标记会陆续出现）</p>
    </div>

    <!-- 行程正文 -->
    <div v-else class="body">
      <!-- Critic 审查报告卡 -->
      <div v-if="review" class="review" :class="{ 'review--pass': review.passed }">
        <div class="rv-head" @click="reviewOpen = !reviewOpen">
          <span class="rv-badge" :class="review.passed ? 'rv-badge--ok' : 'rv-badge--fix'">
            {{ review.passed ? '✓ 审查通过' : '⚠ 需修复' }}
          </span>
          <span class="rv-stats">
            <template v-if="sevCount.high">{{ sevCount.high }} 高风险 · </template>{{ sevCount.medium }} 中 · {{ sevCount.low }} 低
          </span>
          <span v-if="review.revisions" class="rv-rev">已修订 {{ review.revisions }} 轮</span>
          <span class="rv-caret" :class="{ open: reviewOpen }">›</span>
        </div>
        <div v-if="reviewOpen" class="rv-body">
          <p v-if="review.summary" class="rv-summary">{{ review.summary }}</p>
          <p v-if="review.new_places?.length" class="rv-newplaces">
            修订审计：补充了初版未包含的地点——{{ review.new_places.join('、') }}（未经数据核实，建议出行前确认）
          </p>
          <ul v-if="review.issues?.length" class="rv-issues">
            <li
              v-for="(it, k) in review.issues"
              :key="k"
              class="rv-issue"
              :data-sev="it.severity"
            >
              <span class="rv-dim">{{ dimMeta(it.dimension).icon }} {{ dimMeta(it.dimension).label }}</span>
              <span class="rv-sev" :data-sev="it.severity">{{ sevLabel(it.severity) }}</span>
              <span class="rv-desc">{{ it.description }}</span>
            </li>
          </ul>
          <p v-else class="rv-none">未发现问题，行程已通过全部 4 项检查（天气 / 预算 / 时间 / 地理）。</p>
        </div>
      </div>

      <div class="toolbar">
        <div class="seg">
          <button
            class="seg-btn"
            :class="{ on: view === 'structured' }"
            :disabled="!hasStructured"
            @click="view = 'structured'"
          >📋 结构化</button>
          <button
            class="seg-btn"
            :class="{ on: view === 'markdown' }"
            @click="view = 'markdown'"
          >📝 Markdown</button>
        </div>
        <div class="spacer" />
        <template v-if="view === 'markdown'">
          <button class="tb" @click="copy">复制</button>
          <button class="tb" @click="download">下载 .md</button>
        </template>
        <template v-else>
          <button class="tb" @click="downloadJson">下载 .json</button>
        </template>
        <button class="tb tb--primary" @click="doPrint">打印 / 存 PDF</button>
      </div>

      <!-- ===== 结构化视图 ===== -->
      <div v-if="view === 'structured' && hasStructured" class="structured">
        <header class="sp-head">
          <h2 class="sp-title">{{ title }}</h2>
          <p v-if="overview" class="sp-overview">{{ overview }}</p>
        </header>

        <section v-for="(d, i) in days" :key="i" class="sp-day">
          <div class="sp-day-head">
            <span class="sp-day-no">Day {{ i + 1 }}</span>
            <span class="sp-day-date">{{ d.date }}</span>
            <span v-if="d.weekday" class="sp-day-week">周{{ d.weekday }}</span>
            <span v-if="d.weather" class="sp-day-w">
              <span class="sp-day-wicon">{{ icon((d.weather || '').split(' ')[0]) }}</span>
              {{ d.weather }}
            </span>
          </div>
          <p v-if="d.theme" class="sp-day-theme">“{{ d.theme }}”</p>

          <ul class="sp-items">
            <li v-for="(it, j) in d.items" :key="j" class="sp-item">
              <div class="sp-time">{{ it.time }}</div>
              <div class="sp-dot" />
              <div class="sp-card">
                <div class="sp-row-1">
                  <span v-if="it.place" class="sp-cat" :data-cat="it.place.category">
                    {{ catIcon(it.place.category) }}
                  </span>
                  <span class="sp-place">{{ it.place?.name || it.title }}</span>
                  <span v-if="it.place?.rating" class="sp-rating">{{ fmtRating(it.place) }}</span>
                  <span v-if="fmtCost(it.place)" class="sp-cost mono">{{ fmtCost(it.place) }}</span>
                  <span v-if="fmtDuration(it)" class="sp-dur mono">{{ fmtDuration(it) }}</span>
                </div>
                <p v-if="it.title && it.title !== it.place?.name" class="sp-title-line">{{ it.title }}</p>
                <p v-if="it.notes" class="sp-notes">{{ it.notes }}</p>
                <p v-if="it.place?.address" class="sp-addr">📍 {{ it.place.address }}</p>
                <p v-if="it.place?.open_time" class="sp-open">⏰ {{ it.place.open_time }}</p>
                <div v-if="it.transport_to_next" class="sp-leg">
                  <span class="sp-leg-arrow">↘</span>{{ it.transport_to_next }}
                </div>
              </div>
            </li>
          </ul>

          <p v-if="d.summary" class="sp-day-summary">小结：{{ d.summary }}</p>
        </section>

        <section v-if="transportation" class="sp-block">
          <h3 class="sp-h3">🚆 交通建议</h3>
          <p class="sp-trans">{{ transportation }}</p>
        </section>

        <section v-if="budget.length" class="sp-block">
          <h3 class="sp-h3">💰 预算估算</h3>
          <div class="sp-budget">
            <div v-for="(b, k) in budget" :key="k" class="sp-budget-row">
              <span class="sp-budget-cat">{{ b.category }}</span>
              <span class="sp-budget-amt mono">{{ b.amount }}</span>
            </div>
          </div>
        </section>

        <section v-if="tips.length" class="sp-block">
          <h3 class="sp-h3">📌 温馨提示</h3>
          <ul class="sp-tips">
            <li v-for="(t, k) in tips" :key="k">{{ t }}</li>
          </ul>
        </section>
      </div>

      <!-- ===== Markdown 视图 ===== -->
      <article v-else class="md" v-html="html" />
    </div>
  </div>
</template>

<style scoped>
.plan {
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.weather {
  display: flex;
  gap: 8px;
  padding: 11px 12px;
  border-bottom: 1px solid var(--border);
  overflow-x: auto;
  flex-shrink: 0;
}
.wday {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 7px 11px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.038);
  border: 1px solid var(--border);
  min-width: 76px;
  flex-shrink: 0;
}
.wdate { font-size: 10.5px; color: var(--muted); }
.wicon { font-size: 16px; line-height: 1.2; }
.wtxt { font-size: 11px; color: var(--text-2); }
.wtemp { font-size: 11px; color: #9dc0ff; }

.empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px;
  text-align: center;
  gap: 9px;
}
.empty-icon { font-size: 32px; }
.empty .t { font-size: 13.5px; font-weight: 600; }
.empty .d {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.75;
  max-width: 320px;
}
.loader {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 2px solid rgba(79, 140, 255, 0.24);
  border-top-color: var(--accent);
  animation: spin 0.8s linear infinite;
}

.body {
  padding: 0 16px 28px;
}

/* ===== Critic 审查报告卡 ===== */
.review {
  margin-top: 12px;
  border: 1px solid rgba(255, 181, 71, 0.35);
  background: rgba(255, 181, 71, 0.05);
  border-radius: 10px;
  overflow: hidden;
}
.review--pass {
  border-color: rgba(33, 212, 168, 0.32);
  background: rgba(33, 212, 168, 0.045);
}
.rv-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  cursor: pointer;
  user-select: none;
}
.rv-head:hover { background: rgba(255,255,255,0.03); }
.rv-badge {
  font-size: 11.5px;
  font-weight: 700;
  padding: 2px 9px;
  border-radius: 999px;
}
.rv-badge--ok {
  color: #21d4a8;
  background: rgba(33, 212, 168, 0.14);
  border: 1px solid rgba(33, 212, 168, 0.35);
}
.rv-badge--fix {
  color: #ffb547;
  background: rgba(255, 181, 71, 0.13);
  border: 1px solid rgba(255, 181, 71, 0.35);
}
.rv-stats { font-size: 11px; color: var(--muted); }
.rv-rev {
  font-size: 10.5px;
  color: #c792ea;
  padding: 1px 7px;
  border-radius: 999px;
  background: rgba(199,146,234,0.12);
  border: 1px solid rgba(199,146,234,0.3);
}
.rv-caret {
  margin-left: auto;
  font-size: 14px;
  color: var(--muted);
  transition: transform 0.16s;
}
.rv-caret.open { transform: rotate(90deg); }
.rv-body { padding: 0 12px 10px; }
.rv-summary {
  margin: 0 0 7px;
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.65;
}
.rv-newplaces {
  margin: 0 0 7px;
  padding: 6px 9px;
  border-radius: 6px;
  background: rgba(234, 179, 8, 0.1);
  border-left: 3px solid #eab308;
  font-size: 11.5px;
  color: var(--text-2, #888);
  line-height: 1.6;
}
.rv-issues {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.rv-issue {
  display: flex;
  align-items: baseline;
  gap: 7px;
  flex-wrap: wrap;
  padding: 6px 9px;
  font-size: 11.5px;
  background: rgba(255,255,255,0.025);
  border: 1px solid var(--border);
  border-radius: 7px;
  border-left-width: 2.5px;
}
.rv-issue[data-sev="high"]   { border-left-color: #ff5f6d; }
.rv-issue[data-sev="medium"] { border-left-color: #ffb547; }
.rv-issue[data-sev="low"]    { border-left-color: #67e8f9; }
.rv-dim {
  font-size: 10.5px;
  color: var(--text-2);
  white-space: nowrap;
}
.rv-sev {
  font-size: 10px;
  font-weight: 700;
  padding: 0 5px;
  border-radius: 4px;
}
.rv-sev[data-sev="high"]   { color: #ff8a94; background: rgba(255,95,109,0.14); }
.rv-sev[data-sev="medium"] { color: #ffb547; background: rgba(255,181,71,0.13); }
.rv-sev[data-sev="low"]    { color: #67e8f9; background: rgba(103,232,249,0.11); }
.rv-desc { color: var(--text-2); line-height: 1.6; flex: 1; min-width: 0; }
.rv-none {
  margin: 0;
  font-size: 11.5px;
  color: var(--muted);
}

.toolbar {
  position: sticky;
  top: 0;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 11px 0;
  background: linear-gradient(var(--bg-2) 72%, transparent);
  z-index: 3;
}
.spacer { flex: 1; }

.tb {
  padding: 5px 11px;
  border-radius: 7px;
  font-size: 11.5px;
  border: 1px solid var(--border);
  color: var(--text-2);
  background: rgba(255, 255, 255, 0.03);
  transition: all 0.15s;
}
.tb:hover {
  border-color: var(--border-strong);
  color: var(--text);
}
.tb--primary {
  background: var(--accent-soft);
  border-color: rgba(79, 140, 255, 0.4);
  color: #9dc0ff;
}

.seg {
  display: inline-flex;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.025);
}
.seg-btn {
  padding: 5px 11px;
  font-size: 11.5px;
  color: var(--muted);
  background: transparent;
  border: 0;
  transition: all 0.15s;
}
.seg-btn:hover:not(:disabled) { color: var(--text); }
.seg-btn.on {
  color: #9dc0ff;
  background: var(--accent-soft);
}
.seg-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* ===== 结构化视图样式 ===== */
.structured {
  font-size: 12.5px;
  line-height: 1.6;
}
.sp-head {
  padding: 6px 2px 14px;
  border-bottom: 1px dashed var(--border);
  margin-bottom: 14px;
}
.sp-title {
  font-size: 16px;
  font-weight: 700;
  margin: 0 0 6px;
  color: var(--text);
}
.sp-overview {
  margin: 0;
  color: var(--text-2);
  font-size: 12px;
  line-height: 1.7;
}

.sp-day {
  margin: 18px 0 8px;
  padding: 12px 12px 14px;
  background: rgba(255, 255, 255, 0.018);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.sp-day-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 4px;
}
.sp-day-no {
  font-size: 10.5px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 0.6px;
  padding: 2px 7px;
  border-radius: 4px;
  background: var(--accent-soft);
}
.sp-day-date { font-size: 14px; font-weight: 600; color: var(--text); }
.sp-day-week { font-size: 11.5px; color: var(--muted); padding: 2px 7px; background: rgba(255,255,255,0.04); border-radius: 4px; }
.sp-day-w {
  font-size: 11.5px;
  color: var(--text-2);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.sp-day-wicon { font-size: 14px; }

.sp-day-theme {
  font-size: 12px;
  color: var(--muted);
  font-style: italic;
  margin: 4px 0 10px;
}

.sp-items {
  list-style: none;
  margin: 0;
  padding: 0;
}
.sp-item {
  display: grid;
  grid-template-columns: 52px 14px 1fr;
  gap: 8px;
  padding: 8px 0;
  border-top: 1px dashed rgba(255,255,255,0.05);
}
.sp-item:first-child { border-top: 0; padding-top: 4px; }

.sp-time {
  font-size: 12.5px;
  color: #9dc0ff;
  font-weight: 600;
  padding-top: 4px;
}
.sp-dot {
  width: 8px;
  height: 8px;
  margin-top: 9px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px rgba(79,140,255,0.16);
  justify-self: center;
}

.sp-card {
  background: rgba(255,255,255,0.022);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
}
.sp-row-1 {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.sp-cat {
  width: 22px;
  height: 22px;
  border-radius: 5px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
}
.sp-cat[data-cat="sight"]   { background: rgba(79,140,255,0.18);  }
.sp-cat[data-cat="food"]    { background: rgba(255,181,71,0.18);  }
.sp-cat[data-cat="stay"]    { background: rgba(199,146,234,0.18); }
.sp-cat[data-cat="transit"] { background: rgba(103,232,249,0.18); }
.sp-cat[data-cat="other"]   { background: rgba(33,212,168,0.18);  }

.sp-place {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}
.sp-rating {
  font-size: 11px;
  color: #ffb547;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(255,181,71,0.12);
}
.sp-cost, .sp-dur {
  font-size: 10.5px;
  color: #67e8f9;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(103,232,249,0.10);
  margin-left: auto;
}
.sp-dur { color: #c792ea; background: rgba(199,146,234,0.12); }
.sp-cost { margin-left: 4px; }
.sp-rating + .sp-cost { margin-left: auto; }

.sp-title-line {
  margin: 4px 0 2px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text);
}
.sp-notes {
  margin: 3px 0 0;
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.6;
}
.sp-addr, .sp-open {
  margin: 2px 0 0;
  font-size: 11px;
  color: var(--muted);
}
.sp-leg {
  margin-top: 6px;
  display: inline-flex;
  gap: 5px;
  font-size: 10.5px;
  color: var(--muted);
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(255,255,255,0.03);
  border: 1px dashed var(--border);
}
.sp-leg-arrow { color: var(--teal); font-weight: 700; }

.sp-day-summary {
  margin: 10px 0 0;
  padding: 8px 10px;
  font-size: 11.5px;
  color: var(--text-2);
  background: rgba(33,212,168,0.06);
  border-left: 2px solid var(--teal);
  border-radius: 4px;
}

.sp-block {
  margin-top: 16px;
  padding: 12px 14px;
  background: rgba(255,255,255,0.018);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.sp-h3 {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--text);
  margin: 0 0 8px;
}
.sp-trans {
  margin: 0;
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.7;
  white-space: pre-wrap;
}
.sp-budget {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 6px;
}
.sp-budget-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 9px;
  font-size: 11.5px;
  background: rgba(255,255,255,0.025);
  border: 1px solid var(--border);
  border-radius: 6px;
}
.sp-budget-cat { color: var(--text-2); }
.sp-budget-amt { color: #ffb547; font-weight: 600; }
.sp-tips {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.7;
}
.sp-tips li { margin-bottom: 3px; }

/* Markdown 视图 */
.md { font-size: 13px; line-height: 1.7; }
.md :deep(h1) { font-size: 17px; margin: 10px 0 6px; }
.md :deep(h2) { font-size: 14.5px; margin: 14px 0 6px; border-left: 3px solid var(--accent); padding-left: 8px; }
.md :deep(table) {
  border-collapse: collapse;
  width: 100%;
  font-size: 11.5px;
  margin: 6px 0 10px;
}
.md :deep(table th), .md :deep(table td) {
  border: 1px solid var(--border);
  padding: 5px 8px;
  text-align: left;
}
.md :deep(table th) { background: rgba(255,255,255,0.04); font-weight: 600; }
.md :deep(blockquote) {
  border-left: 3px solid var(--teal);
  background: rgba(33,212,168,0.05);
  margin: 6px 0;
  padding: 6px 10px;
  color: var(--text-2);
  font-size: 12px;
}
</style>
