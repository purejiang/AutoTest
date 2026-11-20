import { useEffect } from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'

import App from './App'
import {
  ApiError,
  describeApiError,
  notifyApiError,
  setApiErrorNotifier,
} from '@/api/client'
import './index.css'

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

/** 把 antd message 注入到 api/client 的统一错误提示里。 */
function ApiErrorBridge() {
  const { message } = AntdApp.useApp()

  useEffect(() => {
    setApiErrorNotifier((error) => {
      message.error({
        content: describeApiError(error),
        key: `api-error-${error.code}`,
        duration: 5,
      })
    })
    return () => setApiErrorNotifier(null)
  }, [message])

  return null
}

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <ConfigProvider
    locale={zhCN}
    theme={{
      token: {
        colorPrimary: '#1677ff',
        borderRadius: 6,
        fontSize: 13,
      },
    }}
  >
    <AntdApp>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <ApiErrorBridge />
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </AntdApp>
  </ConfigProvider>,
)
