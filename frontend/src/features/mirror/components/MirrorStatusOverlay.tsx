// 舞台占位层：**只在没有画面可显示时**出现（未启动 / 启动中 / 出错 / 从没收到过帧）。
//
// 一旦出过帧就永不再覆盖舞台——避免操作过程中状态在 streaming ↔ waiting-keyframe 之间
// 抖动时，文字层反复盖住/移开设备画面（用户反馈的“文案动来动去”）。

import { Button, Empty, Spin, Typography } from 'antd'
import type { MirrorHelloInfo, MirrorUiStatus } from '../types'

export interface MirrorStatusOverlayProps {
  status: MirrorUiStatus
  error: string | null
  hello: MirrorHelloInfo | null
  onStart: () => void
  onRetry: () => void
  /** 是否已经显示过画面（解码过帧）：true 时不再覆盖舞台 */
  hasFrame: boolean
}

const SPINNING: MirrorUiStatus[] = ['checking', 'starting', 'connecting', 'waiting-keyframe', 'reconnecting']

export function MirrorStatusOverlay({
  status,
  error,
  hello,
  onStart,
  onRetry,
  hasFrame,
}: MirrorStatusOverlayProps) {
  // 出过画面就不再遮挡：断流时宁可直接看到最后一帧（下方状态行会说明状态）
  if (hasFrame || status === 'streaming') return null

  const spinning = SPINNING.includes(status)
  const showHello = hello && hello.width > 0 && hello.height > 0

  return (
    <div className={`mirror-placeholder${spinning ? ' is-busy' : ''}`}>
      {status === 'idle' ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={null} /> : null}
      {spinning ? <Spin size="large" /> : null}

      <Typography.Text className="mirror-placeholder-title">
        {status === 'idle'
          ? '镜像未启动'
          : status === 'error'
            ? '镜像启动失败'
            : status === 'reconnecting'
              ? '连接断开，正在重连…'
              : '正在准备画面…'}
      </Typography.Text>

      {error ? <Typography.Text className="mirror-placeholder-error">{error}</Typography.Text> : null}
      {!error && showHello && status !== 'idle' ? (
        <Typography.Text className="mirror-placeholder-sub">
          {hello.width}×{hello.height}
          {hello.codec ? ` · ${hello.codec}` : ''}
        </Typography.Text>
      ) : null}

      {status === 'idle' ? (
        <Button type="primary" onClick={onStart}>
          启动镜像
        </Button>
      ) : null}
      {status === 'error' ? (
        <Button onClick={onRetry}>重试</Button>
      ) : null}
      {status === 'idle' ? (
        <Typography.Text className="mirror-placeholder-hint">
          需要浏览器支持 WebCodecs（Chrome / Edge 新版）。不支持时可用“物理录制”。
        </Typography.Text>
      ) : null}
    </div>
  )
}
