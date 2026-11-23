import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import { MutationCache, QueryCache, QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'

import App from './App.vue'
import router from './router'
import { ApiError, describeApiError, notifyApiError, setApiErrorNotifier } from '@/api/client'
import './styles/index.css'

dayjs.locale('zh-cn')

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error, query) => {
      // 允许单查询标记 meta.suppressError（如健康检查离线是预期状态）
      if (query.meta?.suppressError) return
      notifyApiError(error)
    },
  }),
  mutationCache: new MutationCache({
    onError: (error) => {
      notifyApiError(error)
    },
  }),
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (!(error instanceof ApiError)) return false
        if (error.isNetworkError || error.status >= 500) return failureCount < 1
        return false
      },
      refetchOnWindowFocus: false,
      staleTime: 3000,
      gcTime: 5 * 60 * 1000,
    },
    mutations: {
      retry: false,
    },
  },
})

setApiErrorNotifier((error) => {
  ElMessage.error({
    message: describeApiError(error),
    grouping: true,
    duration: 5000,
  })
})

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.use(VueQueryPlugin, { queryClient })
app.mount('#app')
