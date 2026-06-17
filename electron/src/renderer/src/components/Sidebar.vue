<script setup>
import { ref, computed, watch } from 'vue'
import { useConnectionStore } from '@/stores/connection'
import { ChevronRight, ChevronDown, Database, Table2, Columns3, MoreHorizontal, Search } from 'lucide-vue-next'

const conn = useConnectionStore()
const searchQuery = ref('')
const expanded = ref(new Set())

function toggleNode(path) {
  if (expanded.value.has(path)) {
    expanded.value.delete(path)
  } else {
    expanded.value.add(path)
  }
}

function isExpanded(path) {
  return expanded.value.has(path)
}

const filteredTree = computed(() => {
  if (!searchQuery.value.trim()) return conn.schemaTree
  const q = searchQuery.value.toLowerCase()
  return filterNodes(conn.schemaTree, q)
})

function filterNodes(nodes, q) {
  const result = []
  for (const node of nodes) {
    const nameMatch = (node.name || '').toLowerCase().includes(q)
    const childMatches = node.children ? filterNodes(node.children, q) : []
    if (nameMatch || childMatches.length) {
      result.push({
        ...node,
        children: nameMatch ? node.children : childMatches,
      })
    }
  }
  return result
}

watch(() => conn.currentName, () => {
  expanded.value.clear()
})
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-search">
      <Search :size="13" class="search-icon" />
      <input
        v-model="searchQuery"
        type="text"
        placeholder="Search schema…"
        class="search-input"
      />
    </div>

    <div class="tree-container">
      <template v-if="filteredTree.length === 0">
        <div class="empty-state">
          No schema loaded. Select a connection.
        </div>
      </template>

      <div v-for="db in filteredTree" :key="db.path" class="tree-node">
        <div class="tree-row db-row" @click="toggleNode(db.path)">
          <component :is="isExpanded(db.path) ? ChevronDown : ChevronRight" :size="12" class="chevron" />
          <Database :size="13" class="node-icon db-icon" />
          <span class="node-name">{{ db.name }}</span>
          <button class="more-btn" @click.stop>
            <MoreHorizontal :size="13" />
          </button>
        </div>

        <template v-if="isExpanded(db.path) && db.children">
          <div v-for="tbl in db.children" :key="tbl.path" class="tree-node nested">
            <div class="tree-row table-row" @click="toggleNode(tbl.path)">
              <component :is="isExpanded(tbl.path) ? ChevronDown : ChevronRight" :size="12" class="chevron" />
              <Table2 :size="13" class="node-icon table-icon" />
              <span class="node-name">{{ tbl.name }}</span>
              <span v-if="tbl.column_count" class="col-count">{{ tbl.column_count }}</span>
              <button class="more-btn" @click.stop>
                <MoreHorizontal :size="13" />
              </button>
            </div>

            <template v-if="isExpanded(tbl.path) && tbl.children">
              <div v-for="col in tbl.children" :key="col.path" class="tree-node nested-2">
                <div class="tree-row col-row">
                  <Columns3 :size="12" class="node-icon col-icon" />
                  <span class="node-name">{{ col.name }}</span>
                  <span class="col-type">{{ col.data_type }}</span>
                </div>
              </div>
            </template>
          </div>
        </template>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 260px;
  min-width: 200px;
  max-width: 360px;
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sidebar-search {
  display: flex;
  align-items: center;
  padding: 8px 10px;
  gap: 6px;
  border-bottom: 1px solid var(--border);
}

.search-icon {
  color: var(--muted);
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  border: none;
  background: transparent;
  padding: 4px 0;
  font-size: 12px;
  outline: none;
}
.search-input::placeholder {
  color: var(--muted);
}

.tree-container {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}

.empty-state {
  padding: 24px 16px;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
}

.tree-node.nested { padding-left: 16px; }
.tree-node.nested-2 { padding-left: 32px; }

.tree-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  cursor: pointer;
  border-radius: 4px;
  margin: 0 4px;
  transition: background 0.1s;
}
.tree-row:hover {
  background: var(--panel);
}

.chevron {
  color: var(--muted);
  flex-shrink: 0;
}

.node-icon {
  flex-shrink: 0;
}
.db-icon { color: var(--accent); }
.table-icon { color: var(--green); }
.col-icon { color: var(--muted); }

.node-name {
  flex: 1;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.col-count {
  font-size: 10px;
  color: var(--muted);
  background: var(--panel);
  border-radius: 3px;
  padding: 0 4px;
  flex-shrink: 0;
}

.col-type {
  font-size: 10px;
  color: var(--muted);
  font-family: var(--font-mono);
  flex-shrink: 0;
}

.more-btn {
  opacity: 0;
  color: var(--text-2);
  padding: 2px;
  border-radius: 3px;
  flex-shrink: 0;
  transition: opacity 0.1s;
}
.tree-row:hover .more-btn {
  opacity: 1;
}
.more-btn:hover {
  background: var(--panel-2);
}
</style>
