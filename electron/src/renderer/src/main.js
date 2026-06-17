import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import './assets/style.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      redirect: '/assistant',
    },
    {
      path: '/assistant',
      name: 'Assistant',
      component: () => import('./views/AssistantView.vue'),
    },
    {
      path: '/workbench',
      name: 'Workbench',
      component: () => import('./views/WorkbenchView.vue'),
    },
  ],
})

const pinia = createPinia()
const app = createApp(App)
app.use(pinia)
app.use(router)
app.mount('#app')
