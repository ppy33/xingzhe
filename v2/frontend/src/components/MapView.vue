<script setup>
import { ref, watch, onMounted, onBeforeUnmount, computed } from 'vue'
import {
  createMap,
  addPoiMarker,
  drawOrderLine,
  drawRealRoute,
} from '../composables/useAmap.js'

const props = defineProps({
  pois: { type: Array, required: true },
  routes: { type: Array, default: () => [] },
  running: { type: Boolean, default: false },
  activeId: { type: String, default: '' },
})
const emit = defineEmits(['select'])

const container = ref(null)
const loadError = ref('')
const mapReady = ref(false)
const retrying = ref(false)
const showRoutes = ref(true)
const realRoute = ref(true)
const showTransit = ref(false)

let AMap = null
let map = null
let markers = []      // 与 visiblePois 同序
let orderLine = null
let routeObjects = []
let renderedMode = null

/** 默认隐藏地铁站/停车场/机场和纯地址点，否则地图会被搜坐标的过程塞满 */
const visiblePois = computed(() =>
  props.pois.filter((p) =>
    showTransit.value ? p.kind !== 'address' : p.kind !== 'transit' && p.kind !== 'address'
  )
)

const activePoi = computed(() => props.pois.find((p) => p.id === props.activeId) || null)

const KIND_COLOR = {
  sight: '#4f8cff',
  food: '#ffb547',
  stay: '#c792ea',
  transit: '#67e8f9',
  other: '#21d4a8',
}

/** 按地点类型配色（同一类型同色，比按序号更易读） */
function colorOf(poi) {
  return KIND_COLOR[poi?.kind] || KIND_COLOR.other
}

async function initMap() {
  loadError.value = ''
  try {
    const created = await createMap(container.value)
    AMap = created.AMap
    map = created.map
    mapReady.value = true
    syncMarkers()
  } catch (e) {
    loadError.value = e.message || String(e)
  }
}

/** 手动重试加载地图 */
async function retry() {
  retrying.value = true
  await initMap()
  retrying.value = false
}

/** 增量添加新发现的 POI 标记；过滤模式变化时重建 */
function syncMarkers(force = false) {
  if (!mapReady.value) return
  const mode = showTransit.value ? 'all' : 'main'
  if (force || renderedMode !== mode) {
    markers.forEach((m) => map.remove(m))
    markers = []
    renderedMode = mode
  }

  const list = visiblePois.value
  while (markers.length < list.length) {
    const idx = markers.length
    const poi = list[idx]
    const marker = addPoiMarker(AMap, map, poi, idx + 1, colorOf(poi), (p) =>
      emit('select', p.id)
    )
    marker.setMap(map)
    markers.push(marker)
  }

  // 顺序连线（只连可见点）
  if (orderLine) map.remove(orderLine)
  orderLine = drawOrderLine(
    AMap,
    map,
    list.map((p) => p.location),
    '#4f8cff'
  )
}

/** 绘制路线（优先真实路径，失败降级虚线） */
async function syncRoutes() {
  if (!mapReady.value) return
  routeObjects.forEach((o) => map.remove(o))
  routeObjects = []
  if (!showRoutes.value) return

  for (const r of props.routes) {
    let line = null
    if (realRoute.value && r.origin && r.destination) {
      line = await drawRealRoute(AMap, map, r.origin, r.destination, '#21d4a8')
    }
    if (!line && r.origin && r.destination) {
      line = new AMap.Polyline({
        path: [r.origin, r.destination].map((l) => l.split(',').map(Number)),
        strokeColor: '#21d4a8',
        strokeWeight: 3,
        strokeOpacity: 0.6,
        strokeStyle: 'dashed',
        zIndex: 70,
      })
      map.add(line)
    }
    if (line) routeObjects.push(line)
  }
}

function fitAll() {
  if (!mapReady.value || !markers.length) return
  map.setFitView(markers, false, [70, 70, 70, 70])
}

function focusPoi(poi) {
  if (!mapReady.value || !poi) return
  map.setZoomAndCenter(15, poi.location.split(',').map(Number))
}

watch(() => props.pois.length, () => syncMarkers())
watch(showTransit, () => syncMarkers(true))
watch(() => props.routes.length, syncRoutes)
watch(showRoutes, syncRoutes)
watch(realRoute, syncRoutes)
watch(
  () => props.activeId,
  (id) => {
    const poi = props.pois.find((p) => p.id === id)
    if (poi) focusPoi(poi)
  }
)

onMounted(initMap)
onBeforeUnmount(() => {
  if (map) map.destroy()
})

defineExpose({ fitAll, focusPoi })
</script>

<template>
  <div class="map-wrap card">
    <div ref="container" class="map" />

    <!-- 加载中 -->
    <div v-if="!mapReady && !loadError" class="overlay">
      <div class="loader" />
      <p>正在加载高德地图…</p>
    </div>

    <!-- 加载失败 -->
    <div v-if="loadError" class="overlay overlay--error">
      <p class="err-title">地图加载失败</p>
      <p class="err-desc">{{ loadError }}</p>
      <p class="err-hint">
        检查 <span class="mono">.env.local</span> 里的 JSAPI Key 与安全密钥，以及高德控制台的域名白名单
        （开发期填 <span class="mono">localhost</span>）
      </p>
      <button class="retry" :disabled="retrying" @click="retry">
        {{ retrying ? '重试中…' : '重试加载地图' }}
      </button>
    </div>

    <!-- Agent 实时状态条 -->
    <transition name="fade">
      <div v-if="running" class="live">
        <i class="pulse" />
        <span>Agent 正在检索实时数据，地图会随发现自动长出标记…</span>
      </div>
    </transition>

    <!-- 左上角控制 -->
    <div class="controls">
      <button class="ctrl" :class="{ on: showRoutes }" @click="showRoutes = !showRoutes">
        路线 {{ showRoutes ? '开' : '关' }}
      </button>
      <button class="ctrl" :class="{ on: realRoute }" @click="realRoute = !realRoute">
        真实路径
      </button>
      <button class="ctrl" :class="{ on: showTransit }" @click="showTransit = !showTransit">
        含交通点
      </button>
      <button class="ctrl" @click="fitAll">全览</button>
    </div>

    <!-- 图例 -->
    <div v-if="visiblePois.length" class="legend">
      <span class="lg"><i style="background: #4f8cff" />景点</span>
      <span class="lg"><i style="background: #ffb547" />餐饮</span>
      <span class="lg"><i style="background: #c792ea" />住宿</span>
      <span v-if="showTransit" class="lg"><i style="background: #67e8f9" />交通</span>
    </div>

    <!-- 右侧地点列表 -->
    <div v-if="visiblePois.length" class="poi-panel">
      <div class="poi-head">
        <span>已发现 {{ visiblePois.length }} 个地点</span>
        <span v-if="visiblePois.length < pois.length" class="muted">
          另隐藏 {{ pois.length - visiblePois.length }} 个交通点
        </span>
      </div>
      <div class="poi-list">
        <button
          v-for="(p, i) in visiblePois"
          :key="p.id"
          class="poi"
          :class="{ active: p.id === activeId }"
          @click="emit('select', p.id)"
        >
          <span class="poi-idx" :style="{ background: colorOf(p) }">{{ i + 1 }}</span>
          <span class="poi-body">
            <span class="poi-name">{{ p.name }}</span>
            <span class="poi-meta">
              <span v-if="p.rating" class="rate">★ {{ p.rating }}</span>
              <span v-if="p.cost" class="cost">¥{{ p.cost }}</span>
              <span v-if="p.open_time" class="hours">{{ String(p.open_time).slice(0, 16) }}</span>
            </span>
          </span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.map-wrap {
  position: relative;
  overflow: hidden;
  min-height: 0;
}
.map {
  position: absolute;
  inset: 0;
}

.overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: rgba(7, 11, 22, 0.86);
  color: var(--text-2);
  font-size: 13px;
  z-index: 20;
  padding: 24px;
  text-align: center;
}
.loader {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: 2px solid rgba(79, 140, 255, 0.24);
  border-top-color: var(--accent);
  animation: spin 0.8s linear infinite;
}
.overlay--error {
  color: #ff9aa3;
}
.err-title {
  font-size: 15px;
  font-weight: 600;
}
.err-desc {
  font-size: 12.5px;
  max-width: 440px;
}
.err-hint {
  font-size: 11.5px;
  color: var(--muted);
  max-width: 460px;
  line-height: 1.7;
}
.retry {
  margin-top: 4px;
  padding: 7px 18px;
  border-radius: 8px;
  font-size: 12.5px;
  color: #fff;
  background: linear-gradient(135deg, var(--accent), #3d78e6);
  transition: filter 0.15s;
}
.retry:hover:not(:disabled) {
  filter: brightness(1.12);
}
.retry:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.live {
  position: absolute;
  top: 14px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 15px;
  border-radius: 999px;
  background: rgba(9, 14, 28, 0.9);
  border: 1px solid rgba(79, 140, 255, 0.36);
  font-size: 12px;
  color: #b6cdff;
  z-index: 12;
  backdrop-filter: blur(10px);
  white-space: nowrap;
}
.pulse {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 9px var(--accent);
  animation: pulse-dot 1.15s infinite;
}

.controls {
  position: absolute;
  top: 14px;
  left: 14px;
  display: flex;
  gap: 7px;
  z-index: 12;
}
.ctrl {
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 11.5px;
  background: rgba(9, 14, 28, 0.85);
  border: 1px solid var(--border);
  color: var(--text-2);
  backdrop-filter: blur(10px);
  transition: all 0.15s;
}
.ctrl:hover {
  border-color: var(--border-strong);
  color: var(--text);
}
.ctrl.on {
  background: var(--accent-soft);
  border-color: rgba(79, 140, 255, 0.42);
  color: #9dc0ff;
}

.legend {
  position: absolute;
  left: 14px;
  bottom: 14px;
  display: flex;
  gap: 12px;
  padding: 6px 12px;
  border-radius: 999px;
  background: rgba(9, 14, 28, 0.86);
  border: 1px solid var(--border);
  backdrop-filter: blur(10px);
  font-size: 11px;
  color: var(--text-2);
  z-index: 12;
}
.lg {
  display: flex;
  align-items: center;
  gap: 5px;
}
.lg i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.poi-panel {
  position: absolute;
  top: 14px;
  right: 14px;
  bottom: 14px;
  width: 218px;
  background: rgba(9, 14, 28, 0.9);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  backdrop-filter: blur(14px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  z-index: 12;
}
.poi-head {
  padding: 9px 12px;
  font-size: 11.5px;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.poi-head .muted {
  font-size: 10.5px;
  color: #55637a;
}
.poi-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
}
.poi {
  display: flex;
  gap: 8px;
  width: 100%;
  padding: 7px 8px;
  border-radius: 7px;
  text-align: left;
  transition: background 0.14s;
}
.poi:hover {
  background: rgba(255, 255, 255, 0.05);
}
.poi.active {
  background: var(--accent-soft);
}
.poi-idx {
  width: 17px;
  height: 17px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 10px;
  font-weight: 700;
  color: #06101f;
  flex-shrink: 0;
  margin-top: 1px;
}
.poi-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.poi-name {
  font-size: 12px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.poi-meta {
  display: flex;
  gap: 7px;
  font-size: 10.5px;
  color: var(--muted);
}
.rate {
  color: var(--warn);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
