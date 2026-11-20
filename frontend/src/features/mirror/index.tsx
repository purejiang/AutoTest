// 镜像面板（fe-core 在 /devices 页面挂载，依赖的公开签名为 MirrorPanel({ serial, onDeviceChange? })）。
//
// 组成：工具栏 + canvas 舞台 + 首帧/断流状态覆盖层 + 录制工具条。
// 输入：pointer 手势映射为归一化坐标 REST（tap/long_press/swipe）；键盘映射为 text/back。
// 无 WebCodecs：渲染引导页，不做截图轮询。

import { useEffect, useRef, useState } from 'react'
import { Alert, Typography } from 'antd'
import { useMirrorSession } from './hooks/useMirrorSession'
import { usePointerGestures } from './hooks/usePointerGestures'
import { useKeyboardInput } from './hooks/useKeyboardInput'
import { MirrorToolbar } from './components/MirrorToolbar'
import { MirrorStatusOverlay } from './components/MirrorStatusOverlay'
import { RecordingBar } from './components/RecordingBar'
import { UnsupportedNotice } from './components/UnsupportedNotice'
import { maybeRunSelfTestInDev } from './dev/selfTest'
import './mirror.css'

export interface MirrorPanelProps {
  /** 设备序列号（fe-core 设备列表选中项） */
  serial: string
  /** 设备状态变化（如录制停止后刷新列表） */
  onDeviceChange?: () => void
  /** 可选：镜像 WS 鉴权 token（配置了 security.token 时由上层传入） */
  token?: string
  /**
   * 是否渲染内置录制工具条（默认 true，契约 §12 要求录制工具条在镜像页）。
   * fe-core 若在设备页另挂了录制工具条，可传 false 避免重复入口。
   */
  showRecordingBar?: boolean
}

export default function MirrorPanel({
  serial,
  onDeviceChange,
  token,
  showRecordingBar = true,
}: MirrorPanelProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const session = useMirrorSession({ serial, token, canvasRef })
  const gestures = usePointerGestures({
    serial,
    canvasRef,
    enabled: session.status === 'streaming',
    onError: setActionError,
  })
  const keyboard = useKeyboardInput({
    serial,
    enabled: session.status === 'streaming' && session.isSupported,
    onError: setActionError,
  })

  // 切换设备时清掉上一台设备的操作错误
  useEffect(() => {
    setActionError(null)
  }, [serial])

  // 操作类错误 5s 自动消失
  useEffect(() => {
    if (!actionError) return
    const timer = setTimeout(() => setActionError(null), 5000)
    return () => clearTimeout(timer)
  }, [actionError])

  // 开发期自检入口：URL 带 ?mirror_selftest=1 时执行 h264/WS 帧解析断言（不暴露生产 UI）
  useEffect(() => {
    maybeRunSelfTestInDev()
  }, [])

  const focusPanel = (): void => {
    rootRef.current?.focus({ preventScroll: true })
  }

  const handlePointerDown = (
    event: Parameters<typeof gestures.handlers.onPointerDown>[0],
  ): void => {
    focusPanel()
    gestures.handlers.onPointerDown(event)
  }

  return (
    <div
      className="mirror-panel"
      ref={rootRef}
      tabIndex={0}
      onKeyDown={keyboard.onKeyDown}
      aria-label={`设备 ${serial} 镜像面板`}
    >
      <MirrorToolbar serial={serial} session={session} inputBusy={gestures.busy} />

      {actionError ? (
        <Alert
          className="mirror-action-error"
          type="error"
          showIcon
          closable
          message={actionError}
          onClose={() => setActionError(null)}
        />
      ) : null}

      {!session.isSupported ? (
        <UnsupportedNotice />
      ) : (
        <>
          <div className="mirror-stage" onClick={focusPanel}>
            <canvas
              ref={canvasRef}
              className="mirror-canvas"
              {...gestures.handlers}
              onPointerDown={handlePointerDown}
            />
            <MirrorStatusOverlay
              status={session.status}
              error={session.error}
              hello={session.hello}
              onStart={() => void session.startMirror()}
              onRetry={session.reconnect}
            />
            {keyboard.pendingText ? (
              <div className="mirror-typing-hint">待发送输入：{keyboard.pendingText}</div>
            ) : null}
          </div>
          <Typography.Text type="secondary" className="mirror-hint">
            点击画布聚焦后可直接键盘输入（回车发送文本，Esc / Backspace 返回）；拖拽 = swipe，长按 ≥500ms =
            long_press，点击 = tap；滚轮 = 上下滑动。
          </Typography.Text>
        </>
      )}

      {showRecordingBar ? (
        <RecordingBar serial={serial} onDeviceChange={onDeviceChange} disabled={!session.isSupported} />
      ) : null}
    </div>
  )
}
