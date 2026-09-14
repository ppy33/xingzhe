<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import AppHeader from './components/AppHeader.vue'
import ChatPanel from './components/ChatPanel.vue'
import MapView from './components/MapView.vue'
import PlanPanel from './components/PlanPanel.vue'
import AgentTrace from './components/AgentTrace.vue'
import { chatStream, health, tripCheckStream } from './api/client.js'

// ---------- 状态 ----------
const messages = ref([])
const steps = ref([])
const pois = ref([])
const routes = ref([])
const weather = ref([])
const plan = ref('')
const planData = ref(null)  // M3 结构化行程（TripPlan JSON）
const reviewData = ref(null)  // M3 打磨：Critic 审查报告
const optimizeData = ref(null)  // M4：VRPTW 优化前后对比
const checkData = ref(null)  // M5：行程体检结果（问题 + 变更明细）
const checking = ref(false)  // M5：体检进行中
const mockRain = ref(false)  // M5：演示用「模拟雨天」开关
const threadId = ref('')
const running = ref(false)
const activeId = ref('')
const rightTab = ref('plan')
const status = ref({ ok: false, model: '' })
const startedAt = ref(0)
const finishedAt = ref(0)
const now = ref(0)

let controller = null
let ticker = null
const mapRef = ref(null)

// ---------- 派生 ----------
const toolCalls = computed(() => steps.value.filter((s) => s.type === 'tool_call').length)
const elapsed = computed(() => {
  if (!startedAt.value) return '—'
  const end = running.value ? now.value : finishedAt.value || now.value
  const ms = end - startedAt.value
  if (ms <= 0) return '—'
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
})
const stats = computed(() => ({
  toolCalls: toolCalls.value,
  poiCount: pois.value.length,
  elapsed: elapsed.value,
}))

// ---------- 从工具返回里抽结构化信息 ----------
const poiSeen = new Set()

function ingestToolResult(tool, content) {
  let data
  try {
    data = JSON.parse(content)
  } catch {
    return
  }
  if (data.error) return

  // POI 批量结果
  if ((tool === 'poi_search' || tool === 'poi_around') && Array.isArray(data.pois)) {
    for (const p of data.pois) addPoi(p)
  }
  // POI 详情（单个）
  if (tool === 'poi_detail' && data.location && data.name) addPoi(data)

  // 天气
  if (tool === 'weather_forecast' && Array.isArray(data.days)) {
    weather.value = data.days
  }

  // 路径
  if (tool === 'route_plan' && data.origin && data.destination && !data.error) {
    routes.value.push({
      origin: data.origin,
      destination: data.destination,
      mode: data.mode,
      distance_km: data.distance_km,
      duration_min: data.duration_min,
    })
  }
}

function addPoi(p) {
  if (!p || !p.location || !p.name) return
  const id = p.id || p.name
  if (poiSeen.has(id)) {
    // 详情回来了就补全字段
    const exist = pois.value.find((x) => x.id === id)
    if (exist) Object.assign(exist, { ...p, id, kind: exist.kind || kindOf(p) })
    return
  }
  poiSeen.add(id)
  pois.value.push({ ...p, id, kind: kindOf(p) })
}

/** 按高德 POI 分类码判断地点类型，用于地图过滤与配色 */
function kindOf(p) {
  const t = String(p.type || '')
  if (t.includes('交通设施服务')) return 'transit'
  if (t.includes('住宿服务')) return 'stay'
  if (t.includes('餐饮服务')) return 'food'
  if (t.includes('风景名胜') || t.includes('科教文化服务')) return 'sight'
  if (t.includes('购物服务') || t.includes('商业街')) return 'sight'
  if (t.includes('地名地址信息')) return 'address'
  return 'other'
}

// ---------- 对话 ----------
function pushStep(s) {
  steps.value.push({ ...s, ts: Date.now() })
}

async function send(text) {
  if (running.value) return
  controller = new AbortController()
  running.value = true
  startedAt.value = Date.now()
  now.value = Date.now()
  ticker = setInterval(() => (now.value = Date.now()), 100)
  plan.value = ''
  planData.value = null
  reviewData.value = null
  optimizeData.value = null
  checkData.value = null
  weather.value = []
  routes.value = []

  messages.value.push({ role: 'user', content: text })
  messages.value.push({ role: 'ai', content: '好的，我先查一下实时数据…' })

  const aiIndex = messages.value.length - 1

  try {
    await chatStream(
      text,
      threadId.value || undefined,
      (event, data) => {
        if (event === 'start') {
          threadId.value = data.thread_id
        } else if (event === 'step') {
          pushStep(data)
          if (data.type === 'tool_result') {
            ingestToolResult(data.tool, data.content)
          }
        } else if (event === 'token') {
          plan.value = data.content || ''
        } else if (event === 'plan_json') {
          planData.value = data
        } else if (event === 'review') {
          reviewData.value = data
        } else if (event === 'optimize') {
          optimizeData.value = data
        } else if (event === 'done') {
          // 收尾
        } else if (event === 'error') {
          throw new Error(data.message)
        }
      },
      controller.signal
    )

    finishedAt.value = Date.now()
    running.value = false

    if (plan.value) {
      messages.value[aiIndex] = {
        role: 'ai',
        kind: 'plan',
        brief: briefOf(plan.value),
      }
      rightTab.value = 'plan'
      await nextTick()
      mapRef.value?.fitAll()
    } else {
      messages.value[aiIndex] = { role: 'ai', content: '（没有拿到行程输出，请重试）' }
    }
  } catch (e) {
    finishedAt.value = Date.now()
    running.value = false
    const msg = e?.name === 'AbortError' ? '已停止。' : `出错了：${e.message}`
    messages.value[aiIndex] = { role: 'ai', content: msg }
  } finally {
    clearInterval(ticker)
    ticker = null
    controller = null
  }
}

// ---------- M5 行程体检 ----------
/**
 * 行程体检：调后端巡检闭馆 / 天气 / 营业时间，必要时自动改行程。
 * 结果通过 checkData 传给 PlanPanel（问题提示条 + 变更高亮）。
 */
async function runTripCheck() {
  if (!planData.value || checking.value) return
  checking.value = true
  checkData.value = null
  const mockWeather = mockRain.value
    ? { [(planData.value.days?.[0]?.date) || '']: '中雨/小雨' }
    : null
  try {
    await tripCheckStream(
      {
        // demo 模式不落库（threadId 是假的），真实会话才带 thread_id
        thread_id: threadId.value === 'demo-thread' ? '' : threadId.value || '',
        plan_json: planData.value,
        auto_fix: true,
        reoptimize: true,
        mock_weather: mockWeather,
      },
      (event, data) => {
        if (event === 'step') {
          pushStep(data)
        } else if (event === 'trip_update') {
          checkData.value = data
        } else if (event === 'plan_json') {
          planData.value = data
        } else if (event === 'optimize') {
          optimizeData.value = data
        } else if (event === 'error') {
          throw new Error(data.message)
        }
      }
    )
  } catch (e) {
    const msg = e?.name === 'AbortError' ? '体检已取消。' : `体检失败：${e.message}`
    messages.value.push({ role: 'ai', content: msg })
  } finally {
    checking.value = false
  }
}

/** 从行程 Markdown 里摘一句概览，用于对话气泡 */
function briefOf(md) {  const lines = md
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#') && !l.startsWith('|') && !l.startsWith('>'))
  const text = lines.find((l) => l.length > 12) || '行程已生成'
  return text.replace(/[*_`]/g, '').slice(0, 96)
}

function stop() {
  controller?.abort()
}

function pickTab(tab) {
  rightTab.value = tab
}

// ---------- Demo 模式 ----------
/** 预填充一份合成但结构完整的行程，用于截图/UI 验证 */
function loadDemo() {
  messages.value = [
    {
      role: 'user',
      content:
        '从上海出发，9月14日到成都玩3天，两个人，总预算3000元，喜欢美食和人文',
    },
    {
      role: 'ai',
      kind: 'plan',
      brief:
        '上海虹桥高铁往返成都东，3天2晚人均约3000元，覆盖熊猫基地、都江堰、武侯祠、锦里与春熙路商圈。',
    },
  ]

  // 结构化示例（M3 同步版）
  planData.value = {    title: '上海 → 成都 3 天 2 晚',
    overview:
      '沪上 2 人 9 月中旬入川，3 天 2 晚，人均预算 3000 元，主线为熊猫基地、都江堰、三国文化，搭配春熙路/锦里美食，覆盖地铁+高铁+打车。',
    days: [
      {
        date: '2026-09-14',
        weekday: '日',
        weather: '多云 22~28℃',
        theme: '抵达成都，市区漫步',
        items: [
          {
            time: '10:30',
            title: '抵达成都东站',
            place: { name: '成都东站', category: 'transit', address: '成都市成华区东站', location: '104.081,30.620', rating: '4.7', open_time: '全天' },
            duration_min: 30,
            notes: '建议提前 1 小时到站，留出取票/检票时间。',
            transport_to_next: '地铁 2 号线 春熙路方向，约 25 分钟',
          },
          {
            time: '12:00',
            title: '酒店入住',
            place: { name: '成都春熙路亚朵酒店', category: 'stay', address: '成都市锦江区中纱帽街 88 号', location: '104.079,30.656', rating: '4.7', cost: 380, open_time: '全天' },
            duration_min: 30,
            notes: '双人房含早，地铁 2/3 号线春熙路站 D 口步行 3 分钟。',
            transport_to_next: '步行 4 分钟（约 300 米）',
          },
          {
            time: '13:30',
            title: '春熙路 / 太古里',
            place: { name: '春熙路/太古里', category: 'sight', address: '成都市锦江区中纱帽街', location: '104.082,30.657', rating: '4.8', open_time: '10:00-22:00' },
            duration_min: 180,
            notes: 'IFS 熊猫打卡、太古里逛街，午餐推荐龙抄手（春熙路总店）。',
            transport_to_next: '地铁 2 号线 1 站 / 步行 12 分钟',
          },
          {
            time: '17:00',
            title: '人民公园鹤鸣茶社',
            place: { name: '人民公园', category: 'sight', address: '成都市青羊区小南街 8 号', location: '104.055,30.663', rating: '4.6', cost: 15, open_time: '06:30-22:00' },
            duration_min: 90,
            notes: '老成都盖碗茶，看掏耳朵，感受最地道的市井。',
            transport_to_next: '步行 8 分钟',
          },
          {
            time: '19:00',
            title: '晚餐：蜀大侠火锅',
            place: { name: '蜀大侠火锅（春熙路店）', category: 'food', address: '成都市锦江区中纱帽街 65 号', location: '104.078,30.654', rating: '4.5', cost: 120, open_time: '11:00-02:00' },
            duration_min: 120,
            notes: '必点：牛油红锅+鸭血+毛肚+鹅肠。鸳鸯锅底更保险。',
            transport_to_next: '步行回酒店',
          },
        ],
        summary: 'Day 1 主线以放松+美食为主，避免高强度暴走。',
      },
      {
        date: '2026-09-15',
        weekday: '一',
        weather: '阴 21~26℃',
        theme: '熊猫与古堰',
        items: [
          {
            time: '07:30',
            title: '前往熊猫基地',
            place: { name: '成都大熊猫繁育研究基地', category: 'sight', address: '成都市成华区熊猫大道 1375 号', location: '104.146,30.736', rating: '4.9', cost: 55, open_time: '07:30-18:00' },
            duration_min: 210,
            notes: '建议 8:00 前到，看完月亮产房和太阳产房再去小熊猫散步道。',
            transport_to_next: '打车/网约车前往犀浦站，约 35 分钟',
          },
          {
            time: '12:30',
            title: '犀浦站换乘城际',
            place: { name: '犀浦站', category: 'transit', address: '成都市郫都区犀浦镇', location: '103.945,30.770', rating: '4.5', open_time: '06:00-22:30' },
            duration_min: 30,
            notes: 'C 字头城际到都江堰，约 25 分钟，票价 15 元。',
            transport_to_next: '城际列车，约 25 分钟',
          },
          {
            time: '14:00',
            title: '都江堰景区',
            place: { name: '都江堰景区', category: 'sight', address: '成都市都江堰市公园路', location: '103.619,31.006', rating: '4.8', cost: 80, open_time: '08:00-18:00' },
            duration_min: 240,
            notes: '离堆入口 → 鱼嘴分水堤 → 飞沙堰 → 宝瓶口 → 安澜索桥 → 二王庙。',
            transport_to_next: '打车到南桥，约 10 分钟',
          },
          {
            time: '19:00',
            title: '南桥夜市 + 都江堰小吃',
            place: { name: '南桥小吃街', category: 'food', address: '都江堰市公园路南桥附近', location: '103.621,31.000', rating: '4.5', cost: 60, open_time: '11:00-23:00' },
            duration_min: 90,
            notes: '推荐：豆花饭、青城山老腊肉、葱葱卷。',
            transport_to_next: '城际返犀浦，打车回市区',
          },
        ],
        summary: 'Day 2 行程重，体力允许下可加玉垒山电梯（额外 60 元）。',
      },
      {
        date: '2026-09-16',
        weekday: '二',
        weather: '阵雨 20~25℃',
        theme: '人文与返程',
        items: [
          {
            time: '09:00',
            title: '武侯祠',
            place: { name: '武侯祠', category: 'sight', address: '成都市武侯区武侯祠大街 231 号', location: '104.047,30.642', rating: '4.7', cost: 50, open_time: '09:00-18:00' },
            duration_min: 150,
            notes: '跟着讲解员 1.5 小时，重点看诸葛亮殿、刘备殿、三绝碑。',
            transport_to_next: '步行 3 分钟',
          },
          {
            time: '12:00',
            title: '锦里古街',
            place: { name: '锦里古街', category: 'sight', address: '成都市武侯区武侯祠大街中段', location: '104.046,30.643', rating: '4.6', open_time: '全天' },
            duration_min: 180,
            notes: '午餐龙抄手（锦里店），下午买张飞牛肉、郫县豆瓣等伴手礼。',
            transport_to_next: '打车至成都东站，约 25 分钟',
          },
          {
            time: '16:00',
            title: '成都东站返程',
            place: { name: '成都东站', category: 'transit', address: '成都市成华区东站', location: '104.081,30.620', rating: '4.7', open_time: '全天' },
            duration_min: 30,
            notes: '建议提前 1 小时到站，高铁 17:30 左右的车次返沪。',
            transport_to_next: '',
          },
        ],
        summary: 'Day 3 节奏紧，出门前查好返程车次与天气。',
      },
    ],
    transportation:
      '上海虹桥 ⇄ 成都东：高铁 G 字头二等座约 900 元/人，单程约 11 小时。\n市区内以地铁为主，2/3 号线覆盖核心商圈；成都东 ⇄ 熊猫基地建议网约车，犀浦 ⇄ 都江堰走城际铁路（25 分钟/15 元）。',
    budget: [
      { category: '往返高铁', amount: '1800 元/人' },
      { category: '住宿（2 晚）', amount: '760 元（双人房）' },
      { category: '门票', amount: '255 元/人' },
      { category: '餐饮', amount: '400 元/人' },
      { category: '市内交通', amount: '120 元/人' },
      { category: '合计', amount: '约 3000 元/人' },
    ],
    tips: [
      '大熊猫基地周末人爆满，工作日优先；提前 1 天在「成都大熊猫繁育研究基地」公众号实名预约。',
      '9 月中旬成都多雨，建议随身带折叠伞 + 一双可涉水鞋（都江堰景区石阶易湿）。',
      '都江堰景区范围大，体力一般可购买观光车单程 15 元，省去鱼嘴至二王庙的爬坡。',
      '春熙路/太古里一线餐饮排队多，可提前用大众点评取号；想吃龙抄手建议避开 12:00 高峰。',
      '返程建议选 17:30 前的高铁，留出送站安检时间，避免误车。',
    ],
  }

  plan.value = `# 上海 → 成都 3 天 2 晚行程

**出发地**：上海 ｜ **人数**：2 人 ｜ **总预算**：约 3000 元/人

## 行程概览
| 日期 | 主题 | 主要地点 |
|------|------|----------|
| 9 月 14 日 | 抵达成都，市区漫步 | 成都东站 → 春熙路/太古里 → 人民公园 |
| 9 月 15 日 | 熊猫与古堰 | 成都大熊猫基地 → 都江堰景区 |
| 9 月 16 日 | 人文与返程 | 武侯祠/锦里 → 成都东站 |

## Day 1 · 9 月 14 日（周日）
- **上午**：抵达成都东站后，地铁 2 号线直达春熙路，办理入住。
- **下午**：逛太古里、IFS 熊猫打卡，步行至人民公园鹤鸣茶社喝茶。
- **晚餐**：蜀大侠火锅（春熙路店），人均约 120 元。
- **住宿**：成都春熙路亚朵酒店。

## Day 2 · 9 月 15 日（周一）
- **上午**：早起前往成都大熊猫繁育研究基地，建议 8:30 前到。
- **下午**：地铁+城际前往都江堰景区，游览离堆公园、安澜索桥。
- **晚餐**：都江堰南桥小吃街。
- **住宿**：返回市区，住宽窄巷子附近。

## Day 3 · 9 月 16 日（周二）
- **上午**：游览武侯祠，随后步行至锦里古街。
- **中午**：龙抄手（锦里店）尝成都小吃。
- **下午**：采购伴手礼后前往成都东站返程。

## 交通建议
- 上海虹桥 ↔ 成都东：高铁二等座约 900 元/人，单程约 11 小时。
- 市区内以地铁为主，前往都江堰可乘城际列车。

## 预算估算
| 项目 | 人均费用 |
|------|----------|
| 往返高铁 | 1800 元 |
| 住宿（2 晚） | 500 元 |
| 门票 | 200 元 |
| 餐饮 | 400 元 |
| 市内交通 | 100 元 |
| **合计** | **约 3000 元** |

## 温馨提示
- 大熊猫基地需提前预约，周末人流量大。
- 9 月中旬成都多雨，随身携带雨具。`

  weather.value = [
    { date: '2026-09-14', week: '日', day_weather: '多云', temp: '22~28℃' },
    { date: '2026-09-15', week: '一', day_weather: '阴', temp: '21~26℃' },
    { date: '2026-09-16', week: '二', day_weather: '阵雨', temp: '20~25℃' },
  ]

  const demoPois = [
    { id: 'cd-dong', name: '成都东站', type: '交通设施服务', address: '成都市成华区', location: '104.081,30.620', rating: '4.7' },
    { id: 'cd-chunxi', name: '春熙路/太古里', type: '购物服务|商业街', address: '成都市锦江区', location: '104.082,30.657', rating: '4.8' },
    { id: 'cd-renmin', name: '人民公园', type: '风景名胜', address: '成都市青羊区', location: '104.055,30.663', rating: '4.6' },
    { id: 'cd-panda', name: '成都大熊猫繁育研究基地', type: '风景名胜', address: '成都市成华区', location: '104.146,30.736', rating: '4.9' },
    { id: 'cd-dujiang', name: '都江堰景区', type: '风景名胜', address: '成都市都江堰市', location: '103.619,31.006', rating: '4.8' },
    { id: 'cd-wuhou', name: '武侯祠', type: '风景名胜', address: '成都市武侯区', location: '104.047,30.642', rating: '4.7' },
    { id: 'cd-jinli', name: '锦里古街', type: '风景名胜', address: '成都市武侯区', location: '104.046,30.643', rating: '4.6' },
    { id: 'cd-huoguo', name: '蜀大侠火锅（春熙路店）', type: '餐饮服务', address: '成都市锦江区', location: '104.078,30.654', rating: '4.5', cost: '120' },
    { id: 'cd-longchao', name: '龙抄手（锦里店）', type: '餐饮服务', address: '成都市武侯区', location: '104.077,30.652', rating: '4.3', cost: '45' },
    { id: 'cd-hotel1', name: '成都春熙路亚朵酒店', type: '住宿服务', address: '成都市锦江区', location: '104.079,30.656', rating: '4.7', cost: '380' },
    { id: 'cd-hotel2', name: '宽窄巷子美居酒店', type: '住宿服务', address: '成都市青羊区', location: '104.056,30.661', rating: '4.6', cost: '340' },
  ]
  pois.value = []
  poiSeen.clear()
  for (const p of demoPois) addPoi(p)

  routes.value = [
    { origin: '104.081,30.620', destination: '104.082,30.657', mode: 'driving', distance_km: 8.2, duration_min: 22 },
    { origin: '104.082,30.657', destination: '104.055,30.663', mode: 'walking', distance_km: 1.5, duration_min: 20 },
    { origin: '104.055,30.663', destination: '104.146,30.736', mode: 'driving', distance_km: 18, duration_min: 35 },
    { origin: '104.146,30.736', destination: '103.619,31.006', mode: 'driving', distance_km: 48, duration_min: 55 },
    { origin: '103.619,31.006', destination: '104.047,30.642', mode: 'driving', distance_km: 55, duration_min: 60 },
    { origin: '104.047,30.642', destination: '104.046,30.643', mode: 'walking', distance_km: 0.3, duration_min: 5 },
    { origin: '104.046,30.643', destination: '104.081,30.620', mode: 'driving', distance_km: 10, duration_min: 28 },
  ]

  const base = Date.now()
  steps.value = [
    { type: 'tool_call', tool: 'weather_forecast', args: { city: '成都' }, ts: base },
    { type: 'tool_result', tool: 'weather_forecast', content: JSON.stringify({ days: weather.value }), ts: base + 320 },
    { type: 'tool_call', tool: 'poi_search', args: { keywords: '成都 大熊猫基地' }, ts: base + 400 },
    { type: 'tool_result', tool: 'poi_search', content: JSON.stringify({ pois: [demoPois[3]] }), ts: base + 980 },
    { type: 'tool_call', tool: 'poi_search', args: { keywords: '成都 都江堰景区' }, ts: base + 1100 },
    { type: 'tool_result', tool: 'poi_search', content: JSON.stringify({ pois: [demoPois[4]] }), ts: base + 1650 },
    { type: 'tool_call', tool: 'poi_search', args: { keywords: '成都 武侯祠' }, ts: base + 1750 },
    { type: 'tool_result', tool: 'poi_search', content: JSON.stringify({ pois: [demoPois[5]] }), ts: base + 2100 },
    { type: 'tool_call', tool: 'poi_search', args: { keywords: '成都 锦里古街' }, ts: base + 2200 },
    { type: 'tool_result', tool: 'poi_search', content: JSON.stringify({ pois: [demoPois[6]] }), ts: base + 2500 },
    { type: 'tool_call', tool: 'route_plan', args: { origin: '成都东站', destination: '春熙路', mode: 'driving' }, ts: base + 2800 },
    { type: 'tool_result', tool: 'route_plan', content: JSON.stringify({ origin: '104.081,30.620', destination: '104.082,30.657', mode: 'driving', distance_km: 8.2, duration_min: 22 }), ts: base + 3200 },
    { type: 'tool_call', tool: 'critic_review', args: { round: 1, focus: 'weather,budget,time,geo' }, ts: base + 3400 },
    { type: 'tool_result', tool: 'critic_review', content: JSON.stringify({ round: 1, passed: false, issue_count: 2 }), ts: base + 4500 },
    { type: 'tool_call', tool: 'revise_plan', args: { round: 1, issues: 2 }, ts: base + 4600 },
    { type: 'tool_result', tool: 'revise_plan', content: JSON.stringify({ round: 1, chars: 1240 }), ts: base + 6800 },
    { type: 'tool_call', tool: 'critic_review', args: { round: 2, focus: 'weather,budget,time,geo' }, ts: base + 6900 },
    { type: 'tool_result', tool: 'critic_review', content: JSON.stringify({ round: 2, passed: true, issue_count: 1 }), ts: base + 7900 },
    { type: 'tool_call', tool: 'planner_struct', args: { schema: 'TripPlan' }, ts: base + 8000 },
    { type: 'tool_result', tool: 'planner_struct', content: JSON.stringify({ days: 3, items: 12, budget_items: 6, tips: 5 }), ts: base + 9500 },
    { type: 'tool_call', tool: 'optimizer_route', args: { solver: 'ortools', objective: 'travel_time', time_limit_ms: 2500 }, ts: base + 9600 },
    { type: 'tool_result', tool: 'optimizer_route', content: JSON.stringify({ applied: true, days: 2, before_min: 268, after_min: 174, saving_pct: 35.1, solve_ms: 2513 }), ts: base + 12100 },
  ]

  // M4 运筹优化对比（demo）
  // 注意：order_after 与上面 planData 里各天的实际顺序一致（优化结果已回写行程）
  optimizeData.value = {
    applied: true,
    solver: 'ortools-tspTW',
    solve_ms: 2513,
    before_min: 228,
    after_min: 156,
    before_km: 58.5,
    after_km: 38.6,
    saving_pct: 31.6,
    days: [
      {
        date: '2026-09-14',
        points: 5,
        applied: true,
        reordered: true,
        before_min: 96,
        after_min: 68,
        before_km: 26.4,
        after_km: 18.2,
        saving_pct: 29.2,
        order_before: ['成都东站', '酒店', '人民公园鹤鸣茶社', '春熙路/太古里', '蜀大侠火锅'],
        order_after: ['成都东站', '酒店', '春熙路/太古里', '人民公园鹤鸣茶社', '蜀大侠火锅'],
      },
      {
        date: '2026-09-15',
        points: 4,
        applied: true,
        reordered: true,
        before_min: 132,
        after_min: 88,
        before_km: 32.1,
        after_km: 20.4,
        saving_pct: 33.3,
        order_before: ['犀浦站换乘城际', '都江堰景区', '大熊猫基地', '南桥夜市'],
        order_after: ['大熊猫基地', '犀浦站换乘城际', '都江堰景区', '南桥夜市'],
      },
      { date: '2026-09-16', points: 3, applied: false, reason: '地点不足 4 个，未优化' },
    ],
  }

  // Critic 审查报告示例（M3 打磨同步版）
  reviewData.value = {
    passed: true,
    rounds: 2,
    revisions: 1,
    issue_count: 1,
    summary:
      '初版存在两处问题：Day 2 都江堰返程时段与晚餐时间冲突、Day 3 武侯祠→成都东站交通预算低估。Reviser 已完成 1 轮修订，并补充了一个新地点，请注意审计提示。',
    issues: [
      { dimension: 'time', severity: 'low', description: 'Day 3 武侯祠游览 150 分钟偏紧，若租讲解器需再留 30 分钟，建议 16:30 前出发前往成都东站。' },
      { dimension: 'budget', severity: 'low', description: '市内交通预算 120 元按 2 人 3 天估算，若含都江堰往返城际实际约 150 元，已在预算表备注。' },
    ],
    new_places: ['文殊院'],
  }

  finishedAt.value = base + 3200
  startedAt.value = base
  running.value = false
  threadId.value = 'demo-thread'

  const params = new URLSearchParams(window.location.search)
  rightTab.value = params.get('tab') === 'trace' ? 'trace' : 'plan'

  nextTick(() => mapRef.value?.fitAll())
}

// ---------- 初始化 ----------
onMounted(async () => {
  if (new URLSearchParams(window.location.search).has('demo')) {
    loadDemo()
  }
  try {
    const h = await health()
    status.value = { ok: h.status === 'ok', model: h.model }
  } catch {
    status.value = { ok: false, model: '' }
  }
})

onBeforeUnmount(() => {
  clearInterval(ticker)
  controller?.abort()
})
</script>

<template>
  <div class="app">
    <AppHeader :status="status" :stats="stats" />

    <main class="body">
      <ChatPanel
        class="col col--chat"
        :messages="messages"
        :running="running"
        @send="send"
        @stop="stop"
        @pick="pickTab"
      />

      <MapView
        ref="mapRef"
        class="col col--map"
        :pois="pois"
        :routes="routes"
        :running="running"
        :active-id="activeId"
        @select="(id) => (activeId = id)"
      />

      <section class="col col--right card">
        <div class="tabs">
          <button
            class="tab"
            :class="{ on: rightTab === 'plan' }"
            @click="rightTab = 'plan'"
          >
            行程
            <span v-if="plan" class="tag-dot" />
          </button>
          <button
            class="tab"
            :class="{ on: rightTab === 'trace' }"
            @click="rightTab = 'trace'"
          >
            Agent 轨迹
            <span v-if="toolCalls" class="tag-num mono">{{ toolCalls }}</span>
          </button>
        </div>
        <div class="tab-body">
          <PlanPanel
            v-show="rightTab === 'plan'"
            :plan="plan"
            :plan-data="planData"
            :review="reviewData"
            :optimize="optimizeData"
            :check="checkData"
            :checking="checking"
            :mock-rain="mockRain"
            :weather="weather"
            :running="running"
            @run-check="runTripCheck"
            @update:mock-rain="mockRain = $event"
          />
          <AgentTrace v-show="rightTab === 'trace'" :steps="steps" :running="running" />
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.app {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.body {
  flex: 1;
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr) 460px;
  gap: 12px;
  padding: 12px;
  min-height: 0;
}
.col {
  min-height: 0;
}
.col--map {
  min-height: 0;
}
.col--right {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.tab {
  flex: 1;
  padding: 12px 14px;
  font-size: 12.5px;
  color: var(--muted);
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  transition: color 0.15s;
}
.tab:hover {
  color: var(--text-2);
}
.tab.on {
  color: var(--text);
  font-weight: 600;
}
.tab.on::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 20%;
  right: 20%;
  height: 2px;
  border-radius: 2px;
  background: linear-gradient(90deg, var(--accent), var(--teal));
}
.tag-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--teal);
  box-shadow: 0 0 7px var(--teal);
}
.tag-num {
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid var(--border);
}
.tab-body {
  flex: 1;
  min-height: 0;
}

@media (max-width: 1560px) {
  .body {
    grid-template-columns: 320px minmax(0, 1fr) 400px;
  }
}
@media (max-width: 1280px) {
  .body {
    grid-template-columns: 300px minmax(0, 1fr);
    grid-template-rows: 1fr 340px;
  }
  .col--right {
    grid-column: 1 / -1;
  }
}
</style>
