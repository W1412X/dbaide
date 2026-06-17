<script setup>
import { onMounted } from 'vue'
import { useConnectionStore } from '@/stores/connection'
import TopBar from '@/components/TopBar.vue'
import Sidebar from '@/components/Sidebar.vue'

const conn = useConnectionStore()

onMounted(() => {
  conn.bootstrap()
})
</script>

<template>
  <div class="app-layout">
    <TopBar />
    <div class="app-body">
      <Sidebar />
      <main class="app-main">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.app-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.app-main {
  flex: 1;
  overflow: auto;
  background: var(--bg);
}
</style>
