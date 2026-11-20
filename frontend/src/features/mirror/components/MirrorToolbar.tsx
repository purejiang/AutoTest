// 镜像工具栏：状态/分辨率/旋转/fps/丢帧 + 启动、停止、重连、请求关键帧。

import { Button, Space, Tag, Typography } from 'antd'
import type { MirrorSession } from '../hooks/useMirrorSession'
import type { MirrorUiStatus } from '../types'

export interface MirrorToolbarProps {
  serial: string
  session: MirrorSession
  inputBusy?: boolean
}

const STATUS_META: Record<MirrorUiStatus, { text: string; color: string }> = {
  idle: { text: '未启动', color: 'default' },
  checking: { text: '检查状态', color: 'processing' },
  starting: { text: '启动服务', color: 'processing' },
  connecting: { text: '连接中', color: 'processing' },
  'waiting-keyframe': { text: '等待关键帧', color: 'warning' },
  streaming: { text: '已连接', color: 'success' },
  reconnecting: { text: '重连中', color: 'warning' },
  error: { text: '错误', color: 'error' },
}

export function MirrorToolbar({ serial, session, inputBusy }: MirrorToolbarProps) {
  const { status, hello, stats, decoderMode, isSupported } = session
  const meta = STATUS_META[status]
  const busy = status === 'starting' || status === 'checking'
  const resolved = hello && hello.width > 0 && hello.height > 0

  return (
    <Space wrap size={[10, 8]} className="mirror-toolbar" style={{ width: '100%' }}>
      <Tag color={meta.color}>{meta.text}</Tag>
      <Typography.Text type="secondary" title={`serial: ${serial}`}>
        {serial}
      </Typography.Text>
      {resolved ? (
        <Typography.Text>
          {hello.width}×{hello.height}
          {hello.rotation ? ` · 旋转 ${hello.rotation}°` : ''}
        </Typography.Text>
      ) : (
        <Typography.Text type="secondary">分辨率未知</Typography.Text>
      )}
      <Typography.Text type="secondary">渲染 {stats.fps} fps</Typography.Text>
      <Typography.Text type="secondary" title="服务端丢帧 + 解码队列主动丢帧">
        丢帧 {stats.dropped}
        {stats.queueSize > 0 ? ` · 队列 ${stats.queueSize}` : ''}
      </Typography.Text>
      {stats.serverFps !== undefined ? (
        <Typography.Text type="secondary">服务端 {stats.serverFps} fps</Typography.Text>
      ) : null}
      {decoderMode ? (
        <Tag color={decoderMode === 'annexb' ? 'geekblue' : 'purple'} title={decoderMode === 'annexb' ? 'VideoDecoder 直收 Annex B' : 'description=avcC 回退路径'}>
          {decoderMode === 'annexb' ? 'Annex B' : 'avcC'}
        </Tag>
      ) : null}
      {inputBusy ? <Tag color="orange">操作提交中…</Tag> : null}

      <span style={{ flex: 1 }} />

      {status === 'idle' ? (
        <Button type="primary" disabled={!isSupported} onClick={() => void session.startMirror()}>
          启动镜像
        </Button>
      ) : null}
      {status === 'starting' ? (
        <Button type="primary" loading disabled>
          启动中
        </Button>
      ) : null}
      {status !== 'idle' && status !== 'starting' ? (
        <Button danger onClick={() => void session.stopMirror()}>
          停止镜像
        </Button>
      ) : null}
      <Button onClick={session.reconnect} disabled={!isSupported || busy}>
        重新连接
      </Button>
      <Button
        onClick={session.requestKeyframe}
        disabled={status !== 'streaming' && status !== 'waiting-keyframe' && status !== 'reconnecting'}
      >
        请求关键帧
      </Button>
    </Space>
  )
}
