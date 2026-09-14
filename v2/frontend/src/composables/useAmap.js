/**
 * 高德 JS API 2.0 加载与地图操作封装
 */
const KEY = import.meta.env.VITE_AMAP_JSAPI_KEY
const SECURITY = import.meta.env.VITE_AMAP_SECURITY_CODE

let loadPromise = null

function injectScript() {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${KEY}&plugin=AMap.Scale,AMap.ToolBar,AMap.Driving,AMap.Walking`
    script.async = true
    script.onload = () =>
      window.AMap ? resolve(window.AMap) : reject(new Error('脚本已加载但未挂载 AMap'))
    script.onerror = () => reject(new Error('script-error'))
    document.head.appendChild(script)
  })
}

/**
 * 加载高德 JS API（全局只加载一次，失败自动重试）
 * 高德 CDN 偶发抽风，重试比直接报错体验好得多。
 */
export function loadAmap(attempt = 0) {
  if (window.AMap) return Promise.resolve(window.AMap)
  if (loadPromise) return loadPromise

  // 安全密钥必须在脚本加载前设置，否则 JS API 会拒绝服务
  if (SECURITY) {
    window._AMapSecurityConfig = { securityJsCode: SECURITY }
  }
  if (!KEY) {
    return Promise.reject(new Error('缺少 VITE_AMAP_JSAPI_KEY，请在 .env.local 中配置'))
  }

  loadPromise = injectScript()
    .catch(async (err) => {
      loadPromise = null
      if (attempt >= 2) {
        throw new Error(
          err.message === 'script-error'
            ? '高德 JS API 脚本加载失败（已重试 3 次）：检查网络，或高德控制台里该 Key 的状态与域名白名单'
            : err.message
        )
      }
      await new Promise((r) => setTimeout(r, 600 * (attempt + 1)))
      return loadAmap(attempt + 1)
    })

  return loadPromise
}

/** 一天的行程配色（Marker 用） */
export const DAY_COLORS = ['#4f8cff', '#21d4a8', '#ffb547', '#c792ea', '#ff7b72', '#67e8f9']

/**
 * 创建地图实例
 * @param {HTMLElement} container
 */
export async function createMap(container) {
  const AMap = await loadAmap()
  const map = new AMap.Map(container, {
    zoom: 11,
    center: [104.066, 30.573],
    mapStyle: 'amap://styles/darkblue',
    viewMode: '2D',
    showLabel: true,
  })
  map.addControl(new AMap.Scale())
  map.addControl(new AMap.ToolBar({ position: { top: '84px', right: '16px' } }))
  return { map, AMap }
}

/**
 * 生成一个带序号的标记 DOM
 */
export function markerContent(index, color, active = false) {
  return `<div class="xz-marker ${active ? 'is-active' : ''}" style="--c:${color}">
    <span>${index}</span>
  </div>`
}

/**
 * 往地图上添加 POI 标记
 * @returns {{marker: any, poi: any}}
 */
export function addPoiMarker(AMap, map, poi, index, color, onClick) {
  const marker = new AMap.Marker({
    position: poi.location.split(',').map(Number),
    content: markerContent(index, color),
    offset: new AMap.Pixel(-14, -14),
    zIndex: 120 + index,
    title: poi.name,
  })
  marker.on('click', () => onClick && onClick(poi))
  map.add(marker)
  return marker
}

/** 把若干坐标点用虚线连起来（行程顺序示意） */
export function drawOrderLine(AMap, map, locations, color) {
  if (locations.length < 2) return null
  const line = new AMap.Polyline({
    path: locations.map((l) => l.split(',').map(Number)),
    strokeColor: color,
    strokeWeight: 3,
    strokeOpacity: 0.62,
    strokeStyle: 'dashed',
    strokeDasharray: [9, 7],
    lineJoin: 'round',
    zIndex: 60,
  })
  map.add(line)
  return line
}

/**
 * 用 JS API 规划真实驾车路线并绘制（失败时返回 null，调用方降级为虚线）
 */
export function drawRealRoute(AMap, map, origin, destination, color) {
  return new Promise((resolve) => {
    try {
      const driving = new AMap.Driving({ map: null, hideMarkers: true, policy: 0 })
      driving.search(
        new AMap.LngLat(...origin.split(',').map(Number)),
        new AMap.LngLat(...destination.split(',').map(Number)),
        (status, result) => {
          if (status !== 'complete' || !result?.routes?.length) return resolve(null)
          const path = []
          for (const step of result.routes[0].steps) {
            path.push(...step.path.map((p) => [p.lng, p.lat]))
          }
          const line = new AMap.Polyline({
            path,
            strokeColor: color,
            strokeWeight: 4,
            strokeOpacity: 0.85,
            lineJoin: 'round',
            showDir: true,
            zIndex: 70,
          })
          map.add(line)
          resolve(line)
        }
      )
    } catch {
      resolve(null)
    }
  })
}
