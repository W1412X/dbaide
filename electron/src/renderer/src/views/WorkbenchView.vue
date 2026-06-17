<script setup>
import { ref } from 'vue'
import { Play, Plus } from 'lucide-vue-next'
import { dispatch } from '@/api'
import { useConnectionStore } from '@/stores/connection'

const conn = useConnectionStore()
const sql = ref('SELECT 1')
const results = ref(null)
const error = ref('')
const loading = ref(false)
const tabs = ref([{ id: 1, name: 'Query 1' }])
const activeTab = ref(1)

async function executeSql() {
  if (!sql.value.trim() || loading.value) return
  loading.value = true
  error.value = ''
  results.value = null

  try {
    const data = await dispatch('execute_sql', {
      name: conn.currentName,
      sql: sql.value,
    })
    results.value = data
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function addTab() {
  const id = Math.max(...tabs.value.map((t) => t.id)) + 1
  tabs.value.push({ id, name: `Query ${id}` })
  activeTab.value = id
}
</script>

<template>
  <div class="workbench-view">
    <!-- Tab bar -->
    <div class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        :class="['tab', { active: tab.id === activeTab }]"
        @click="activeTab = tab.id"
      >
        {{ tab.name }}
      </button>
      <button class="tab add-tab" @click="addTab">
        <Plus :size="14" />
      </button>
    </div>

    <!-- Editor -->
    <div class="editor-pane">
      <textarea
        v-model="sql"
        class="sql-editor"
        spellcheck="false"
        placeholder="Enter SQL…"
        @keydown.ctrl.enter="executeSql"
        @keydown.meta.enter="executeSql"
      />
      <div class="editor-toolbar">
        <button class="run-btn" @click="executeSql" :disabled="loading">
          <Play :size="14" />
          <span>Run</span>
        </button>
        <span class="shortcut-hint">⌘ Enter</span>
      </div>
    </div>

    <!-- Results -->
    <div class="results-pane">
      <div v-if="error" class="result-error">{{ error }}</div>

      <div v-else-if="results && results.columns" class="result-table-wrap">
        <table class="result-table">
          <thead>
            <tr>
              <th v-for="col in results.columns" :key="col">{{ col }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in results.rows" :key="i">
              <td v-for="(val, j) in row" :key="j">{{ val ?? 'NULL' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="result-footer">
          {{ results.rows.length }} rows
          <span v-if="results.elapsed_ms"> · {{ results.elapsed_ms }}ms</span>
        </div>
      </div>

      <div v-else-if="loading" class="result-loading">Running…</div>

      <div v-else class="result-empty">Results will appear here</div>
    </div>
  </div>
</template>

<style scoped>
.workbench-view {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.tab-bar {
  display: flex;
  align-items: center;
  gap: 1px;
  padding: 4px 8px 0;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}

.tab {
  padding: 6px 14px;
  font-size: 12px;
  color: var(--text-2);
  border-radius: 6px 6px 0 0;
  border: 1px solid transparent;
  border-bottom: none;
  transition: all 0.1s;
}
.tab:hover { color: var(--text); }
.tab.active {
  background: var(--bg);
  color: var(--text);
  border-color: var(--border);
}
.add-tab {
  padding: 6px 8px;
  color: var(--muted);
}
.add-tab:hover { color: var(--text); }

.editor-pane {
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid var(--border);
  min-height: 120px;
  max-height: 40%;
}

.sql-editor {
  flex: 1;
  resize: none;
  border: none;
  border-radius: 0;
  padding: 12px 16px;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  background: var(--bg);
  color: var(--text);
  outline: none;
}

.editor-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: var(--surface);
}

.run-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 14px;
  background: var(--accent);
  color: white;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  transition: background 0.15s;
}
.run-btn:hover:not(:disabled) { background: var(--accent-hover); }
.run-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.shortcut-hint {
  font-size: 11px;
  color: var(--muted);
}

.results-pane {
  flex: 1;
  overflow: auto;
}

.result-error {
  padding: 16px;
  color: var(--red);
  font-size: 13px;
  white-space: pre-wrap;
}

.result-loading, .result-empty {
  padding: 24px;
  color: var(--muted);
  text-align: center;
  font-size: 12px;
}

.result-table-wrap {
  overflow: auto;
  height: 100%;
}

.result-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  font-family: var(--font-mono);
}
.result-table th {
  position: sticky;
  top: 0;
  background: var(--surface);
  color: var(--muted);
  font-weight: 500;
  text-align: left;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.result-table td {
  padding: 5px 10px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.result-table tr:hover td {
  background: var(--panel);
}

.result-footer {
  padding: 6px 12px;
  font-size: 11px;
  color: var(--muted);
  border-top: 1px solid var(--border);
  background: var(--surface);
  position: sticky;
  bottom: 0;
}
</style>
