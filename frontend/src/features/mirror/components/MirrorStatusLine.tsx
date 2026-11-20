// 舞台下方的状态行：状态 / 分辨率 / 渲染帧率 / 丢帧 / 解码模式 / 待发送输入。
//
// 设计约束（按用户反馈）：镜像舞台里**只允许出现设备屏幕**，其它文案一律放外面。
// 本行固定高度 + tabular-nums + 不换行（超出省略），保证操作时不会有任何布局跳动。

import { Tooltip, Typography } from 'antd'
import type { MirrorSession } from '../hooks/useMirrorSession'
import type { MirrorUiStatus } from '../types'

export interface MirrorStatusLineProps {
  serial: string
  session: MirrorSession
  pendingText?: string
  busy?: boolean
}

const STATUS_TEXT: Record<MirrorUiStatus, string> = {
  idle: '未启动',
  checking: '检查中',
  starting: '启动服务',
  connecting: '连接中',
  'waiting-keyframe': '等待关键帧',
  streaming: '镜像中',
  reconnecting: '重连中',
  error: '出错',
}

const STATUS_TONE: Record<MirrorUiStatus, string> = {
  idle: 'idle',
  checking: 'busy',
  starting: 'busy',
  connecting: 'busy',
  'waiting-keyframe': 'warn',
  streaming: 'live',
  reconnecting: 'warn',
  error: 'error',
}

export function MirrorStatusLine({ serial, session, pendingText, busy }: MirrorStatusLineProps) {
  const { status, hello, stats, decoderMode } = session
  const resolved = hello && hello.width > 0 && hello.height > 0
  const input = pendingText?.trim()

  return (
    <div className="mirror-statusline" role="status" aria-live="polite">
      <span className={`mirror-dot mirror-dot-${STATUS_TONE[status]}`} aria-hidden />
      <span className="mirror-statusline-strong">{STATUS_TEXT[status]}</span>
      <span className="mirror-statusline-sep" aria-hidden />
      <Tooltip title="设备序列号">
        <span className="mirror-statusline-item mirror-statusline-serial">{serial}</span>
      </Tooltip>
      <span className="mirror-statusline-item">
        {resolved ? `${hello.width}×${hello.height}` : '分辨率 —'}
        {resolved && hello.rotation ? ` / ${hello.rotation}°` : ''}
      </span>
      <span className="mirror-statusline-item">{stats.fps} fps</span>
      {(stats.dropped ?? 0) > 0 ? (
        <Tooltip title="服务端丢帧 + 解码队列主动丢帧（为保低延迟而丢）">
          <span className="mirror-statusline-item mirror-statusline-warn">丢帧 {stats.dropped}</span>
        </Tooltip>
      ) : null}
      {decoderMode ? (
        <Tooltip title={decoderMode === 'annexb' ? 'VideoDecoder 直收 Annex B' : 'description=avcC 回退路径'}>
          <span className="mirror-statusline-item mirror-statusline-muted">
            {decoderMode === 'annexb' ? 'Annex B' : 'avcC'}
          </span>
        </Tooltip>
      ) : null}
      <span className="mirror-statusline-spacer" />
      {busy ? <span className="mirror-statusline-item mirror-statusline-muted">提交中…</span> : null}
      {input ? (
        <Tooltip title={`待发送：${input}（回车发送）`}>
          <span className="mirror-statusline-item mirror-statusline-input">输入：{input}</span>
        </Tooltip>
      ) : (
        <Typography.Text className="mirror-statusline-item mirror-statusline-muted mirror-statusline-hint">
          键盘打字 → 回车发送
        </Typography.Text>
      )}
    </div>
  )
}
