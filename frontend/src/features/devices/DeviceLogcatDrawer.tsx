// logcat 抽屉：刷新取最近 N 行，可自动刷新
import { Alert, App, Button, Drawer, Empty, Skeleton, Space, Switch, Typography } from 'antd'
import { CopyOutlined, ReloadOutlined } from '@ant-design/icons'

import { describeApiError } from '@/api/client'
import { useDeviceLogcat } from '@/api/devices'
import { useUiStore } from '@/store/ui'

export interface DeviceLogcatDrawerProps {
  serial: string | null
  open: boolean
  onClose: () => void
}

export default function DeviceLogcatDrawer({ serial, open, onClose }: DeviceLogcatDrawerProps) {
  const { message } = App.useApp()
  const autoRefresh = useUiStore((state) => state.logcatAutoRefresh)
  const setAutoRefresh = useUiStore((state) => state.setLogcatAutoRefresh)
  const query = useDeviceLogcat(serial, 200, {
    enabled: open,
    refetchInterval: autoRefresh ? 3000 : false,
  })

  const lines = query.data ?? []

  const handleCopy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(lines.join('\n'))
      message.success('logcat 已复制到剪贴板')
    } catch {
      message.error('复制失败（浏览器不允许剪贴板）')
    }
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={760}
      title={
        <Space>
          <span>logcat</span>
          <Typography.Text type="secondary" className="code-text">
            {serial ?? ''}
          </Typography.Text>
          <Typography.Text type="secondary" className="small-text">
            最近 {lines.length} 行
          </Typography.Text>
        </Space>
      }
      extra={
        <Space>
          <Switch checked={autoRefresh} onChange={setAutoRefresh} checkedChildren="自动刷新" unCheckedChildren="手动" />
          <Button size="small" icon={<CopyOutlined />} onClick={() => void handleCopy()} disabled={!lines.length}>
            复制
          </Button>
          <Button size="small" icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => void query.refetch()}>
            刷新
          </Button>
        </Space>
      }
      styles={{ body: { padding: 12, height: '100%', background: '#0b0b0b' } }}
    >
      {query.isLoading ? (
        <Skeleton active paragraph={{ rows: 12 }} />
      ) : query.isError ? (
        <Alert type="error" showIcon message="logcat 获取失败" description={describeApiError(query.error)} />
      ) : lines.length === 0 ? (
        <Empty description="暂无日志" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ marginTop: 80 }} />
      ) : (
        <pre className="logcat-pre">{lines.join('\n')}</pre>
      )}
    </Drawer>
  )
}
