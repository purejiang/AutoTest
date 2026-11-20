// 首帧/断流状态覆盖层：连接中 / 启动服务 / 等待关键帧 / 错误（带重试）。
// 只在非 streaming 状态显示；streaming 时覆盖层整块移除，避免遮挡 pointer 事件。

import { Alert, Button, Empty, Space, Spin, Typography } from 'antd'
import type { MirrorHelloInfo, MirrorUiStatus } from '../types'

export interface MirrorStatusOverlayProps {
  status: MirrorUiStatus
  error: string | null
  hello: MirrorHelloInfo | null
  onStart: () => void
  onRetry: () => void
}

const TITLES: Record<MirrorUiStatus, string> = {
  idle: '镜像未启动',
  checking: '正在检查镜像状态…',
  starting: '正在启动镜像服务…',
  connecting: '正在连接镜像 WebSocket…',
  'waiting-keyframe': '等待关键帧…',
  streaming: '',
  reconnecting: '连接断开，正在自动重连…',
  error: '镜像出错',
}

const SPINNING: MirrorUiStatus[] = ['checking', 'starting', 'connecting', 'waiting-keyframe', 'reconnecting']

export function MirrorStatusOverlay({ status, error, hello, onStart, onRetry }: MirrorStatusOverlayProps) {
  if (status === 'streaming') return null
  const spinning = SPINNING.includes(status)
  const showHello = hello && hello.width > 0 && hello.height > 0

  return (
    <div className="mirror-overlay">
      <div className="mirror-overlay-card">
        {status === 'idle' ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<span style={{ color: 'rgba(255,255,255,0.75)' }}>镜像服务未启动</span>}
          />
        ) : spinning ? (
          <Spin size="large" />
        ) : null}

        <Typography.Text className="mirror-overlay-title">{TITLES[status]}</Typography.Text>

        {showHello ? (
          <Typography.Text className="mirror-overlay-sub">
            {hello.width}×{hello.height}
            {hello.rotation ? ` · 旋转 ${hello.rotation}°` : ''}
            {hello.codec ? ` · ${hello.codec}` : ''}
          </Typography.Text>
        ) : null}

        {error ? (
          <Alert
            type={status === 'error' ? 'error' : 'warning'}
            showIcon
            message={error}
            className="mirror-overlay-alert"
          />
        ) : null}

        <Space>
          {status === 'idle' ? (
            <Button type="primary" onClick={onStart}>
              启动镜像
            </Button>
          ) : null}
          {status === 'error' || status === 'reconnecting' || status === 'waiting-keyframe' ? (
            <Button type="primary" onClick={onRetry}>
              重试
            </Button>
          ) : null}
        </Space>

        {status === 'idle' ? (
          <Typography.Text className="mirror-overlay-hint">
            启动后通过 scrcpy + WebCodecs 实时镜像；无 WebCodecs 的浏览器请改用物理录制。
          </Typography.Text>
        ) : null}
      </div>
    </div>
  )
}
