// 设备截图查看（modal 放大 + 下载 + 刷新）
import { useEffect, useState } from 'react'
import { Alert, App, Button, Empty, Modal, Skeleton, Space } from 'antd'
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'

import { describeApiError } from '@/api/client'
import { useDeviceScreenshot } from '@/api/devices'

export interface ScreenshotModalProps {
  serial: string | null
  open: boolean
  onClose: () => void
}

export default function ScreenshotModal({ serial, open, onClose }: ScreenshotModalProps) {
  const { message } = App.useApp()
  const [nonce, setNonce] = useState(0)
  const query = useDeviceScreenshot(serial, { enabled: open, nonce })
  const [objectUrl, setObjectUrl] = useState<string | null>(null)

  useEffect(() => {
    if (!query.data) {
      setObjectUrl(null)
      return
    }
    const url = URL.createObjectURL(query.data)
    setObjectUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [query.data])

  const handleDownload = (): void => {
    if (!objectUrl) return
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = `screenshot-${(serial ?? 'device').replace(/[^\w.-]+/g, '_')}.png`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    message.success('开始下载截图')
  }

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width={780}
      title={`截图 · ${serial ?? ''}`}
      footer={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => setNonce((value) => value + 1)} loading={query.isFetching}>
            刷新
          </Button>
          <Button icon={<DownloadOutlined />} onClick={handleDownload} disabled={!objectUrl} type="primary">
            下载
          </Button>
        </Space>
      }
    >
      {query.isError ? (
        <Alert type="error" showIcon message="截图失败" description={describeApiError(query.error)} />
      ) : !objectUrl ? (
        <Skeleton.Image active style={{ width: '100%', height: 360 }} />
      ) : (
        <div style={{ textAlign: 'center', background: '#fafafa', borderRadius: 8, padding: 8 }}>
          <img src={objectUrl} alt={`${serial} 截图`} style={{ maxWidth: '100%', maxHeight: '70vh' }} />
        </div>
      )}
      {!query.isError && !objectUrl && !query.isFetching ? (
        <Empty description="暂无截图" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : null}
    </Modal>
  )
}
