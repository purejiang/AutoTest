// 应用外壳：Sider 菜单 + Header(后端健康) + 路由表
import { lazy, Suspense, useEffect, useMemo } from 'react'
import {
  Badge,
  Button,
  Layout,
  Menu,
  Result,
  Space,
  Spin,
  Tooltip,
  Typography,
  type MenuProps,
} from 'antd'
import {
  FileTextOutlined,
  MobileOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SettingOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons'
import { Navigate, Outlet, useLocation, useNavigate, Routes, Route } from 'react-router-dom'

import { setAuthToken } from '@/api/client'
import { useHealth, useSettings } from '@/api/settings'

const DevicesPage = lazy(() => import('@/features/devices'))
const ScriptsPage = lazy(() => import('@/features/scripts'))
const ScriptEditorPage = lazy(() => import('@/features/scripts/ScriptEditorPage'))
const RunsPage = lazy(() => import('@/features/runs'))
const RunDetailPage = lazy(() => import('@/features/runs/RunDetailPage'))
const RecorderPage = lazy(() => import('@/features/recorder'))
const RecordingDetailPage = lazy(() => import('@/features/recorder/RecordingDetailPage'))
const SettingsPage = lazy(() => import('@/features/settings'))

const { Header, Sider, Content } = Layout

const NAV_ITEMS: MenuProps['items'] = [
  { key: '/devices', icon: <MobileOutlined />, label: '设备' },
  { key: '/scripts', icon: <FileTextOutlined />, label: '脚本' },
  { key: '/runs', icon: <PlayCircleOutlined />, label: '运行' },
  { key: '/recorder', icon: <VideoCameraOutlined />, label: '录制' },
  { key: '/settings', icon: <SettingOutlined />, label: '设置' },
]

const PAGE_TITLES: Record<string, string> = {
  '/devices': '设备',
  '/scripts': '脚本',
  '/runs': '运行',
  '/recorder': '录制',
  '/settings': '设置',
}

function activeMenuKey(pathname: string): string {
  const match = NAV_ITEMS?.find((item) => {
    const key = String(item?.key ?? '')
    return key && (pathname === key || pathname.startsWith(`${key}/`))
  })
  return match ? String(match.key) : '/devices'
}

function pageTitle(pathname: string): string {
  const key = activeMenuKey(pathname)
  if (pathname.startsWith('/scripts/')) return '脚本编辑器'
  if (pathname.startsWith('/runs/')) return '运行详情'
  if (pathname.startsWith('/recorder/')) return '录制详情'
  return PAGE_TITLES[key] ?? 'AutoTest'
}

/** 把 settings.effective["security.token"] 注入 api/client（所有 fetch 统一带 Bearer）。 */
function AuthTokenBridge() {
  const { data } = useSettings()

  useEffect(() => {
    const value = data?.effective?.['security.token']
    setAuthToken(typeof value === 'string' ? value : '')
  }, [data])

  return null
}

function BackendHealth() {
  const { data, isError, isLoading, isFetching, refetch } = useHealth()

  if (isLoading) {
    return <Spin size="small" />
  }

  if (isError || !data) {
    return (
      <Space size={4}>
        <Badge status="error" text="后端离线" />
        <Button
          size="small"
          type="text"
          icon={<ReloadOutlined />}
          loading={isFetching}
          onClick={() => void refetch()}
          title="重试连接"
        />
      </Space>
    )
  }

  const backendCommit = data.build?.commit ?? '未知'
  const backendDirty = data.build?.dirty ? ' (+未提交改动)' : ''
  const buildTime = (() => {
    try {
      return new Date(__APP_BUILD_TIME__).toLocaleString('zh-CN', { hour12: false })
    } catch {
      return __APP_BUILD_TIME__
    }
  })()
  const tooltip = [
    `状态：${data.status}`,
    `后端版本：${data.version} · 提交 ${backendCommit}${backendDirty}`,
    `前端版本：${__APP_VERSION__} · 提交 ${__APP_COMMIT__} · 构建 ${buildTime}`,
    `数据库：${data.db}`,
    `adb：${data.adb?.path ?? '—'} ${data.adb?.version ?? ''}`,
    `数据目录：${data.data_dir}`,
  ].join('\n')

  return (
    <Tooltip title={<pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{tooltip}</pre>}>
      <Space size={6}>
        <Badge status="success" text={`后端 ${data.version}`} />
        <Typography.Text type="secondary" className="small-text">
          页面 ${__APP_VERSION__}·{__APP_COMMIT__}
        </Typography.Text>
        <Typography.Text type="secondary" className="small-text">
          adb 设备 {data.adb?.devices ?? 0}
        </Typography.Text>
        <Button
          size="small"
          type="text"
          icon={<ReloadOutlined />}
          loading={isFetching}
          onClick={() => void refetch()}
          title="刷新状态"
        />
      </Space>
    </Tooltip>
  )
}

function NotFoundPage() {
  const navigate = useNavigate()
  return (
    <Result
      status="404"
      title="404"
      subTitle="页面不存在"
      extra={
        <Button type="primary" onClick={() => navigate('/devices')}>
          回到设备页
        </Button>
      }
    />
  )
}

function AppLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const selectedKeys = useMemo(() => [activeMenuKey(location.pathname)], [location.pathname])

  useEffect(() => {
    document.title = `${pageTitle(location.pathname)} · AutoTest`
  }, [location.pathname])

  return (
    <Layout className="app-layout">
      <AuthTokenBridge />
      <Sider width={190} theme="dark">
        <div className="app-logo">
          AutoTest
          <span className="app-logo-sub">Android 自动化</span>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={selectedKeys}
          items={NAV_ITEMS}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header className="app-header">
          <span className="app-header-title">{pageTitle(location.pathname)}</span>
          <BackendHealth />
        </Header>
        <Content className="app-content">
          <Suspense
            fallback={
              <div className="app-loading">
                <Spin tip="页面加载中…" size="large">
                  <div style={{ padding: 50 }} />
                </Spin>
              </div>
            }
          >
            <Outlet />
          </Suspense>
        </Content>
      </Layout>
    </Layout>
  )
}

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/devices" replace />} />
        <Route path="devices" element={<DevicesPage />} />
        <Route path="scripts" element={<ScriptsPage />} />
        <Route path="scripts/:id" element={<ScriptEditorPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/:id" element={<RunDetailPage />} />
        <Route path="recorder" element={<RecorderPage />} />
        <Route path="recorder/:id" element={<RecordingDetailPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
