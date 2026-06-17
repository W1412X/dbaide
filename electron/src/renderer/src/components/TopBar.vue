<script setup>
import { useConnectionStore } from '@/stores/connection'
import { useRouter, useRoute } from 'vue-router'
import { computed } from 'vue'
import { MessageSquare, Terminal } from 'lucide-vue-next'

const conn = useConnectionStore()
const router = useRouter()
const route = useRoute()

const currentMode = computed(() => route.name || 'Assistant')

function switchMode(mode) {
  router.push({ name: mode })
}
</script>

<template>
  <header class="topbar">
    <div class="topbar-left">
      <span class="brand">DBAide</span>
      <select
        class="conn-select"
        :value="conn.currentName"
        @change="conn.switchConnection($event.target.value)"
      >
        <option v-for="c in conn.connections" :key="c.name" :value="c.name">
          {{ c.name }} · {{ c.type }}
        </option>
      </select>
    </div>

    <div class="mode-switch">
      <button
        :class="['mode-btn', { active: currentMode === 'Assistant' }]"
        @click="switchMode('Assistant')"
      >
        <MessageSquare :size="14" />
        <span>Assistant</span>
      </button>
      <button
        :class="['mode-btn', { active: currentMode === 'Workbench' }]"
        @click="switchMode('Workbench')"
      >
        <Terminal :size="14" />
        <span>Workbench</span>
      </button>
    </div>

    <div class="topbar-right">
      <span class="status-badge">Idle</span>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  height: 42px;
  padding: 0 12px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  -webkit-app-region: drag;
  gap: 12px;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 10px;
  -webkit-app-region: no-drag;
  padding-left: 72px; /* space for macOS traffic lights */
}

.brand {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.3px;
  white-space: nowrap;
}

.conn-select {
  padding: 4px 8px;
  font-size: 12px;
  border-radius: var(--radius-md);
  max-width: 200px;
  -webkit-app-region: no-drag;
}

.mode-switch {
  display: flex;
  gap: 2px;
  background: var(--panel);
  border-radius: var(--radius-md);
  padding: 2px;
  -webkit-app-region: no-drag;
}

.mode-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 12px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 6px;
  color: var(--text-2);
  transition: all 0.15s;
}
.mode-btn:hover {
  background: var(--panel-2);
}
.mode-btn.active {
  background: var(--panel-2);
  color: var(--text);
}

.topbar-right {
  margin-left: auto;
  -webkit-app-region: no-drag;
}

.status-badge {
  font-size: 11px;
  color: var(--muted);
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  background: var(--panel);
}
</style>
