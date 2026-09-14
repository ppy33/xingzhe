<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  current: { type: Object, default: () => ({}) },
  saving: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'save'])

const form = ref({ api_key: '', base_url: '', model: '', model_fast: '' })

watch(
  () => props.open,
  (v) => {
    if (v && props.current) {
      form.value = {
        api_key: '',
        base_url: props.current.base_url || '',
        model: props.current.model || '',
        model_fast: props.current.model_fast || '',
      }
    }
  }
)

function submit() {
  emit('save', { ...form.value })
}
</script>

<template>
  <div v-if="open" class="mask" @click.self="emit('close')">
    <div class="panel">
      <div class="head">
        <span class="title">接入你自己的模型</span>
        <button class="x" @click="emit('close')">✕</button>
      </div>

      <p class="hint">
        不填则使用服务端默认配置。填了 API Key 即可接入任意 OpenAI 兼容模型（DeepSeek / OpenAI / 智谱 / Moonshot 等）。
      </p>

      <div class="field">
        <label>API Key</label>
        <input
          v-model="form.api_key"
          type="password"
          placeholder="sk-..."
          autocomplete="off"
        />
      </div>
      <div class="field">
        <label>Base URL</label>
        <input v-model="form.base_url" placeholder="https://api.deepseek.com/v1" />
      </div>
      <div class="field-row">
        <div class="field">
          <label>主模型</label>
          <input v-model="form.model" placeholder="deepseek-v4-pro" />
        </div>
        <div class="field">
          <label>快速模型（审查用）</label>
          <input v-model="form.model_fast" placeholder="deepseek-flash" />
        </div>
      </div>

      <div class="actions">
        <button class="btn ghost" @click="emit('close')">取消</button>
        <button class="btn primary" :disabled="saving" @click="submit">
          {{ saving ? '保存中…' : '保存并生效' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mask {
  position: fixed;
  inset: 0;
  background: rgba(4, 8, 16, 0.62);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.panel {
  width: 480px;
  background: #10162a;
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 24px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.5);
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.title {
  font-size: 16px;
  font-weight: 600;
}
.x {
  background: none;
  border: none;
  color: var(--muted);
  font-size: 16px;
  cursor: pointer;
}
.hint {
  font-size: 12.5px;
  color: var(--muted);
  line-height: 1.6;
  margin: 0 0 16px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}
.field-row {
  display: flex;
  gap: 12px;
}
.field-row .field {
  flex: 1;
}
label {
  font-size: 12px;
  color: var(--text-2);
}
input {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 9px 12px;
  color: var(--text);
  font-size: 13px;
  font-family: inherit;
}
input:focus {
  outline: none;
  border-color: var(--accent);
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}
.btn {
  padding: 9px 18px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid var(--border);
}
.ghost {
  background: transparent;
  color: var(--text-2);
}
.primary {
  background: linear-gradient(135deg, var(--accent), var(--teal));
  color: #fff;
  border: none;
  font-weight: 600;
}
.primary:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
