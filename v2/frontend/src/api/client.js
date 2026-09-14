/**
 * 后端接口客户端
 * - chat()       单轮，返回完整结果
 * - chatStream() SSE 流式，逐事件回调（Agent 轨迹面板用）
 */
const BASE = import.meta.env.VITE_API_BASE || ''

export async function health() {
  const res = await fetch(`${BASE}/health`)
  if (!res.ok) throw new Error(`health ${res.status}`)
  return res.json()
}

export async function chat(message, threadId) {
  const res = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, thread_id: threadId }),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail || `请求失败 ${res.status}`)
  }
  return res.json()
}

/**
 * 通用 SSE 读取：把响应体按事件回调出去（chat/stream 与 trip/check/stream 共用）
 */
async function readSSE(res, onEvent) {
  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail || `请求失败 ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE 事件以空行分隔
    let idx
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)

      let event = 'message'
      const dataLines = []
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (!dataLines.length) continue
      try {
        onEvent(event, JSON.parse(dataLines.join('\n')))
      } catch {
        onEvent(event, { raw: dataLines.join('\n') })
      }
    }
  }
}

/**
 * SSE 流式对话
 * @param {string} message
 * @param {string|undefined} threadId
 * @param {(event: string, data: any) => void} onEvent
 * @param {AbortSignal} [signal]
 */
export async function chatStream(message, threadId, onEvent, signal) {
  const res = await fetch(`${BASE}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, thread_id: threadId }),
    signal,
  })
  return readSSE(res, onEvent)
}

/**
 * 行程体检（SSE）
 * @param {{thread_id?: string, plan_json?: object, auto_fix?: boolean, reoptimize?: boolean, mock_weather?: object|null}} payload
 * @param {(event: string, data: any) => void} onEvent
 * @param {AbortSignal} [signal]
 */
export async function tripCheckStream(payload, onEvent, signal) {
  const res = await fetch(`${BASE}/api/trip/check/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
  return readSSE(res, onEvent)
}

/** 历史行程列表（M5 持久化） */
export async function listTrips(limit = 20) {
  const res = await fetch(`${BASE}/api/trips?limit=${limit}`)
  if (!res.ok) throw new Error(`trips ${res.status}`)
  return res.json()
}

/** 埋点汇总统计 + 最近明细（M6 可观测） */
export async function fetchMetrics(limit = 50) {
  const res = await fetch(`${BASE}/api/metrics?limit=${limit}`)
  if (!res.ok) throw new Error(`metrics ${res.status}`)
  return res.json()
}

/** 读取当前 LLM 配置（api_key 脱敏） */
export async function getLlmConfig() {
  const res = await fetch(`${BASE}/api/llm-config`)
  if (!res.ok) throw new Error(`llm-config ${res.status}`)
  return res.json()
}

/** 保存自定义 LLM 配置（api_key/base_url/model/model_fast，空值回落默认） */
export async function setLlmConfig(payload) {
  const res = await fetch(`${BASE}/api/llm-config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`llm-config ${res.status}`)
  return res.json()
}
