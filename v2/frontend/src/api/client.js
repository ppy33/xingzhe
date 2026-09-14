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
