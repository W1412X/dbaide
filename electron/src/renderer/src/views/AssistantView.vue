<script setup>
import { ref } from 'vue'
import { Send } from 'lucide-vue-next'
import { dispatchStream } from '@/api'
import { useConnectionStore } from '@/stores/connection'

const conn = useConnectionStore()
const input = ref('')
const messages = ref([])
const loading = ref(false)

async function submit() {
  const question = input.value.trim()
  if (!question || loading.value) return

  messages.value.push({ role: 'user', content: question })
  input.value = ''
  loading.value = true

  try {
    const result = await dispatchStream(
      'ask',
      {
        name: conn.currentName,
        question,
      },
      (progress) => {
        // Update status during streaming
      }
    )
    messages.value.push({
      role: 'assistant',
      content: result?.answer || result?.markdown || JSON.stringify(result),
    })
  } catch (err) {
    messages.value.push({ role: 'error', content: err.message })
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="assistant-view">
    <div class="messages-area">
      <div v-if="messages.length === 0" class="welcome">
        <h2>DBAide Assistant</h2>
        <p>Ask questions about your database in natural language.</p>
      </div>

      <div
        v-for="(msg, i) in messages"
        :key="i"
        :class="['message', `message-${msg.role}`]"
      >
        <div class="message-bubble">
          {{ msg.content }}
        </div>
      </div>

      <div v-if="loading" class="message message-assistant">
        <div class="message-bubble typing">
          <span class="dot" /><span class="dot" /><span class="dot" />
        </div>
      </div>
    </div>

    <div class="composer">
      <input
        v-model="input"
        type="text"
        placeholder="Ask a question…"
        class="composer-input"
        @keydown.enter="submit"
        :disabled="loading"
      />
      <button class="send-btn" @click="submit" :disabled="!input.trim() || loading">
        <Send :size="16" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.assistant-view {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.messages-area {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.welcome {
  margin: auto;
  text-align: center;
  color: var(--muted);
}
.welcome h2 {
  font-size: 20px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 8px;
}
.welcome p {
  font-size: 13px;
}

.message {
  display: flex;
}
.message-user {
  justify-content: flex-end;
}

.message-bubble {
  max-width: 70%;
  padding: 10px 14px;
  border-radius: var(--radius-lg);
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-user .message-bubble {
  background: var(--accent);
  color: white;
  border-bottom-right-radius: 4px;
}

.message-assistant .message-bubble {
  background: var(--panel);
  color: var(--text);
  border-bottom-left-radius: 4px;
}

.message-error .message-bubble {
  background: rgba(252, 92, 101, 0.15);
  color: var(--red);
  border: 1px solid rgba(252, 92, 101, 0.3);
}

.typing {
  display: flex;
  gap: 4px;
  padding: 12px 16px;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--muted);
  animation: bounce 1.4s infinite ease-in-out;
}
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

.composer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 24px 16px;
  border-top: 1px solid var(--border);
  background: var(--surface);
}

.composer-input {
  flex: 1;
  padding: 10px 14px;
  font-size: 13px;
  border-radius: var(--radius-lg);
}

.send-btn {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--accent);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
}
.send-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}
.send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
