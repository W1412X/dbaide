import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { dispatch } from '@/api'

export const useConnectionStore = defineStore('connection', () => {
  const connections = ref([])
  const currentName = ref('')
  const models = ref([])
  const defaultModel = ref('')
  const schemaTree = ref([])
  const loading = ref(false)

  const current = computed(() =>
    connections.value.find((c) => c.name === currentName.value) || null
  )

  async function bootstrap() {
    loading.value = true
    try {
      const data = await dispatch('bootstrap')
      connections.value = data.connections || []
      models.value = data.models || []
      defaultModel.value = data.default_model || ''
      const def = data.default_connection || ''
      if (def) currentName.value = def
      else if (connections.value.length) currentName.value = connections.value[0].name
    } finally {
      loading.value = false
    }
  }

  async function switchConnection(name) {
    currentName.value = name
    await loadSchema()
  }

  async function loadSchema() {
    if (!currentName.value) return
    const data = await dispatch('schema_tree', { name: currentName.value })
    schemaTree.value = data.tree || []
  }

  return {
    connections,
    currentName,
    current,
    models,
    defaultModel,
    schemaTree,
    loading,
    bootstrap,
    switchConnection,
    loadSchema,
  }
})
