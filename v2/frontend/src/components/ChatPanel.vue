<script setup>
import { ref, nextTick, watch } from 'vue'

const props = defineProps({
  messages: { type: Array, required: true },
  running: { type: Boolean, default: false },
})
const emit = defineEmits(['send', 'stop', 'pick'])

const input = ref('')
const listEl = ref(null)

const EXAMPLES = [
  '从上海出发，9月14日到成都玩3天，两个人，总预算3000元，喜欢美食和人文',
  '帮我规划西安2天亲子游，孩子6岁，想少走路',
  '杭州3天，预算充足，想吃好住好',
]

function send() {
  const text = input.value.trim()
  if (!text || props.running) return
  emit('send', text)
  input.value = ''
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function useExample(text) {
  input.value = text
  send()
}

watch(
  () => props.messages.length,
  async () => {
    await nextTick()
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
  }
)
</script>

<template>
  <aside class="chat card">
    <div class="head">
      <span class="title">对话</span>
      <span v-if="running" class="chip chip--accent">
        <i class="spin" />规划中
      </span>
    </div>

    <div ref="listEl" class="list">
      <div v-if="!messages.length" class="empty">
        <div class="empty-icon">🧭</div>
        <p class="empty-title">说一句话，我给你排一趟真行程</p>
        <p class="empty-desc">
          景点、天气、交通、营业时间都来自高德实时数据，不是模板套话。
        </p>
        <div class="examples">
          <button v-for="ex in EXAMPLES" :key="ex" class="example" @click="useExample(ex)">
            {{ ex }}
          </button>
        </div>
      </div>

      <div
        v-for="(m, i) in messages"
        :key="i"
        class="msg"
        :class="m.role === 'user' ? 'msg--user' : 'msg--ai'"
      >
        <div class="avatar" :class="m.role">{{ m.role === 'user' ? '我' : '行' }}</div>
        <div class="bubble">
          <template v-if="m.role === 'ai' && m.kind === 'plan'">
            <div class="plan-done">
              <span class="chip chip--teal">行程已生成</span>
              <button class="link-btn" @click="emit('pick', 'plan')">在右侧查看 →</button>
            </div>
            <p class="plan-brief">{{ m.brief }}</p>
          </template>
          <template v-else>{{ m.content }}</template>
        </div>
      </div>
    </div>

    <div class="composer">
      <textarea
        v-model="input"
        rows="2"
        placeholder="例如：成都玩3天，喜欢美食和人文（Enter 发送，Shift+Enter 换行）"
        @keydown="onKeydown"
      />
      <button v-if="running" class="btn btn--stop" @click="emit('stop')">停止</button>
      <button v-else class="btn" :disabled="!input.trim()" @click="send">发送</button>
    </div>
  </aside>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 16px;
  border-bottom: 1px solid var(--border);
}
.title {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.4px;
}
.spin {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 1.5px solid rgba(157, 192, 255, 0.35);
  border-top-color: #9dc0ff;
  animation: spin 0.75s linear infinite;
}

.list {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
}

.empty {
  padding: 18px 6px;
  text-align: center;
}
.empty-icon {
  font-size: 30px;
  margin-bottom: 10px;
}
.empty-title {
  font-size: 13.5px;
  font-weight: 600;
  margin-bottom: 6px;
}
.empty-desc {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.7;
  margin-bottom: 16px;
}
.examples {
  display: flex;
  flex-direction: column;
  gap: 8px;
  text-align: left;
}
.example {
  padding: 9px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.03);
  color: var(--text-2);
  font-size: 12px;
  line-height: 1.6;
  transition: all 0.16s;
}
.example:hover {
  border-color: rgba(79, 140, 255, 0.42);
  background: var(--accent-soft);
  color: var(--text);
}

.msg {
  display: flex;
  gap: 9px;
  animation: slide-in 0.24s ease;
}
.msg--user {
  flex-direction: row-reverse;
}
.avatar {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  font-size: 11.5px;
  font-weight: 600;
  flex-shrink: 0;
}
.avatar.user {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text);
}
.avatar.ai {
  background: linear-gradient(135deg, var(--accent), var(--teal));
  color: #fff;
}
.bubble {
  max-width: 82%;
  padding: 9px 12px;
  border-radius: 11px;
  font-size: 12.8px;
  line-height: 1.72;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg--user .bubble {
  background: var(--accent-soft);
  border: 1px solid rgba(79, 140, 255, 0.26);
  border-bottom-right-radius: 3px;
}
.msg--ai .bubble {
  background: rgba(255, 255, 255, 0.045);
  border: 1px solid var(--border);
  border-bottom-left-radius: 3px;
}
.plan-done {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.link-btn {
  font-size: 11.5px;
  color: #7fabff;
}
.link-btn:hover {
  text-decoration: underline;
}
.plan-brief {
  color: var(--muted);
  font-size: 12px;
}

.composer {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  padding: 12px;
  border-top: 1px solid var(--border);
}
textarea {
  flex: 1;
  padding: 9px 11px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: rgba(0, 0, 0, 0.24);
  color: var(--text);
  font-size: 12.8px;
  line-height: 1.6;
  transition: border-color 0.16s;
}
textarea:focus {
  border-color: rgba(79, 140, 255, 0.5);
}
textarea::placeholder {
  color: #55637a;
}

.btn {
  padding: 9px 18px;
  border-radius: var(--radius-sm);
  background: linear-gradient(135deg, var(--accent), #3d78e6);
  color: #fff;
  font-size: 12.8px;
  font-weight: 600;
  transition: filter 0.16s;
  white-space: nowrap;
}
.btn:hover:not(:disabled) {
  filter: brightness(1.12);
}
.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.btn--stop {
  background: rgba(255, 95, 109, 0.16);
  border: 1px solid rgba(255, 95, 109, 0.4);
  color: #ff8a94;
}
</style>
